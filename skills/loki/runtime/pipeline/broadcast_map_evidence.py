"""Stage-qualified, answer-free evidence activation for BroadcastMap generation.

This module does not create structure.  It makes already-prepared inputs visible to
the sole manager-outline finalizer with explicit roles, availability, lineage and
negative authority.  Answer-derived artifacts are represented only as excluded
guard rows; their content is never accepted by this API.
"""

from __future__ import annotations

import hashlib

import json

import re

from typing import Any, Iterable, Mapping

def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

_CANONICAL_TIMED_STT_LINE = re.compile(
    r"^\s*\[(\d{2}:\d{2}:\d{2})\]\s*(.+?)\s*$"
)

def _canonical_stt_rows(chunks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return de-duplicated source rows without making chunk layout authoritative."""

    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        for line in str(chunk.get("text") or "").splitlines():
            match = _CANONICAL_TIMED_STT_LINE.match(line)
            if not match:
                continue
            excerpt = match.group(2).strip()
            if not excerpt:
                continue
            sec = _hms_to_sec(match.group(1))
            rows.setdefault((sec, excerpt), {"start_sec": sec, "text": excerpt})
    return [rows[key] for key in sorted(rows)]

def _canonical_source_revision(source_type: str, rows: Iterable[Mapping[str, Any]]) -> str:
    return _canonical_sha256({"source_type": source_type, "rows": list(rows)})

def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

_CONTEXT_NAMING_ANSWER_LINE = re.compile(
    r"""(?ix)
    ^\s*(?:(?:[-*•>]+|\#{1,6}|[0-9]{1,3}[.)])\s*)?(?:
        \[(?:D[12]|Point|Candidate|포인트|후보|실제\s*목차|주요\s*장면|Point\s*검토창|하이라이트\s*후보\s*구간)\]
        |(?:D[12]|Point|Candidate|포인트|후보)(?=\s|:|[을를은는이가])
        |(?:실제\s*목차|주요\s*장면|하이라이트\s*후보\s*구간)(?=\s|:|$)
    )
    """
)

_CONTEXT_NAMING_INSTRUCTION_LINE = re.compile(
    r"""(?ix)(?:
        ^\s*(?:system|assistant|user|prompt)\s*:
        |\b(?:ignore|must|follow|instruction|prompt|output|generate|write|summarize)\b
        |(?:무시(?:하|해|하고)?|반드시|지시(?:문)?|따르(?:라|세요)?|작성(?:하|해|하세요)|
           출력(?:하|해|하세요)|생성(?:하|해|하세요)|요약(?:하|해|하세요)|사용하세요)
    )"""
)

def _safe_context_naming_text(value: Any) -> str:
    """Keep source layout while removing explicit prompt/answer-shaped lines.

    A timestamp is valid source context, not an answer by itself.  Custody keeps
    previous summaries/corrections out of this argument; this sanitizer removes
    explicit D1/D2/Point/Candidate answers and instructions without flattening
    paragraph boundaries, dividers, or timestamp-following descriptions.
    """

    safe_lines: list[str] = []
    for raw_line in str(value or "").splitlines():
        line = raw_line.rstrip()
        if (
            line.strip()
            and (
                _CONTEXT_NAMING_ANSWER_LINE.match(line)
                or _CONTEXT_NAMING_INSTRUCTION_LINE.search(line)
            )
        ):


            safe_lines.append("")
            continue
        safe_lines.append(line)
    return _bounded_text("\n".join(safe_lines), 3_600)

def _hms_to_sec(value: str) -> int:
    parts = [int(part) for part in str(value).split(":")]
    if len(parts) != 3:
        raise ValueError(f"invalid HH:MM:SS value: {value}")
    return parts[0] * 3600 + parts[1] * 60 + parts[2]
