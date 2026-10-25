"""Opt-in V22 composition in the existing T4 owner and normal gateway.

Raw/locator receipts stay on disk. Request-local short refs are one-to-one
projections, never replacements for source identity or semantic approvals.
"""

from __future__ import annotations

from .editorial_source_timing import native_author_rows

from .evidence_world.contracts import digest

CONTRACT = "editorial.context-author.v22"

def context_source(*, source, packet):
    if not source:
        raise ValueError("context_author_requires_original_source")
    catalog, rows = {}, []
    for row in native_author_rows(source):
        ref = "e" + str(row["ordinal"] + 1)
        if ref in catalog:
            raise ValueError("ambiguous_context_source_ref")
        rows.append([ref, row["time_sec"], row["end_sec"], row["text"]])
        catalog[ref] = dict(evidence_id=ref, source_type="stt", role="primary_semantic",
            time_sec=row["time_sec"], source_end_sec=row["end_sec"], source_text=row["text"],
            native_start_sec=row["time_sec"], native_end_sec=row["end_sec"], native_ref=row["ref"],
            exact_source_ref=row["ref"], source_revision=source["asset"]["bytes_sha256"])
    timing = dict(contract=source["contract"], source_id=source["asset"]["source_id"],
        source_revision=source["asset"]["bytes_sha256"], receipt_sha256=source["sha256"],
        clock_authority="observed subtitle cues; audio and pixels not verified")
    from .editorial_source_timing import bound_live_source_clock
    live_clock = bound_live_source_clock(source)
    if live_clock:
        timing.update(source_clock=live_clock, clock_authority=live_clock["policy"], playable=False)
    for row in packet.get("chat_rows") or []:
        ref = row["ref"]
        if ref in catalog:
            raise ValueError("ambiguous_context_source_ref")
        catalog[ref] = dict(evidence_id=ref, source_type="replay_chat", role="primary_semantic",
            time_sec=row["time_sec"], source_text=row["text"], exact_source_ref=ref,
            source_revision=packet.get("chat_source_revision") or digest(packet.get("chat_rows")))
    unlocated = [[r["ordinal"], r.get("raw_text"), r.get("uncertainties", [])]
        for r in source["records"] if not r.get("time_index")]
    payload = dict(source=timing, stt_columns=["ref", "start_sec", "end_sec", "original_text"],
        stt=rows, unlocated_stt=unlocated, chat=packet.get("chat_rows") or [],
        discovery_signals=packet.get("support_proposals") or [],


        availability={"stt": "preserved original cues supplied in this request, including unlocated rows; whole-broadcast coverage is not established by this packet",
            "chat": "sampled" if packet.get("chat_rows") else "not_in_input",
            "pixels": "not_in_input", "audio": "not_listened"},
        navigation_context=packet.get("navigation_source_reads"))
    if live_clock:
        payload["availability"]["stt"] = "selected original live cues only; whole-broadcast coverage unconfirmed"
    if packet.get("live_manager_context") is not None:
        from .live_editorial_source import validate_live_manager_context_projection
        payload["live_manager_context"] = validate_live_manager_context_projection(
            packet["live_manager_context"], source_clock=packet.get("live_manager_context_clock") or live_clock)
        payload["live_manager_context_clock"] = packet.get("live_manager_context_clock") or live_clock
        payload["availability"]["live_manager_context"] = "included_untrusted_context_not_primary_evidence"
    if packet.get("supplemental_sources") is not None:
        from .editorial_supplemental_sources import validate_supplemental_context
        supplemental = validate_supplemental_context(packet["supplemental_sources"],
            video_no=source["asset"]["video_no"])
        payload["supplemental_sources"] = supplemental
        payload["availability"]["manager_context"] = "request_included_untrusted_naming_context"
        payload["availability"]["timeline_comments"] = (
            "request_included_untrusted_context" if supplemental["timeline_comment_context"].get("anchors")
            else "no_anchors_in_bound_source")
    if (payload['navigation_context'] or {}).get('signal_orientation'):
        from .evidence_world.signal_view import request_view
        if payload['navigation_context']['source_id'] != source['asset']['source_id']:
            raise ValueError('context_signal_source_mismatch')
        payload['navigation_context'] = {**payload['navigation_context'],
            'signal_orientation': request_view(payload['navigation_context']['signal_orientation'])}
        payload['discovery_signals'] = [*payload['discovery_signals'], dict(
            kind='source_signal_orientation', same_request_path='navigation_context.signal_orientation',
            policy='Inspect the supplied derived hints, then originals. These hints do not approve value, meaning, heard audio or clip matches.')]
    return payload, dict(contract=CONTRACT, native_timing=timing, evidence_catalog=catalog,
                         independent_source_links=True)
