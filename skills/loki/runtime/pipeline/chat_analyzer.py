"""채팅 기반 하이라이트 추출 (chzzk_editor.py 분석 알고리즘 재사용)"""

import logging

import math

import re

from collections import Counter, defaultdict

logger = logging.getLogger("pipeline")

WINDOW_SEC = 10

PEAK_MERGE_SEC = 30

Z_THRESHOLD = 2.0

_COMMAND_PREFIX_PATTERN = re.compile(r'^!\s*[\w가-힣]')

_LAUGHTER_PATTERNS = [
    re.compile(r'ㅋ{2,}'),
    re.compile(r'ㅎ{2,}'),
    re.compile(r'개웃|웃(?:기|겨|김|기네|긴|겼|긴다|기다|음)'),
    re.compile(r'재밌|재미있'),
    re.compile(r'(?:아니|뭐야|진짜|미치겠|미쳤|실화).{0,20}ㅋ{2,}'),
]

_LAUGHTER_REPEAT_PATTERN = re.compile(r'ㅋ{2,}|ㅎ{2,}')

_PUNCT_SPACE_PATTERN = re.compile(r'[\s\W_]+', re.UNICODE)

_REACTION_FAMILIES = ("surprise", "question")

_REACTION_SIGNAL_KEYS = {
    "surprise": "reaction_surprise",
    "question": "reaction_question",
}

_REACTION_FAMILY_PATTERNS = {
    "surprise": [
        re.compile(r'헐+|헉+|ㄷ{2,}|와{2,}|우와+'),
        re.compile(r'대박|미쳤|미친|레전드|뭐야|wow|wtf|omg', re.IGNORECASE),
    ],
    "question": [
        re.compile(r'[?？]+'),
        re.compile(r'뭐(?:야|지|임|냐)|왜|어케|어떻게'),
    ],
}

def _is_command_message(msg: str) -> bool:
    if not msg:
        return False
    return bool(_COMMAND_PREFIX_PATTERN.match(msg))

def _filter_command_messages(chats: list[dict]) -> list[dict]:
    """투표/명령어 채팅 제외. peak 분석 + LLM 입력에서 노이즈로 작용."""
    return [c for c in chats if not _is_command_message(c.get("msg", ""))]

def _is_donation_event(chat: dict) -> bool:
    evidence = chat.get("chat_evidence")
    return isinstance(evidence, dict) and evidence.get("event") == "donation"

def normalize_laughter_message(msg: str) -> str:
    """웃음 echo 비교용 정규화. ㅋㅋ 길이 차이와 공백/문장부호 차이를 흡수한다."""
    text = (msg or "").lower()
    text = _LAUGHTER_REPEAT_PATTERN.sub(lambda m: m.group(0)[0] * 2, text)
    return _PUNCT_SPACE_PATTERN.sub("", text)

def is_laughter_message(msg: str) -> bool:
    """`ㅋㅋ+문장`을 포함한 한국어 웃음/재미 반응 여부."""
    if not msg:
        return False
    return any(pattern.search(msg) for pattern in _LAUGHTER_PATTERNS)

def _reaction_families_for_message(msg: str) -> list[str]:
    if not msg:
        return []
    return [
        family
        for family in _REACTION_FAMILIES
        if any(pattern.search(msg) for pattern in _REACTION_FAMILY_PATTERNS[family])
    ]

def is_surprise_message(msg: str) -> bool:
    """헉/헐/ㄷㄷ/대박 계열 놀람 반응 여부."""
    return "surprise" in _reaction_families_for_message(msg)

def is_question_message(msg: str) -> bool:
    """물음표/뭐야 계열 질문 반응 여부."""
    return "question" in _reaction_families_for_message(msg)

def _chat_user_key(chat: dict, index: int) -> str:
    """도배 방지용 유저 키. uid가 없으면 nick, 둘 다 없으면 행 위치로 fallback."""
    return str(chat.get("uid") or chat.get("user") or chat.get("nick") or f"row:{index}")

KEYWORD_WEIGHTS = {
    r'ㅋ{2,}':          1.0,
    r'ㅎ{2,}':          0.8,
    r'lol|lmao|ㄹㅇㅋ': 1.0,
    r'웃기|재밌|재미있': 0.8,
    r'ㄷ{2,}':          1.5,
    r'대박|미쳤|레전드|ㄹㅈㄷ': 1.5,
    r'와{2,}|우와+|오{3,}': 1.2,
    r'헐{1,}|헉{1,}':   1.2,
    r'wow|wtf|omg':     1.2,
    r'클립|편집|이거다|명장면': 2.5,
    r'저장|캡처|스샷':  2.0,
    r'하이라이트|짤':   2.0,
    r'ㅠ{2,}|ㅜ{2,}':   0.8,
    r'울었|눈물|감동':  1.0,
    r'ㅅㅂ|시발|존나|개ㅈ': 0.9,
    r'화남|열받|빡침':  0.9,
    r'후원|도네|구독|응원': 1.8,
    r'치어스|cheer':    1.8,
}

_compiled_keywords = [(re.compile(pat, re.IGNORECASE), w) for pat, w in KEYWORD_WEIGHTS.items()]

def score_message(msg: str) -> float:
    score = 0.0
    for pattern, weight in _compiled_keywords:
        if pattern.search(msg):
            score += weight
    return max(1.0, score)

def build_time_series(chats: list[dict], window_sec: int = WINDOW_SEC) -> dict:
    donation_rows = [chat for chat in chats if _is_donation_event(chat)]
    chats = _filter_command_messages(chats)
    if not chats and not donation_rows:
        return {"buckets": {}, "duration_sec": 0}

    buckets = defaultdict(lambda: {
        "count": 0,
        "score": 0.0,
        "laughter_count": 0,
        "laughter_users": set(),
        "laughter_norm_counts": Counter(),
        "laughter_norm_users": defaultdict(set),
        "reaction_counts": Counter(),
        "reaction_users": defaultdict(set),
        "donation_count": 0,
    })

    duration_ms = max(c["ms"] for c in (chats or donation_rows))

    for index, chat in enumerate(chats):
        sec = chat["ms"] / 1000.0
        bucket = int(sec // window_sec) * window_sec
        buckets[bucket]["count"] += 1
        msg = chat.get("msg", "")
        buckets[bucket]["score"] += score_message(msg)
        user_key = _chat_user_key(chat, index)
        if is_laughter_message(msg):
            normalized = normalize_laughter_message(msg)
            buckets[bucket]["laughter_count"] += 1
            buckets[bucket]["laughter_users"].add(user_key)
            if normalized:
                buckets[bucket]["laughter_norm_counts"][normalized] += 1
                buckets[bucket]["laughter_norm_users"][normalized].add(user_key)
        for family in _reaction_families_for_message(msg):
            buckets[bucket]["reaction_counts"][family] += 1
            buckets[bucket]["reaction_users"][family].add(user_key)



    for chat in donation_rows:
        sec = chat["ms"] / 1000.0
        bucket = int(sec // window_sec) * window_sec
        buckets[bucket]["donation_count"] += 1

    out = {}
    for bucket, data in buckets.items():
        echo_user_counts = {
            text: len(users)
            for text, users in data["laughter_norm_users"].items()
        }
        echo_text, echo_user_max = ("", 0)
        if echo_user_counts:
            echo_text, echo_user_max = max(
                echo_user_counts.items(),
                key=lambda item: (item[1], data["laughter_norm_counts"][item[0]]),
            )
        count = data["count"] or 1
        row = {
            "count": data["count"],
            "score": data["score"],
            "laughter_count": data["laughter_count"],
            "laughter_user_count": len(data["laughter_users"]),
            "laughter_ratio": data["laughter_count"] / count,
            "laughter_echo_max": echo_user_max,
            "laughter_echo_text": echo_text,
            **(
                {"donation_count": data["donation_count"]}
                if data["donation_count"] > 0
                else {}
            ),
        }
        for family in _REACTION_FAMILIES:
            family_count = data["reaction_counts"][family]
            row[f"{family}_count"] = family_count
            row[f"{family}_user_count"] = len(data["reaction_users"][family])
            row[f"{family}_ratio"] = family_count / count
        out[bucket] = row

    return {"buckets": out, "duration_sec": duration_ms / 1000.0}

def z_score_peaks(
    buckets: dict, threshold: float = Z_THRESHOLD, *, donation_ranking: bool = False,
) -> list[dict]:
    if not donation_ranking:


        buckets = {
            t: row for t, row in buckets.items()
            if not (row.get("donation_count") and row.get("count", 0) == 0)
        }
    if not buckets:
        return []

    times = sorted(buckets.keys())
    counts = [buckets[t]["count"] for t in times]
    scores = [buckets[t]["score"] for t in times]
    laughter_strengths = [
        buckets[t].get("laughter_count", 0)
        + buckets[t].get("laughter_user_count", 0) * 1.5
        + buckets[t].get("laughter_echo_max", 0) * 2.0
        + buckets[t].get("laughter_ratio", 0.0) * 5.0
        for t in times
    ]
    reaction_strengths_by_family = {
        family: [
            buckets[t].get(f"{family}_count", 0)
            + buckets[t].get(f"{family}_user_count", 0) * 1.75
            + buckets[t].get(f"{family}_ratio", 0.0) * 4.0
            for t in times
        ]
        for family in _REACTION_FAMILIES
    }
    donation_counts = [buckets[t].get("donation_count", 0) if donation_ranking else 0 for t in times]

    def stats(lst):
        mean = sum(lst) / len(lst)
        var = sum((x - mean) ** 2 for x in lst) / len(lst)
        std = math.sqrt(var) if var > 0 else 1.0
        return mean, std

    count_mean, count_std = stats(counts)
    score_mean, score_std = stats(scores)
    laughter_mean, laughter_std = stats(laughter_strengths)
    reaction_stats = {
        family: stats(strengths)
        for family, strengths in reaction_strengths_by_family.items()
    }
    donation_mean, donation_std = stats(donation_counts)
    max_count = max(counts) or 1
    max_score = max(scores) or 1
    max_laughter = max(laughter_strengths) or 1
    max_reaction = max(
        [max(strengths) for strengths in reaction_strengths_by_family.values() if strengths] or [1]
    ) or 1
    max_donation = max(donation_counts) or 1

    peaks = []
    for index, (t, cnt, sc, laughter_strength) in enumerate(zip(times, counts, scores, laughter_strengths)):
        bucket = buckets[t]
        z_count = (cnt - count_mean) / count_std
        z_score_val = (sc - score_mean) / score_std
        z_laughter_val = (laughter_strength - laughter_mean) / laughter_std
        donation_count = bucket.get("donation_count", 0) if donation_ranking else 0
        z_donation_val = (donation_count - donation_mean) / donation_std
        donation_burst = donation_count >= 2 and z_donation_val >= threshold
        laughter_count = bucket.get("laughter_count", 0)
        laughter_user_count = bucket.get("laughter_user_count", 0)
        laughter_ratio = bucket.get("laughter_ratio", 0.0)
        laughter_echo_max = bucket.get("laughter_echo_max", 0)
        laughter_burst = (
            (laughter_count >= 5 and laughter_user_count >= 3)
            or (laughter_ratio >= 0.30 and laughter_user_count >= 3)
            or laughter_echo_max >= 3
            or (z_laughter_val >= threshold and laughter_user_count >= 2)
        )
        reaction_clusters = []
        reaction_details = {}
        strongest_reaction = 0.0
        for family in _REACTION_FAMILIES:
            family_strength = reaction_strengths_by_family[family][index]
            family_mean, family_std = reaction_stats[family]
            family_z = (family_strength - family_mean) / family_std
            family_count = bucket.get(f"{family}_count", 0)
            family_user_count = bucket.get(f"{family}_user_count", 0)
            family_ratio = bucket.get(f"{family}_ratio", 0.0)
            family_cluster = (
                (family_count >= 3 and family_user_count >= 3)
                or (family_ratio >= 0.25 and family_user_count >= 3)
                or (family_z >= threshold and family_count >= 2 and family_user_count >= 2)
            )
            if family_cluster:
                reaction_clusters.append(family)
                strongest_reaction = max(strongest_reaction, family_strength)
            reaction_details[f"{family}_cluster"] = family_cluster
            reaction_details[f"{family}_count"] = family_count
            reaction_details[f"{family}_user_count"] = family_user_count
            reaction_details[f"{family}_ratio"] = round(family_ratio, 4)
            reaction_details[f"z_{family}_reaction"] = round(family_z, 2)
        reaction_cluster = bool(reaction_clusters)
        if (
            z_count >= threshold
            or z_score_val >= threshold
            or laughter_burst
            or reaction_cluster
            or donation_burst
        ):
            base_composite = (cnt / max_count) * 0.4 + (sc / max_score) * 0.6
            laughter_component = (laughter_strength / max_laughter) * 0.25 if laughter_strength else 0.0
            reaction_component = (strongest_reaction / max_reaction) * 0.10 if strongest_reaction else 0.0
            donation_component = (donation_count / max_donation) * 0.15 if donation_count else 0.0
            composite = max(
                base_composite,
                base_composite * 0.85 + laughter_component,
                base_composite * 0.90 + reaction_component,
                base_composite * 0.90 + donation_component,
            )
            if laughter_burst:
                composite *= 1.15
            if donation_burst:
                composite *= 1.10
            composite = min(1.0, composite)
            source_signals = ["chat"]
            if z_count >= threshold:
                source_signals.append("chat_density")
            if z_score_val >= threshold:
                source_signals.append("keyword_score")
            if laughter_burst:
                source_signals.append("laughter_burst")
            if donation_burst:
                source_signals.append("donation_density")
            source_signals.extend(_REACTION_SIGNAL_KEYS[family] for family in reaction_clusters)
            peaks.append({
                "sec": t,
                "count": cnt,
                "raw_score": sc,
                "z_count": round(z_count, 2),
                "z_score": round(z_score_val, 2),
                "z_laughter": round(z_laughter_val, 2),
                "laughter_burst": laughter_burst,
                "laughter_count": laughter_count,
                "laughter_user_count": laughter_user_count,
                "laughter_ratio": round(laughter_ratio, 4),
                "laughter_echo_max": laughter_echo_max,
                "laughter_echo_text": bucket.get("laughter_echo_text", ""),
                **(
                    {
                        "donation_burst": donation_burst,
                        "donation_count": donation_count,
                        "z_donation": round(z_donation_val, 2),
                    }
                    if donation_count > 0
                    else {}
                ),
                "reaction_families": reaction_clusters,
                **reaction_details,
                "source_signals": source_signals,
                "composite": round(composite, 4),
            })
    return peaks

def merge_peaks(peaks: list[dict], merge_sec: int = PEAK_MERGE_SEC) -> list[dict]:
    if not peaks:
        return []

    def finalize(group: list[dict]) -> dict:
        best = max(
            group,
            key=lambda p: (
                p.get("composite", 0),
                bool(p.get("laughter_burst")),
                bool(p.get("reaction_families")),
            ),
        )
        best["cluster_count"] = len(group)


        best["start_sec"] = min(p["sec"] for p in group)
        best["end_sec"] = max(p["sec"] + WINDOW_SEC for p in group)
        best["peak_seconds"] = [p["sec"] for p in group]
        best["peak_count_sum"] = sum(p["count"] for p in group)
        best["laughter_burst"] = any(p.get("laughter_burst") for p in group)
        best["laughter_count_sum"] = sum(p.get("laughter_count", 0) for p in group)
        best["laughter_user_peak"] = max((p.get("laughter_user_count", 0) for p in group), default=0)
        donation_count_sum = sum(p.get("donation_count", 0) for p in group)
        if donation_count_sum:
            best["donation_burst"] = any(p.get("donation_burst") for p in group)
            best["donation_count_sum"] = donation_count_sum
        best["reaction_families"] = sorted({
            family
            for p in group
            for family in (p.get("reaction_families") or [])
        })
        for family in _REACTION_FAMILIES:
            best[f"{family}_cluster"] = any(p.get(f"{family}_cluster") for p in group)
            best[f"{family}_count_sum"] = sum(p.get(f"{family}_count", 0) for p in group)
            best[f"{family}_user_peak"] = max((p.get(f"{family}_user_count", 0) for p in group), default=0)
        best["source_signals"] = sorted({
            signal
            for p in group
            for signal in (p.get("source_signals") or [])
        })
        return best

    sorted_peaks = sorted(peaks, key=lambda p: p["sec"])
    merged = []
    group = [sorted_peaks[0]]

    for peak in sorted_peaks[1:]:
        has_reaction_cluster = peak.get("reaction_families") or any(p.get("reaction_families") for p in group)
        effective_merge_sec = 45 if (
            peak.get("laughter_burst")
            or any(p.get("laughter_burst") for p in group)
            or has_reaction_cluster
        ) else merge_sec
        if peak["sec"] - group[-1]["sec"] <= effective_merge_sec:
            group.append(peak)
        else:
            merged.append(finalize(group))
            group = [peak]

    merged.append(finalize(group))

    return merged

def compute_adaptive_top_n(
    duration_sec: int | float | None,
    *,
    per_hour: int = 2,
    min_count: int = 8,
    max_count: int = 20,
) -> int:
    """영상 길이(초) → 추출할 하이라이트 개수.

    산식: clamp(min_count, round(duration_h * per_hour), max_count).
    duration_sec 가 None / 비양수면 min_count 반환 (안전 fallback).

    예 (per_hour=6, min=8, max=40):
      0.5h → 8 (min), 1h → 8, 2h → 12, 4h → 24, 7h+ → 40 (cap).
    """
    if not duration_sec or duration_sec <= 0:
        return max(1, min_count)
    hours = duration_sec / 3600.0
    raw = round(hours * per_hour)
    return max(min_count, min(max_count, raw))

def find_edit_points(
    chats: list[dict],
    top_n: int | None = None,
    *,
    duration_sec: int | float | None = None,
    per_hour: int = 2,
    min_count: int = 8,
    max_count: int = 20,
    donation_ranking: bool = False,
) -> list[dict]:
    """메인 분석 파이프라인. composite 내림차순 정렬된 하이라이트 리스트 반환.

    top_n 명시 시 그 값을 그대로 사용 (legacy / 실험 스크립트 호환).
    None 이면 duration_sec 으로 적응형 개수 계산 (compute_adaptive_top_n).
    duration_sec 도 None 이면 chats 의 마지막 ms 에서 추정.
    """
    logger.info("채팅 분석 시작...")

    raw_count = len(chats)
    filtered_chats = _filter_command_messages(chats)
    if raw_count - len(filtered_chats):
        logger.info(
            f"  명령어/투표 필터: {raw_count - len(filtered_chats):,}개 제외 "
            f"(raw {raw_count:,} → {len(filtered_chats):,})"
        )

    top_n_source = "explicit"
    if top_n is None:
        if duration_sec is None and filtered_chats:
            duration_sec = max((c.get("ms", 0) for c in filtered_chats), default=0) / 1000.0
            top_n_source = "adaptive(chat_ms_fallback)"
        else:
            top_n_source = "adaptive(duration_sec)"
        top_n = compute_adaptive_top_n(
            duration_sec, per_hour=per_hour, min_count=min_count, max_count=max_count,
        )
        logger.info(
            f"  적응형 top_n={top_n} (source={top_n_source}, "
            f"duration_sec={duration_sec or 0:.0f}, per_hour={per_hour}, "
            f"min={min_count}, max={max_count})"
        )

    ts = build_time_series(chats if donation_ranking else filtered_chats, WINDOW_SEC)
    logger.info(f"  시계열 변환 완료: {len(ts['buckets'])}개 버킷")

    raw_peaks = z_score_peaks(ts["buckets"], Z_THRESHOLD, donation_ranking=donation_ranking)
    logger.info(f"  Z-score 피크 탐지: {len(raw_peaks)}개")

    merged = merge_peaks(raw_peaks, PEAK_MERGE_SEC)
    merged.sort(key=lambda p: p["composite"], reverse=True)

    for rank, p in enumerate(merged, 1):
        p["rank"] = rank

    logger.info(f"  최종 하이라이트: {len(merged)}개 (상위 {min(top_n, len(merged))}개 사용)")
    return merged[:top_n]
