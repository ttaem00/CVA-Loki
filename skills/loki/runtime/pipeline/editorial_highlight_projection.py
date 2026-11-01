"""Own QG1 authority projection, rendering, validation, and staging."""

from __future__ import annotations

from copy import deepcopy

import hashlib

import json

from .editorial_contract import validate_editorial_authority_shape

def _clock_seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in str(value).split(":"))
    return hours * 3600 + minutes * 60 + seconds

def _highlight_story_refs(highlight: dict) -> list[str]:
    refs = highlight.get("story_refs") or (
        [highlight.get("story_ref")] if highlight.get("story_ref") else []
    )
    return list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))

def broadcast_map_identity_hash(value: dict) -> str:
    """Hash only the BroadcastMap-owned D1/D2/Point identity fields."""

    def normalized(row: dict, keys: tuple[str, ...]) -> dict:
        return {key: str(row.get(key) or "") for key in keys}

    value_ranges = [
        dict(row) for row in value.get("ranges") or [] if isinstance(row, dict)
    ]
    if not value_ranges:
        value_ranges = [
            dict(row)
            for key in ("d1", "d2")
            for row in value.get(key) or []
            if isinstance(row, dict)
        ]
    value_points = [
        dict(row) for row in value.get("points") or [] if isinstance(row, dict)
    ]
    identity = {
        "d1": [
            normalized(
                row,
                ("id", "level", "parent_id", "start", "end", "title", "content"),
            )
            for row in value_ranges if row.get("level") == "D1"
        ],
        "d2": [
            normalized(
                row,
                ("id", "level", "parent_id", "start", "end", "title", "content"),
            )
            for row in value_ranges if row.get("level") == "D2"
        ],
        "points": [
            normalized(row, ("id", "timestamp", "title", "content"))
            for row in value_points
        ],
    }
    return hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

def build_qg1_preview(
    *,
    broadcast_map: dict,
    editorial_result: dict,
    quality: dict,
    story_selection: dict | None = None,
    projection_mode: str = "shadow_only",
    source_broadcast_map: dict | None = None,
) -> dict:
    """Project one frozen authority revision without making semantic decisions."""

    ranges = [dict(row) for row in broadcast_map.get("ranges") or [] if isinstance(row, dict)]
    source_points = [dict(row) for row in broadcast_map.get("points") or [] if isinstance(row, dict)]
    d1 = [row for row in ranges if row.get("level") == "D1"]
    d2 = [row for row in ranges if row.get("level") == "D2"]
    highlights = list(editorial_result.get("highlights") or [])
    revision = str(editorial_result.get("authority_revision_id") or "")
    if not revision:
        raise ValueError("qg1_authority_revision_id_required")
    if projection_mode not in {"shadow_only", "qg1_review_candidate"}:
        raise ValueError("invalid_qg1_projection_mode")

    story_selection = dict(story_selection or {})
    collaboration = editorial_result.get("collaboration_periods", broadcast_map.get("collaboration_periods"))

    collaboration_diagnostics = broadcast_map.get("collaboration_diagnostics", editorial_result.get("collaboration_diagnostics"))
    if ("collaboration_periods" in editorial_result and "collaboration_periods" in broadcast_map
            and editorial_result["collaboration_periods"] != broadcast_map["collaboration_periods"]):
        raise ValueError("qg1_collaboration_authority_conflict")
    selected_points = {
        str(row.get("point_ref") or row.get("point_shadow_id") or ""): row
        for row in story_selection.get("selected_points") or []
        if isinstance(row, dict)
    }
    selected_story_refs = {
        str(row.get("story_packet_id") or "")
        for row in story_selection.get("decisions") or []
        if isinstance(row, dict) and row.get("decision") == "select"
    }
    highlight_by_point = {
        str(point_ref): highlight
        for highlight in highlights
        if isinstance(highlight, dict)
        for point_ref in highlight.get("point_refs") or []
    }
    points = []
    for row in source_points:
        timestamp = str(row.get("timestamp") or "")
        projected = {
            "id": str(row.get("id") or ""),
            "timestamp": timestamp,
            "title": str(row.get("title") or ""),
            "content": str(row.get("content") or ""),
            "story_ref": str(row.get("story_ref") or row.get("story_packet_id") or ""),
            "d1_ref": str(row.get("d1_ref") or ""),
            "d2_ref": str(row.get("d2_ref") or ""),
            "evidence_refs": list(row.get("evidence_refs") or row.get("used_evidence_ids") or []),
        }
        selected = selected_points.get(projected["id"])
        if selected and str(selected.get("story_packet_id") or "") in selected_story_refs:
            projected.update({
                "story_ref": str(selected.get("story_packet_id") or ""),
                "d1_ref": str(selected.get("d1_ref") or projected["d1_ref"]),
                "d2_ref": str(selected.get("d2_ref") or ""),
                "evidence_refs": list(selected.get("used_evidence_ids") or []),
            })
        linked_highlight = highlight_by_point.get(projected["id"])
        if linked_highlight:
            projected["d1_ref"] = str((linked_highlight.get("d1_refs") or [""])[0])
        points.append(projected)

    stories = []
    rejected_stories = []
    for decision in story_selection.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        story_ref = str(decision.get("story_packet_id") or "")
        if decision.get("decision") == "select":
            stories.append({
                "story_ref": story_ref,
                "title": str(decision.get("title") or ""),
                "why_notable": str(decision.get("why_notable") or ""),
                "roles": {
                    str(role): list(refs or [])
                    for role, refs in (decision.get("roles") or {}).items()
                },
                "evidence_refs": list(decision.get("used_evidence_ids") or []),
            })
        elif decision.get("decision") == "reject":
            rejected_stories.append({
                "story_ref": story_ref,
                "reason": str(decision.get("reject_reason") or ""),
            })
    if stories:
        selected_story_refs = {row["story_ref"] for row in stories}
        missing_story_refs = {
            story_ref
            for row in highlights
            for story_ref in _highlight_story_refs(row)
        } - selected_story_refs
        if missing_story_refs:
            raise ValueError("qg1_highlight_story_ref_missing_from_selection")

    identity_map = {
        "d1": [
            {key: row.get(key) for key in ("id", "level", "parent_id", "start", "end", "title", "content")}
            for row in d1
        ],
        "d2": [
            {key: row.get(key) for key in ("id", "level", "parent_id", "start", "end", "title", "content")}
            for row in d2
        ],
        "points": [
            {key: row.get(key) for key in ("id", "timestamp", "title", "content")}




            for row in points
        ],
    }
    broadcast_map_sha256 = broadcast_map_identity_hash({
        "d1": identity_map["d1"],
        "d2": identity_map["d2"],
        "points": identity_map["points"],
    })
    source_broadcast_map_sha256 = broadcast_map_identity_hash(
        source_broadcast_map if source_broadcast_map is not None else broadcast_map
    )
    if broadcast_map_sha256 != source_broadcast_map_sha256:
        raise ValueError("qg1_broadcast_map_authority_parity_failed")
    preview = {
        "schema_version": "chzz.qg1_preview.v1",
        "projection_mode": projection_mode,
        "authority_revision_id": revision,
        "broadcast_map_sha256": broadcast_map_sha256,
        "source_broadcast_map_sha256": source_broadcast_map_sha256,
        "broadcast_map": {"d1": d1, "d2": d2, "points": points},
        "stories": stories,
        "editorial_highlights": highlights,
        **({"collaboration_periods": deepcopy(collaboration)} if collaboration is not None else {}),
        **({"collaboration_diagnostics": deepcopy(collaboration_diagnostics)} if collaboration_diagnostics is not None else {}),
        **({'attention_receipts':deepcopy(editorial_result['attention_receipts'])}
           if editorial_result.get('attention_receipts') else {}),
        **({"source_exploration": deepcopy(editorial_result["source_exploration"])}
           if editorial_result.get("source_exploration") else {}),
        **({"completion_state": editorial_result["completion_state"],
            "unresolved_group_outcomes": editorial_result.get("unresolved_group_outcomes") or [],
            "unresolved_proposals": editorial_result.get("unresolved_proposals") or []}
           if editorial_result.get("completion_state") else {}),
        **({"unresolved_questions": deepcopy(
            editorial_result["full_author_projection"].get("unresolved_questions") or [])}
           if isinstance(editorial_result.get("full_author_projection"), dict) else {}),
        "story_dispositions": list(editorial_result.get("story_dispositions") or []),
        "rejected_stories": rejected_stories,
        "inputs_and_calls": {
            "broadcast_map_counts": {
                "d1": len(d1),
                "d2": len(d2),
                "points": len(points),
            },
            "highlight_count": len(highlights),
            "rejected_proposal_count": len(
                editorial_result.get("rejected_proposals") or []
            ),
            "projection_model_call_count": 0,
            "source_highlight_model_call_count": int(
                quality.get("cumulative_call_count") or 0
            ),
            "source_successful_model_call_count": int(
                quality.get("successful_call_count") or 0
            ),
            "source_failed_model_call_count": int(
                quality.get("failed_call_count") or 0
            ),
        },
        "safety": {
            "broadcast_map_is_frozen": True,
            "point_structure_preserved": (
                broadcast_map_sha256 == source_broadcast_map_sha256
                and all(
                    row.get("timestamp")
                    and not ({"currentTime", "start", "end", "range", "window", "context_start", "event_timestamp"} & set(row))
                    for row in points
                )
            ),
            "source_spans_are_only_highlight_time_authority": all(
                "source_spans" in row
                and not ({"start", "end", "start_sec", "end_sec", "range", "window"} & set(row))
                for row in highlights
            ),
            "stable_saved_public_changed": bool(
                editorial_result.get("stable_saved_public_changed", False)
            ),
        },
    }
    validate_editorial_authority_shape(
        preview,
        allow_legacy_without_story=not stories,
    )
    return preview

def validate_qg1_preview_payload(
    preview: dict,
    *,
    expected_broadcast_map: dict | None = None,
) -> dict:
    """Fail closed when a QG1 payload is not bound to the current BroadcastMap."""

    if not isinstance(preview, dict):
        raise ValueError("invalid_qg1_preview_payload")
    if preview.get("schema_version") != "chzz.qg1_preview.v1":
        raise ValueError("invalid_qg1_preview_schema")



    validate_editorial_authority_shape(preview, allow_legacy_without_story=True)
    if 'attention_receipts' in preview:
        from .editorial_attention_agenda import validate_receipts
        validate_receipts(preview['attention_receipts'])
    internal_map = dict(preview.get("broadcast_map") or {})
    self_map = {
        "ranges": list(internal_map.get("d1") or []) + list(internal_map.get("d2") or []),
        "points": list(internal_map.get("points") or []),
    }
    self_hash = broadcast_map_identity_hash(self_map)
    if preview.get("broadcast_map_sha256") != self_hash:
        raise ValueError("qg1_preview_broadcast_map_mismatch")
    if expected_broadcast_map is not None:
        external_hash = broadcast_map_identity_hash(expected_broadcast_map)





        bound_hashes = {
            str(preview.get("source_broadcast_map_sha256") or ""),
            str(preview.get("broadcast_map_sha256") or ""),
        } - {""}
        if external_hash not in bound_hashes:
            raise ValueError("qg1_preview_broadcast_map_mismatch")
    return preview
