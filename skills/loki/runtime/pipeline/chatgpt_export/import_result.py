"""Lossless, read-only import of an external editorial draft for product review.

This is deliberately not QG1 authority, pipeline completion, or an approval.
Original declarations and exact cut times survive beside a seconds-only product
navigation projection. No files are written and no model/provider is called.
"""

from __future__ import annotations

from copy import deepcopy

import hashlib

import json

from .contract import validate_result as contract

from ..editorial_contract import validate_editorial_authority_shape

from ..manager_outline import validate_structured_manager_outline

SCHEMA = "chzz.external_editorial_review.v1"

PROJECTION_MODE = "external_review_draft"

def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

def _clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"

def _projection(result):
    ranges = []
    for key, level in (("d1", "D1"), ("d2", "D2")):
        for row in result["broadcast_map"][key]:
            if int(row["start_sec"]) >= int(row["end_sec"]):
                raise ValueError("external_range_below_product_clock_precision")
            ranges.append(dict(id=row["id"], level=level,
                parent_id=row.get("d1_ref") or "", start=_clock(row["start_sec"]),
                end=_clock(row["end_sec"]), title=row["title"], content=row["summary"],
                evidence_refs=deepcopy(row["evidence_refs"]), boundary_status=row["boundary_status"],
                support_status=row["support_status"]))
    points = deepcopy(result["broadcast_map"]["points"])
    for point in points:
        point["timestamp"] = _clock(point["timestamp"])
        point["d2_ref"] = point["d2_ref"] or ""
    point_by_id = {p["id"]: p for p in points}
    highlights = deepcopy(result["editorial_highlights"])
    for highlight in highlights:


        highlight["story_summary"] = highlight["edit_rationale"]
        highlight["d1_refs"] = list(dict.fromkeys(point_by_id[p]["d1_ref"] for p in highlight["point_refs"]))
        highlight["d2_refs"] = list(dict.fromkeys(point_by_id[p]["d2_ref"] for p in highlight["point_refs"] if point_by_id[p]["d2_ref"]))
    outline = dict(ranges=ranges, points=points, candidates=[])
    validate_structured_manager_outline(outline, duration_sec=result["duration_sec"])
    packet = dict(
        source_vod_id=result["source_vod_id"], duration_sec=result["duration_sec"],
        broadcast_map=dict(d1=[r for r in ranges if r["level"] == "D1"],
                           d2=[r for r in ranges if r["level"] == "D2"], points=points),
        stories=deepcopy(result["stories"]), editorial_highlights=highlights,
        completion_state="external_unreviewed", pipeline_complete=False,
        analysis_status=result["analysis_status"], review_status=result["review_status"],
    )
    for key in ("pending_highlights", "story_outcomes", "context_relations", "context_reads",
                "evidence_ledger", "signal_decisions", "unresolved", "timeline_gaps", "coverage"):
        packet[key] = deepcopy(result[key])
    if "collaboration_periods" in result:
        from .contract.collaboration_periods import recover_collaboration_periods
        packet["collaboration_periods"] = recover_collaboration_periods(
            result["collaboration_periods"], result["duration_sec"])["periods"]
    validate_editorial_authority_shape(packet)
    return packet

def validate_imported_editorial_result(packet):
    """Fail closed on changed provenance/selection; never promote to QG1.

    Rechecks structure and the deterministic source-to-display projection. A
    previous native check receipt is not a fresh independent source-file check.
    """
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA:
        raise ValueError("invalid_external_editorial_schema")
    if packet.get("projection_mode") != PROJECTION_MODE:
        raise ValueError("invalid_external_editorial_projection_mode")
    unsigned = {key: value for key, value in packet.items() if key != "projection_sha256"}
    if packet.get("projection_sha256") != _digest(unsigned):
        raise ValueError("external_editorial_projection_changed")
    result, manifest = packet["source_result"], packet["source_manifest"]
    provenance = packet["provenance"]
    if (provenance.get("result_value_sha256") != _digest(result)
            or provenance.get("manifest_value_sha256") != _digest(manifest)
            or provenance.get("product_approval") is not False
            or provenance.get("media_verification") != "not_performed_by_importer"):
        raise ValueError("external_editorial_provenance_changed")
    contract.validate(result, manifest, None, verify_native=False)
    expected = _projection(result)
    if any(packet.get(key) != value for key, value in expected.items()):
        raise ValueError("external_editorial_selection_changed")
    return packet
