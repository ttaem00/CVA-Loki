"""BroadcastMap renderers for manager Markdown and editable tagged outline text.

This module owns deterministic output from an already validated BroadcastMap.
It depends only on shared schema/check contracts and never imports the parser.
"""

from __future__ import annotations

from typing import Any

from .check import validate_structured_manager_outline

def project_manager_outline_markdown(
    outline: dict[str, Any],
    *,
    duration_sec: int | None = None,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Project BroadcastMap into the deterministic, manager-visible Markdown view."""
    validate_structured_manager_outline(outline, duration_sec=duration_sec)
    ranges = list(outline.get("ranges") or [])
    points = list(outline.get("points") or [])
    candidates = list(outline.get("candidates") or [])
    evidence_by_id = evidence_by_id or {}
    rows: list[str] = ["## 방송 흐름", ""]

    def append_item(heading: str, item: dict[str, Any]) -> None:
        rows.append(f"{heading} {str(item.get('title') or '').strip()}")
        content = str(item.get("content") or "").strip()
        if content:
            rows.extend([content, ""])
        else:
            rows.append("")

    for parent in (item for item in ranges if item.get("level") == "D1"):
        append_item(f"### {parent['start']}–{parent['end']}", parent)
        for child in ranges:
            if child.get("level") == "D2" and child.get("parent_id") == parent.get("id"):
                append_item(f"#### {child['start']}–{child['end']}", child)

    rows.extend(["## 주요 장면", ""])
    for item in points:
        append_item(f"### {item['timestamp']}", item)
        evidence = evidence_by_id.get(str(item.get("id") or "")) or {}
        reaction = str(evidence.get("actual_reaction") or "").strip()
        sources = evidence.get("sources") or []
        if reaction:
            rows.extend([f"- 실제 반응: {reaction}", ""])
        if sources:
            rows.extend([f"- 확인 근거: {', '.join(str(value) for value in sources)}", ""])

    rows.extend(["## 검토 범위", ""])
    for item in candidates:
        append_item(f"### {item['start']}–{item['end']}", item)
    return "\n".join(rows).rstrip() + "\n"
