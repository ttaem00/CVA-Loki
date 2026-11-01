"""Versioned cross-object contract for the accepted editorial authority packet.

This module does not select editorial content. It only prevents Point, Story,
and Highlight from acquiring overlapping product or time authority.
"""

from __future__ import annotations

from typing import Any, Mapping

def full_author_completion_state(*, unresolved_proposals: object, source_clock: object = None,
                                 unresolved_flows: object = None) -> str:
    """Keep unfinished broadcast writing distinct from independently valid cuts."""
    if unresolved_proposals:
        return "partial_pending_repair"
    return "partial_pending_context" if source_clock or unresolved_flows else "authored"

_POINT_FORBIDDEN_TIME_FIELDS = {
    "start",
    "start_sec",
    "end",
    "end_sec",
    "context_start",
    "context_end",
    "event_timestamp",
    "exact_event_timestamp",
    "review_start",
    "review_end",
    "review_window",
}

_STORY_FORBIDDEN_NAVIGATION_FIELDS = _POINT_FORBIDDEN_TIME_FIELDS | {
    "timestamp",
    "href",
    "url",
    "currentTime",
}

_HIGHLIGHT_FORBIDDEN_RANGE_FIELDS = {
    "start",
    "start_sec",
    "end",
    "end_sec",
    "highlight_start",
    "highlight_end",
    "review_window",
    "context_range",
    "clip_range",
}

_CANONICAL_LANE_FORBIDDEN_FIELDS = {
    "candidate_id",
    "candidate_refs",
    "raw_signal",
    "raw_signals",
    "signal_metadata",
}

def _rows(value: Any, error: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise ValueError(error)
    return value

def _unique_nonempty_ids(rows: list[Mapping[str, Any]], field: str, error: str) -> set[str]:
    values = [str(row.get(field) or "").strip() for row in rows]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(error)
    return set(values)

def _reject_fields(row: Mapping[str, Any], forbidden: set[str], error: str) -> None:
    if forbidden.intersection(row):
        raise ValueError(error)

def _clock_seconds(value: Any) -> float:
    parts = str(value or "").split(":")
    if len(parts) != 3:
        raise ValueError("invalid_broadcast_map_clock")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("invalid_broadcast_map_clock") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("invalid_broadcast_map_clock")
    return float(hours * 3600 + minutes * 60 + seconds)

def _derive_source_span_structure_refs(
    *,
    start: float,
    end: float,
    d1_index: Mapping[str, Mapping[str, Any]],
    d2_index: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    """Resolve span membership from frozen ranges without using D2 as a clip."""

    def _intersecting_ranges(
        index: Mapping[str, Mapping[str, Any]],
    ) -> list[tuple[float, float, str]]:
        values = []
        for ref, row in index.items():
            range_start = _clock_seconds(row.get("start"))
            range_end = _clock_seconds(row.get("end"))
            if range_start < end and start < range_end:
                values.append((range_start, range_end, ref))
        return sorted(values, key=lambda item: (item[0], item[1], item[2]))

    intersecting_d1 = _intersecting_ranges(d1_index)
    if not intersecting_d1:
        raise ValueError("source_span_clock_outside_broadcast_map_d1")
    covered_until = start
    for range_start, range_end, _ref in intersecting_d1:
        if range_start > covered_until:
            raise ValueError("source_span_crosses_unmapped_d1_gap")
        covered_until = max(covered_until, range_end)
        if covered_until >= end:
            break
    if covered_until < end:
        raise ValueError("source_span_clock_outside_broadcast_map_d1")

    intersecting_d2 = _intersecting_ranges(d2_index)
    return (
        [item[2] for item in intersecting_d1],
        [item[2] for item in intersecting_d2],
    )

def validate_editorial_authority_shape(
    packet: Mapping[str, Any],
    *,
    allow_legacy_without_story: bool = False,
) -> Mapping[str, Any]:
    """Validate only the shared Point/Story/Highlight authority invariants.

    Semantic quality, evidence truth, span ordering, and BroadcastMap hashes stay
    with their existing owners. This is deliberately a small parity boundary.
    """

    if not isinstance(packet, Mapping):
        raise ValueError("invalid_editorial_authority_packet")
    if "collaboration_periods" in packet:
        from .chatgpt_export.contract.collaboration_periods import validate_collaboration_periods
        validate_collaboration_periods(packet["collaboration_periods"], packet.get("duration_sec"))
    broadcast_map = packet.get("broadcast_map")
    if not isinstance(broadcast_map, Mapping):
        raise ValueError("invalid_editorial_broadcast_map")

    points = _rows(broadcast_map.get("points"), "invalid_editorial_points")
    stories = _rows(packet.get("stories"), "invalid_editorial_stories")
    highlights = _rows(packet.get("editorial_highlights"), "invalid_editorial_highlights")

    point_ids = _unique_nonempty_ids(points, "id", "invalid_or_duplicate_point_id")
    story_ids = _unique_nonempty_ids(stories, "story_ref", "invalid_or_duplicate_story_ref")

    legacy_without_story = (
        allow_legacy_without_story
        and not stories
        and all(not str(point.get("story_ref") or "").strip() for point in points)
    )
    point_story_refs: list[str] = []
    story_ref_by_point_id: dict[str, str] = {}
    for point in points:
        _reject_fields(point, _POINT_FORBIDDEN_TIME_FIELDS, "point_has_second_time_authority")
        _reject_fields(point, _CANONICAL_LANE_FORBIDDEN_FIELDS, "candidate_or_signal_mixed_into_canonical_lane")
        timestamp = point.get("timestamp")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float, str))
            or not str(timestamp).strip()
        ):
            raise ValueError("point_requires_single_timestamp")
        story_ref = str(point.get("story_ref") or "").strip()
        if not story_ref and not legacy_without_story:
            raise ValueError("point_requires_story_ref")
        if story_ref:
            point_story_refs.append(story_ref)
            story_ref_by_point_id[str(point.get("id") or "").strip()] = story_ref

    for story in stories:
        _reject_fields(story, _STORY_FORBIDDEN_NAVIGATION_FIELDS, "story_has_time_or_navigation_authority")
        _reject_fields(story, _CANONICAL_LANE_FORBIDDEN_FIELDS, "candidate_or_signal_mixed_into_canonical_lane")

    if not legacy_without_story and (
        len(point_story_refs) != len(set(point_story_refs)) or set(point_story_refs) != story_ids
    ):
        raise ValueError("point_story_must_be_exactly_one_to_one")

    context_authored = bool(highlights) and all(
        row.get("composition_contract") == "editorial.context-author.v22" and row.get("native_timing")
        for row in highlights)
    if len(highlights) > len(points) and not context_authored:
        raise ValueError("highlight_count_must_be_between_zero_and_point_count")

    for highlight in highlights:
        _reject_fields(highlight, _HIGHLIGHT_FORBIDDEN_RANGE_FIELDS, "highlight_has_second_range_authority")
        _reject_fields(highlight, _CANONICAL_LANE_FORBIDDEN_FIELDS, "candidate_or_signal_mixed_into_canonical_lane")
        refs = highlight.get("point_refs")
        independent = (highlight.get("story_link_mode") == "source_independent"
            and highlight.get("composition_contract") == "editorial.context-author.v22"
            and bool(highlight.get("native_timing")))
        if independent and (refs or highlight.get("story_refs") or highlight.get("story_ref")):
            raise ValueError("independent_highlight_has_story_or_point_link")
        if not isinstance(refs, list) or (not refs and not independent):
            raise ValueError("highlight_requires_one_or_more_point_refs")
        normalized_refs = [str(ref or "").strip() for ref in refs]
        if any(not ref for ref in normalized_refs) or not set(normalized_refs).issubset(point_ids):
            raise ValueError("highlight_references_unknown_point")
        raw_story_refs = highlight.get("story_refs") or (
            [highlight.get("story_ref")] if highlight.get("story_ref") else []
        )
        if not legacy_without_story:
            if not isinstance(raw_story_refs, list) or (not raw_story_refs and not independent):
                raise ValueError("highlight_requires_one_or_more_story_refs")
            normalized_story_refs = [str(ref or "").strip() for ref in raw_story_refs]
            if (
                any(not ref for ref in normalized_story_refs)
                or len(normalized_story_refs) != len(set(normalized_story_refs))
                or not set(normalized_story_refs).issubset(story_ids)
            ):
                raise ValueError("highlight_references_unknown_story")
            expected_story_refs = {
                story_ref_by_point_id[point_ref] for point_ref in normalized_refs
            }
            if set(normalized_story_refs) != expected_story_refs:
                raise ValueError("highlight_story_refs_must_match_point_refs")
        spans = _rows(highlight.get("source_spans"), "highlight_requires_source_spans")
        if not spans:
            raise ValueError("highlight_requires_source_spans")
        for span in spans:
            start = span.get("start_sec")
            end = span.get("end_sec")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or float(end) <= float(start)
            ):
                raise ValueError("invalid_highlight_source_span")
            span_refs = span.get("point_refs")
            if span_refs is not None:
                if not isinstance(span_refs, list) or not {str(ref) for ref in span_refs}.issubset(point_ids):
                    raise ValueError("highlight_span_references_unknown_point")

    return packet
