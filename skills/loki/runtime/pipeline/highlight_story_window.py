"""Build one evidence-linked Story Window without changing legacy draft behavior."""

from __future__ import annotations

from copy import deepcopy

import hashlib

import json

import math

from typing import Any, Mapping, Sequence

from pipeline.context_structure.contracts import ContextStructureContractError

from pipeline.editorial_contract import _derive_source_span_structure_refs

from pipeline.editorial_span_authoring import _normalize_authored_source_span_boundaries

from pipeline.story_packet_selector import ROLE_KEYS, validate_highlight_story_evidence

def project_editorial_highlight_durations(highlight: Mapping[str, Any], *, original: Mapping[str, Any] | None = None) -> dict:
    """Refresh existing duration views from the sole canonical cut geometry."""
    result = deepcopy(highlight)
    spans = result.get("source_spans") or []
    if original is not None:
        original_spans = original.get("source_spans") or []
        if len(spans) != len(original_spans):
            raise ValueError("editorial_highlight_span_count_changed")
        for field in ("total_source_duration_sec", "excluded_gap_duration_sec", "estimated_edited_duration"):
            if (field in highlight) != (field in original):
                raise ValueError("editorial_duration_projection_invalid")
        for before, submitted in zip(original_spans, spans):
            if ("duration_sec" in submitted) != ("duration_sec" in before):
                raise ValueError("editorial_duration_projection_invalid")
    if not spans:
        return result
    total = sum(float(span["end_sec"]) - float(span["start_sec"]) for span in spans)
    for span in spans:
        if "duration_sec" in span:
            span["duration_sec"] = float(span["end_sec"]) - float(span["start_sec"])
    if "total_source_duration_sec" in result:
        result["total_source_duration_sec"] = total
    if "excluded_gap_duration_sec" in result:
        result["excluded_gap_duration_sec"] = float(spans[-1]["end_sec"]) - float(spans[0]["start_sec"]) - total
    if "estimated_edited_duration" in result:
        result["estimated_edited_duration"].update(min_sec=total, max_sec=total)
    if original is not None:
        for field in ("total_source_duration_sec", "excluded_gap_duration_sec", "estimated_edited_duration"):
            if highlight.get(field) != original.get(field) and highlight.get(field) != result.get(field):
                raise ValueError("editorial_duration_projection_invalid")
        for before, submitted, canonical in zip(original["source_spans"], highlight["source_spans"], spans):
            if submitted.get("duration_sec") != before.get("duration_sec") and submitted.get("duration_sec") != canonical.get("duration_sec"):
                raise ValueError("editorial_duration_projection_invalid")
    return result

def _fail(code: str) -> None:
    raise ContextStructureContractError(code, code)

def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":"), sort_keys=True)
        return text.encode("utf-8")
    except (TypeError, ValueError):
        _fail("invalid_candidate_proposal")

def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()

def _finite(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(code)
    number = float(value)
    return 0.0 if number == 0 else number

EDITORIAL_SCHEMA_VERSION = "chzz.editorial_highlight.v1"

EDITORIAL_SET_SCHEMA_VERSION = "chzz.editorial_highlight_set.v1"

_EDITORIAL_STORY_ROLES = frozenset(ROLE_KEYS) | {"development", "conversation"}

_EDITORIAL_BEAT_ROLES = {
    "introduction",
    "development",
    "core_event",
    "reaction",
    "ending",
}

def _editorial_error(code: str) -> ValueError:
    return ValueError(code)

def _editorial_text(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise _editorial_error(f"{field}_required")
    return text

def _editorial_refs(value: Any, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise _editorial_error(f"{field}_must_be_a_list")
    refs = [str(item or "").strip() for item in value]
    if any(not item for item in refs) or len(refs) != len(set(refs)):
        raise _editorial_error(f"{field}_must_have_unique_nonempty_refs")
    if not allow_empty and not refs:
        raise _editorial_error(f"{field}_required")
    return refs

def _editorial_seconds(value: str) -> float:
    parts = str(value or "").split(":")
    if len(parts) != 3:
        raise _editorial_error("invalid_broadcast_map_clock")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as exc:
        raise _editorial_error("invalid_broadcast_map_clock") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise _editorial_error("invalid_broadcast_map_clock")
    return float(hours * 3600 + minutes * 60 + seconds)

def _is_primary_semantic_ref(ref: str, *, approved_primary_refs: set[str] | None = None) -> bool:
    return ref.startswith(("stt-sec:", "stt-occ:", "chat-ms:", "chzzk-semantic:")) or ref in (approved_primary_refs or set())

def _broadcast_map_indexes(broadcast_map: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    ranges = [row for row in broadcast_map.get("ranges") or [] if isinstance(row, dict)]
    points = [row for row in broadcast_map.get("points") or [] if isinstance(row, dict)]
    d1 = {str(row.get("id") or ""): row for row in ranges if row.get("level") == "D1"}
    d2 = {str(row.get("id") or ""): row for row in ranges if row.get("level") == "D2"}
    point = {str(row.get("id") or ""): row for row in points}
    if not d1 or any(not key for key in [*d1, *d2, *point]):
        raise _editorial_error("invalid_frozen_broadcast_map")
    if any(str(row.get("parent_id") or "") not in d1 for row in d2.values()):
        raise _editorial_error("invalid_frozen_broadcast_map_hierarchy")
    return d1, d2, point

def _normalize_editorial_highlight(
    proposal: Mapping[str, Any],
    *,
    d1_index: Mapping[str, dict[str, Any]],
    d2_index: Mapping[str, dict[str, Any]],
    point_index: Mapping[str, dict[str, Any]],
    authority_revision_id: str,
    highlight_producer_run_ref: str,
    source_duration_sec: float,
    story_requirement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise _editorial_error("highlight_proposal_must_be_an_object")
    independent_source = bool((story_requirement or {}).get("independent_source_links"))
    story_refs = _editorial_refs(
        proposal.get("story_refs")
        or ([proposal.get("story_ref")] if proposal.get("story_ref") else []),
        field="highlight_story_refs",
        allow_empty=story_requirement is None or independent_source,
    )
    story_ref = story_refs[0] if story_refs else ""
    if story_requirement is not None and not story_refs and not independent_source:
        raise _editorial_error("highlight_story_ref_required")
    approved_primary_refs = {str(evidence_id) for evidence_id, row in (story_requirement or {}).get("evidence_catalog", {}).items()
        if isinstance(row, Mapping)
        and str(row.get("source_type") or "") in {"stt", "replay_chat", "chat"}}
    coverage_evidence_aliases = dict(
        (story_requirement or {}).get("coverage_evidence_aliases") or {}
    )




    coverage_alias_mode = bool(coverage_evidence_aliases)
    if len(set(coverage_evidence_aliases.values())) != len(
        coverage_evidence_aliases
    ):
        raise _editorial_error("semantic_evidence_alias_must_be_one_to_one")
    title = _editorial_text(proposal.get("title"), field="highlight_title")
    story_summary = _editorial_text(
        proposal.get("story_summary"), field="highlight_story_summary"
    )
    d1_refs = _editorial_refs(proposal.get("d1_refs") or [], field="highlight_d1_refs", allow_empty=True)
    d2_refs = _editorial_refs(proposal.get("d2_refs") or [],
                              field="highlight_d2_refs", allow_empty=True)
    point_refs = _editorial_refs(
        proposal.get("point_refs") or [],
        field="highlight_point_refs",
        allow_empty=independent_source and not story_refs,
    )
    if independent_source and not story_refs and point_refs:
        raise _editorial_error("independent_highlight_cannot_invent_point_link")
    if any(ref not in d1_index for ref in d1_refs):
        raise _editorial_error("highlight_references_unknown_d1")
    if any(ref not in d2_index for ref in d2_refs):
        raise _editorial_error("highlight_references_unknown_d2")
    if any(ref not in point_index for ref in point_refs):
        raise _editorial_error("highlight_references_unknown_point")
    required_point_refs = _editorial_refs(
        (story_requirement or {}).get("point_refs") or [],
        field="story_required_point_refs",
        allow_empty=True,
    )
    if required_point_refs and set(point_refs) != set(required_point_refs):
        raise _editorial_error("highlight_point_refs_must_match_composed_stories")
    if any(str(d2_index[ref].get("parent_id") or "") not in d1_refs for ref in d2_refs):
        raise _editorial_error("highlight_d2_parent_not_referenced")

    raw_spans = proposal.get("source_spans")
    if not isinstance(raw_spans, (list, tuple)) or not raw_spans:
        raise _editorial_error("highlight_requires_source_spans")
    validated_raw_spans: list[dict[str, Any]] = []
    previous_end = -1.0
    for index, raw in enumerate(raw_spans):
        if not isinstance(raw, Mapping):
            raise _editorial_error("source_span_must_be_an_object")
        start = _finite(raw.get("start_sec"), "invalid_source_span_clock")
        end = _finite(raw.get("end_sec"), "invalid_source_span_clock")
        if start < 0 or end <= start or end > source_duration_sec:
            raise _editorial_error("invalid_source_span_clock")
        if start < previous_end:
            raise _editorial_error("source_spans_must_be_ordered_and_nonoverlapping")
        roles = _editorial_refs(raw.get("story_roles"), field="source_span_story_roles")
        if any(role not in _EDITORIAL_STORY_ROLES for role in roles):
            raise _editorial_error("invalid_source_span_story_role")
        span_d1_refs = _editorial_refs(raw.get("d1_refs") or [], field="source_span_d1_refs", allow_empty=True)
        span_d2_refs = _editorial_refs(raw.get("d2_refs") or [],
                                       field="source_span_d2_refs", allow_empty=True)
        span_point_refs = _editorial_refs(
            raw.get("point_refs") or [],
            field="source_span_point_refs",
            allow_empty=True,
        )
        if any(ref not in d1_index for ref in span_d1_refs):
            raise _editorial_error("source_span_references_unknown_d1")
        if any(ref not in d2_index for ref in span_d2_refs):
            raise _editorial_error("source_span_references_unknown_d2")
        if not set(span_point_refs).issubset(point_refs):
            raise _editorial_error("source_span_point_refs_escape_highlight")
        try:
            derived_d1_refs, derived_d2_refs = _derive_source_span_structure_refs(
                start=start, end=end, d1_index=d1_index, d2_index=d2_index)
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
        if (not set(span_d1_refs).issubset(derived_d1_refs)
                or not set(span_d2_refs).issubset(derived_d2_refs)):
            raise _editorial_error("source_span_refs_escape_canonical_clock")
        evidence_refs = _editorial_refs(
            raw.get("evidence_refs"), field="source_span_evidence_refs"
        )
        has_primary_evidence = any(
            ref in coverage_evidence_aliases
            or (
                (
                    not coverage_alias_mode
                    and _is_primary_semantic_ref(
                        ref, approved_primary_refs=approved_primary_refs
                    )
                )
                or (
                    coverage_alias_mode
                    and ref in approved_primary_refs
                    and ref.startswith(
                        ("stt-sec:", "stt-occ:", "chat-ms:", "chzzk-semantic:")
                    )
                )
            )
            for ref in evidence_refs
        )
        if not has_primary_evidence:
            raise _editorial_error("span_requires_primary_semantic_evidence")
        if (story_requirement or {}).get("native_timing"):
            from .editorial_source_timing import validate_native_reference_intersections
            validate_native_reference_intersections(refs=evidence_refs, spans=[raw],
                catalog=(story_requirement or {}).get("evidence_catalog") or {})
        validated_raw_spans.append(
            {
                "span_index": index,
                "start_sec": start,
                "end_sec": end,
                "duration_sec": end - start,
                "story_roles": roles,
                "d1_refs": derived_d1_refs,
                "d2_refs": derived_d2_refs,
                "point_refs": [],
                "evidence_refs": evidence_refs,
                "reason": _editorial_text(raw.get("reason"), field="source_span_reason"),
                "_raw_exclusion_before": raw.get("exclusion_before"),
            }
        )
        previous_end = end

    try:
        spans, raw_to_canonical_span, exclusion_evidence_refs = (
            _normalize_authored_source_span_boundaries(
                validated_raw_spans,
                evidence_catalog=(story_requirement or {}).get("evidence_catalog") or {},
                approved_primary_refs=approved_primary_refs,
                gap_coverage_batches=(story_requirement or {}).get("gap_coverage_batches") or [],
            )
        )
    except ValueError as exc:
        raise _editorial_error(str(exc)) from exc

    coverage_obligations = list(
        (story_requirement or {}).get("coverage_obligations") or []
    )
    covering_span_indexes_by_card: dict[str, set[int]] = {}
    for obligation in coverage_obligations:
        if not isinstance(obligation, Mapping):
            raise _editorial_error("invalid_semantic_coverage_obligation")
        card_ref = _editorial_text(
            obligation.get("card_ref"), field="semantic_coverage_card_ref"
        )
        start_sec = _finite(
            obligation.get("start_sec"), "invalid_semantic_coverage_interval"
        )
        end_sec = _finite(
            obligation.get("end_sec"), "invalid_semantic_coverage_interval"
        )
        if start_sec < 0 or end_sec < start_sec or end_sec > source_duration_sec:
            raise _editorial_error("invalid_semantic_coverage_interval")
        obligation_refs = set(
            _editorial_refs(
                obligation.get("evidence_refs"),
                field="semantic_coverage_evidence_refs",
            )
        )
        if coverage_alias_mode:
            canonical_catalog = (story_requirement or {}).get("evidence_catalog") or {}
            approved_refs_by_interval = {
                transport_ref
                for transport_ref, canonical_ref in coverage_evidence_aliases.items()
                if isinstance(canonical_catalog.get(canonical_ref), Mapping)
                and start_sec
                <= float(
                    canonical_catalog[canonical_ref].get("time_sec")
                    if canonical_catalog[canonical_ref].get("time_sec") is not None
                    else -1
                )
                <= end_sec
            }
            if not approved_refs_by_interval:
                raise _editorial_error("semantic_evidence_alias_missing")
        else:
            approved_refs_by_interval = obligation_refs
        covering_indexes = {
            index
            for index, span in enumerate(spans)
            if float(span["start_sec"]) <= start_sec
            and end_sec <= float(span["end_sec"])
            and (
                bool(
                    approved_refs_by_interval.intersection(
                        set(span.get("evidence_refs") or [])
                    )
                )
                if coverage_alias_mode
                else approved_refs_by_interval.issubset(
                    set(span.get("evidence_refs") or [])
                )
            )
        }
        if not covering_indexes:
            raise _editorial_error(
                "highlight_does_not_cover_required_semantic_evidence_interval"
            )
        if card_ref in covering_span_indexes_by_card:
            covering_span_indexes_by_card[card_ref].intersection_update(
                covering_indexes
            )
        else:
            covering_span_indexes_by_card[card_ref] = set(covering_indexes)

    sibling_groups = list(
        (story_requirement or {}).get("required_sibling_span_groups") or []
    )
    if sibling_groups:
        sibling_groups = _editorial_refs(
            sibling_groups,
            field="required_sibling_span_groups",
        )
        if any(
            not covering_span_indexes_by_card.get(card_ref)
            for card_ref in sibling_groups
        ):
            raise _editorial_error(
                "required_sibling_context_has_no_complete_source_span"
            )
        if any(
            covering_span_indexes_by_card[left].intersection(
                covering_span_indexes_by_card[right]
            )
            for left_index, left in enumerate(sibling_groups)
            for right in sibling_groups[left_index + 1 :]
        ):
            raise _editorial_error(
                "distant_reconciled_contexts_require_sibling_source_spans"
            )

    canonical_d1_refs: list[str] = []
    canonical_d2_refs: list[str] = []
    for span_index, span in enumerate(spans):
        try:
            derived_d1_refs, derived_d2_refs = _derive_source_span_structure_refs(
                start=float(span["start_sec"]),
                end=float(span["end_sec"]),
                d1_index=d1_index,
                d2_index=d2_index,
            )
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
        span["span_index"] = span_index
        span["d1_refs"] = derived_d1_refs
        span["d2_refs"] = derived_d2_refs
        span["point_refs"] = [
            ref
            for ref in point_refs
            if float(span["start_sec"])
            <= _editorial_seconds(str(point_index[ref].get("timestamp") or ""))
            <= float(span["end_sec"])
        ]
        for ref in derived_d1_refs:
            if ref not in canonical_d1_refs:
                canonical_d1_refs.append(ref)
        for ref in derived_d2_refs:
            if ref not in canonical_d2_refs:
                canonical_d2_refs.append(ref)





    raw_beats = proposal.get("story_beats")
    if not isinstance(raw_beats, (list, tuple)) or not raw_beats:
        raise _editorial_error("highlight_requires_story_beats")
    span_evidence = {ref for span in spans for ref in span["evidence_refs"]}
    story_evidence_contract: dict[str, Any] = {}
    if story_requirement is not None:
        available = _editorial_refs(story_requirement.get("available_evidence_refs") or [],
                                    field="story_available_evidence_refs", allow_empty=True)
        catalog = story_requirement.get("evidence_catalog") or {}
        if not isinstance(catalog, Mapping):
            raise _editorial_error("invalid_story_evidence_catalog")
        try:
            story_evidence_contract = validate_highlight_story_evidence(
                required_story_roles=story_requirement.get("required_story_roles") or {},
                available_evidence_refs=available, author_evidence_refs=story_requirement.get("author_evidence_refs") or available,
                used_evidence_refs=sorted(span_evidence | exclusion_evidence_refs),
                evidence_catalog=catalog, source_spans=spans)
        except ValueError as exc:
            raise _editorial_error(str(exc)) from exc
    beats: list[dict[str, Any]] = []
    beat_roles: set[str] = set()
    for raw in raw_beats:
        if not isinstance(raw, Mapping):
            raise _editorial_error("story_beat_must_be_an_object")
        role = str(raw.get("role") or "")
        if role not in _EDITORIAL_BEAT_ROLES or role in beat_roles:
            raise _editorial_error("invalid_or_duplicate_story_beat_role")
        indexes = raw.get("span_indexes")
        if (
            not isinstance(indexes, (list, tuple))
            or not indexes
            or any(isinstance(value, bool) or not isinstance(value, int) for value in indexes)
            or len(indexes) != len(set(indexes))
            or any(value < 0 or value >= len(raw_to_canonical_span) for value in indexes)
        ):
            raise _editorial_error("invalid_story_beat_span_indexes")
        canonical_indexes = list(
            dict.fromkeys(raw_to_canonical_span[value] for value in indexes)
        )
        evidence_refs = _editorial_refs(
            raw.get("evidence_refs"), field="story_beat_evidence_refs"
        )
        if not set(evidence_refs).issubset(span_evidence):
            raise _editorial_error("story_beat_evidence_not_in_source_spans")
        if (story_requirement or {}).get("native_timing"):
            from .editorial_source_timing import validate_native_reference_intersections
            validate_native_reference_intersections(refs=evidence_refs,
                spans=[spans[i] for i in canonical_indexes],
                catalog=(story_requirement or {}).get("evidence_catalog") or {})
        beats.append(
            {
                "role": role,
                "span_indexes": canonical_indexes,
                "evidence_refs": evidence_refs,
                "reason": _editorial_text(raw.get("reason"), field="story_beat_reason"),
            }
        )
        beat_roles.add(role)
    derived_role_sources = {
        "introduction": {"setup"},
        "development": {"development", "performance"},
        "core_event": {"event"},
        "reaction": {"reaction"},
        "ending": {"result"},
    }
    for beat_role, span_roles in derived_role_sources.items():
        if beat_role in beat_roles:
            continue
        indexes = [
            index
            for index, span in enumerate(spans)
            if set(span["story_roles"]) & span_roles
        ]
        if not indexes:
            continue
        evidence_refs = sorted({
            ref for index in indexes for ref in spans[index]["evidence_refs"]
        })
        beats.append({
            "role": beat_role,
            "span_indexes": indexes,
            "evidence_refs": evidence_refs,
            "reason": "source_spans의 정본 story role에서 계산된 표시 역할",
        })
        beat_roles.add(beat_role)




    estimate = proposal.get("estimated_edited_duration")
    if not isinstance(estimate, Mapping):
        raise _editorial_error("estimated_edited_duration_required")
    minimum = _finite(estimate.get("min_sec"), "invalid_estimated_edited_duration")
    maximum = _finite(estimate.get("max_sec"), "invalid_estimated_edited_duration")
    total_source_duration = sum(float(span["duration_sec"]) for span in spans)
    if minimum <= 0 or maximum < minimum:
        raise _editorial_error("invalid_estimated_edited_duration")




    estimated = {
        "min_sec": total_source_duration,
        "max_sec": total_source_duration,
        "category": _editorial_text(
            estimate.get("category"), field="estimated_edited_duration_category"
        ),
    }
    first_start = float(spans[0]["start_sec"])
    last_end = float(spans[-1]["end_sec"])
    edit_mode = "continuous" if len(spans) == 1 else "multi_span"
    used_evidence = sorted(span_evidence | exclusion_evidence_refs | {
        ref for beat in beats for ref in beat["evidence_refs"]
    })
    body = {
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "authority_revision_id": authority_revision_id,
        "title": title,
        "story_summary": story_summary,
        "d1_refs": canonical_d1_refs,
        "d2_refs": canonical_d2_refs,
        "point_refs": point_refs,
        **({"story_refs": story_refs} if story_refs else {}),
        **({"story_ref": story_ref} if story_ref else {}),
        "source_spans": spans,
        "edit_mode": edit_mode,
        "story_beats": beats,
        "story_completeness": {
            "state": "complete", "present_roles": sorted(beat_roles),
            "basis": "authored_evidence_contract", "quality_verified": False,
        },
        "total_source_duration_sec": total_source_duration,
        "estimated_edited_duration": estimated,
        "excluded_gap_duration_sec": (last_end - first_start) - total_source_duration,
        "used_evidence_refs": used_evidence,
        **({"native_timing": (story_requirement or {})["native_timing"]} if (story_requirement or {}).get("native_timing") else {}),
        **(story_evidence_contract if story_requirement is not None else {}),
        "missing_information": [str(item) for item in proposal.get("missing_information") or []],
        "uncertainties": [str(item) for item in proposal.get("uncertainties") or []],
        "highlight_producer_run_ref": highlight_producer_run_ref,
        "classification": "editorial_highlight",
        "counts_as_highlight": True,
    }
    body["highlight_id"] = "highlight-" + _sha(body)
    return body

def _selected_owner_composition_fields(proposal, revision):


    if not isinstance(revision, dict):
        raise _editorial_error("e1_selected_composition_changed")
    canonical = json.dumps({k: v for k, v in revision.items() if k != "revision_id"},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if (revision.get("revision_id") != hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            or revision.get("composition_decision", {}).get("action") != "compose"
            or proposal != revision.get("draft")):
        raise _editorial_error("e1_selected_composition_changed")
    copy = json.loads(json.dumps(proposal))
    fields = {k: copy[k] for k in ("composition_form", "thread_refs",
        "editorial_lead_refs", "editorial_agenda_refs", "editorial_group_refs") if k in copy}
    fields.update(editorial_hypothesis_refs=[revision["owner_ref"]],
        editorial_owner_revision=revision["revision_id"])
    return fields

def build_editorial_highlight_set(
    *,
    broadcast_map: Mapping[str, Any],
    authority_revision_id: str,
    proposals: Sequence[Mapping[str, Any]],
    highlight_producer_run_ref: str,
    source_duration_sec: float,
    point_review_windows: Sequence[Mapping[str, Any]] = (),
    story_requirements: Mapping[str, Mapping[str, Any]] | None = None,
    context_requirements: Mapping[str, Mapping[str, Any]] | None = None,
    composition_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate Highlights; legacy review windows are never persisted."""

    revision = _editorial_text(authority_revision_id, field="authority_revision_id")
    producer = _editorial_text(
        highlight_producer_run_ref, field="highlight_producer_run_ref"
    )
    duration = _finite(source_duration_sec, "invalid_source_duration_sec")
    if duration <= 0:
        raise _editorial_error("invalid_source_duration_sec")
    d1_index, d2_index, point_index = _broadcast_map_indexes(broadcast_map)
    highlights: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    requirements = dict(story_requirements or {})
    contexts = dict(context_requirements or {})
    if composition_evidence is not None:
        if contexts or composition_evidence.get("contract") != "editorial.context-author.v22":
            raise _editorial_error("invalid_context_author_contract")
        if not composition_evidence.get("native_timing") or not composition_evidence.get("evidence_catalog"):
            raise _editorial_error("context_author_requires_native_evidence")
    seen_contexts: set[str] = set()
    seen_story_refs: set[str] = set()
    for index, proposal in enumerate(proposals):
        try:
            story_refs = _editorial_refs(
                proposal.get("story_refs")
                or ([proposal.get("story_ref")] if proposal.get("story_ref") else []),
                field="highlight_story_refs",
                allow_empty=not requirements or bool((composition_evidence or {}).get("independent_source_links")),
            )
            if requirements:
                if any(story_ref not in requirements for story_ref in story_refs):
                    raise _editorial_error("highlight_references_unknown_story")
            context_requirement: Mapping[str, Any] | None = None
            context_identity = str(proposal.get("context_identity") or "").strip()
            if contexts:
                if not context_identity or context_identity not in contexts:
                    raise _editorial_error(
                        "highlight_requires_known_reconciled_context_identity"
                    )
                if context_identity in seen_contexts:
                    raise _editorial_error(
                        "reconciled_context_must_materialize_exactly_once"
                    )
                context_requirement = contexts[context_identity]
                exact_story_refs = list(
                    context_requirement.get("required_story_refs_exact") or []
                )
                if story_refs != exact_story_refs:
                    raise _editorial_error(
                        "highlight_must_match_exact_reconciled_story_union"
                    )
                required_card_refs = list(
                    context_requirement.get("required_card_refs") or []
                )
                if list(proposal.get("required_card_refs") or []) != required_card_refs:
                    raise _editorial_error(
                        "highlight_must_match_exact_reconciled_card_union"
                    )
            combined_requirement: dict[str, Any] | None = None
            if requirements:
                combined_requirement = {
                    "point_refs": [],
                    "required_story_roles": {},
                    "available_evidence_refs": [],
                    "author_evidence_refs": [],
                    "evidence_catalog": {},
                    "gap_coverage_batches": [],
                    "coverage_obligations": [],
                    "required_sibling_span_groups": [],
                }
                for story_ref in story_refs:
                    requirement = requirements[story_ref]
                    point_ref = str(requirement.get("point_ref") or "").strip()
                    point_refs = requirement.get("point_refs") or ([point_ref] if point_ref else [])
                    for ref in point_refs:
                        if ref not in combined_requirement["point_refs"]:
                            combined_requirement["point_refs"].append(ref)
                    for role, refs in (requirement.get("required_story_roles") or {}).items():
                        combined_requirement["required_story_roles"].setdefault(role, [])
                        for ref in refs:
                            if ref not in combined_requirement["required_story_roles"][role]:
                                combined_requirement["required_story_roles"][role].append(ref)
                    for field in ("available_evidence_refs", "author_evidence_refs"):
                        for ref in requirement.get(field) or []:
                            if ref not in combined_requirement[field]:
                                combined_requirement[field].append(ref)
                    for evidence_id, row in (requirement.get("evidence_catalog") or {}).items():
                        existing = combined_requirement["evidence_catalog"].get(evidence_id)
                        if existing is not None and existing != row:
                            raise _editorial_error("conflicting_story_evidence_catalog")
                        combined_requirement["evidence_catalog"][evidence_id] = row
                    for batch in requirement.get("gap_coverage_batches") or []:
                        if batch not in combined_requirement["gap_coverage_batches"]:
                            combined_requirement["gap_coverage_batches"].append(batch)
                    for obligation in requirement.get("coverage_obligations") or []:
                        if obligation not in combined_requirement["coverage_obligations"]:
                            combined_requirement["coverage_obligations"].append(
                                obligation
                            )
                    for card_ref in requirement.get(
                        "required_sibling_span_groups"
                    ) or []:
                        if card_ref not in combined_requirement[
                            "required_sibling_span_groups"
                        ]:
                            combined_requirement[
                                "required_sibling_span_groups"
                            ].append(card_ref)
                if context_requirement is not None:
                    combined_requirement["coverage_obligations"] = list(
                        context_requirement.get("coverage_obligations") or []
                    )
                    combined_requirement["native_timing"] = dict(context_requirement.get("native_timing") or {})
                    if "coverage_evidence_aliases" in context_requirement:
                        combined_requirement["coverage_evidence_aliases"] = dict(
                            context_requirement.get("coverage_evidence_aliases") or {}
                        )
                    combined_requirement["required_sibling_span_groups"] = list(
                        context_requirement.get("required_sibling_span_groups") or []
                    )
            if composition_evidence is not None:


                if combined_requirement is None and not composition_evidence.get("independent_source_links"):
                    raise _editorial_error("context_author_requires_existing_story_link")
                if combined_requirement is None:
                    combined_requirement = {"point_refs": []}
                proposal_catalog = dict(composition_evidence["evidence_catalog"])
                delivered_refs = composition_evidence.get("proposal_evidence_refs", {}).get(index)
                if delivered_refs is not None:
                    proposal_catalog = {k: v for k, v in proposal_catalog.items() if k in delivered_refs}
                combined_requirement.update({
                    "independent_source_links": bool(composition_evidence.get("independent_source_links")),
                    "required_story_roles": {role: [] for role in ROLE_KEYS}, "coverage_obligations": [],
                    "required_sibling_span_groups": [], "gap_coverage_batches": [],
                    "coverage_evidence_aliases": {},
                    "evidence_catalog": proposal_catalog,
                    "native_timing": dict(composition_evidence["native_timing"]),
                    "available_evidence_refs": list(proposal_catalog),
                    "author_evidence_refs": list(proposal_catalog),
                })
            normalized = _normalize_editorial_highlight(
                proposal,
                d1_index=d1_index,
                d2_index=d2_index,
                point_index=point_index,
                authority_revision_id=revision,
                highlight_producer_run_ref=producer,
                source_duration_sec=duration,
                story_requirement=combined_requirement,
            )
            normalized_story_refs = set(normalized.get("story_refs") or [])
            if composition_evidence is not None:
                if "frames" in composition_evidence:
                    from .editorial_visual_input import validate_frame_references


                    validate_frame_references([normalized], composition_evidence["frames"])
                normalized["selection_reason"] = _editorial_text(proposal.get("selection_reason"), field="selection_reason")
                normalized["composition_contract"] = "editorial.context-author.v22"
                if composition_evidence.get("independent_source_links") and not normalized_story_refs:
                    normalized["story_link_mode"] = "source_independent"
                owner_revisions = composition_evidence.get("proposal_owner_revisions")
                frozen = composition_evidence.get("frozen_editorial_compositions", {}).get(index)
                if frozen is not None and owner_revisions is not None:




                    if proposal != frozen:
                        raise _editorial_error("composition_changed_frozen_sibling")
                    for key in ("editorial_group_refs", "editorial_hypothesis_refs", "editorial_lead_refs",
                                "thread_refs", "composition_form", "composition_change", "editorial_owner_revision"):
                        if key in frozen:
                            normalized[key] = deepcopy(frozen[key])
                    for original, span in zip(frozen["source_spans"], normalized["source_spans"]):
                        if "editorial_beat_refs" in original:
                            span["editorial_beat_refs"] = deepcopy(original["editorial_beat_refs"])
                    owner_revisions = None
                if owner_revisions is not None:
                    if index not in owner_revisions:
                        raise _editorial_error("broadcast_owner_proposal_provenance_incomplete")
                    normalized.update(_selected_owner_composition_fields(proposal, owner_revisions[index]))
                    normalized["highlight_id"] = "highlight-" + _sha({k:v for k,v in normalized.items() if k != "highlight_id"})
                elif composition_evidence.get("e1_selected_owner_revision"):
                    normalized.update(_selected_owner_composition_fields(proposal,
                        composition_evidence["e1_selected_owner_revision"]))
                    normalized["highlight_id"] = "highlight-" + _sha({k:v for k,v in normalized.items() if k != "highlight_id"})
                elif composition_evidence.get("editorial_composition"):
                    from .editorial_marker_desk import composition_fields
                    frozen = composition_evidence.get("frozen_editorial_compositions", {}).get(index)
                    if frozen is not None:
                        if proposal != frozen:
                            raise _editorial_error("composition_changed_frozen_sibling")
                        for key in ("editorial_group_refs", "editorial_hypothesis_refs", "editorial_lead_refs", "thread_refs", "composition_form", "composition_change"):
                            if key in frozen:
                                normalized[key] = frozen[key]
                        for original, span in zip(frozen["source_spans"], normalized["source_spans"]):
                            if "editorial_beat_refs" in original:
                                span["editorial_beat_refs"] = original["editorial_beat_refs"]
                    else:
                        normalized.update(composition_fields(proposal, normalized,
                            composition_evidence["editorial_composition"], proposal_catalog))
                    normalized["highlight_id"] = "highlight-" + _sha({k:v for k,v in normalized.items() if k != "highlight_id"})
            if context_requirement is not None and context_requirement.get("selection_reason"):


                normalized["selection_reason"] = str(context_requirement["selection_reason"])
            if proposal.get("image") is not None:
                from .editorial_visual_input import selected_frame_image
                normalized["image"] = selected_frame_image(proposal["image"],
                    frames=(composition_evidence or {}).get("frames", {}),
                    source_spans=normalized["source_spans"])
                normalized["highlight_id"] = "highlight-" + _sha({k:v for k,v in normalized.items() if k != "highlight_id"})
            for existing in highlights:
                if not normalized_story_refs.intersection(existing.get("story_refs") or []):
                    continue
                if any(
                    max(float(left["start_sec"]), float(right["start_sec"]))
                    < min(float(left["end_sec"]), float(right["end_sec"]))
                    for left in existing.get("source_spans") or []
                    for right in normalized.get("source_spans") or []
                ):
                    raise _editorial_error(
                        "highlight_reuses_story_with_overlapping_source_footage"
                    )
            highlights.append(normalized)
            seen_story_refs.update(story_refs)
            if context_identity:
                seen_contexts.add(context_identity)
        except ValueError as exc:
            rejected.append({"proposal_index": index,
                             **({"context_identity": str(proposal.get("context_identity") or "")} if isinstance(proposal, Mapping) and proposal.get("context_identity") else {}),
                             **({"story_refs": list(proposal.get("story_refs") or [])} if isinstance(proposal, Mapping) and proposal.get("story_refs") else {}),
                             **({"story_ref": str(proposal.get("story_ref") or "")} if isinstance(proposal, Mapping) and proposal.get("story_ref") else {}),
                             "reason": str(exc)})
    for context_identity in contexts:
        if context_identity not in seen_contexts and not any(
            row.get("context_identity") == context_identity for row in rejected
        ):
            rejected.append(
                {
                    "context_identity": context_identity,
                    "reason": "reconciled_context_not_materialized",
                }
            )
    result = {
        "schema_version": EDITORIAL_SET_SCHEMA_VERSION,
        "authority_revision_id": revision,
        "highlight_producer_run_ref": producer,
        "authority": {
            "broadcast_map_owner": "pipeline.manager_outline.finalize_manager_outline",
            "highlight_owner": "pipeline.highlight_story_window.build_editorial_highlight_set",
            "source_spans_are_canonical": True,
            "display_clock_is_derived_from_source_spans": True,
        },
        "highlights": highlights,
        "rejected_proposals": rejected,
        "story_dispositions": [
            {
                "story_ref": story_ref,
                "disposition": (
                    "materialized"
                    if story_ref in seen_story_refs
                    else "not_selected_as_highlight"
                ),
            }
            for story_ref in requirements
        ],
        "quality_summary": {
            "highlight_pass_count": len(highlights),
            "rejected_proposal_count": len(rejected),
            "fixed_window_highlight_pass_count": 0,
        },
    }
    result["set_sha256"] = _sha(result)
    return result
