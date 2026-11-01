"""Build and reconcile grounded editorial Highlight candidate cards."""

from __future__ import annotations

from .editorial_highlight_projection import _clock_seconds

from .story_packet_selector import ROLE_KEYS

def _approved_story_highlight_inputs(*, outline: dict, point_candidate_shadow: dict) -> tuple[list[dict], dict[str, dict]]:
    """Project frozen R1 decisions into H-KO packets without reselecting them."""

    selection = point_candidate_shadow.get("story_point_selection") or {}
    decisions = [row for row in selection.get("decisions") or [] if row.get("decision") == "select"]
    if not decisions:
        return [], {}
    if not selection.get("all_packets_decided"):
        raise ValueError("approved_story_selection_is_incomplete")
    packet_index = {str(row.get("story_packet_id") or ""): row for row in point_candidate_shadow.get("story_packets") or []}
    ledger = point_candidate_shadow.get("story_evidence_ledger") or {}
    selected_points = {str(row.get("story_packet_id") or ""): row for row in selection.get("selected_points") or []}
    ranges = {str(row.get("id") or ""): row for row in outline.get("ranges") or []}
    packets: list[dict] = []
    requirements: dict[str, dict] = {}
    for decision in decisions:
        story_ref = str(decision.get("story_packet_id") or "")
        packet = packet_index.get(story_ref)
        selected_point = selected_points.get(story_ref)
        if not story_ref or not packet or not selected_point:
            raise ValueError("approved_story_packet_contract_incomplete")
        d2_ref = str(packet.get("d2_ref") or selected_point.get("d2_ref") or "")
        d2 = ranges.get(d2_ref) or {}
        d1_ref = str(d2.get("parent_id") or packet.get("d1_ref") or selected_point.get("d1_ref") or "")
        d1 = ranges.get(d1_ref) or {}


        if (d2_ref and (not d2 or not d2.get("parent_id"))) or not d1_ref or not d1:
            raise ValueError("approved_story_references_unknown_d2")
        if not d2_ref and d1.get("level") != "D1":
            raise ValueError("approved_story_references_unknown_d1")
        source_envelope = d2 if d2_ref else d1
        point_ref = str(
            selected_point.get("point_ref")
            or selected_point.get("point_shadow_id")
            or ""
        )
        evidence_ids = [str(ref) for ref in packet.get("evidence_ids") or []]
        evidence_rows = []
        evidence_catalog = {}
        for evidence_id in evidence_ids:
            row = ledger.get(evidence_id)
            if not isinstance(row, dict):
                raise ValueError("approved_story_evidence_missing_from_ledger")
            public_row = {
                key: row.get(key)
                for key in ("evidence_id", "role", "point_start_eligible", "source_type", "time_sec", "source_text", "exact_source_ref")
                if key in row
            }
            evidence_catalog[evidence_id] = public_row
        roles = {key: list((decision.get("roles") or {}).get(key) or []) for key in ROLE_KEYS}
        required_refs = list(dict.fromkeys(ref for refs in roles.values() for ref in refs))
        required_times = [float(evidence_catalog[ref]["time_sec"]) for ref in required_refs]
        author_evidence_ids = [
            evidence_id for evidence_id in evidence_ids
            if evidence_id in required_refs
            or min(required_times) - 30 <= float(evidence_catalog[evidence_id].get("time_sec") or -1) <= max(required_times) + 30
        ]
        evidence_rows = [evidence_catalog[evidence_id] for evidence_id in author_evidence_ids]
        source_ranges = [{"start_sec": _clock_seconds(source_envelope["start"]), "end_sec": _clock_seconds(source_envelope["end"])}]
        if not d2_ref and any(not source_ranges[0]["start_sec"] <= time <= source_ranges[0]["end_sec"] for time in required_times):
            raise ValueError("approved_story_evidence_outside_d1")
        seed = {"story_seed_id": story_ref, "story_ref": story_ref, "d1_refs": [d1_ref], "d2_refs": [d2_ref] if d2_ref else [],
                "point_refs": [point_ref] if point_ref else [], "source_ranges": source_ranges}
        packets.append({
            "story_seed": seed, "story_ref": story_ref,
            "approved_story": {"title": decision.get("title"), "why_notable": decision.get("why_notable"), "roles": roles},
            "required_story_roles": roles, "required_evidence_refs": required_refs,
            "allowed_evidence_refs": author_evidence_ids, "evidence_catalog": evidence_rows,
            "source_ranges": source_ranges,
        })
        requirements[story_ref] = {
            "point_ref": point_ref,
            "required_story_roles": roles, "available_evidence_refs": evidence_ids,
            "author_evidence_refs": author_evidence_ids, "evidence_catalog": evidence_catalog,
        }
    return packets, requirements
