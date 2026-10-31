"""Preview-only editor cut candidates from summary/editor signals."""

from __future__ import annotations

import hashlib

from typing import Any, Iterable

SCHEMA_VERSION = "editor_cut_candidate.v1"

def build_editor_cut_candidates(
    segment_ledger: Iterable[dict[str, Any]],
    *,
    boundary_signals: Iterable[dict[str, Any]] = (),
    snap_window_sec: int | float = 5,
) -> list[dict[str, Any]]:
    """Build raw-free, review-first cut candidates with original and snapped times."""

    boundaries = [_boundary(row) for row in boundary_signals or [] if isinstance(row, dict)]
    boundaries = [row for row in boundaries if row is not None]
    candidates: list[dict[str, Any]] = []
    for row in segment_ledger or []:
        if not isinstance(row, dict) or row.get("kind") not in {"highlight", "timeline", "chapter"}:
            continue
        start = _int_or_none(row.get("start_sec"))
        if start is None:
            continue
        end = _int_or_none(row.get("end_sec"))
        if end is None:
            end = start + 10
        if end <= start:
            continue
        snapped_start, start_ref = _snap_time(start, boundaries, snap_window_sec)
        snapped_end, end_ref = _snap_time(end, boundaries, snap_window_sec)
        snapped_start, start_ref, snapped_end, end_ref = _avoid_shared_boundary_snap(
            start,
            end,
            snapped_start,
            start_ref,
            snapped_end,
            end_ref,
        )
        if snapped_end <= snapped_start:
            snapped_end = end
            end_ref = None
        source_refs = _source_refs(row, start_ref, end_ref)
        confidence = _confidence(row, start_ref, end_ref)
        candidates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": _candidate_id(row, snapped_start, snapped_end),
                "segment_id": str(row.get("segment_id") or ""),
                "kind": str(row.get("kind") or ""),
                "title": str(row.get("title") or ""),
                "start_sec": snapped_start,
                "end_sec": snapped_end,
                "duration_sec": snapped_end - snapped_start,
                "original_start_sec": start,
                "original_end_sec": end,
                "snap_reason": _snap_reason(start_ref, end_ref),
                "source_signal_refs": source_refs,
                "confidence": confidence,
                "needs_review": confidence != "high",
                "uncertainty": "exact_cut" if confidence == "high" else "suggested_range",
            }
        )
    candidates.sort(key=lambda item: (item["start_sec"], item["end_sec"], item["candidate_id"]))
    return candidates

def _boundary(row: dict[str, Any]) -> dict[str, Any] | None:
    sec = _int_or_none(row.get("sec", row.get("start_sec")))
    if sec is None:
        return None
    return {
        "sec": sec,
        "signal_id": str(row.get("signal_id") or row.get("source_id") or row.get("id") or row.get("event_id") or ""),
        "signal_type": str(row.get("signal_type") or row.get("marker_type") or row.get("kind") or row.get("source") or "boundary"),
        "confidence": _normalize_confidence(row.get("confidence")),
    }

def _snap_time(value: int, boundaries: list[dict[str, Any]], window_sec: int | float) -> tuple[int, dict[str, Any] | None]:
    window = max(0, _int_or_none(window_sec) or 0)
    best: dict[str, Any] | None = None
    best_distance: int | None = None
    for boundary in boundaries:
        distance = abs(int(boundary["sec"]) - value)
        if distance > window:
            continue
        if best is None or best_distance is None or distance < best_distance:
            best = boundary
            best_distance = distance
    if best is None:
        return value, None
    return int(best["sec"]), best

def _avoid_shared_boundary_snap(
    start: int,
    end: int,
    snapped_start: int,
    start_ref: dict[str, Any] | None,
    snapped_end: int,
    end_ref: dict[str, Any] | None,
) -> tuple[int, dict[str, Any] | None, int, dict[str, Any] | None]:
    if not start_ref or not end_ref or not _same_boundary(start_ref, end_ref):
        return snapped_start, start_ref, snapped_end, end_ref
    start_distance = abs(int(start_ref["sec"]) - start)
    end_distance = abs(int(end_ref["sec"]) - end)
    if start_distance == end_distance:
        return start, None, end, None
    if start_distance < end_distance:
        return snapped_start, start_ref, end, None
    return start, None, snapped_end, end_ref

def _same_boundary(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("sec"),
        left.get("signal_id"),
        left.get("signal_type"),
    ) == (
        right.get("sec"),
        right.get("signal_id"),
        right.get("signal_type"),
    )

def _source_refs(row: dict[str, Any], start_ref: dict[str, Any] | None, end_ref: dict[str, Any] | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    segment_id = str(row.get("segment_id") or "")
    if segment_id:
        refs.append({"type": "segment_ledger", "segment_id": segment_id, "kind": str(row.get("kind") or "")})
    for role, ref in (("start_boundary", start_ref), ("end_boundary", end_ref)):
        if ref:
            refs.append(
                {
                    "type": role,
                    "signal_id": ref["signal_id"],
                    "signal_type": ref["signal_type"],
                    "confidence": ref["confidence"],
                }
            )
    return refs

def _snap_reason(start_ref: dict[str, Any] | None, end_ref: dict[str, Any] | None) -> str:
    if start_ref and end_ref:
        return "start_and_end_snapped_to_nearby_boundaries"
    if start_ref:
        return "start_snapped_to_nearby_boundary"
    if end_ref:
        return "end_snapped_to_nearby_boundary"
    return "segment_time_preview_no_boundary_snap"

def _confidence(row: dict[str, Any], start_ref: dict[str, Any] | None, end_ref: dict[str, Any] | None) -> str:
    if (
        start_ref
        and end_ref
        and row.get("kind") == "highlight"
        and start_ref.get("confidence") == "high"
        and end_ref.get("confidence") == "high"
    ):
        return "high"
    if start_ref or end_ref or row.get("kind") == "highlight":
        return "medium"
    return "low"

def _candidate_id(row: dict[str, Any], start: int, end: int) -> str:
    seed = "|".join([str(row.get("segment_id") or ""), str(row.get("kind") or ""), str(start), str(end)])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"cut_{digest}"

def _int_or_none(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None

def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "medium").strip().lower()
    return confidence if confidence in {"high", "medium", "low"} else "medium"
