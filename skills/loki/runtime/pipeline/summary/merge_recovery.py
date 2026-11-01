"""Primary owner for merge structure repair and fallback recovery."""

from __future__ import annotations

import re

from .parse import _is_timeline_format_appendix_line

SUMMARY_MERGE_FALLBACK_MARKER = "<!-- summary_merge_fallback: chunk_scaffold.v1 -->"

SUMMARY_MERGE_FALLBACK_SCHEMA_VERSION = "summary_merge_fallback_quality.v1"

SUMMARY_MERGE_FALLBACK_TIMELINE_MAX_BLOCKS = 32

_TIMELINE_ENTRY_START_RE = re.compile(
    r"^\s{0,4}[-*]\s*\*{0,4}\s*(?:\[VOD\s+\d+\]\s*)?\[?\d{1,2}:\d{2}:\d{2}",
)

_STRAY_HR_RE = re.compile(r"\n{2,}---\n{2,}")

_MAIN_TIMELINE_SECTION_RE = re.compile(
    r"(###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n)(.+?)(?=\n###\s|\Z)",
    re.S,
)

def _strip_stray_hr(text: str) -> str:
    'LLM이 에코한 마크다운 수평선(---)을 제거한다.'
    return _STRAY_HR_RE.sub("\n\n", text)

def _strip_invalid_timeline_appendix(text: str) -> str:
    """Remove model-emitted format legends accidentally placed in timeline."""
    if not text:
        return text

    def _clean(match: "re.Match[str]") -> str:
        header, body = match.group(1), match.group(2)
        lines = body.splitlines(keepends=True)
        seen_entry = False
        kept: list[str] = []
        for line in lines:
            if _TIMELINE_ENTRY_START_RE.match(line):
                seen_entry = True
            if seen_entry and _is_timeline_format_appendix_line(line):
                break
            kept.append(line)
        if len(kept) == len(lines):
            return match.group(0)
        return header + "".join(kept).rstrip() + "\n"

    return _MAIN_TIMELINE_SECTION_RE.sub(_clean, text, count=1)
