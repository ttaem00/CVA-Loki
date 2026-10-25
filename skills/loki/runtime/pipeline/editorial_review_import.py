"""Source-preserving composition for an explicitly requested editorial review.

An external draft is never renamed to QG1 or counted as a model generation.
The normal editor owns subsequent revisions; immutable input packets remain
available for comparison and rollback beside the editable product projection.
"""

from copy import deepcopy

from hashlib import sha256

import json

from .chatgpt_export.import_result import validate_imported_editorial_result

from .editorial_contract import validate_editorial_authority_shape

from .editorial_highlight_projection import broadcast_map_identity_hash

MODE = "composed_review_draft"

SCHEMA = "chzz.composed_editorial_review.v1"

EDITABLE = {"broadcast_map", "stories", "editorial_highlights", "broadcast_map_sha256",
            "review_revision_sha256", "manager_edited", "collaboration_periods"}

def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

def _sec(clock):
    h, m, s = map(int, clock.split(":"))
    return h * 3600 + m * 60 + s

def _map(packet):
    data = packet["broadcast_map"]
    return dict(ranges=data["d1"] + data["d2"], points=data["points"])

def seal_review_revision(packet):
    """Called after the normal editor's bounded edit/outline validation."""
    result = deepcopy(packet)
    result["broadcast_map_sha256"] = broadcast_map_identity_hash(_map(result))
    result["manager_edited"] = any(result[k] != result["initial_projection"][k]
        for k in ("broadcast_map", "stories", "editorial_highlights"))
    result["manager_edited"] |= result.get("collaboration_periods") != result["initial_projection"].get("collaboration_periods")
    result["review_revision_sha256"] = _digest({k: v for k, v in result.items()
        if k != "review_revision_sha256"})
    return result

def compose_editorial_review(external, generated=None, *, additional_point_ids=(),
                             additional_highlight_ids=(), generated_source_vod_id=None):
    """Compose explicit selections; no ranking, automatic acceptance or LLM call."""
    validate_imported_editorial_result(external)
    selected = list(additional_point_ids)
    selected_highlights = list(additional_highlight_ids)
    if len(selected) != len(set(selected)):
        raise ValueError("duplicate_composed_point_selection")
    if len(selected_highlights) != len(set(selected_highlights)):
        raise ValueError("duplicate_composed_highlight_selection")
    if (selected or selected_highlights) and generated is None:
        raise ValueError("composed_generated_source_required")
    result = {k: deepcopy(external[k]) for k in (
        "source_vod_id", "duration_sec", "broadcast_map", "stories", "editorial_highlights",
        "pending_highlights", "story_outcomes", "context_relations", "context_reads",
        "evidence_ledger", "signal_decisions", "unresolved", "timeline_gaps", "coverage")}
    if "collaboration_periods" in external:
        result["collaboration_periods"] = deepcopy(external["collaboration_periods"])
    origins = {p["id"]: "external_draft" for p in result["broadcast_map"]["points"]}
    if generated is not None:
        source_id = generated.get("source_vod_id") or generated_source_vod_id
        if not source_id or str(source_id) != str(external["source_vod_id"]) or (
                generated_source_vod_id is not None and str(generated_source_vod_id) != str(source_id)):
            raise ValueError("composed_generated_vod_mismatch")
        from .editorial_highlight_projection import validate_qg1_preview_payload
        validate_qg1_preview_payload(generated)
        points = {p["id"]: p for p in generated["broadcast_map"]["points"]}
        stories = {s["story_ref"]: s for s in generated["stories"]}
        highlights = {h["highlight_id"]: h for h in generated["editorial_highlights"]}
        if not set(selected_highlights) <= highlights.keys():
            raise ValueError("composed_highlight_selection_missing")
        dependencies = list(dict.fromkeys([*selected, *(p for h in selected_highlights
            for p in highlights[h]["point_refs"])]))
        for point_id in dependencies:
            if point_id not in points:
                raise ValueError("composed_point_selection_missing")
            point = deepcopy(points[point_id])
            story = deepcopy(stories[point["story_ref"]])
            point["id"] = "generated:" + point_id
            point["story_ref"] = story["story_ref"] = "generated:" + story["story_ref"]
            second = _sec(point["timestamp"])
            for key in ("d1", "d2"):
                matches = [r["id"] for r in result["broadcast_map"][key]
                    if _sec(r["start"]) <= second < _sec(r["end"])]
                if not matches:
                    raise ValueError("composed_point_outside_broadcast_map")
                point[key + "_ref"] = matches[0]
            result["broadcast_map"]["points"].append(point)
            result["stories"].append(story)
            origins[point["id"]] = "generated_review"
        for highlight_id in selected_highlights:
            highlight = deepcopy(highlights[highlight_id])
            highlight["highlight_id"] = "generated:" + highlight_id
            highlight["point_refs"] = ["generated:" + ref for ref in highlight["point_refs"]]
            highlight["story_refs"] = ["generated:" + ref for ref in highlight.get("story_refs", [])]
            if highlight.get("story_ref"):
                highlight["story_ref"] = "generated:" + highlight["story_ref"]
            for span in highlight["source_spans"]:
                if "point_refs" in span:
                    span["point_refs"] = ["generated:" + ref for ref in span["point_refs"]]
                for key in ("d1", "d2"):
                    span[key + "_refs"] = [r["id"] for r in result["broadcast_map"][key]
                        if _sec(r["start"]) < span["end_sec"] and span["start_sec"] < _sec(r["end"])]
            for key in ("d1", "d2"):
                highlight[key + "_refs"] = list(dict.fromkeys(ref for span in highlight["source_spans"]
                    for ref in span[key + "_refs"]))
            result["editorial_highlights"].append(highlight)
    result["broadcast_map"]["points"].sort(key=lambda p: (_sec(p["timestamp"]), p["id"]))
    result.update(schema_version=SCHEMA, projection_mode=MODE,
        completion_state="composed_unreviewed", pipeline_complete=False,
        provenance=dict(source_kind="manager_requested_composition", product_approval=False,
            automatic_generation=False, point_origins=origins,
            media_verification="not_performed_by_importer",
            native_source_validation=external["validation"]["native_source_validation"]),
        source_material=dict(external=deepcopy(external), generated=deepcopy(generated),
            generated_source_vod_id=generated_source_vod_id, additional_point_ids=selected),
        selection_policy="Explicit editor selection; original inputs retained; no target count.")
    if selected_highlights:
        result["source_material"]["additional_highlight_ids"] = selected_highlights
        result["provenance"]["highlight_origins"] = {h["highlight_id"]:
            ("generated_review" if h["highlight_id"].startswith("generated:") else "external_draft")
            for h in result["editorial_highlights"]}
    result["source_material_sha256"] = _digest(result["source_material"])
    result["initial_projection"] = {k: deepcopy(result[k]) for k in
        ("broadcast_map", "stories", "editorial_highlights")}
    if "collaboration_periods" in result:
        result["initial_projection"]["collaboration_periods"] = deepcopy(result["collaboration_periods"])
    return seal_review_revision(result)

def _frozen(row, fields):
    return {k: v for k, v in row.items() if k not in fields}

def _validate_revision_fields(packet):
    initial = packet["initial_projection"]
    original_periods = {row["id"]: row for row in initial.get("collaboration_periods", [])}
    if any(row != original_periods.get(row["id"]) and row["source_kind"] != "manager_declared"
           for row in packet.get("collaboration_periods", [])):
        raise ValueError("composed_collaboration_source_cannot_be_forged")
    for key in ("d1", "d2"):
        before = {r["id"]: r for r in initial["broadcast_map"][key]}
        after = {r["id"]: r for r in packet["broadcast_map"][key]}
        if before.keys() != after.keys() or any(_frozen(before[i], {"title", "content", "start", "end"}) !=
                _frozen(after[i], {"title", "content", "start", "end"}) for i in before):
            raise ValueError("composed_outline_lineage_changed")
    point_refs = {ref for h in initial["editorial_highlights"] for ref in h["point_refs"]}
    for key, id_key, editable in (
            ("points", "id", {"title", "content", "timestamp", "d1_ref", "d2_ref"}),
            ("stories", "story_ref", {"title", "why_notable"}),
            ("editorial_highlights", "highlight_id", {"title", "story_summary", "selection_reason", "d1_refs", "d2_refs"})):
        before_rows = initial["broadcast_map"][key] if key == "points" else initial[key]
        after_rows = packet["broadcast_map"][key] if key == "points" else packet[key]
        before, after = ({r[id_key]: r for r in rows} for rows in (before_rows, after_rows))
        if key == "editorial_highlights" and before.keys() != after.keys():
            raise ValueError("composed_highlight_selection_changed")
        if key == "points" and (before.keys() - after.keys()) & point_refs:
            raise ValueError("composed_highlight_point_removed")
        for identity in before.keys() & after.keys():
            old, new = deepcopy(before[identity]), deepcopy(after[identity])
            if key == "editorial_highlights":
                from .highlight_story_window import project_editorial_highlight_durations


                project_editorial_highlight_durations(new, original=old)
                for row in (old, new):
                    row.pop("total_source_duration_sec", None)
                    row.pop("excluded_gap_duration_sec", None)
                    if "estimated_edited_duration" in row:
                        row["estimated_edited_duration"].pop("min_sec", None)
                        row["estimated_edited_duration"].pop("max_sec", None)
                    row["source_spans"] = [_frozen(span, {"start_sec", "end_sec", "duration_sec", "d1_refs", "d2_refs"})
                        for span in row["source_spans"]]
            if _frozen(old, editable) != _frozen(new, editable):
                raise ValueError("composed_evidence_lineage_changed")
        for identity in after.keys() - before.keys():
            if after[identity].get("evidence_refs") or after[identity].get("roles"):
                raise ValueError("composed_manual_point_has_automatic_evidence")
    from .manager_outline import validate_structured_manager_outline
    validate_structured_manager_outline(_map(packet), duration_sec=packet["duration_sec"])
    for point in packet["broadcast_map"]["points"]:
        second = _sec(point["timestamp"])
        for key in ("d1", "d2"):
            ref = point.get(key + "_ref")
            if not ref:
                continue
            matched = next((r for r in packet["broadcast_map"][key] if r["id"] == ref), None)
            if matched is None or not _sec(matched["start"]) <= second <= _sec(matched["end"]):
                raise ValueError("composed_point_range_ref_invalid")
            if key == "d2" and point.get("d1_ref") and matched.get("parent_id") != point["d1_ref"]:
                raise ValueError("composed_point_parent_mismatch")
    import math
    for highlight in packet["editorial_highlights"]:
        last_end = -1
        for span in highlight["source_spans"]:
            start, end = float(span["start_sec"]), float(span["end_sec"])
            if not math.isfinite(start + end) or not 0 <= start < end <= packet["duration_sec"] or start < last_end:
                raise ValueError("composed_span_clock_invalid")
            last_end = end
    edited = any(packet[k] != initial[k] for k in ("broadcast_map", "stories", "editorial_highlights"))
    edited |= packet.get("collaboration_periods") != initial.get("collaboration_periods")
    if packet.get("manager_edited") is not edited:
        raise ValueError("composed_revision_edit_state_invalid")

def validate_composed_review(packet, *, expected_broadcast_map=None, expected_source_material_sha256=None):
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA or packet.get("projection_mode") != MODE:
        raise ValueError("invalid_composed_review_schema")
    if packet.get("review_revision_sha256") != _digest({k: v for k, v in packet.items()
            if k != "review_revision_sha256"}):
        raise ValueError("composed_review_revision_changed")
    sources = packet["source_material"]
    if packet.get("source_material_sha256") != _digest(sources) or (
            expected_source_material_sha256 is not None and packet.get("source_material_sha256") != expected_source_material_sha256):
        raise ValueError("composed_review_source_changed")
    original = compose_editorial_review(sources["external"], sources["generated"],
        additional_point_ids=sources["additional_point_ids"],
        additional_highlight_ids=sources.get("additional_highlight_ids", ()),
        generated_source_vod_id=sources.get("generated_source_vod_id"))
    if {k: v for k, v in packet.items() if k not in EDITABLE} != {
            k: v for k, v in original.items() if k not in EDITABLE}:
        raise ValueError("composed_review_provenance_changed")
    validate_editorial_authority_shape(packet)
    _validate_revision_fields(packet)
    actual = broadcast_map_identity_hash(_map(packet))
    if packet.get("broadcast_map_sha256") != actual or (expected_broadcast_map is not None
            and broadcast_map_identity_hash(expected_broadcast_map) != actual):
        raise ValueError("composed_review_broadcast_map_mismatch")
    return packet
