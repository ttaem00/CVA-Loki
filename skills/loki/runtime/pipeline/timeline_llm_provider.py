"""치지직 댓글 타임라인 LLM Provider — llm_gateway 경유 통합 호출.

fallback 순서는 pipeline_config.json 의 llm_task_provider_order.timeline
(우선) → llm_default_provider_order (전역 설정) 로 결정.
gateway 가 provider 가용성을 자동 판별하며, 전부 실패 시 None 반환.
"""

from __future__ import annotations

import logging

import re

from .llm_structured_output import validate_comment_timeline_items

from .timeline_alignment import hms_to_sec

from .utils import sec_to_hms

logger = logging.getLogger("pipeline")

_CONTEXT_TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s*)")

_CONTEXT_TITLE_MAX_CHARS = 35

def _clean_context_timeline_title(line: str, time_value: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", str(line or ""))
    text = _CONTEXT_TIMECODE_RE.sub(" ", text, count=1)
    text = _LEADING_LIST_MARKER_RE.sub("", text)
    text = re.sub(r"[`*_#>\[\]()]+", " ", text)
    text = re.sub(r"^[\s:：\-–—~·|]+|[\s:：\-–—~·|]+$", "", text)
    text = " ".join(text.split())
    if not text:
        text = f"{time_value} 챕터"
    if len(text) > _CONTEXT_TITLE_MAX_CHARS:
        text = text[:_CONTEXT_TITLE_MAX_CHARS].rstrip(" .,:;:：-–—~·|")
    return text

def extract_context_comment_timeline_items(
    context_doc: str | None,
    *,
    max_items: int = 18,
) -> list[dict[str, str]]:
    """Parse manager-curated timed context lines into comment timeline items.

    Manager edit lessons showed that a timed per-VOD context table is the most
    reliable navigation skeleton for `### 치지직 댓글 타임라인`. This parser is
    deliberately conservative: it only uses lines that contain an explicit
    `M:SS`, `MM:SS`, or `H:MM:SS` marker and a non-empty title after removing
    that marker.
    """

    text = str(context_doc or "").strip()
    if not text:
        return []

    rows: list[tuple[int, int, dict[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        match = _CONTEXT_TIMECODE_RE.search(stripped)
        if not match:
            continue
        raw_tc = match.group(1)
        try:
            sec = hms_to_sec(raw_tc)
        except (TypeError, ValueError):
            continue
        tc = sec_to_hms(sec)
        title = _clean_context_timeline_title(stripped, raw_tc)
        key = (tc, title)
        if key in seen:
            continue
        seen.add(key)
        rows.append((sec, index, {"tc": tc, "title": title}))

    if not rows:
        return []
    rows.sort(key=lambda row: (row[0], row[1]))
    items = [row[2] for row in rows[: max(1, int(max_items or 1))]]
    try:
        return validate_comment_timeline_items(items)
    except Exception as exc:
        logger.warning("context comment timeline parse failed validation: %s", exc)
        return []
