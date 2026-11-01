"""Primary owner for final summary Markdown normalization and anchor injection."""

from __future__ import annotations

import logging

import re

from typing import Optional

from urllib.parse import quote, urlparse

from ..models import VODInfo

from ..models import UserClipsBundle

from ..utils import sec_to_hms

from .parse import _SRT_XVAL_THRESHOLD_SEC, _hms_to_sec

from ..sanity_gate import find_missing_promoted_anchors

from .parse import _parse_summary_sections

logger = logging.getLogger("pipeline")

from .render_primitives import _chzzk_video_url_at, _hms_to_seconds

from .viewer_editor import _build_source_vod_url, _source_id, _source_platform, _validate_editorial_review_projection

from .merge_recovery import _strip_invalid_timeline_appendix, _strip_stray_hr

_SRT_XVAL_QUOTE_RE = re.compile(r'"([^"]{10,60})"')

_SRT_XVAL_ENTRY_RE = re.compile(
    r"^(\s{0,4}[-*]\s*\*{0,4}\s*(?:\[VOD\s+\d+\]\s*)?\[?)(\d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)

_SRT_XVAL_MIN_INFORMATIVE_CHARS = 6

_SRT_XVAL_NOISE_RUN_RE = re.compile(r"(?:[ㅋㅎ]{2,}|[ㅠㅜ]{2,}|ㄷ{2,})")

_SRT_XVAL_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣ㅋㅎㅠㅜㄷ]+")

_SRT_XVAL_NOISE_TOKEN_RE = re.compile(r"^[ㅋㅎㅠㅜㄷ]+$")

_EDITOR_BRIEF_SECTION_RE = re.compile(
    r"(?ms)^(?:[ \t]*\n)*[ \t]*###(?!#)\s*(?:🎬\s*)?[^\n]*편집 포인트[^\n]*\n"
    r".+?(?=\n[ \t]*#{1,3}(?!#)\s|\n---\s*\n|\Z)",
)

_EDITOR_BRIEF_WIDE_HEADING_RE = re.compile(
    r"(?m)^[ \t]*#{1,6}\s*(?:🎬\s*)?[^\n]*편집 포인트[^\n]*$"
)

_RETIRED_EDITOR_REVIEW_SECTION_RE = re.compile(
    r"(?ms)^(?:[ \t]*\n)*[ \t]*###(?!#)\s*(?:📝\s*)?에디터의?\s*방송\s*후기[^\n]*\n"
    r".*?(?=\n[ \t]*#{1,3}(?!#)\s|\n---\s*\n|\Z)",
)

_HIGHLIGHT_ONELINE_RE = re.compile(
    r'^(\s*\d+\.\s*\*\*\[?\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?\]?\*\*\s*)'
    r'(.+?)\s*\(\s*추천 이유\s*[:：]\s*(.+?)\)\s*$',
    re.M,
)

_CHZZK_TC_BRACKET_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?)\]"
    r"(?:\(\s*(https?://[^)\s]+)\s*\))?",
    re.IGNORECASE,
)

_MD_CODE_SPAN_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")

def _strip_editor_brief_section(md: str) -> str:
    '시청자 surface (`.md` / `.html` raw fallback) 에서 편집자 전용 섹션 제거.\n\n    . 입력에 섹션이 없으면 그대로 반환. `_parse_summary_sections` 가\n    이미 `editor_brief_points` 로 데이터를 보관했음을 전제로 한다.\n    '
    if not md or "편집 포인트" not in md:
        return md
    stripped = _EDITOR_BRIEF_SECTION_RE.sub("", md)
    if _EDITOR_BRIEF_WIDE_HEADING_RE.search(stripped):
        logger.warning("editor brief heading remains after viewer strip; malformed heading may need review")

    return stripped.rstrip() + ("\n" if md.endswith("\n") else "")

def _strip_retired_editor_review_section(md: str) -> str:
    """Keep old reports readable while preventing the retired review from new output."""
    if not md or "방송 후기" not in md:
        return md
    stripped = _RETIRED_EDITOR_REVIEW_SECTION_RE.sub("", md)
    return stripped.rstrip() + ("\n" if md.endswith("\n") else "")

def _source_video_url_at(vod_info: VODInfo, tc_or_range: str) -> str:
    start_tc = str(tc_or_range or "").split("~", 1)[0].strip()
    seconds = _hms_to_seconds(start_tc)
    source_url = _build_source_vod_url(vod_info)
    if seconds is None or not source_url:
        return ""
    if _source_platform(vod_info) == "youtube":
        return f"{source_url}&t={seconds}s"
    return f"{source_url}?currentTime={seconds}"

def _build_header_links_md(vod_info: VODInfo, public_url_base: str) -> str:
    """요약 .md 의 "## 방송 분석 리포트:" 바로 아래에 주입할 공유용 링크 블록.

    SNS 미리보기용 OG 메타태그는 report.html 에 붙이므로 URL 은 report.html
    직접 경로(Cloudflare 가 .html 를 자동 strip) 를 사용한다.
    """
    lines: list[str] = []
    if public_url_base:
        report_url = f"{public_url_base.rstrip('/')}/vods/{vod_info.video_no}/report"
        lines.append(f"- **🔗 요약 웹페이지:** [{report_url}]({report_url})")
    source_url = _build_source_vod_url(vod_info)
    if source_url:
        source_label = "YouTube 원본 영상" if _source_platform(vod_info) == "youtube" else "치지직 다시보기"
        lines.append(f"- **▶️ {source_label}:** [{source_url}]({source_url})")
    return "\n".join(lines)

def _reformat_highlights_multiline(md: str) -> str:
    """하이라이트 섹션의 one-liner 포맷을 multiline 으로 변환.

    Before: `1. **[tc~tc]** title (추천 이유: X)`
    After:  `1. **[tc~tc]** title\n    - 추천 이유: X`

    이미 multiline 포맷이면 no-op (regex 가 매칭 안됨).
    """
    import re as _re
    def _sub(m):
        prefix, title, reason = m.group(1), m.group(2).strip().rstrip("*").strip(), m.group(3).strip().rstrip(")").strip()
        return f"{prefix}{title}\n    - 추천 이유: {reason}"
    md = _HIGHLIGHT_ONELINE_RE.sub(_sub, md)


    def _space_items(section_match: "_re.Match") -> str:
        body = section_match.group(0)

        spaced = _re.sub(r"(?<!\n)\n([ \t]*\d+\.\s*\*\*\[?\d{2}:\d{2}:\d{2})", r"\n\n\1", body)
        return spaced
    md = _re.sub(
        r"###\s*(?:🎬\s*)?[^\n]*하이라이트[^\n]*\n.+?(?=\n###\s|\n---|\Z)",
        _space_items,
        md,
        flags=_re.S,
    )
    return md

def _is_chzzk_vod_url(url: str) -> bool:
    """`https://chzzk.naver.com/video/<digits>` 패턴 매치.

    RFC 3986 §3.2.2: scheme/host case-insensitive. urllib `.hostname` 가 lowercase
    normalize. path 는 `/video/<digits>` 만 허용 (current chzzk VOD URL 포맷).
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if (parsed.hostname or "").lower() != "chzzk.naver.com":
        return False
    return bool(re.match(r"^/video/\d+/?$", parsed.path or ""))

def _inject_chzzk_timestamp_links(md: str, video_no: str) -> str:
    """타임라인/하이라이트의 `[HH:MM:SS]` 또는 `[HH:MM:SS~HH:MM:SS]` 토큰을
    `[HH:MM:SS](https://chzzk.naver.com/video/{video_no}?currentTime=N)` 형태의
    인라인 마크다운 링크로 자동 변환.

    멱등 (idempotent): 이미 chzzk 인라인 링크가 박혀 있어도 시간 기준으로 URL 을
    재계산해 stale 링크를 갱신한다 — 사용자가 textarea 에서 timestamp 만 바꿔도
    다음 save 때 URL 이 자동으로 새 시간에 맞춰진다. 대문자 chzzk URL 도 정상
    인식 (RFC 3986).

    인접한 비-chzzk URL `[HH:MM:SS](https://example.com/...)` 는 사용자 콘텐츠로
    간주해 그대로 둔다 (이전 구현은 비-chzzk URL 을 미소비해 trailing fragment
    가 남아 double-link 깨짐 발생). Code span / fenced block 안의 `[HH:MM:SS]`
    토큰 또한 변환하지 않는다 — 마크다운 의미가 다르다.
    """
    if not video_no:
        return md

    def _sub(m: "re.Match[str]") -> str:
        tc = m.group(1)
        existing_url = m.group(2)
        new_url = _chzzk_video_url_at(video_no, tc)
        if not new_url:
            return m.group(0)
        if existing_url and not _is_chzzk_vod_url(existing_url):
            return m.group(0)
        return f"[{tc}]({new_url})"

    parts = _MD_CODE_SPAN_RE.split(md)
    for i, segment in enumerate(parts):
        if i % 2 == 1:
            continue
        parts[i] = _CHZZK_TC_BRACKET_RE.sub(_sub, segment)
    return "".join(parts)

def _is_youtube_video_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "youtu.be"}:
        return False
    if host == "youtu.be":
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]{11}/?", parsed.path or ""))
    return parsed.path == "/watch"

def _inject_source_timestamp_links(md: str, vod_info: VODInfo) -> str:
    if _source_platform(vod_info) == "chzzk":
        return _inject_chzzk_timestamp_links(md, _source_id(vod_info))

    def _sub(match: "re.Match[str]") -> str:
        tc = match.group(1)
        existing_url = match.group(2)
        new_url = _source_video_url_at(vod_info, tc)
        if not new_url:
            return match.group(0)
        if existing_url and not (
            _is_youtube_video_url(existing_url) or _is_chzzk_vod_url(existing_url)
        ):
            return match.group(0)
        return f"[{tc}]({new_url})"

    parts = _MD_CODE_SPAN_RE.split(md)
    for index, segment in enumerate(parts):
        if index % 2 == 0:
            parts[index] = _CHZZK_TC_BRACKET_RE.sub(_sub, segment)
    return "".join(parts)

def _format_injected_label(anchor: dict) -> str:
    """`[INJECTED · 시청자 클립 19/2471]` 형태 라벨 — anchor like/play 메타 반영.

    issue stop condition 의 사용자 가시성 핵심 — LLM-authored 항목과 시각적 구분.
    """
    parts: list[str] = ["시청자 클립"]
    like = anchor.get("like_count")
    play = anchor.get("play_count")
    if isinstance(like, int) and isinstance(play, int):
        parts.append(f"{like}/{play}")
    elif isinstance(play, int):
        parts.append(f"▶ {play:,}")
    return f"[INJECTED · {' '.join(parts)}]"

def _format_anchor_timeline_entry(anchor: dict) -> str:
    """missing anchor → 타임라인 섹션 bullet 1개 (시간순 삽입 대상)."""
    offset = int(anchor["offset_sec"])
    hms = sec_to_hms(offset)
    title = (anchor.get("title") or "시청자 클립 anchor").strip()
    label = _format_injected_label(anchor)
    summary = "시청자가 클립으로 잘라낸 시점. 본문 evidence 는 같은 시각의 자막/채팅을 참조."
    evidence = "\"시청자 클립 anchor\""
    return (
        f"- **[{hms}] {title} {label}** (분위기: 💬)\n"
        f"    - 내용: {summary}\n"
        f"    - 근거: {evidence}\n"
    )

def _format_anchor_highlight_entry(idx: int, anchor: dict) -> str:
    """missing anchor → 하이라이트 섹션 entry 1개 (섹션 끝 추가)."""
    offset = int(anchor["offset_sec"])
    hms = sec_to_hms(offset)
    title = (anchor.get("title") or "시청자 클립 anchor").strip()
    label = _format_injected_label(anchor)
    return (
        f"{idx}. **[{hms}~{hms}]** {title} {label}\n"
        f"    - 추천 이유: 시청자가 직접 클립으로 잘라낸 호평 신호 시점.\n"
    )

def _inject_missing_promoted_anchors(
    summary: str,
    promoted_anchors: Optional[list],
    *,
    evidence_lookup=None,
    tolerance_sec: int = 60,
) -> tuple[str, list[dict], list[str]]:
    ' 결정론 inject — must-include anchor 누락분을 timeline/highlight 에 주입.\n\n    반환:\n      (mutated_summary, injected_anchors, evidence_missing_warnings)\n\n    evidence_lookup(offset_sec, tolerance) → bool. None 이면 모든 anchor 가\n    evidence 있다고 가정 (T3 silence spec 미적용 — 호출자가 책임).\n\n    silent skip 금지: evidence 없는 anchor 는 fail-loud warning + footer marker\n    `<!-- T4_INJECT_WARNINGS: ... -->` 를 summary 끝에 부착해 _parse_summary_sections\n    가 `injection_warnings` 로 노출.\n    '
    if not promoted_anchors:
        return summary, [], []

    missing = find_missing_promoted_anchors(
        summary, promoted_anchors, tolerance_sec=tolerance_sec,
    )
    if not missing:
        return summary, [], []

    injected: list[dict] = []
    evidence_missing: list[str] = []
    for anchor in missing:
        try:
            offset = int(anchor["offset_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        has_evidence = True
        if evidence_lookup is not None:
            try:
                has_evidence = bool(evidence_lookup(offset, tolerance_sec))
            except Exception as e:
                logger.warning(f"evidence_lookup 예외 (offset={offset}): {e}")
                has_evidence = False
        if not has_evidence:
            label = anchor.get("clip_uid") or sec_to_hms(offset)
            evidence_missing.append(
                f"anchor {label} (offset={sec_to_hms(offset)}) — evidence 부족, inject skip"
            )
            logger.warning(
                f"T4 anchor inject skip (no evidence): clip_uid={anchor.get('clip_uid')} "
                f"offset={sec_to_hms(offset)}"
            )
            continue
        injected.append(anchor)

    if not injected and not evidence_missing:
        return summary, [], []


    inject_timeline = [a for a in injected if a.get("_missing_in_timeline")]
    inject_highlight = [a for a in injected if a.get("_missing_in_highlight")]
    summary = _splice_anchor_into_timeline(summary, inject_timeline)
    summary = _append_anchor_to_highlights(summary, inject_highlight)

    if evidence_missing:
        warning_block = (
            f"\n\n<!-- T4_INJECT_WARNINGS:\n"
            + "\n".join(evidence_missing)
            + "\n-->\n"
            f"\n> ⚠️ 시청자 클립 {len(evidence_missing)}개가 evidence 부족으로 누락되었습니다.\n"
        )
        summary = summary.rstrip() + warning_block

    return summary, injected, evidence_missing

def _splice_anchor_into_timeline(summary: str, anchors: list[dict]) -> str:
    """타임라인 섹션 안에 anchor entry 들을 시간순 삽입."""
    if not anchors:
        return summary
    section_re = re.compile(
        r"(###\s*(?:📍\s*)?[^\n]*타임라인[^\n]*\n)(.+?)(?=\n###\s|\Z)",
        flags=re.S,
    )
    m = section_re.search(summary)
    if not m:

        block = "\n### 📍 타임라인 상세 요약\n\n" + "".join(
            _format_anchor_timeline_entry(a) for a in sorted(anchors, key=lambda x: int(x["offset_sec"]))
        ) + "\n"
        return summary.rstrip() + block

    header, body = m.group(1), m.group(2)

    entry_starts = list(re.finditer(
        r"(?:^|\n)(?=\s*[-*]\s*\**\s*\[\d{2}:\d{2}:\d{2})", body
    ))
    blocks: list[tuple[int, str]] = []
    if entry_starts:
        positions = [s.start() for s in entry_starts] + [len(body)]
        prefix = body[:positions[0]]
        for i in range(len(entry_starts)):
            chunk = body[positions[i]:positions[i + 1]]
            tcm = re.search(r"\[(\d{2}):(\d{2}):(\d{2})\]", chunk)
            if tcm:
                offset = _hms_seconds(tcm)
                blocks.append((offset, chunk))
            else:
                blocks.append((10**9, chunk))
    else:
        prefix = body
    for a in anchors:
        blocks.append((int(a["offset_sec"]), "\n" + _format_anchor_timeline_entry(a)))
    blocks.sort(key=lambda x: x[0])
    new_body = prefix.rstrip("\n") + "\n" + "".join(b[1] for b in blocks)
    return summary[:m.start()] + header + new_body + summary[m.end():]

def _hms_seconds(tcm: "re.Match[str]") -> int:
    return int(tcm.group(1)) * 3600 + int(tcm.group(2)) * 60 + int(tcm.group(3))

def _append_anchor_to_highlights(summary: str, anchors: list[dict]) -> str:
    """하이라이트 섹션 끝에 anchor 들을 추가 (시간순)."""
    if not anchors:
        return summary
    section_re = re.compile(
        r"(###\s*(?:🎬\s*)?[^\n]*하이라이트[^\n]*\n)(.+?)(?=\n###\s|\Z)",
        flags=re.S,
    )
    m = section_re.search(summary)
    sorted_anchors = sorted(anchors, key=lambda x: int(x["offset_sec"]))
    if not m:
        block = "\n### 🎬 하이라이트 추천 구간\n\n" + "".join(
            _format_anchor_highlight_entry(i + 1, a) for i, a in enumerate(sorted_anchors)
        ) + "\n"
        return summary.rstrip() + block
    header, body = m.group(1), m.group(2)
    existing_count = len(re.findall(r"^\s*\d+\.\s*\*\*\[", body, flags=re.M))
    addition = "\n" + "".join(
        _format_anchor_highlight_entry(existing_count + i + 1, a)
        for i, a in enumerate(sorted_anchors)
    )
    new_body = body.rstrip() + addition + "\n"
    return summary[:m.start()] + header + new_body + summary[m.end():]

def _chapter_item_sec(item: dict) -> int | None:
    try:
        return _hms_to_sec(str(item.get("tc") or ""))
    except (TypeError, ValueError):
        return None

def _comment_chapter_learning_row(item: dict, *, reason: str = "") -> dict:
    row = {
        "tc": str(item.get("tc") or "").strip(),
        "title": " ".join(str(item.get("title") or "").split()).strip(),
        "start_sec": _chapter_item_sec(item),
    }
    if reason:
        row["reason"] = reason
    return row

def _spread_chapter_items(items: list[dict], *, max_items: int) -> list[dict]:
    limit = max(1, int(max_items or 1))
    if len(items) <= limit:
        return items
    rows = [
        (sec, item)
        for item in items
        if (sec := _chapter_item_sec(item)) is not None
    ]
    if len(rows) <= limit:
        return [item for _sec, item in rows]
    rows.sort(key=lambda row: row[0])
    keep_indexes: set[int] = set()
    for i in range(limit):
        keep_indexes.add(round(i * (len(rows) - 1) / (limit - 1)))
    return [rows[index][1] for index in sorted(keep_indexes)]

def _format_comment_chapter_timeline_entry(item: dict) -> str:
    tc = str(item.get("tc") or "").strip()
    title = " ".join(str(item.get("title") or "").split()).strip()
    if not tc or not title:
        return ""
    return (
        f"- **[{tc}] {title}** (분위기: 💬)\n"
        "    - 내용: 방송 흐름을 보완하는 전환점입니다.\n"
        "    - 근거: 방송 흐름 전환점\n"
    )

def _inject_comment_chapters_into_timeline(
    summary: str,
    *,
    duplicate_window_sec: int = 180,
    max_inserted: int = 8,
) -> tuple[str, dict]:
    """Supplement the main detailed timeline with private comment chapter anchors.

    `### 치지직 댓글 타임라인` remains hidden as a standalone viewer card, but its
    rewritten chapter anchors should still help the primary summary timeline.
    Insert only missing, rewritten chapter rows and avoid exposing source comment
    text, authors, source labels, recommendation counts, or reply counts.
    """

    learning = {
        "schema_version": "comment_timeline_supplement_learning.v2",
        "status": "absent",
        "source": "chzzk_comment_timeline",
        "raw_comment_text_included": False,
        "recommendation_counts_included": False,
        "duplicate_window_sec": int(duplicate_window_sec),
        "max_inserted": int(max_inserted),
        "candidate_count": 0,
        "inserted_count": 0,
        "duplicate_skipped_count": 0,
        "cap_skipped_count": 0,
        "invalid_skipped_count": 0,
        "inserted": [],
        "duplicate_skipped": [],
        "cap_skipped": [],
    }

    sec = _parse_summary_sections(summary)
    chapters = [dict(item) for item in (sec.get("chzzk_comment_timeline") or [])]
    if not chapters:
        return summary, learning
    learning["status"] = "evaluated"
    learning["candidate_count"] = len(chapters)

    existing_secs: list[int] = []
    for item in sec.get("timeline") or []:
        tc = str(item.get("tc") or "").split("~", 1)[0].strip()
        try:
            existing_secs.append(_hms_to_sec(tc))
        except (TypeError, ValueError):
            continue
    detail_match = re.search(
        r"###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n(.+?)(?=\n###\s|\Z)",
        summary,
        flags=re.S,
    )
    if detail_match:
        for h, m, s in re.findall(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?!\d)", detail_match.group(1)):
            try:
                existing_secs.append(int(h) * 3600 + int(m) * 60 + int(s))
            except ValueError:
                continue
    existing_secs = sorted(set(existing_secs))

    missing: list[dict] = []
    for item in chapters:
        sec_value = _chapter_item_sec(item)
        title = str(item.get("title") or "").strip()
        if sec_value is None or not title:
            learning["invalid_skipped_count"] += 1
            continue
        if any(abs(sec_value - existing) <= duplicate_window_sec for existing in existing_secs):
            learning["duplicate_skipped"].append(_comment_chapter_learning_row(item, reason="near_existing_timeline"))
            continue
        missing.append(item)

    selected = _spread_chapter_items(missing, max_items=max_inserted)
    selected = [item for item in selected if _format_comment_chapter_timeline_entry(item)]
    selected_keys = {
        (_comment_chapter_learning_row(item).get("tc"), _comment_chapter_learning_row(item).get("title"))
        for item in selected
    }
    learning["cap_skipped"] = [
        _comment_chapter_learning_row(item, reason="max_inserted_cap")
        for item in missing
        if (
            _comment_chapter_learning_row(item).get("tc"),
            _comment_chapter_learning_row(item).get("title"),
        ) not in selected_keys
    ]
    learning["duplicate_skipped_count"] = len(learning["duplicate_skipped"])
    learning["cap_skipped_count"] = len(learning["cap_skipped"])
    learning["inserted"] = [
        _comment_chapter_learning_row(item)
        for item in selected
        if _chapter_item_sec(item) is not None
    ]
    learning["inserted_count"] = len(learning["inserted"])
    if not selected:
        return summary, learning

    section_re = re.compile(
        r"(###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n)(.+?)(?=\n###\s|\Z)",
        flags=re.S,
    )
    addition_blocks = [
        "\n" + _format_comment_chapter_timeline_entry(item)
        for item in selected
    ]
    match = section_re.search(summary)
    if not match:
        block = "\n### 📍 타임라인 상세 요약\n" + "".join(addition_blocks) + "\n"
        comment_match = re.search(r"\n###\s*치지직 댓글 타임라인\s*\n", summary)
        if comment_match:
            return summary[:comment_match.start()] + block + summary[comment_match.start():], learning
        return summary.rstrip() + block, learning

    header, body = match.group(1), match.group(2)
    entry_starts = list(re.finditer(
        r"(?:^|\n)(?=\s*[-*]\s*\**\s*\[\d{2}:\d{2}:\d{2})", body
    ))
    blocks: list[tuple[int, str]] = []
    prefix = ""
    if entry_starts:
        positions = [entry.start() for entry in entry_starts] + [len(body)]
        prefix = body[:positions[0]]
        for index in range(len(entry_starts)):
            chunk = body[positions[index]:positions[index + 1]]
            tcm = re.search(r"\[(\d{2}):(\d{2}):(\d{2})\]", chunk)
            blocks.append((_hms_seconds(tcm), chunk) if tcm else (10**9, chunk))
    else:
        prefix = body
    for item in selected:
        sec_value = _chapter_item_sec(item)
        if sec_value is None:
            continue
        blocks.append((sec_value, "\n" + _format_comment_chapter_timeline_entry(item)))
    blocks.sort(key=lambda row: row[0])
    new_body = prefix + "".join(block for _sec, block in blocks).rstrip() + "\n"
    return summary[:match.start()] + header + new_body + summary[match.end():], learning

def _sec_to_hms_str(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _canonical_srt_xval_text(text: str) -> str:
    """Normalize quote/SRT text for conservative timestamp cross-validation."""
    lowered = str(text or "").lower()
    without_noise_runs = _SRT_XVAL_NOISE_RUN_RE.sub(" ", lowered)
    wordish = _SRT_XVAL_NON_WORD_RE.sub(" ", without_noise_runs)
    tokens = [
        token
        for token in wordish.split()
        if not _SRT_XVAL_NOISE_TOKEN_RE.fullmatch(token)
    ]
    return " ".join(tokens)

def _informative_srt_xval_len(text: str) -> int:
    return len(re.findall(r"[0-9a-z가-힣]", text))

def _find_quote_in_srt(
    quote: str, cues: list, *, entry_ms: int = 0,
) -> Optional[int]:
    """SRT cues에서 정보량 있는 인용 텍스트를 검색, 가장 가까운 cue의 start_ms 반환.

    채팅 근거의 "ㅋㅋㅋ" 같은 흔한 꼬리만으로 SRT를 매칭하면 엉뚱한 실제 시점을
    붙일 수 있으므로, 반복 웃음/감탄 문자를 제거한 뒤 충분한 문구만 찾는다.
    다중 매치 시 entry_ms와 가장 가까운 것을 선택한다. 매치가 0이면 None.
    """
    q = _canonical_srt_xval_text(quote)
    if _informative_srt_xval_len(q) < _SRT_XVAL_MIN_INFORMATIVE_CHARS:
        return None
    matches: list[int] = []
    for cue in cues:
        cue_text = _canonical_srt_xval_text(" ".join(cue.text_lines))
        if q in cue_text:
            matches.append(cue.start_ms)
    if not matches:
        return None
    return min(matches, key=lambda ms: abs(ms - entry_ms))

def _cross_validate_srt_timestamps(summary: str, srt_cues: list) -> str:
    '타임라인 항목의 인용 발언을 SRT에서 검색, 3분+ 차이 시 인라인 시점 표기 삽입.'
    tl_match = re.search(
        r"###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n(.+?)(?=\n###\s|\Z)",
        summary, flags=re.S,
    )
    if not tl_match:
        return summary

    tl_body = tl_match.group(1)
    corrections: list[tuple[int, int, str]] = []

    entries = re.split(
        r"\n(?=\s{0,4}[-*]\s*\*{0,4}\s*(?:\[VOD\s+\d+\]\s*)?\[?\d{2}:\d{2}:\d{2})",
        tl_body,
    )

    offset = tl_match.start(1)
    pos = 0
    for entry in entries:
        entry_m = _SRT_XVAL_ENTRY_RE.match(entry)
        if not entry_m:
            pos += len(entry) + 1
            continue
        entry_tc_sec = _hms_to_sec(entry_m.group(2))

        quotes = _SRT_XVAL_QUOTE_RE.findall(entry)
        entry_ms = entry_tc_sec * 1000
        matches: list[tuple[str, int, int]] = []
        has_local_srt_support = False
        for quote in quotes:
            srt_ms = _find_quote_in_srt(quote, srt_cues, entry_ms=entry_ms)
            if srt_ms is None:
                continue
            srt_sec = srt_ms // 1000
            gap = abs(srt_sec - entry_tc_sec)
            matches.append((quote, srt_sec, gap))
            if gap < _SRT_XVAL_THRESHOLD_SEC:
                has_local_srt_support = True

        if has_local_srt_support:
            pos += len(entry) + 1
            continue

        for quote, srt_sec, gap in matches:
            if gap >= _SRT_XVAL_THRESHOLD_SEC:
                actual_hms = _sec_to_hms_str(srt_sec)
                marker = f" [실제 시점: {actual_hms}]"
                q_pos = entry.find(f'"{quote}"')
                if q_pos >= 0:
                    abs_pos = offset + pos + q_pos + len(f'"{quote}"')
                    corrections.append((abs_pos, 0, marker))
                    logger.info(
                        "SRT cross-val: quote %r entry=%s actual=%s gap=%ds",
                        quote[:20], entry_m.group(2), actual_hms, gap,
                    )
                break
        pos += len(entry) + 1

    if not corrections:
        return summary

    corrections.sort(key=lambda c: c[0], reverse=True)
    result = summary
    for ins_pos, _, marker in corrections:
        result = result[:ins_pos] + marker + result[ins_pos:]
    return result

def _postprocess_summary_md(
    summary: str,
    vod_info: VODInfo,
    public_url_base: str,
    *,
    promoted_anchors: Optional[list] = None,
    evidence_lookup=None,
    srt_cues: Optional[list] = None,
    comment_timeline_supplement_sink: Optional[dict] = None,
) -> str:
    'Claude 출력 .md 에 가공을 적용.\n\n    1. `## 📋 방송 분석 리포트:` 라인 바로 아래에 공유 링크 2줄 주입.\n       이미 들어있으면 no-op.\n    2.  — must-include anchor 결정론 inject (promoted_anchors 가 있을 때).\n       evidence_lookup 미충족 anchor 는 silent skip 금지, footer warning.\n    3. 하이라이트 추천 구간의 one-liner 포맷을 multiline 으로 정돈.\n    4. 모든 `[HH:MM:SS]` / `[HH:MM:SS~HH:MM:SS]` 타임코드를 chzzk 시점 인라인\n       마크다운 링크로 자동 변환 (멱등).\n    5. SRT 교차검증: 인용 발언이 세션 타임스탬프와 3분+ 차이 시 인라인 시점 표기.\n    '
    import re as _re

    header_block = _build_header_links_md(vod_info, public_url_base)

    missing_header_lines = [
        line for line in header_block.splitlines()
        if line and line not in summary
    ]
    if missing_header_lines:
        missing_header_block = "\n".join(missing_header_lines)
        title_re = _re.compile(r"^(##\s+.*?방송 분석 리포트[^\n]*)\n", _re.M)
        m = title_re.search(summary)
        if m:
            insertion = m.group(1) + "\n\n" + missing_header_block + "\n"
            summary = summary[:m.start()] + insertion + summary[m.end():]
        else:
            summary = missing_header_block + "\n\n" + summary

    if promoted_anchors:
        summary, injected, missing_evidence = _inject_missing_promoted_anchors(
            summary, promoted_anchors, evidence_lookup=evidence_lookup,
        )
        if injected:
            logger.info(
                f"T4 anchor inject: {len(injected)} entries injected "
                f"(evidence_missing={len(missing_evidence)})"
            )

    summary, comment_timeline_supplement_learning = _inject_comment_chapters_into_timeline(summary)
    if isinstance(comment_timeline_supplement_sink, dict):
        comment_timeline_supplement_sink.clear()
        comment_timeline_supplement_sink.update(comment_timeline_supplement_learning)
    if comment_timeline_supplement_learning.get("inserted_count"):
        logger.info(
            "comment chapter timeline supplement: %d entries injected into main timeline",
            comment_timeline_supplement_learning.get("inserted_count"),
        )

    summary = _strip_stray_hr(summary)
    summary = _strip_retired_editor_review_section(summary)
    summary = _strip_invalid_timeline_appendix(summary)

    if srt_cues:
        summary = _cross_validate_srt_timestamps(summary, srt_cues)

    summary = _reformat_highlights_multiline(summary)
    summary = _inject_source_timestamp_links(summary, vod_info)


    summary = _strip_editor_brief_section(summary)
    return summary

def _editorial_duration_label(seconds: float) -> str:
    duration = max(0, int(round(seconds)))
    if duration < 60:
        return f"{duration}초"
    minutes, remainder = divmod(duration, 60)
    if minutes < 60:
        return f"{minutes}분 {remainder:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분"

def _project_editorial_review_markdown(packet: dict, *, video_no: str) -> str:
    """Project accepted editorial content without internal review identifiers."""

    view = _validate_editorial_review_projection(packet)
    points = view["points"]
    points_by_id = view["points_by_id"]
    stories_by_ref = view["stories_by_ref"]
    highlights = view["highlights"]
    referenced_points: set[str] = set()
    rows = ["## 편집 Highlight", ""]

    for highlight in highlights:
        spans = list(highlight.get("source_spans") or [])
        start = int(float(spans[0]["start_sec"]))
        end = int(float(spans[-1]["end_sec"]))
        used_duration = sum(
            float(span["end_sec"]) - float(span["start_sec"])
            for span in spans
        )
        excluded_duration = sum(
            max(0, float(spans[index]["start_sec"]) - float(spans[index - 1]["end_sec"]))
            for index in range(1, len(spans))
        )
        point_refs = [str(value) for value in highlight.get("point_refs") or []]
        point_stories = [
            stories_by_ref[str(points_by_id[point_ref].get("story_ref") or "")]
            for point_ref in point_refs
        ]
        why_notable = (str(highlight.get("selection_reason") or "").strip()
                       if "selection_reason" in highlight else " / ".join(dict.fromkeys(
                           str(story.get("why_notable") or "").strip()
                           for story in point_stories
                           if str(story.get("why_notable") or "").strip()
                       )) or "-")
        rows.extend([
            f"### {highlight.get('title') or '편집 Highlight'}",
            str(highlight.get("story_summary") or "").strip(),
            f"- 왜 볼 가치가 있나: {why_notable}",
        ])
        if len(spans) == 1:
            rows.append(
                f"- [연속 구간 {sec_to_hms(start)}–{sec_to_hms(end)} 보기]"
                f"(https://chzzk.naver.com/video/{video_no}?currentTime={start})"
            )
        else:
            rows.extend([
                f"- [첫 장면부터 보기]"
                f"(https://chzzk.naver.com/video/{video_no}?currentTime={start})",
                f"- 연결 장면 {len(spans)}개 · 실제 사용 {_editorial_duration_label(used_duration)}"
                + (
                    f" · 중간 {_editorial_duration_label(excluded_duration)} 제외"
                    if excluded_duration > 0 else ""
                ),
            ])
        rows.append(
            "- 편집 구간: "
            + "; ".join(
                f"{sec_to_hms(int(float(span['start_sec'])))}–{sec_to_hms(int(float(span['end_sec'])))}"
                + (f" ({span.get('reason')})" if span.get("reason") else "")
                + (
                    f" [앞 공백 제외: {span['exclusion_before'].get('reason')}]"
                    if isinstance(span.get("exclusion_before"), dict)
                    and str(span["exclusion_before"].get("reason") or "").strip()
                    else ""
                )
                for span in spans
            )
        )
        referenced_points.update(point_refs)
        point_rows = []
        for point_ref in point_refs:
            point = points_by_id.get(point_ref)
            point_start = _hms_to_seconds(str(point.get("timestamp") or ""))
            story = stories_by_ref[str(point.get("story_ref") or "")]
            point_rows.append(
                f"  - [{point.get('timestamp')} {point.get('title') or ''}]"
                f"(https://chzzk.naver.com/video/{video_no}?currentTime={point_start})\n"
                f"    - 근거: {story.get('why_notable') or '-'}"
            )
        if point_rows:
            rows.append("- 이 시각부터 보기:")
            rows.extend(point_rows)
        rows.append("- Highlight 근거:")
        rows.append(
            "  - " + (str(highlight.get("story_summary") or "").strip() or "승인된 Point 근거를 묶은 편집 구간")
        )
        for span in spans:
            if str(span.get("reason") or "").strip():
                rows.append(f"  - {span.get('reason')}")
        rows.append("")

    standalone = [
        point for point in points
        if str(point.get("id") or "") not in referenced_points
    ]
    if standalone:
        rows.extend([f"### 나머지 주요 장면 {len(standalone)}개", ""])
        for point in standalone:
            point_start = _hms_to_seconds(str(point.get("timestamp") or ""))
            story = stories_by_ref[str(point.get("story_ref") or "")]
            rows.append(
                f"- [{point.get('timestamp')} {point.get('title') or ''}]"
                f"(https://chzzk.naver.com/video/{video_no}?currentTime={point_start})\n"
                f"  - 근거: {story.get('why_notable') or '-'}"
            )
    return "\n".join(rows).rstrip() + "\n"

def _strip_editorial_review_markdown(report_markdown: str) -> str:
    """Remove only the projection-owned section, preserving later report text."""

    return re.sub(
        r"(?:^---[ \t]*\n+)?^## (?:승인된 편집 Highlight|편집 Highlight)[ \t]*\n.*?(?=^## |\Z)",
        "",
        str(report_markdown or ""),
        flags=re.MULTILINE | re.DOTALL,
    )

def _merge_editorial_review_markdown(
    report_markdown: str,
    packet: dict,
    *,
    video_no: str,
) -> str:
    """Replace the projection-owned editorial section without rewriting the report."""

    projected = _project_editorial_review_markdown(packet, video_no=video_no)



    report_markdown = re.sub(
        r"\n## 검토 범위\s*\n.*?(?=\n## |\Z)",
        "",
        report_markdown,
        count=1,
        flags=re.DOTALL,
    )
    report_markdown = _strip_editorial_review_markdown(report_markdown)
    return report_markdown.rstrip() + "\n\n---\n\n" + projected

def _normalize_promoted_anchors_for_inject(
    promoted_anchors: Optional[list],
    user_clips: Optional[UserClipsBundle],
) -> list[dict]:
    """`(off, like, play)` 튜플 또는 dict 리스트 → inject 가 요구하는 dict shape.

    enrich: user_clips bundle 의 (offset, like, play) 매칭 클립에서 title/clip_uid
    회수. 매칭 실패 시 default title 로 graceful degradation (inject 자체는 진행).

    이미 dict 인 항목은 통과 (offset_sec 만 검증). 불량 항목은 silently drop —
    production 의 _inject_missing_promoted_anchors 가 None offset 에 죽지 않도록.
    """
    if not promoted_anchors:
        return []
    by_key: dict[tuple[int, int, int], object] = {}
    if user_clips is not None:
        for c in (getattr(user_clips, "clips", None) or []):
            try:
                if c.offset_sec is None:
                    continue
                by_key[(int(c.offset_sec), int(c.like_count or 0),
                        int(c.play_count or 0))] = c
            except (TypeError, ValueError, AttributeError):
                continue
    out: list[dict] = []
    for a in promoted_anchors:
        try:
            if isinstance(a, dict):
                if a.get("offset_sec") is None:
                    continue
                out.append(dict(a))
                continue
            off = int(a[0]); like = int(a[1]); play = int(a[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        d: dict = {"offset_sec": off, "like_count": like, "play_count": play}
        c = by_key.get((off, like, play))
        if c is not None:
            d["clip_uid"] = getattr(c, "clip_uid", "") or ""
            d["title"] = getattr(c, "title", "") or ""
        out.append(d)
    return out
