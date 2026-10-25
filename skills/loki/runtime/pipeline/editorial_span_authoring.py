"""Author-only normalization for continuous editorial source footage."""

from __future__ import annotations

import math

import re

from decimal import Decimal

from typing import Any, Mapping

def _semantic_evidence_time(
    ref: str, evidence_catalog: Mapping[str, Any]
) -> float | None:
    native = evidence_catalog.get(ref)
    if isinstance(native, Mapping) and "native_start_sec" in native:
        return float(native["native_start_sec"])
    if ref.startswith("stt-sec:"):
        try:
            return float(ref.split(":", 1)[1])
        except ValueError:
            return None
    if ref.startswith("chat-ms:"):
        try:
            return float(ref.split(":", 2)[1]) / 1000.0 if re.fullmatch(r"chat-ms:[0-9]+(?::[0-9a-f]{20})?", ref) else None
        except ValueError:
            return None
    row = evidence_catalog.get(ref)
    if not isinstance(row, Mapping):
        return None
    for key in ("time_sec", "canonical_time_sec"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            return number
    return None

def _role_only_reason(reason: str) -> bool:
    compact = re.sub(r"[^a-z가-힣]+", " ", reason.lower()).strip()
    tokens = set(compact.split())
    role_tokens = {
        "story", "role", "transition", "beat", "setup", "development",
        "performance", "event", "reaction", "result", "conversation",
        "역할", "전환", "도입", "전개", "사건", "반응", "결말",
    }
    return not tokens or tokens.issubset(role_tokens)

def _evidence_overlaps_gap(ref, catalog, start, end):
    """Intervals need positive overlap; point evidence must be strictly inside.

    None is an unknown/invalid clock, never a zero-length interval or a cue
    whose time can be guessed. This proves location only, not a good omission.
    """
    row = catalog.get(ref) or {}
    if any(key in row for key in ("native_start_sec", "native_end_sec", "source_end_sec")):
        a = row.get("native_start_sec", row.get("time_sec"))
        b = row.get("native_end_sec", row.get("source_end_sec"))
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b)) or not 0 <= a < b:
            return None
        return start < b and a < end
    try:
        point = _semantic_evidence_time(ref, catalog)
    except (ValueError, TypeError):
        return None
    if point is None or not math.isfinite(point) or point < 0:
        return None
    return start < point < end

def _normalize_authored_source_span_boundaries(
    raw_spans: list[dict[str, Any]],
    *,
    evidence_catalog: Mapping[str, Any],
    approved_primary_refs: set[str],
    gap_coverage_batches: list[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[int], set[str]]:
    """Merge touching footage and require grounded reasons for every real cut."""

    spans: list[dict[str, Any]] = []
    raw_to_canonical: list[int] = []
    exclusion_evidence_refs: set[str] = set()
    for raw_index, raw in enumerate(raw_spans):
        start = float(raw["start_sec"])
        prior_end = float(raw_spans[raw_index - 1]["end_sec"]) if raw_index else -1.0
        raw_exclusion = raw.pop("_raw_exclusion_before", None)
        if raw_index == 0 and raw_exclusion is not None:
            raise ValueError("first_source_span_cannot_have_exclusion_rationale")
        native_seam = raw_index and raw_exclusion is None and _native_millisecond_seam(
            raw_spans[raw_index - 1], raw, evidence_catalog
        )
        if raw_index and start > prior_end and not native_seam:
            if not isinstance(raw_exclusion, Mapping):
                raise ValueError("source_span_gap_requires_exclusion_rationale")
            reason = str(raw_exclusion.get("reason") or "").strip()
            if not reason:
                raise ValueError("source_span_exclusion_reason_required")
            if _role_only_reason(reason):
                raise ValueError("source_span_gap_cannot_be_justified_by_story_role")
            refs_value = raw_exclusion.get("evidence_refs")
            if not isinstance(refs_value, (list, tuple)):
                raise ValueError("source_span_exclusion_evidence_refs_must_be_a_list")
            refs = [str(ref or "").strip() for ref in refs_value]
            if not refs or any(not ref for ref in refs) or len(refs) != len(set(refs)):
                raise ValueError(
                    "source_span_exclusion_evidence_refs_must_have_unique_nonempty_refs"
                )
            intersections = [_evidence_overlaps_gap(ref, evidence_catalog, prior_end, start) for ref in refs]
            if any(value is None for value in intersections) or not any(intersections):
                raise ValueError("source_span_exclusion_evidence_must_be_inside_gap")
            if not all(
                ref.startswith(("stt-sec:", "chat-ms:", "chzzk-semantic:"))
                or ref in approved_primary_refs
                for ref in refs
            ):
                raise ValueError(
                    "source_span_exclusion_requires_primary_semantic_evidence"
                )
            overlapping_batches = sorted(
                (
                    batch
                    for batch in gap_coverage_batches or []
                    if float(batch.get("end_sec") or 0) > prior_end
                    and float(batch.get("start_sec") or 0) < start
                ),
                key=lambda batch: (
                    float(batch.get("start_sec") or 0),
                    float(batch.get("end_sec") or 0),
                ),
            )
            uncovered_batches: list[Mapping[str, Any]] = []
            coverage_cursor = prior_end
            for batch in overlapping_batches:
                batch_start = max(prior_end, float(batch.get("start_sec") or 0))
                batch_end = min(start, float(batch.get("end_sec") or 0))
                if batch_end <= batch_start:
                    continue
                if batch_start > coverage_cursor:
                    raise ValueError(
                        "source_span_exclusion_bridge_coverage_unavailable"
                    )
                batch_refs = {
                    str(ref)
                    for ref in batch.get("evidence_refs") or []
                    if str(ref)
                    and _evidence_overlaps_gap(str(ref), evidence_catalog, batch_start, batch_end)
                }
                if not batch_refs:
                    raise ValueError(
                        "source_span_gap_needs_source_reread"
                    )
                if not batch_refs.intersection(refs):
                    uncovered_batches.append(batch)
                coverage_cursor = max(coverage_cursor, batch_end)
            if overlapping_batches and coverage_cursor < start:
                raise ValueError(
                    "source_span_exclusion_bridge_coverage_unavailable"
                )
            if uncovered_batches:
                raise ValueError(
                    "source_span_exclusion_evidence_does_not_cover_gap"
                )
            raw["exclusion_before"] = {"reason": reason, "evidence_refs": refs}
            exclusion_evidence_refs.update(refs)

        if spans and start == float(spans[-1]["end_sec"]):
            previous = spans[-1]
            previous["end_sec"] = raw["end_sec"]
            previous["duration_sec"] = (
                float(raw["end_sec"]) - float(previous["start_sec"])
            )
            for key in ("story_roles", "evidence_refs"):
                previous[key] = list(dict.fromkeys([*previous[key], *raw[key]]))
            if raw["reason"] != previous["reason"]:
                previous["reason"] = f'{previous["reason"]} / {raw["reason"]}'
            raw_to_canonical.append(len(spans) - 1)
            continue
        raw["span_index"] = len(spans)
        spans.append(raw)
        raw_to_canonical.append(len(spans) - 1)
    return spans, raw_to_canonical, exclusion_evidence_refs

def _native_millisecond_seam(left, right, catalog):
    """Recognize adjacent native cue serialization, without moving either cut.

    This exception is limited to the source-bound eN occurrence namespace.
    It says nothing about audible boundaries or the quality of a larger cut.
    """
    end, start = left["end_sec"], right["start_sec"]
    if Decimal(str(start)) - Decimal(str(end)) != Decimal("0.001"):
        return False
    for ref in left.get("evidence_refs", []):
        if not re.fullmatch(r"e[1-9][0-9]*", ref):
            continue
        next_ref = "e" + str(int(ref[1:]) + 1)
        if next_ref not in right.get("evidence_refs", []):
            continue
        a, b = catalog.get(ref, {}), catalog.get(next_ref, {})
        clocks = [row.get(key) for row in (a, b) for key in ("native_start_sec", "native_end_sec")]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in clocks)
                or not 0 <= clocks[0] < clocks[1] or not 0 <= clocks[2] < clocks[3]):
            continue
        if (a.get("source_type") == b.get("source_type") == "stt"
                and a.get("source_revision") and a["source_revision"] == b.get("source_revision")
                and str(a.get("native_ref", "")).startswith("stt-occ:")
                and str(b.get("native_ref", "")).startswith("stt-occ:")
                and a["native_ref"] != b["native_ref"]
                and a.get("native_end_sec") == end and b.get("native_start_sec") == start
                and not any(_evidence_overlaps_gap(key, catalog, end, start) is True for key in catalog)):
            return True
    return False
