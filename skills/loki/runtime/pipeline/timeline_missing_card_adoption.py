"""Bounded adoption adapter for verified viewer-clip timeline nominations."""

from __future__ import annotations

import re

from typing import Any

from .summary_evidence_pack import build_timeline_evidence_shadow_contract

_TIMELINE_CARD_RE = re.compile(r"^- \*\*\[(\d{2,}):(\d{2}):(\d{2})\]")

_LEGACY_UNTRUSTED_BODY = (
    "내용: 같은 시각의 실제 자막 또는 채팅과 시청자 인기 클립으로 확인된 장면."
)

def remove_legacy_untrusted_missing_cards(markdown: str) -> str:
    """Remove cards created by the retired time-only viewer-clip adopter.

    The old adopter treated nearby source timestamps as proof that a clip title
    described the same scene. Removal is deliberately limited to the exact
    deterministic block signature emitted by that adopter.
    """

    lines = str(markdown or "").splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if not _TIMELINE_CARD_RE.match(lines[index]):
            kept.append(lines[index])
            index += 1
            continue
        block_end = index + 1
        while block_end < len(lines):
            if _TIMELINE_CARD_RE.match(lines[block_end]) or lines[block_end].startswith("### "):
                break
            block_end += 1
        block = lines[index:block_end]
        normalized = [line.strip() for line in block]
        generated_signature = (
            f"- {_LEGACY_UNTRUSTED_BODY}" in normalized
            and any(line.startswith("- 시청자 클립: [") for line in normalized)
            and any(
                line.startswith("- 근거:")
                and "시청자 클립 " in line
                and ("자막 " in line or "채팅 " in line)
                for line in normalized
            )
        )
        if generated_signature:
            while kept and not kept[-1].strip():
                kept.pop()
            index = block_end
            continue
        kept.extend(block)
        index = block_end
    return "\n".join(kept).rstrip() + ("\n" if str(markdown or "").endswith("\n") else "")

def apply_verified_missing_cards(
    markdown: str,
    *,
    video_no: str,
    duration_sec: int | float | None,
    segment_ledger: list[dict[str, Any]],
    timeline_source_evidence: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Keep nominations as review data but never add them to public timelines."""

    shadow = build_timeline_evidence_shadow_contract(
        segment_ledger=segment_ledger,
        timeline_source_evidence=timeline_source_evidence,
    )
    return (
        remove_legacy_untrusted_missing_cards(markdown),
        shadow,
    )
