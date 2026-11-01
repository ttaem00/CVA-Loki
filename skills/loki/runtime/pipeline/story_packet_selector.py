"""Story-packet decisions with one code-owned Point navigation clock.

The model decides editorial meaning and evidence roles, never a timestamp.
Canonical time stays in the E1 ledger and is resolved after every packet has
one validated select/reject decision.
"""

from __future__ import annotations

import hashlib

import json

import re

from copy import deepcopy

from collections.abc import Mapping, Sequence

from typing import Any

ROLE_KEYS = ("setup", "performance", "event", "reaction", "result")

def validate_highlight_story_evidence(
    *,
    required_story_roles: Mapping[str, Sequence[str]],
    available_evidence_refs: Sequence[str],
    author_evidence_refs: Sequence[str] | None = None,
    used_evidence_refs: Sequence[str],
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    source_spans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Close the R1 role/evidence contract for one persisted Highlight.

    The semantic author chooses spans.  Code verifies that every canonical role
    ref survived and records a disposition for every supplied packet ref.
    """

    if set(required_story_roles) != set(ROLE_KEYS):
        raise ValueError("invalid_required_story_roles")
    roles: dict[str, list[str]] = {}
    for role in ROLE_KEYS:
        value = required_story_roles.get(role) or []
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"required_story_role_{role}_must_be_a_list")
        refs = [str(ref or "").strip() for ref in value]
        if any(not ref for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError(f"required_story_role_{role}_must_have_unique_nonempty_refs")
        roles[role] = refs
    available = [str(ref or "").strip() for ref in available_evidence_refs]
    if any(not ref for ref in available) or len(available) != len(set(available)):
        raise ValueError("story_available_evidence_refs_must_have_unique_nonempty_refs")
    used = {str(ref or "").strip() for ref in used_evidence_refs}
    permitted = {str(ref or "").strip() for ref in (author_evidence_refs or available)}
    required = list(dict.fromkeys(ref for role in ROLE_KEYS for ref in roles[role]))
    if not set(required).issubset(available):
        raise ValueError("required_story_evidence_not_available")
    if not set(required).issubset(permitted):
        raise ValueError("required_story_evidence_not_sent_to_author")
    if not used.issubset(permitted):
        raise ValueError("highlight_uses_evidence_outside_approved_story")
    dispositions = []
    for evidence_id in available:
        if evidence_id in used:
            dispositions.append({"evidence_id": evidence_id, "disposition": "included", "reason": "referenced_by_persisted_source_span"})
            continue
        row = evidence_catalog.get(evidence_id) or {}
        time_sec = row.get("time_sec", row.get("canonical_time_sec"))
        inside = isinstance(time_sec, (int, float)) and not isinstance(time_sec, bool) and any(
            float(span["start_sec"]) <= float(time_sec) <= float(span["end_sec"])
            for span in source_spans
        )
        dispositions.append({
            "evidence_id": evidence_id,
            "disposition": "unused_with_reason",
            "reason": (
                "available_inside_span_but_not_selected_for_this_highlight"
                if inside
                else "canonical_story_evidence_preserved_outside_selected_highlight_footage"
            ),
        })
    return {"required_story_roles": roles, "required_evidence_refs": required, "evidence_dispositions": dispositions}

def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()

def _hms(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

def _row_time(row: Mapping[str, Any]) -> int:
    if "time_sec" in row:
        return int(row["time_sec"])
    return int(row["canonical_time_sec"])

def audit_primary_evidence_dispositions(
    *, packets: Sequence[Mapping[str, Any]], evidence_ledger: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    owned: dict[str, str] = {}
    overlap: dict[str, list[str]] = {}
    explicit: dict[str, str] = {}
    for packet in packets:
        packet_id = str(packet.get("story_packet_id") or "")
        for item in packet.get("primary_dispositions") or []:
            evidence_id = str(item.get("evidence_id") or "")
            disposition = str(item.get("disposition") or "")
            if disposition == "included_primary":
                if evidence_id in owned:
                    raise ValueError("primary evidence has more than one owning packet")
                owned[evidence_id] = packet_id
            elif disposition == "moved_overlap_context":
                overlap.setdefault(evidence_id, []).append(packet_id)
        for item in packet.get("explicit_exclusions") or []:
            explicit[str(item.get("evidence_id") or "")] = str(item.get("reason") or "")
    expected = {
        evidence_id
        for evidence_id, row in evidence_ledger.items()
        if isinstance(row, Mapping) and str(row.get("role") or "") == "primary_semantic"
    }
    silent = sorted(expected - set(owned) - set(explicit))
    if silent:
        raise ValueError(f"primary evidence silently dropped: {silent[:3]}")
    return {
        "owned_primary_count": len(owned),
        "overlap_context_count": sum(len(values) for values in overlap.values()),
        "explicit_exclusion_count": len(explicit),
        "silent_drop_count": 0,
        "owner_by_evidence_id": owned,
        "explicit_exclusions": explicit,
    }

def _validated_refs(
    values: Any, *, allowed: set[str], label: str, allow_empty: bool = True
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    refs = [str(value) for value in values]
    if not allow_empty and not refs:
        raise ValueError(f"{label} must not be empty")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} contains duplicate evidence IDs")
    if any(value not in allowed for value in refs):
        raise ValueError(f"{label} references evidence outside its packet")
    return refs

def _resolve_point_start(
    *, roles: Mapping[str, list[str]], evidence_ledger: Mapping[str, Mapping[str, Any]]
) -> tuple[str, int, str | None]:
    for role_name, fallback_reason in (
        ("setup", None),
        ("performance", "setup_missing_used_performance"),
        ("event", "setup_and_performance_missing_used_event"),
    ):
        eligible: list[tuple[int, str]] = []
        for evidence_id in roles[role_name]:
            row = evidence_ledger.get(evidence_id)
            if (
                isinstance(row, Mapping)
                and str(row.get("role") or "") == "primary_semantic"
                and bool(row.get("point_start_eligible", False))
            ):
                eligible.append((_row_time(row), evidence_id))
        if eligible:
            sec, evidence_id = min(eligible, key=lambda value: (value[0], value[1]))
            return evidence_id, sec, fallback_reason
    raise ValueError("selected story has no eligible setup, performance, or event start")

def normalize_unique_story_evidence_refs(response, packets, evidence_ledger):
    """Repair a single trailing hex digit only against a unique supplied ID."""
    from copy import deepcopy
    result = deepcopy(response)
    indexes = {p['story_packet_id']: set(p.get('evidence_ids', [])) & set(evidence_ledger) for p in packets}
    changes = []
    for decision in result.get('decisions', []):
        if not isinstance(decision, dict):
            continue
        allowed = indexes.get(decision.get('story_packet_id'), set())
        def refs(values):
            if not isinstance(values, list):
                return values
            output = []
            for ref in values:
                matches = [full for full in allowed if isinstance(ref, str)
                    and re.fullmatch(r'evidence-[0-9a-f]{23,25}', ref)
                    and (len(full) == len(ref)+1 and full.startswith(ref)
                         or len(ref) == len(full)+1 and ref.startswith(full))]
                fixed = matches[0] if ref not in allowed and len(matches) == 1 else ref
                if fixed != ref:
                    changes.append(dict(story_packet_id=decision['story_packet_id'], original=ref, restored=fixed,
                                        rule='unique_packet_bound_trailing_hex_digit'))
                output.append(fixed)
            return output
        if 'used_evidence_ids' in decision:
            decision['used_evidence_ids'] = refs(decision['used_evidence_ids'])
        if isinstance(decision.get('roles'), dict):
            decision['roles'] = {key: refs(value) for key,value in decision['roles'].items()}
        for update in decision.get('editorial_thread_updates') or []:
            if isinstance(update, dict):
                update['evidence_refs'] = refs(update.get('evidence_refs'))
    return result, changes

def materialize_story_point_selection(
    *,
    response: Mapping[str, Any],
    packets: Sequence[Mapping[str, Any]],
    evidence_ledger: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one Story decision for every packet without owning Point time."""

    if set(response) != {"decisions"} or not isinstance(response.get("decisions"), list):
        raise ValueError("invalid story selector response shape")
    response, reference_normalizations = normalize_unique_story_evidence_refs(response, packets, evidence_ledger)
    packet_index = {str(packet.get("story_packet_id") or ""): packet for packet in packets}
    if "" in packet_index or len(packet_index) != len(packets):
        raise ValueError("input packets must have unique non-empty IDs")
    point_refs = [
        str(packet.get("point_ref") or "") for packet in packets
        if str(packet.get("point_ref") or "")
    ]
    if len(point_refs) != len(set(point_refs)):
        raise ValueError("canonical Point must have exactly one Story packet")

    decision_by_packet: dict[str, Mapping[str, Any]] = {}
    for decision in response["decisions"]:
        if not isinstance(decision, Mapping):
            raise ValueError("each packet decision must be an object")
        packet_id = str(decision.get("story_packet_id") or "")
        if packet_id not in packet_index:
            raise ValueError("unknown story_packet_id")
        if packet_id in decision_by_packet:
            raise ValueError("duplicate story packet decision")
        decision_by_packet[packet_id] = decision
    missing = sorted(set(packet_index) - set(decision_by_packet))
    if missing:
        raise ValueError(f"story packets omitted from response: {missing[:3]}")

    selected_points: list[dict[str, Any]] = []
    rejected_packets: list[dict[str, str]] = []
    normalized_decisions: list[dict[str, Any]] = []
    discarded_unknown_evidence_refs: set[str] = set()
    discarded_cross_packet_evidence_refs: set[str] = set()
    for packet in packets:
        packet_id = str(packet["story_packet_id"])
        decision = decision_by_packet[packet_id]
        kind = str(decision.get("decision") or "")
        if kind == "reject":
            if str(packet.get("point_ref") or ""):
                raise ValueError("canonical Point requires exactly one Story")
            if set(decision) != {"story_packet_id", "decision", "reject_reason"}:
                raise ValueError("reject decision must use closed shape")
            reason = str(decision.get("reject_reason") or "").strip()
            if not reason:
                raise ValueError("reject decision requires a reason")
            row = {"story_packet_id": packet_id, "decision": "reject", "reject_reason": reason}
            rejected_packets.append({"story_packet_id": packet_id, "reject_reason": reason})
            normalized_decisions.append(row)
            continue
        if kind != "select":
            raise ValueError("packet decision must be select or reject")
        owner_mode = "editorial_owner_cards" in packet
        if owner_mode and "editorial_working_view" not in packet:
            raise ValueError("story_candidate_owner_requires_working_view")
        expected_keys = {
            "story_packet_id", "decision", "used_evidence_ids", "roles"
        } | (set() if owner_mode else {"title", "why_notable"})
        working_view = packet.get("editorial_working_view")
        if working_view is not None:
            from .editorial_working_view import check_refs, validate_view
            validate_view(working_view)
            expected_keys.add("editorial_hypothesis_refs")
            check_refs(decision.get("editorial_hypothesis_refs"),
                       {r["hypothesis_ref"] for r in working_view["hypotheses"]}, "story_hypothesis")
            if owner_mode:
                from .editorial_working_view import validate_owner_request
                validate_owner_request(packet["editorial_owner_cards"], working_view)
                if len(decision["editorial_hypothesis_refs"]) > 1:
                    raise ValueError("story_candidate_owner_requires_zero_or_one_ref")
            from .editorial_marker_desk import active, validate_story_updates
            if not owner_mode and active(working_view):
                expected_keys.add("editorial_thread_updates")
                validate_story_updates(decision.get("editorial_thread_updates"), working_view,
                                         packet.get("evidence_ids") or [])
            if not owner_mode and working_view.get("local_profile"):
                from .editorial_local_leads import check_lead_refs
                expected_keys.add("editorial_lead_refs")
                check_lead_refs(decision.get("editorial_lead_refs"),working_view,"story_lead")
            if not owner_mode and working_view.get('attention_profile'):
                expected_keys.add('editorial_agenda_refs')
                check_refs(decision.get('editorial_agenda_refs'),
                    {a['agenda_ref'] for a in working_view['attention_agenda']},'story_agenda')
        if set(decision) != expected_keys:
            raise ValueError("select decision must use closed shape")
        if owner_mode:
            selected_owner_ref = next(iter(decision["editorial_hypothesis_refs"]), "")
            owner_index = {row["owner_ref"]: row for row in packet["editorial_owner_cards"]["cards"]}
            selected_owner = owner_index.get(selected_owner_ref) or {}
            title = str(packet.get("point_title") or selected_owner.get("viewer_value_hypothesis") or "").strip()
            why_notable = str(selected_owner.get("viewer_value_hypothesis") or packet.get("point_content")
                               or packet.get("point_title") or "").strip()
        else:
            title = str(decision.get("title") or "").strip()
            why_notable = str(decision.get("why_notable") or "").strip()
        if not title or not why_notable:
            raise ValueError("selected story requires title and why_notable")
        allowed = {str(value) for value in packet.get("evidence_ids") or []}
        raw_roles = decision.get("roles")
        if not isinstance(raw_roles, Mapping) or set(raw_roles) != set(ROLE_KEYS):
            raise ValueError("selector roles must use closed shape")
        raw_used = decision.get("used_evidence_ids")
        outside_refs = {
            str(value)
            for values in (raw_used, *(raw_roles[key] for key in ROLE_KEYS))
            if isinstance(values, list)
            for value in values
            if str(value) not in allowed
        }
        if outside_refs:
            if str(packet.get("point_ref") or ""):
                known_cross_packet_refs = outside_refs.intersection(evidence_ledger)




                discarded_cross_packet_evidence_refs.update(
                    known_cross_packet_refs
                )
                discarded_unknown_evidence_refs.update(
                    outside_refs - known_cross_packet_refs
                )
                raw_used = [
                    value for value in raw_used
                    if str(value) in allowed
                ] if isinstance(raw_used, list) else raw_used
                raw_roles = {
                    key: [
                        value for value in raw_roles[key]
                        if str(value) in allowed
                    ] if isinstance(raw_roles[key], list) else raw_roles[key]
                    for key in ROLE_KEYS
                }
            else:




                reason = "invalid_evidence_reference"
                row = {
                    "story_packet_id": packet_id,
                    "decision": "reject",
                    "reject_reason": reason,
                }
                rejected_packets.append({"story_packet_id": packet_id, "reject_reason": reason})
                normalized_decisions.append(row)
                continue
        used = _validated_refs(
            raw_used, allowed=allowed,
            label="used_evidence_ids", allow_empty=False,
        )
        roles = {
            key: _validated_refs(raw_roles[key], allowed=allowed, label=f"roles.{key}")
            for key in ROLE_KEYS
        }
        if len(roles["setup"]) > 1:
            raise ValueError("roles.setup must contain at most one minimum-sufficient start")
        role_refs = {value for values in roles.values() for value in values}
        if not role_refs:
            raise ValueError("selected story requires at least one role evidence reference")
        used = list(
            dict.fromkeys(
                [
                    *used,
                    *(
                        ref
                        for key in ROLE_KEYS
                        for ref in roles[key]
                    ),
                ]
            )
        )
        body = {
            "story_packet_id": packet_id,
            "d2_ref": str(packet.get("d2_ref") or ""),
            "title": title,
            "why_notable": why_notable,
            "used_evidence_ids": used,
            "roles": roles,
        }
        point_ref = str(packet.get("point_ref") or "")
        if working_view is not None:
            body["editorial_hypothesis_refs"] = list(decision["editorial_hypothesis_refs"])
            if owner_mode:
                card = next((row for row in packet["editorial_owner_cards"]["cards"]
                             if row["owner_ref"] in decision["editorial_hypothesis_refs"]), None)
                if card:
                    body["editorial_lead_refs"] = list(card["source_locations"]["lead_refs"])
                    body["editorial_agenda_refs"] = list(card["source_locations"]["agenda_refs"])
            elif active(working_view):
                validate_story_updates(decision["editorial_thread_updates"], working_view, used)
                body["editorial_thread_updates"] = deepcopy(decision["editorial_thread_updates"])
            if not owner_mode and working_view.get("local_profile"):
                body["editorial_lead_refs"] = list(decision["editorial_lead_refs"])
            if not owner_mode and working_view.get('attention_profile'):
                body['editorial_agenda_refs']=list(decision['editorial_agenda_refs'])
        if point_ref:
            body.update({
                "point_ref": point_ref,
                "d1_ref": str(packet.get("d1_ref") or ""),
                "authority": {
                    "point_owner": "broadcast_map_finalizer",
                    "story_time_authority": "none",
                },
            })
        else:
            point_start_id, sec, fallback_reason = _resolve_point_start(
                roles=roles, evidence_ledger=evidence_ledger
            )
            body.update({
                "point_start_evidence_id": point_start_id,
                "point_start_fallback_reason": fallback_reason,
                "timestamp": _hms(sec),
                "currentTime": sec,
                "link_currentTime": sec,
                "authority": {
                    "clock_owner": "code_owned_evidence_ledger",
                    "adoption_status": "point_shadow_only",
                },
            })
            body["point_shadow_id"] = "point-shadow-" + _sha(body)[:24]
        selected_points.append(body)
        normalized_decisions.append({
            "story_packet_id": packet_id,
            "decision": "select",
            **({"editorial_hypothesis_refs": list(decision["editorial_hypothesis_refs"])} if working_view is not None else {}),
            **({"editorial_lead_refs": list(body.get("editorial_lead_refs", []))} if owner_mode else
               ({"editorial_lead_refs": list(decision["editorial_lead_refs"])} if working_view is not None and working_view.get("local_profile") else {})),
            **({"editorial_agenda_refs": list(body.get("editorial_agenda_refs", []))} if owner_mode else {}),
            **({'editorial_agenda_refs':list(decision['editorial_agenda_refs'])} if not owner_mode and working_view is not None and working_view.get('attention_profile') else {}),
            **({"editorial_thread_updates": deepcopy(decision["editorial_thread_updates"])} if not owner_mode and working_view is not None and active(working_view) else {}),
            "title": title,
            "why_notable": why_notable,
            "used_evidence_ids": used,
            "roles": roles,
        })

    return {
        "decisions": normalized_decisions,
        "selected_points": selected_points,
        "stories": selected_points,
        "rejected_packets": rejected_packets,
        "packet_decision_count": len(normalized_decisions),
        "packet_count": len(packets),
        "all_packets_decided": True,
        "reference_normalizations": reference_normalizations,
        "discarded_unknown_evidence_ref_count": len(
            discarded_unknown_evidence_refs
        ),
        "discarded_cross_packet_evidence_ref_count": len(
            discarded_cross_packet_evidence_refs
        ),
    }
