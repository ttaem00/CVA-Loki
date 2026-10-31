"""3.0.2's source-reading writer, using the existing tool/replay and projections.

This boundary changes editorial responsibility, not provider admission. It is
only reached for a fresh explicitly selected job; manager-saved authority is
resolved by the normal entry before generation.
"""

from copy import deepcopy

from contextlib import contextmanager

import math

import re

from .evidence_world.contracts import AssetBinding, digest

from .evidence_world.records import assert_asset_fresh

from .editorial_contract import full_author_completion_state

PROFILE = "full_broadcast_v302"

CONTRACT = "editorial.full-author.v302"

RESPONSE_FIELDS = {"base_revision", "summary", "summary_evidence_refs", "ranges", "points",
                   "stories", "proposals", "group_outcomes", "unresolved_questions"}

def build_broadcast_flow_context(*, source, base_revision, existing_broadcast_map=None,
                                 activity_context=None, orientations=()):
    """Bind the writer's actual inputs, without inventing an activity inventory.

    Observations are fallible input pieces, not one activity or one required
    output each. The whole-source responsibility also applies without T2.
    """
    inputs = [dict(ref="broadcast", kind="original_broadcast")]
    for row in (existing_broadcast_map or {}).get("ranges", []):
        inputs.append(dict(ref="map:" + row["id"], kind="prior_range", range=deepcopy(row)))
    for row in (activity_context or {}).get("entries", []):
        inputs.append(dict(ref=row["ref"], kind="t2_observation", observation_ref=row["ref"]))
    for orientation in orientations:
        value = orientation["content"]
        prefix = "orientation:" + orientation["binding"]["sha256"] + ":"
        if value.get("schema_version") == "editorial.activity-context.v1":
            for row in value["entries"]:
                inputs.append(dict(ref=prefix + row["ref"], kind="t2_observation",
                                   observation_ref=row["ref"]))
        outline = value.get("outline", value.get("broadcast_map", value))
        for row in outline.get("ranges", []) if isinstance(outline, dict) else []:
            if isinstance(row, dict) and row.get("id"):
                inputs.append(dict(ref=prefix + row["id"], kind="prior_range", range=deepcopy(row)))
    return dict(source_revision=source["sha256"], base_revision=base_revision, inputs=inputs)

def _text(value, label):
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise ValueError("full_author_invalid_" + label)
    return value

def _refs(values, catalog):
    if (not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values)
            or len(values) != len(set(values)) or not set(values) <= set(catalog)):
        raise ValueError("full_author_undelivered_evidence")
    return values

@contextmanager
def _author_item(path):
    """Attach an authored location to an existing failure, without revalidating."""
    try:
        yield
    except ValueError as error:
        if path is not None:
            error.item_path = list(path)
        raise

def _seconds(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2,}:\d{2}:\d{2}", value):
        raise ValueError("full_author_invalid_clock")
    h, m, s = map(int, value.split(":"))
    if m >= 60 or s >= 60:
        raise ValueError("full_author_invalid_clock")
    return h * 3600 + m * 60 + s

def _point_clock_agrees(timestamp, native_start_sec):



    return abs(_seconds(timestamp) - int(native_start_sec)) <= 2

def _point_anchor_agrees(row, native_start_sec):
    refs = row.get("evidence_refs")
    return (isinstance(refs, list) and row.get("start_evidence_ref") in refs
            and isinstance(native_start_sec, (int, float))
            and ("timestamp" not in row or _point_clock_agrees(row["timestamp"], native_start_sec)))

def full_source_context(*, source, packet):
    from .editorial_context_author import context_source
    view, evidence = context_source(source=source, packet=packet)
    for ref, row in packet.get("_delivered_chat_records", {}).items():
        clock = row.get("time_index")
        evidence["evidence_catalog"][ref] = dict(evidence_id=ref, source_type="replay_chat",
            role="primary_semantic", time_sec=clock["start_us"]/1e6 if clock else None,
            source_text=row["raw_text"], exact_source_ref=ref, source_revision=row.get("asset_id"))
    if packet.get('elio_story_context') is not None:
        from .editorial_supplemental_sources import validate_elio_context
        view['elio_story_context'] = validate_elio_context(packet['elio_story_context'],
            video_no=source['asset']['video_no'])
        view['availability']['elio_story_context'] = 'published_reader_background_not_vod_evidence'
    from .editorial_context_exploration import add_active_frames
    from .editorial_visual_input import frame_evidence
    add_active_frames(view, packet)




    frames = {**packet.get("_delivered_media_frames", {}), **packet.get("_media_frames", {})}
    if frames:
        evidence["frames"] = frames
        evidence["evidence_catalog"].update(frame_evidence(frames))



    view["unlocated_stt_columns"] = ["ref", "ordinal", "original_text", "raw_time", "uncertainties"]
    view["unlocated_stt"] = []
    for row in source["records"]:
        if row.get("time_index") or row.get("raw_text") is None:
            continue
        ref = "e" + str(row["ordinal"] + 1)
        if ref in evidence["evidence_catalog"]:
            raise ValueError("full_author_ambiguous_original")
        native_ref = "stt-occ:" + row["record_id"][:24]
        evidence["evidence_catalog"][ref] = dict(evidence_id=ref, source_type="stt",
            role="unlocated_context", source_text=row["raw_text"], time_sec=None,
            native_start_sec=None, native_end_sec=None, native_ref=native_ref,
            exact_source_ref=native_ref, source_revision=source["asset"]["bytes_sha256"])
        view["unlocated_stt"].append([ref, row["ordinal"], row["raw_text"],
            row.get("raw_time"), row.get("uncertainties", [])])
    return view, evidence

def _delivered_evidence_catalog(evidence, delivered_source_refs):
    catalog = evidence["evidence_catalog"]
    if delivered_source_refs is None:
        return catalog
    delivered = set(delivered_source_refs)
    return {ref: row for ref, row in catalog.items()
            if (row.get("source_type") not in {"stt", "replay_chat"}
                or row.get("native_ref", row.get("exact_source_ref")) in delivered)}

def _range_times(row, catalog):
    if "start_evidence_ref" not in row and "end_evidence_ref" not in row:
        return _seconds(row["start"]), _seconds(row["end"])
    anchors = [catalog.get(row.get(key), {}) for key in ("start_evidence_ref", "end_evidence_ref")]
    if any(anchor.get("source_type") != "stt" for anchor in anchors):
        raise ValueError("full_author_range_has_no_native_support")
    start, end = anchors[0].get("native_start_sec"), anchors[1].get("native_end_sec")
    if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (start, end)):
        raise ValueError("full_author_range_has_no_native_support")
    return start, end

def _bound_flow_context(source, base_revision, packet, broadcast_flow_context):
    context = (broadcast_flow_context if broadcast_flow_context is not None
               else packet.get("_broadcast_flow_context"))
    if context is None:
        context = build_broadcast_flow_context(source=source, base_revision=base_revision)
    if (context.get("source_revision") != source["sha256"]
            or context.get("base_revision") != base_revision):
        raise ValueError("full_author_flow_context_binding_changed")
    return context

def _flow_outcome_findings(payload, catalog, flow_context):
    """Use the same relation checks for final validation and its one item repair."""
    flow_inputs = {row["ref"]: row for row in flow_context["inputs"]}
    final_ranges = {row["id"]: row for row in payload["ranges"]
                    if isinstance(row, dict) and isinstance(row.get("id"), str)}
    findings = []
    for index, row in enumerate(payload["group_outcomes"]):
        try:
            state = row.get("terminal_state")
            flow_refs = row.get("flow_refs", [])
            if (not isinstance(row.get("reason"), str) or not row["reason"].strip()
                    or state not in {"no_editorial_value", "relation_broken", "source_unavailable",
                                     "context_unresolved", "represented", "source_corrected", "no_broadcast_activity"}):
                raise ValueError("full_author_outcome_invalid")
            if (not isinstance(flow_refs, list) or any(not isinstance(ref, str) for ref in flow_refs)
                    or not set(flow_refs) <= flow_inputs.keys()):
                raise ValueError("full_author_flow_reference_invalid")
            is_pending = state in {"source_unavailable", "context_unresolved"}
            if not (flow_refs and is_pending and row.get("evidence_refs") == []):
                _refs(row.get("evidence_refs"), catalog)
            if state == "represented":
                if not flow_refs:
                    raise ValueError("full_author_flow_reference_invalid")
                targets = row.get("range_refs")
                if (not isinstance(targets, list) or not targets
                        or any(not isinstance(ref, str) or ref not in final_ranges for ref in targets)):
                    raise ValueError("full_author_flow_target_invalid")
            elif state in {"source_corrected", "no_broadcast_activity"} and not flow_refs:
                raise ValueError("full_author_flow_reference_invalid")
        except ValueError as error:
            findings.append(dict(path=["group_outcomes", index], error=str(error),
                instruction="Recheck this judgment against exact delivered originals and the final repaired ranges. "
                    "Use only input addresses from broadcast_flow_context; represented requires nonempty flow_refs "
                    "and range_refs naming actual final ranges. "
                    "Preserve its flow addresses; do not union refs or remove a judgment merely to pass validation. "
                    "Leave unsupported relations unresolved. Unrelated items are frozen."))
    return findings

def validate_response(payload, *, base_revision, source, packet, duration_sec, highlight_producer_run_ref=None,
                      delivered_source_refs=None, broadcast_flow_context=None, range_boundary_contract=None):
    """Validate meaning-bearing model output; never infer a Point or Story.

    Native subtitle clocks license text-based navigation only. Pixel/audio
    verification remains absent and is not upgraded by these structural checks.
    """
    from .editorial_context_author import context_source
    from .outline.check import validate_structured_manager_outline
    from .story_packet_selector import materialize_story_point_selection, audit_primary_evidence_dispositions, ROLE_KEYS
    from .editorial_highlight_candidate_discovery import _approved_story_highlight_inputs
    from .highlight_story_window import build_editorial_highlight_set
    from .editorial_author_source import NATIVE_RANGE_CONTRACT
    from .outline.schema import seconds_to_hms
    if not isinstance(payload, dict) or set(payload) != RESPONSE_FIELDS:
        raise ValueError("full_author_response_shape")
    if payload["base_revision"] != base_revision:
        raise ValueError("full_author_base_revision_changed")
    if not source or not duration_sec or source["asset"]["source_locator"].get("kind") == "temporal_projection":
        raise ValueError("full_author_verified_vod_clock_required")
    for key in ("ranges", "points", "stories", "proposals", "group_outcomes", "unresolved_questions"):
        if not isinstance(payload[key], list):
            raise ValueError("full_author_response_shape")
    if any(not isinstance(row, dict) for row in payload["group_outcomes"]):
        raise ValueError("full_author_outcome_invalid")
    _, evidence = full_source_context(source=source, packet=packet)
    catalog = _delivered_evidence_catalog(evidence, delivered_source_refs)
    summary_refs = payload["summary_evidence_refs"]
    with _author_item(["summary_evidence_refs"] if isinstance(summary_refs, list)
                      and all(isinstance(ref, str) for ref in summary_refs) else None):
        try:
            _refs(summary_refs, catalog)
        except ValueError as error:
            raise ValueError("full_author_summary_evidence_invalid") from error
    summary = _text(payload["summary"], "summary")
    elio = packet.get('elio_story_context') or {}
    if elio.get('summary_link_policy') == 'related_public_reader_v1':
        links = ['[{}](<{}>)'.format(re.sub(r'[\[\]<>\\\r\n]', ' ', page['title']), page['url'])
            for page in elio['pages'] if page['status'] == 'available' and page['url'] not in summary]
        if links:


            summary += ' 참고 스토리 타임라인: ' + ' · '.join(links) + '.'
    ranges, points, point_inputs, range_errors = [], [], {}, []
    for index, row in enumerate(payload["ranges"]):
        anchored = "start_evidence_ref" in row or "end_evidence_ref" in row if isinstance(row, dict) else False
        native_required = range_boundary_contract == NATIVE_RANGE_CONTRACT
        expected = {"id", "level", "title", "content", "evidence_refs"} | (
            {"start_evidence_ref", "end_evidence_ref"} if anchored or native_required else {"start", "end"})
        if not isinstance(row, dict):
            raise ValueError("full_author_range_shape")
        try:
            with _author_item(["ranges", index]):
                if set(row) - {"image"} != expected | ({"parent_id"} if row.get("level") == "D2" else set()):
                    raise ValueError("full_author_range_shape")
                refs = _refs(row["evidence_refs"], catalog)
                _text(row["title"], "range_title")
                _text(row["content"], "range_content")
                if anchored:
                    _refs(list(dict.fromkeys([row["start_evidence_ref"], row["end_evidence_ref"]])), catalog)
                start, end = _range_times(row, catalog)
                if anchored and not 0 <= start < end <= duration_sec:
                    raise ValueError("full_author_range_has_no_native_support")


                if not anchored and not any(isinstance(catalog[r].get("native_start_sec"), (int, float))
                           and start <= int(catalog[r]["native_start_sec"]) <= end for r in refs):
                    raise ValueError("full_author_range_has_no_native_support")
        except ValueError as error:
            range_errors.append(error)
            continue
        projected_range = {k:v for k,v in row.items() if k not in {"evidence_refs", "image"}}
        if anchored:
            projected_range.update(start=seconds_to_hms(int(start)), end=seconds_to_hms(int(end)))
        ranges.append(projected_range)
    if range_errors:
        range_errors[0].range_findings = [dict(path=error.item_path, error=str(error),
            instruction="Recheck this range against exact delivered originals and its native clock. "
                "Preserve its ID and parent; do not alias or union refs to make it pass.")
            for error in range_errors]
        raise range_errors[0]
    for row, projected_range in zip(payload["ranges"], ranges):
        if row.get("image") is not None:
            from .editorial_visual_input import selected_frame_image
            projected_range["image"] = selected_frame_image(row["image"], frames=evidence.get("frames", {}),
                source_spans=[dict(zip(("start_sec", "end_sec"), _range_times(row, catalog)))])
    for index, row in enumerate(payload["points"]):
        required = {"id", "title", "content", "d2_ref", "start_evidence_ref", "evidence_refs"}
        if not isinstance(row, dict) or not required <= set(row) or not set(row) <= required | {"timestamp"}:
            raise ValueError("full_author_point_shape")
        with _author_item(["points", index]):
            _refs(row["evidence_refs"], catalog)
            start = catalog.get(row["start_evidence_ref"])
            if not _point_anchor_agrees(row, start.get("native_start_sec") if start else None):
                raise ValueError("full_author_point_clock_not_native")
            sec = int(start["native_start_sec"])
            timestamp = f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"
            d2 = next((r for r in ranges if r["id"] == row["d2_ref"] and r["level"] == "D2"), None)
            if not d2 or not _seconds(d2["start"]) <= sec <= _seconds(d2["end"]):
                raise ValueError("full_author_point_requires_authored_d2")
            _text(row["title"], "point_title")
            _text(row["content"], "point_content")
        points.append({**{k:row[k] for k in ("id", "title", "content")}, "timestamp":timestamp})
        point_inputs[row["id"]] = row
    outline = dict(ranges=ranges, points=points, candidates=[])
    try:
        validate_structured_manager_outline(outline, duration_sec=duration_sec)
    except ValueError as error:
        related = getattr(error, "range_ids", ())
        if related:
            indexes = {str(row["id"]): index for index, row in enumerate(payload["ranges"])}
            error.range_findings = [dict(path=["ranges", indexes[range_id]], error=str(error),
                related_range_ids=list(related),
                instruction="Recheck these related ranges together against exact delivered originals and native boundaries. "
                    "Preserve each ID, level and parent; all unrelated items are frozen. "
                    "Do not sort, clip, merge or move parents to make the geometry pass. "
                    "Request needed originals, or leave an unsupported relationship unresolved.")
                for range_id in related]
            error.item_path = error.range_findings[0]["path"]
        raise
    if not ranges or not any(r["level"] == "D1" for r in ranges):
        raise ValueError("full_author_broadcast_map_missing")
    story_rows = payload["stories"]
    if [r.get("point_ref") for r in story_rows if isinstance(r, dict)] != [r["id"] for r in points]:
        raise ValueError("full_author_every_point_requires_story")
    packets, decisions, primary_owners = [], [], {}
    for index, story in enumerate(story_rows):
        if set(story) != {"story_ref", "point_ref", "title", "why_notable", "used_evidence_ids", "roles"}:
            raise ValueError("full_author_story_shape")
        with _author_item(["stories", index]):
            refs = _refs(story["used_evidence_ids"], catalog)
            if any(catalog[r].get("role") == "unlocated_context" for r in refs):
                raise ValueError("full_author_story_unlocated_clock_requires_resolution")
            if not isinstance(story["roles"], dict) or set(story["roles"]) != set(ROLE_KEYS):
                raise ValueError("full_author_story_roles")
            for role_refs in story["roles"].values():
                if not isinstance(role_refs, list) or not set(role_refs) <= set(refs):
                    raise ValueError("full_author_story_role_outside_evidence")
        point = point_inputs[story["point_ref"]]
        d2 = next(r for r in ranges if r["id"] == point["d2_ref"])
        dispositions = []
        for ref in refs:
            owner = primary_owners.setdefault(ref, story["story_ref"])
            dispositions.append(dict(evidence_id=ref,
                disposition="included_primary" if owner == story["story_ref"] else "moved_overlap_context",
                owner_packet_id=owner))
        packets.append(dict(story_packet_id=_text(story["story_ref"], "story_ref"),
            point_ref=point["id"], d2_ref=d2["id"], d1_ref=d2["parent_id"],
            evidence_ids=refs, primary_dispositions=dispositions))
        decisions.append(dict(story_packet_id=story["story_ref"], decision="select",
            title=story["title"], why_notable=story["why_notable"], used_evidence_ids=refs, roles=story["roles"]))
    selection = materialize_story_point_selection(response={"decisions": decisions}, packets=packets, evidence_ledger=catalog)
    story_catalog = {r:catalog[r] for r in primary_owners}
    audit = audit_primary_evidence_dispositions(packets=packets, evidence_ledger=story_catalog)
    shadow = dict(story_packets=packets, story_evidence_ledger=story_catalog,
                  story_point_selection=selection, story_primary_evidence_audit=audit)
    _, requirements = _approved_story_highlight_inputs(outline=outline, point_candidate_shadow=shadow)


    cut_catalog = {r:row for r,row in catalog.items() if row.get("role") != "unlocated_context"}
    for requirement in requirements.values():
        requirement["evidence_catalog"].update(cut_catalog)
        requirement["author_evidence_refs"] = list(cut_catalog)
    cut_evidence = {**evidence, "evidence_catalog":cut_catalog}
    revision = digest(dict(base_revision=base_revision, source_revision=source["sha256"],
                           outline=outline, summary=summary, selection=selection))
    result = build_editorial_highlight_set(broadcast_map=outline, authority_revision_id=revision,
        proposals=payload["proposals"], highlight_producer_run_ref=highlight_producer_run_ref or "full-author-302:" + digest(payload),
        source_duration_sec=duration_sec, story_requirements=requirements, composition_evidence=cut_evidence)
    outcomes = payload["group_outcomes"]
    flow_context = _bound_flow_context(source, base_revision, packet, broadcast_flow_context)
    flow_inputs = {row["ref"]: row for row in flow_context["inputs"]}
    flow_findings = _flow_outcome_findings(payload, catalog, flow_context)
    if flow_findings:
        error = ValueError(flow_findings[0]["error"])
        error.item_path = flow_findings[0]["path"]
        error.flow_findings = flow_findings
        raise error


    body_fields = ("id", "level", "parent_id", "title", "content", "start", "end")
    addressed = {ref for ref, item in flow_inputs.items()
                 if item["kind"] == "prior_range"
                 and any(all(row.get(key) == item["range"].get(key) for key in body_fields)
                         for row in ranges)}
    represented = addressed | {ref for row in outcomes if row["terminal_state"] == "represented"
                               for ref in row.get("flow_refs", [])}
    flow_pending = []
    for row in outcomes:
        state, flow_refs = row["terminal_state"], row.get("flow_refs", [])
        if flow_refs:

            addressed.update(flow_refs)
            if (state not in {"represented", "source_corrected", "no_broadcast_activity"}
                    or "broadcast" in flow_refs and state != "represented"):



                pending_refs = [ref for ref in flow_refs
                                if state not in {"no_editorial_value", "relation_broken"}
                                or ref not in represented]
                if pending_refs:
                    flow_pending.append({**row, "flow_refs": pending_refs})
    questions = payload["unresolved_questions"]
    for question in questions:
        text = question.get("question") if isinstance(question, dict) else question
        if not isinstance(text, str) or not text.strip():
            raise ValueError("full_author_questions_invalid")
    pending = [r for r in outcomes if r["terminal_state"] in {"source_unavailable", "context_unresolved"}]
    missing_flows = sorted(flow_inputs.keys() - addressed)
    if missing_flows:
        flow_pending.append(dict(terminal_state="context_unresolved", flow_refs=missing_flows,
            reason="The writer has not connected these inputs to the final broadcast text or a source-grounded correction.",
            evidence_refs=[]))
    pending.extend(row for row in flow_pending if row not in pending)
    source_clock = evidence["native_timing"].get("source_clock")
    result.update(composition_contract=CONTRACT, adoption_status="shadow_only", stable_saved_public_changed=False,
        unresolved_group_outcomes=pending, unresolved_proposals=result.get("rejected_proposals", []),
        completion_state=full_author_completion_state(
            unresolved_proposals=result.get("rejected_proposals"), source_clock=source_clock,
            unresolved_flows=flow_pending),
        full_author_projection=dict(base_revision=base_revision, revision=revision, outline=outline,
            summary=summary, story_shadow=shadow, unresolved_questions=questions,
            broadcast_flow_review=dict(context_sha256=digest(flow_context),
                outcomes=deepcopy([row for row in outcomes if row.get("flow_refs")]),
                unaddressed_flow_refs=missing_flows)))
    if source_clock:



        result.update(source_clock=source_clock, vod_seek_authorized=False, cut_export_authorized=False,
            pending_source_conditions=[*result.get("pending_source_conditions", []),
                "whole_broadcast_coverage_unconfirmed", "vod_mapping_unconfirmed"])
    return result

def validate_current_conversation_response(payload, *, job, source, packet, duration_sec,
                                           raw_sha256, source_reads):
    """Use the normal semantic projection with truthful native author provenance."""
    from .editorial_production_job import current_conversation_job_binding
    identity = current_conversation_job_binding(job)
    mode = identity["summary_author_mode"]
    assert_asset_fresh(AssetBinding(**source["asset"]))
    bound = next(row for row in identity["source_assets"] if row["family"] == "stt")
    if (source["asset"]["video_no"] != identity["video_no"]
            or source["asset"]["bytes_sha256"] != bound["sha256"]):
        raise ValueError("current_conversation_native_source_mismatch")
    result = validate_response(payload, base_revision=identity["base_revision"], source=source,
        packet=packet, duration_sec=duration_sec,
        highlight_producer_run_ref="current-conversation:" + digest([identity, raw_sha256]))
    result["full_author_receipt"] = dict(profile=PROFILE,
        selected_mode=mode, actual_provider=mode,
        actual_model=identity["author"]["actual_model"], author=deepcopy(identity["author"]),
        job_id=identity["job_id"], job_identity_sha256=digest(identity),
        source_revision=source["sha256"], native_source_sha256=bound["sha256"],
        raw_response_sha256=raw_sha256, source_reads=deepcopy(source_reads),
        source_rows_understood="UNVERIFIED", audio_listened=False,
        pixels_seen="UNVERIFIED" if packet.get("_delivered_media_frames") else False,
        pixels_delivered=False, returned_frame_refs=sorted(packet.get("_delivered_media_frames", {})),
        call_count=0, external_transport_calls=0,
        existing_response_relabelled=False)
    result["full_author_projection"]["story_shadow"]["story_provider_meta"] = dict(
        actual_model=identity["author"]["actual_model"], selected_provider=mode)
    return result
