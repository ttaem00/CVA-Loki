"""Deterministic manager-only adaptive context range builder."""

from __future__ import annotations

import hashlib

import json

import math

import re

from typing import Any, Mapping

from .compression import compress_adaptive_context_payload

from .providers.ruptures_boundaries import detect_boundary_buckets

SCHEMA_VERSION = "adaptive_context_ranges.v1"

RULE_VERSION = "adaptive_context_ranges_rules.v2"

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_SAFE_TEXT = re.compile(r"\s+")

_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")

_URL = re.compile(r"(?:https?|file|ftp)://", re.IGNORECASE)

_BUCKET_SEC = 30

_MIN_RANGE_BUCKETS = 2

_MAX_CHILD_RANGES = 120

_MAX_UNKNOWN_GAPS = 120

_MAX_SOURCE_DURATION_SEC = 48 * 60 * 60

_STATE_NOT_ANALYZED = "not_analyzed"

_STATE_NO_USEFUL = "no_useful_context_ranges"

_STATE_PARTIAL = "partial_signal_coverage"

_STATE_DISABLED = "disabled"

_PROFILES = (
    "game_match",
    "watch_event",
    "collab_multi_pov",
    "talk_story",
    "creative_work",
    "mixed_unknown",
)

_SEGMENT_KINDS = (
    "versus_bracket",
    "watch_along",
    "match_watch",
    "game_session",
    "within_game_phase",
    "collab",
    "topic_discussion",
    "transition",
    "unknown",
)

_COMMENT_LANES = {"comment_context_range", "comment_moment_seed", "comment_story_window"}

_STRUCTURE_FAMILIES = ("comment", "subtitle", "ocr", "visual", "live_context")

_INTEREST_FAMILIES = ("chat", "audio")

_EDIT_FAMILIES = ("summary",)

_ALL_FAMILIES = _STRUCTURE_FAMILIES + _INTEREST_FAMILIES + _EDIT_FAMILIES

_PROFILE_LABELS = {
    "game_match": "게임·경기",
    "watch_event": "같이보기·관전",
    "collab_multi_pov": "합방·멀티 시점",
    "talk_story": "토크·이야기",
    "creative_work": "작업·창작",
    "mixed_unknown": "공통 화면",
}

_KIND_LABELS = {
    "versus_bracket": "VS·월드컵",
    "watch_along": "같이보기",
    "match_watch": "경기 관전",
    "game_session": "게임 세션",
    "within_game_phase": "게임 안 단계",
    "collab": "합방",
    "topic_discussion": "주제 토크",
    "transition": "전환",
    "unknown": "구간 종류 확인 필요",
}

_PROFILE_FROM_HINT = {
    "game_match": "game_match",
    "watch_event": "watch_event",
    "collab_multi_pov": "collab_multi_pov",
    "talk_story": "talk_story",
    "creative_work": "creative_work",
    "mixed_unknown": "mixed_unknown",
}

_KIND_FROM_HINT = {
    "versus_bracket": "versus_bracket",
    "watch_along": "watch_along",
    "match_watch": "match_watch",
    "game_session": "game_session",
    "within_game_phase": "within_game_phase",
    "collab": "collab",
    "topic_discussion": "topic_discussion",
}

_TEXT_PATTERNS = (
    (re.compile(r"월드컵|(?<![0-9a-z])v(?:s|\.)(?![0-9a-z])", re.IGNORECASE), ("watch_event", "versus_bracket")),
    (re.compile(r"같이\s*보기|watch[\s-]?along", re.IGNORECASE), ("watch_event", "watch_along")),
    (re.compile(r"경기|관전|중계|결승|준결승", re.IGNORECASE), ("watch_event", "match_watch")),
    (re.compile(r"합방|콜라보|multi[- ]?pov|디코", re.IGNORECASE), ("collab_multi_pov", "collab")),
    (re.compile(r"라운드|보스전|퀘스트|로비|매칭|밴픽|phase|stage|level", re.IGNORECASE), ("game_match", "within_game_phase")),
    (re.compile(r"게임|플레이|랭크|솔랭|전투", re.IGNORECASE), ("game_match", "game_session")),
    (re.compile(r"잡담|토크|이야기|썰|질답|q\\s*&\\s*a", re.IGNORECASE), ("talk_story", "topic_discussion")),
    (re.compile(r"그림|드로잉|코딩|작곡|편집|제작|창작", re.IGNORECASE), ("creative_work", "transition")),
)

def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{hashlib.sha1(_canonical(payload)).hexdigest()[:16]}"

def _spread_limit(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    indexes = {
        round(index * (len(rows) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [rows[index] for index in sorted(indexes)]

def _require_token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(field_name)
    return value

def _duration(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("invalid_zero_duration")
    if value > _MAX_SOURCE_DURATION_SEC:
        raise ValueError("duration_budget_exceeded")
    return float(value)

def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)

def _safe_text(value: Any, limit: int = 120) -> str:
    text = _SAFE_TEXT.sub(" ", str(value or "")).strip()
    if (
        not text
        or _WINDOWS_PATH.match(text)
        or text.startswith(("/", "\\\\"))
        or _URL.search(text)
    ):
        return ""
    return text[:limit].rstrip()

def _bucket_count(duration_sec: float, bucket_sec: int) -> int:
    return max(1, int(math.ceil(duration_sec / bucket_sec)))

def _bucket_index(sec: float, *, duration_sec: float, bucket_sec: int) -> int:
    clipped = min(max(0.0, sec), max(0.0, duration_sec - 0.001))
    return int(clipped // bucket_sec)

def _new_bucket() -> dict[str, Any]:
    return {
        "families": {family: {"event_count": 0, "buckets": 0} for family in _ALL_FAMILIES},
        "profile_hints": [],
        "kind_hints": [],
        "activity_hints": [],
        "topic_hints": [],
        "event_ids": [],
    }

def _event_family(row: Mapping[str, Any]) -> str | None:
    lane = str(row.get("lane") or "")
    kind = str(row.get("kind") or "")
    if lane in _COMMENT_LANES:
        return "comment"
    if lane in {"subtitle_signal", "transcript_signal", "asr_signal"} or kind in {
        "subtitle",
        "transcript",
        "asr",
    }:
        return "subtitle"
    if lane == "ocr_signal" or kind in {"ocr", "ocr_signal"}:
        return "ocr"
    if lane in {"audio_peak"}:
        return "audio"
    if lane in {"visual_scene"}:
        return "visual"
    if lane in {"existing_segments", "highlight"}:
        return "summary"
    if lane in {"live_context", "context_activity_shadow", "context_topic_shadow"}:
        return "live_context"
    return None

def _kind_profile_from_text(text: str) -> tuple[str, str]:
    for pattern, result in _TEXT_PATTERNS:
        if pattern.search(text):
            return result
    return "mixed_unknown", "unknown"

def _classify_event(row: Mapping[str, Any]) -> tuple[str, str]:
    profile_hint = str(row.get("broadcast_profile_hint") or "").strip()
    kind_hint = str(row.get("content_form_hint") or row.get("segment_kind") or "").strip()
    if profile_hint in _PROFILE_FROM_HINT:
        profile = _PROFILE_FROM_HINT[profile_hint]
        kind = _KIND_FROM_HINT.get(kind_hint, "unknown")
        return profile, kind
    text = " ".join(
        value
        for value in (
            _safe_text(row.get("label"), 120),
            _safe_text(row.get("activity"), 80),
            _safe_text(row.get("topic"), 80),
        )
        if value
    )
    return _kind_profile_from_text(text)

def _accumulate_range(
    buckets: list[dict[str, Any]],
    start_sec: float,
    end_sec: float,
    *,
    duration_sec: float,
    bucket_sec: int,
    family: str,
    event_count: int = 1,
    profile_hint: str = "",
    kind_hint: str = "",
    activity_hint: str = "",
    topic_hint: str = "",
    event_id: str = "",
    hint_weight: int = 1,
) -> None:
    if family not in _ALL_FAMILIES:
        return
    start = _safe_float(start_sec)
    end = _safe_float(end_sec)
    if start is None:
        return
    if end is None or end <= start:
        end = min(duration_sec, start + bucket_sec)
    if end <= start:
        return
    start_idx = _bucket_index(start, duration_sec=duration_sec, bucket_sec=bucket_sec)
    end_idx = _bucket_index(max(start, end - 0.001), duration_sec=duration_sec, bucket_sec=bucket_sec)
    for index in range(start_idx, end_idx + 1):
        bucket = buckets[index]
        family_row = bucket["families"][family]
        family_row["event_count"] += int(event_count)
        family_row["buckets"] = 1
        if profile_hint and profile_hint != "mixed_unknown":
            bucket["profile_hints"].extend([profile_hint] * max(1, hint_weight))
        if kind_hint and kind_hint != "unknown":
            bucket["kind_hints"].extend([kind_hint] * max(1, hint_weight))
        if activity_hint:
            bucket["activity_hints"].append(activity_hint)
        if topic_hint:
            bucket["topic_hints"].append(topic_hint)
        if event_id and _TOKEN.fullmatch(event_id):
            bucket["event_ids"].append(event_id)

def _project_buckets(
    *,
    duration_sec: float,
    workbench_events: list[Mapping[str, Any]],
    subtitle_buckets: list[Mapping[str, Any]],
    chat_buckets: list[Mapping[str, Any]],
    comment_context_events: list[Mapping[str, Any]],
    bucket_sec: int,
) -> list[dict[str, Any]]:
    bucket_total = _bucket_count(duration_sec, bucket_sec)
    buckets = [_new_bucket() for _ in range(bucket_total)]

    for row in comment_context_events:
        if not isinstance(row, Mapping):
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        if start is None:
            continue
        profile, kind = _classify_event(row)
        _accumulate_range(
            buckets,
            start,
            end if end is not None else start + bucket_sec,
            duration_sec=duration_sec,
            bucket_sec=bucket_sec,
            family="comment",
            profile_hint=profile,
            kind_hint=kind,
            activity_hint=str(row.get("activity_hint") or ""),
            topic_hint=str(
                row.get("topic_hint") or row.get("topic") or ""
            ),
            event_id=str(row.get("event_id") or ""),
            hint_weight=2,
        )

    for row in workbench_events:
        if not isinstance(row, Mapping):
            continue
        family = _event_family(row)
        if family is None:
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        if start is None:
            continue
        profile, kind = _classify_event(row)
        axis = str(row.get("axis") or "")
        label = _safe_text(row.get("label"), 80)
        raw_free_family = family in {"subtitle", "ocr"}
        _accumulate_range(
            buckets,
            start,
            end if end is not None else start + bucket_sec,
            duration_sec=duration_sec,
            bucket_sec=bucket_sec,
            family=family,
            profile_hint=profile,
            kind_hint=kind,
            activity_hint=(
                ""
                if raw_free_family
                else str(
                    row.get("activity_hint")
                    or row.get("activity")
                    or (label if axis == "activity" else "")
                )
            ),
            topic_hint=(
                ""
                if raw_free_family
                else str(row.get("topic") or (label if axis == "topic" else ""))
            ),
            event_id=str(row.get("event_id") or ""),
        )

    for row in subtitle_buckets:
        if not isinstance(row, Mapping):
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        if start is None:
            continue
        opaque_ref = next(
            (
                str(row.get(field) or "")
                for field in (
                    "event_id",
                    "evidence_ref",
                    "subtitle_ref",
                    "transcript_ref",
                )
                if _TOKEN.fullmatch(str(row.get(field) or ""))
            ),
            "",
        )
        _accumulate_range(
            buckets,
            start,
            end if end is not None else start + bucket_sec,
            duration_sec=duration_sec,
            bucket_sec=bucket_sec,
            family="subtitle",
            event_id=opaque_ref,
        )

    for row in chat_buckets:
        if not isinstance(row, Mapping):
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        count = int(row.get("count") or 0)
        if start is None or count <= 0:
            continue
        _accumulate_range(
            buckets,
            start,
            end if end is not None else start + bucket_sec,
            duration_sec=duration_sec,
            bucket_sec=bucket_sec,
            family="chat",
            event_count=count,
        )

    return buckets

def _feature_vector(bucket: Mapping[str, Any]) -> list[float]:
    return [
        float(bucket["families"][family]["buckets"])
        for family in _ALL_FAMILIES
    ] + [
        min(1.0, len(bucket.get("profile_hints") or []) / 2.0),
        min(1.0, len(bucket.get("kind_hints") or []) / 2.0),
    ]

def _boundary_indexes(
    buckets: list[Mapping[str, Any]],
    *,
    comment_context_events: list[Mapping[str, Any]],
    duration_sec: float,
    bucket_sec: int,
    provider_enabled: bool,
) -> tuple[list[int], dict[str, Any]]:
    boundaries = {0, len(buckets)}
    for row in comment_context_events:
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        if start is not None:
            boundaries.add(_bucket_index(start, duration_sec=duration_sec, bucket_sec=bucket_sec))
        if end is not None and end > 0:
            boundaries.add(min(len(buckets), _bucket_index(max(0.0, end - 0.001), duration_sec=duration_sec, bucket_sec=bucket_sec) + 1))
    features = [_feature_vector(bucket) for bucket in buckets]
    for index in range(1, len(features)):
        previous = features[index - 1]
        current = features[index]
        changes = sum(1 for left, right in zip(previous, current) if abs(left - right) >= 0.5)
        if changes >= 2:
            boundaries.add(index)
    receipt = detect_boundary_buckets(features, enabled=provider_enabled)
    if receipt.get("status") == "available":
        for value in receipt.get("boundary_bucket_indexes") or []:
            if isinstance(value, int) and 0 < value < len(buckets):
                boundaries.add(value)
    ordered = sorted(boundaries)
    merged = [ordered[0]]
    for value in ordered[1:-1]:
        if value - merged[-1] >= _MIN_RANGE_BUCKETS:
            merged.append(value)
    if ordered[-1] - merged[-1] < _MIN_RANGE_BUCKETS and len(merged) > 1:
        merged.pop()
    merged.append(ordered[-1])
    return merged, receipt

def _choose_label(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = _safe_text(value, 80)
        if text:
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0][0]

def _mode(values: list[str], allowed: tuple[str, ...], fallback: str) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if value in allowed:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return fallback
    winner, winner_count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    if winner_count == 1 and len(counts) > 1:
        return fallback
    return winner

def _range_support(
    buckets: list[Mapping[str, Any]],
    start_idx: int,
    end_idx: int,
) -> dict[str, Any]:
    families = {
        family: {
            "bucket_count": 0,
            "event_count": 0,
        }
        for family in _ALL_FAMILIES
    }
    profile_hints: list[str] = []
    kind_hints: list[str] = []
    activity_hints: list[str] = []
    topic_hints: list[str] = []
    event_ids: list[str] = []
    for index in range(start_idx, end_idx):
        bucket = buckets[index]
        for family in _ALL_FAMILIES:
            family_row = bucket["families"][family]
            families[family]["bucket_count"] += int(family_row["buckets"])
            families[family]["event_count"] += int(family_row["event_count"])
        profile_hints.extend(str(value) for value in bucket["profile_hints"])
        kind_hints.extend(str(value) for value in bucket["kind_hints"])
        activity_hints.extend(str(value) for value in bucket["activity_hints"])
        topic_hints.extend(str(value) for value in bucket["topic_hints"])
        event_ids.extend(str(value) for value in bucket["event_ids"])
    support_families = [family for family in _ALL_FAMILIES if families[family]["bucket_count"] > 0]
    structure_count = sum(1 for family in _STRUCTURE_FAMILIES if families[family]["bucket_count"] > 0)
    interest_count = sum(1 for family in _INTEREST_FAMILIES if families[family]["bucket_count"] > 0)
    edit_count = sum(1 for family in _EDIT_FAMILIES if families[family]["bucket_count"] > 0)
    return {
        "families": families,
        "support_families": support_families,
        "structure_family_count": structure_count,
        "interest_family_count": interest_count,
        "edit_family_count": edit_count,
        "profile": _mode(profile_hints, _PROFILES, "mixed_unknown"),
        "kind": _mode(kind_hints, _SEGMENT_KINDS, "unknown"),
        "activity_label": _choose_label(activity_hints),
        "topic_label": _choose_label(topic_hints),
        "event_ids": sorted({value for value in event_ids if value}),
    }

def _selected_title(support: Mapping[str, Any]) -> str:
    kind = str(support.get("kind") or "unknown")
    if kind != "unknown" and kind in _KIND_LABELS:
        return _KIND_LABELS[kind]
    profile = str(support.get("profile") or "mixed_unknown")
    if profile != "mixed_unknown" and profile in _PROFILE_LABELS:
        return _PROFILE_LABELS[profile]
    return "맥락 구간 후보"

def _aggregate_preview(support: Mapping[str, Any]) -> str:
    family_count = len(support.get("support_families") or [])
    kind = str(support.get("kind") or "unknown")
    profile = str(support.get("profile") or "mixed_unknown")
    safe_label = (
        _KIND_LABELS.get(kind)
        if kind != "unknown"
        else _PROFILE_LABELS.get(profile, "공통 화면")
    )
    return f"{safe_label} · 근거 {family_count}종"

def _confidence_band(support: Mapping[str, Any]) -> str:
    structure_count = int(support.get("structure_family_count") or 0)
    total = len(support.get("support_families") or [])
    if structure_count >= 3 and total >= 4:
        return "high"
    if structure_count >= 2 and total >= 3:
        return "medium"
    return "low"

def _boundary_alternatives(
    *,
    start_sec: float,
    end_sec: float,
    bucket_sec: int,
    duration_sec: float,
    provider_receipt: Mapping[str, Any],
) -> list[dict[str, Any]]:
    alternatives = [
        {
            "label": "chosen",
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "reason": "comment_seeds_and_feature_changes",
            "selected": True,
        }
    ]
    start_alt = max(0.0, start_sec - bucket_sec)
    end_alt = min(duration_sec, end_sec + bucket_sec)
    if start_alt < start_sec:
        alternatives.append(
            {
                "label": "earlier_bucket",
                "start_sec": round(start_alt, 3),
                "end_sec": round(end_sec, 3),
                "reason": "neighbor_bucket",
                "selected": False,
            }
        )
    if end_alt > end_sec:
        alternatives.append(
            {
                "label": "later_bucket",
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_alt, 3),
                "reason": "neighbor_bucket",
                "selected": False,
            }
        )
    provider_indexes = provider_receipt.get("boundary_bucket_indexes")
    if isinstance(provider_indexes, list) and provider_indexes:
        nearby = min(
            (
                int(value) * bucket_sec
                for value in provider_indexes
                if isinstance(value, int)
            ),
            key=lambda value: abs(value - start_sec),
            default=None,
        )
        if (
            nearby is not None
            and 0 < nearby < duration_sec
            and 0 < abs(nearby - start_sec) <= bucket_sec * 2
        ):
            alternatives.append(
                {
                    "label": "provider_observed_change",
                    "start_sec": round(nearby, 3),
                    "end_sec": round(end_sec, 3),
                    "reason": "optional_provider_local_boundary",
                    "selected": False,
                }
            )
    valid: list[dict[str, Any]] = []
    for row in alternatives:
        alternative_start = _safe_float(row.get("start_sec"))
        alternative_end = _safe_float(row.get("end_sec"))
        if (
            alternative_start is None
            or alternative_end is None
            or alternative_start < 0
            or alternative_start >= alternative_end
            or alternative_end > duration_sec
        ):
            continue
        valid.append(row)
    return valid[:3]

def _build_child_ranges(
    buckets: list[Mapping[str, Any]],
    boundaries: list[int],
    *,
    source_asset_id: str,
    source_clock_ref: str,
    duration_sec: float,
    bucket_sec: int,
    provider_receipt: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranges: list[dict[str, Any]] = []
    unknown_gaps: list[dict[str, Any]] = []
    for start_idx, end_idx in zip(boundaries, boundaries[1:]):
        if end_idx - start_idx < _MIN_RANGE_BUCKETS:
            start_sec = round(start_idx * bucket_sec, 3)
            end_sec = round(min(duration_sec, end_idx * bucket_sec), 3)
            if end_sec > start_sec:
                unknown_gaps.append(
                    {
                        "gap_id": _stable_id(
                            "adaptive-gap",
                            {
                                "source_asset_id": source_asset_id,
                                "source_clock_ref": source_clock_ref,
                                "start_sec": start_sec,
                                "end_sec": end_sec,
                            },
                        ),
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "title": "짧아서 확인이 필요한 구간",
                        "analysis_state": _STATE_PARTIAL,
                        "support_families": [],
                        "reason": "below_minimum_context_duration",
                    }
                )
            continue
        start_sec = round(start_idx * bucket_sec, 3)
        end_sec = round(min(duration_sec, end_idx * bucket_sec), 3)
        if end_sec <= start_sec:
            continue
        support = _range_support(buckets, start_idx, end_idx)
        family_count = len(support["support_families"])
        strong_structure = int(support["structure_family_count"]) >= 2
        comment_with_interest = (
            support["families"]["comment"]["bucket_count"] > 0
            and int(support["interest_family_count"]) >= 1
        )
        if family_count < 2 or not (strong_structure or comment_with_interest):
            gap_id = _stable_id(
                "adaptive-gap",
                {
                    "source_asset_id": source_asset_id,
                    "source_clock_ref": source_clock_ref,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                },
            )
            unknown_gaps.append(
                {
                    "gap_id": gap_id,
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "title": "근거 부족 구간",
                    "analysis_state": _STATE_PARTIAL,
                    "support_families": support["support_families"],
                }
            )
            continue
        range_id = _stable_id(
            "adaptive-range",
            {
                "source_asset_id": source_asset_id,
                "source_clock_ref": source_clock_ref,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "profile": support["profile"],
                "kind": support["kind"],
                "event_ids": support["event_ids"],
            },
        )
        ranges.append(
            {
                "context_range_id": range_id,
                "parent_context_range_id": None,
                "child_context_range_ids": [],
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title": _selected_title(support),
                "preview_text": _aggregate_preview(support),
                "broadcast_profile": support["profile"],
                "segment_kind": support["kind"],
                "confidence_band": _confidence_band(support),
                "activity_state": support["activity_label"] or "unknown",
                "topic_state": support["topic_label"] or "unknown",
                "analysis_state": _STATE_PARTIAL,
                "support_modality_families": support["support_families"],
                "evidence_modality_breakdown": support["families"],
                "boundary_alternatives": _boundary_alternatives(
                    start_sec=start_sec,
                    end_sec=end_sec,
                    bucket_sec=bucket_sec,
                    duration_sec=duration_sec,
                    provider_receipt=provider_receipt,
                ),
                "risk_flags": sorted(
                    {
                        "comment_seed_is_weak",
                        *(
                            ["missing_interest_signal"]
                            if int(support["interest_family_count"]) == 0
                            else []
                        ),
                        *(
                            ["single_structure_family"]
                            if int(support["structure_family_count"]) < 2
                            else []
                        ),
                        *(
                            ["mixed_unknown_profile"]
                            if support["profile"] == "mixed_unknown"
                            else []
                        ),
                    }
                ),
                "source_event_ids": support["event_ids"],
            }
        )
    return ranges, unknown_gaps

def _build_parent_ranges(
    child_ranges: list[dict[str, Any]],
    *,
    source_asset_id: str,
    source_clock_ref: str,
) -> list[dict[str, Any]]:
    parents: list[dict[str, Any]] = []
    grouped: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in child_ranges:
        if (
            current
            and row["broadcast_profile"] == current[-1]["broadcast_profile"]
            and row["start_sec"] - current[-1]["end_sec"] <= _BUCKET_SEC
        ):
            current.append(row)
        else:
            if current:
                grouped.append(current)
            current = [row]
    if current:
        grouped.append(current)
    for group in grouped:
        if len(group) < 2:
            continue
        parent_id = _stable_id(
            "adaptive-parent",
            {
                "source_asset_id": source_asset_id,
                "source_clock_ref": source_clock_ref,
                "children": [row["context_range_id"] for row in group],
            },
        )
        profile = str(group[0]["broadcast_profile"])
        parent = {
            "context_range_id": parent_id,
            "parent_context_range_id": None,
            "child_context_range_ids": [row["context_range_id"] for row in group],
            "start_sec": group[0]["start_sec"],
            "end_sec": group[-1]["end_sec"],
            "title": f"{_PROFILE_LABELS.get(profile, '방송')} 큰 구간",
            "preview_text": group[0]["preview_text"],
            "broadcast_profile": profile,
            "segment_kind": "transition",
            "confidence_band": group[0]["confidence_band"],
            "activity_state": group[0]["activity_state"],
            "topic_state": "unknown",
            "analysis_state": _STATE_PARTIAL,
            "support_modality_families": sorted(
                {
                    family
                    for row in group
                    for family in row["support_modality_families"]
                }
            ),
            "evidence_modality_breakdown": {},
            "boundary_alternatives": [],
            "risk_flags": ["parent_range_summary_only"],
            "source_event_ids": sorted(
                {
                    value
                    for row in group
                    for value in row["source_event_ids"]
                }
            ),
        }
        parents.append(parent)
        for child in group:
            child["parent_context_range_id"] = parent_id
    return parents

def build_adaptive_context_ranges(
    *,
    source_asset_id: str,
    source_clock_ref: str,
    source_duration_sec: float,
    workbench_events: list[Mapping[str, Any]],
    subtitle_buckets: list[Mapping[str, Any]],
    chat_buckets: list[Mapping[str, Any]],
    comment_context_events: list[Mapping[str, Any]],
    enabled: bool = True,
    provider_enabled: bool = False,
    bucket_sec: int = _BUCKET_SEC,
) -> dict[str, Any]:
    """Build hierarchical, manager-only adaptive context ranges."""

    source_asset_id = _require_token(source_asset_id, "invalid_source_asset_id")
    source_clock_ref = _require_token(source_clock_ref, "invalid_source_clock_ref")
    duration_sec = _duration(source_duration_sec)
    if not enabled:
        return {
            "schema_version": SCHEMA_VERSION,
            "rule_version": RULE_VERSION,
            "source_asset_id": source_asset_id,
            "source_clock_ref": source_clock_ref,
            "source_duration_sec": duration_sec,
            "bucket_sec": bucket_sec,
            "analysis_state": _STATE_DISABLED,
            "policy": {
                "manager_only": True,
                "public_payload": False,
                "ranking_or_default_changed": False,
                "manager_manual_intervals_required": False,
            },
            "provider_receipt": detect_boundary_buckets([[0.0]], enabled=False),
            "ranges": [],
            "unknown_gaps": [],
            "summary": {"range_count": 0, "parent_range_count": 0, "child_range_count": 0, "unknown_gap_count": 0},
        }

    buckets = _project_buckets(
        duration_sec=duration_sec,
        workbench_events=list(workbench_events or []),
        subtitle_buckets=list(subtitle_buckets or []),
        chat_buckets=list(chat_buckets or []),
        comment_context_events=list(comment_context_events or []),
        bucket_sec=bucket_sec,
    )
    evidence_present = any(
        bucket["families"][family]["event_count"] > 0
        for bucket in buckets
        for family in _ALL_FAMILIES
    )
    if not evidence_present:
        return {
            "schema_version": SCHEMA_VERSION,
            "rule_version": RULE_VERSION,
            "source_asset_id": source_asset_id,
            "source_clock_ref": source_clock_ref,
            "source_duration_sec": duration_sec,
            "bucket_sec": bucket_sec,
            "analysis_state": _STATE_NOT_ANALYZED,
            "policy": {
                "manager_only": True,
                "public_payload": False,
                "ranking_or_default_changed": False,
                "manager_manual_intervals_required": False,
            },
            "provider_receipt": detect_boundary_buckets([[0.0]], enabled=provider_enabled),
            "ranges": [],
            "unknown_gaps": [],
            "summary": {"range_count": 0, "parent_range_count": 0, "child_range_count": 0, "unknown_gap_count": 0},
        }

    boundaries, provider_receipt = _boundary_indexes(
        buckets,
        comment_context_events=list(comment_context_events or []),
        duration_sec=duration_sec,
        bucket_sec=bucket_sec,
        provider_enabled=provider_enabled,
    )
    child_candidates, unknown_gap_candidates = _build_child_ranges(
        buckets,
        boundaries,
        source_asset_id=source_asset_id,
        source_clock_ref=source_clock_ref,
        duration_sec=duration_sec,
        bucket_sec=bucket_sec,
        provider_receipt=provider_receipt,
    )
    compression_children = [dict(row) for row in child_candidates]
    compression_parents = _build_parent_ranges(
        compression_children,
        source_asset_id=source_asset_id,
        source_clock_ref=source_clock_ref,
    )
    compressed_context_structure = compress_adaptive_context_payload(
        {
            "ranges": [*compression_parents, *compression_children],
            "unknown_gaps": unknown_gap_candidates,
        }
    )
    child_ranges = _spread_limit(child_candidates, _MAX_CHILD_RANGES)
    unknown_gaps = _spread_limit(unknown_gap_candidates, _MAX_UNKNOWN_GAPS)
    if not child_ranges:
        return {
            "schema_version": SCHEMA_VERSION,
            "rule_version": RULE_VERSION,
            "source_asset_id": source_asset_id,
            "source_clock_ref": source_clock_ref,
            "source_duration_sec": duration_sec,
            "bucket_sec": bucket_sec,
            "analysis_state": _STATE_NO_USEFUL,
            "policy": {
                "manager_only": True,
                "public_payload": False,
                "ranking_or_default_changed": False,
                "manager_manual_intervals_required": False,
            },
            "provider_receipt": provider_receipt,
            "ranges": [],
            "unknown_gaps": unknown_gaps,
            "compressed_context_structure": compressed_context_structure,
            "summary": {
                "range_count": 0,
                "parent_range_count": 0,
                "child_range_count": 0,
                "unknown_gap_count": len(unknown_gaps),
                "child_candidate_count": len(child_candidates),
                "child_hidden_count": max(
                    0, len(child_candidates) - len(child_ranges)
                ),
                "unknown_gap_candidate_count": len(unknown_gap_candidates),
                "unknown_gap_hidden_count": max(
                    0, len(unknown_gap_candidates) - len(unknown_gaps)
                ),
            },
        }
    parent_ranges = _build_parent_ranges(
        child_ranges,
        source_asset_id=source_asset_id,
        source_clock_ref=source_clock_ref,
    )
    ranges = [*parent_ranges, *child_ranges]
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "source_asset_id": source_asset_id,
        "source_clock_ref": source_clock_ref,
        "source_duration_sec": duration_sec,
        "bucket_sec": bucket_sec,
        "analysis_state": _STATE_PARTIAL,
        "policy": {
            "manager_only": True,
            "public_payload": False,
            "ranking_or_default_changed": False,
            "manager_manual_intervals_required": False,
        },
        "provider_receipt": provider_receipt,
        "ranges": ranges,
        "unknown_gaps": unknown_gaps,
        "compressed_context_structure": compressed_context_structure,
        "summary": {
            "range_count": len(ranges),
            "parent_range_count": len(parent_ranges),
            "child_range_count": len(child_ranges),
            "unknown_gap_count": len(unknown_gaps),
            "child_candidate_count": len(child_candidates),
            "child_hidden_count": max(
                0, len(child_candidates) - len(child_ranges)
            ),
            "unknown_gap_candidate_count": len(unknown_gap_candidates),
            "unknown_gap_hidden_count": max(
                0, len(unknown_gap_candidates) - len(unknown_gaps)
            ),
            "bucket_count": len(buckets),
            "support_families": list(_ALL_FAMILIES),
        },
    }
