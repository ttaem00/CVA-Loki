"""Opt-in V22 composition in the existing T4 owner and normal gateway.

Raw/locator receipts stay on disk. Request-local short refs are one-to-one
projections, never replacements for source identity or semantic approvals.
"""

from __future__ import annotations

from .editorial_source_timing import native_author_rows

from .evidence_world.contracts import digest

CONTRACT = "editorial.context-author.v22"

SIGNAL_PRIORITY_PROFILE = 'weighted_signals_v1'

def _signal_priority_context(packet, view):
    """Project admitted VOD hints; never read originals or change their ranks."""
    import math
    from html import escape
    from .editorial_signal_scoring import DEFAULT_WEIGHTS, MODEL_VERSION, score_candidates

    if packet.get('signal_priority_profile') != SIGNAL_PRIORITY_PROFILE:
        raise ValueError('unknown_signal_priority_profile')
    projected_navigation = packet.get('_signal_priority_navigation')
    navigation = projected_navigation or view.get('navigation_context') or {}
    navigation_path = ('author_source_context.signal_orientation' if projected_navigation
        else 'source_material.navigation_context.signal_orientation.first_page')
    page = (navigation.get('signal_orientation') or {}).get('first_page') or {}
    states = page.get('family_status') or {}
    if navigation and (navigation.get('source_id') != view['source']['source_id']
            or page.get('source_id') != view['source']['source_id']
            or page.get('world_revision') != navigation.get('world_revision')):
        raise ValueError('signal_priority_source_binding')

    def number(value):
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
        return None

    def span(a, b):
        a, b = number(a), number(b)
        return (a, b) if a is not None and b is not None and 0 <= a <= b else None

    def overlaps(a, b):
        if a[0] == a[1]:
            return a[0] == b[0] if b[0] == b[1] else b[0] <= a[0] < b[1]
        if b[0] == b[1]:
            return a[0] <= b[0] < a[1]
        return max(a[0], b[0]) < min(a[1], b[1])

    peaks, clips, anchors, candidates, paths = [], [], [], [], {}

    def offer(identifier, interval, path):
        candidates.append(dict(id=identifier, start_sec=interval[0], end_sec=interval[1], features={}))
        paths[identifier] = path

    # The first page is a frozen overview, not a licence to join a live clock.
    vod_hints = bool(navigation and not view['source'].get('source_clock'))
    if vod_hints and states.get('chat') in {'present', 'partial'}:
        for index, row in enumerate(packet.get('support_proposals') or []):
            interval = span(row.get('start_sec'), row.get('end_sec'))
            if interval is not None:
                peaks.append((interval, row))
                offer(f'chat-{index}', interval, f'source_material.discovery_signals[{index}]')


        if not peaks:
            for index, row in enumerate(page.get('hints') or []):
                if row.get('kind') != 'chat_peak_cluster' or row.get('family') != 'chat':
                    continue
                interval = row.get('interval_sec') or []
                interval = span(*interval) if len(interval) == 2 else None
                if interval is not None:
                    peaks.append((interval, row.get('stats') or {}))
                    offer(f'chat-hint-{index}', interval, f'{navigation_path}.hints[{index}]')
    attention = page.get('clip_attention') or {}
    if vod_hints and states.get('clip') in {'present', 'partial'}:
        for index, row in enumerate(attention.get('regions') or []):
            interval = row.get('interval_sec') or []
            interval = span(*interval) if len(interval) == 2 else None
            count = number(row.get('clip_count'))
            if interval is not None and interval[1] > interval[0] and count is not None and count >= 0:
                clips.append((interval, count))
                offer(f'clip-{index}', interval, f'{navigation_path}.clip_attention.regions[{index}]')
    comments = (view.get('supplemental_sources') or {}).get('timeline_comment_context') or {}
    comments_known = vod_hints and comments.get('source_status') in {'ok', 'no_comments'}
    if comments_known:
        for index, row in enumerate(comments.get('anchors') or []):
            time = number(row.get('time_sec'))
            if time is not None and time >= 0:
                anchors.append(time)
                offer(f'comment-{index}', (time, time), f'source_material.supplemental_sources.timeline_comment_context.anchors[{index}]')

    clip_complete = (states.get('clip') == 'absent_confirmed' or
        (states.get('clip') == 'present' and attention.get('total_regions') == len(clips)))
    for candidate in candidates:
        interval = (candidate['start_sec'], candidate['end_sec'])
        features = candidate['features']
        related = [row for other, row in peaks if overlaps(interval, other)]
        density = [number(row.get('z_count')) for row in related]
        density = [max(0.0, value) for value in density if value is not None]
        features['chat_density'] = max(density) if density else None
        for family, feature in (('laughter', 'laughter'), ('question', 'question'), ('surprise', 'surprise')):
            rates = []
            for row in related:
                count = number(row.get(f'{family}_count_sum'))
                windows = row.get('peak_seconds') or []
                if count is not None and windows:
                    rates.append(count / (10 * len(windows)))
                else:
                    count = number(row.get(f'{family}_count'))
                    if count is not None:
                        rates.append(count / 10)
            features[feature] = max(rates) if rates else None
        clip_rates = [count * 60 / (other[1] - other[0]) for other, count in clips
                      if overlaps(interval, other)]
        features['clips'] = max(clip_rates) if clip_rates else 0.0 if clip_complete else None
        features['comments'] = (sum(overlaps(interval, (time, time)) for time in anchors)
            / max(1.0, (interval[1] - interval[0]) / 60)) if comments_known else None

        features['viewers'] = None

    scored = score_candidates(candidates)
    units = dict(chat_density='positive command-filtered density z',
        laughter='filtered messages/second over detected 10s peak windows',
        question='filtered messages/second over detected 10s peak windows',
        surprise='filtered messages/second over detected 10s peak windows',
        clips='located clip entries/minute in their original region',
        comments='selected timed anchors/minute, minimum reference duration 1 minute',
        viewers='unavailable: viewer VOD clock not admitted')
    labels = dict(chat_density='밀도 양의z', laughter='웃음/초', question='의문 표현/초',
                  surprise='놀람/초', clips='클립/분', comments='댓글앵커/기준분')
    rows = []
    for row in scored:
        value = '미산정' if row['score'] is None else f"{row['score']:.1f}/100"
        observed = ' · '.join(f'{labels[name]} {raw:.2f}' for name, raw in row['raw_features'].items()
                              if raw is not None and name in labels)
        text = f'탐색 신호 {value}' + (' · ' + observed if observed else '')
        rows.append(dict(id=row['id'], start_sec=row['start_sec'], end_sec=row['end_sec'],
            score=None if row['score'] is None else round(row['score'], 1), coverage=row['coverage'],
            source_path=paths[row['id']], summary_html='<strong>' + escape(text) + '</strong>'))
    shared = dict(profile=SIGNAL_PRIORITY_PROFILE, model_version=MODEL_VERSION,
        status='ready' if rows else 'empty' if vod_hints else 'source_unavailable',
        source_id=view['source']['source_id'], source_revision=view['source']['source_revision'],
        world_revision=navigation.get('world_revision'), original_read=False, meaning_complete=False,
        scope='Existing delivered source hints only; partial overview remains partial. '
              'Observed support, not editorial value/probability. Missing weight is not redistributed. '
              'Do not exclude quiet information, reorder original hints, or treat this as final selection.')
    return ({**shared, 'rows': rows}, {**shared, 'weights': dict(DEFAULT_WEIGHTS),
        'feature_units': units, 'candidates': scored})

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
    evidence = dict(contract=CONTRACT, native_timing=timing, evidence_catalog=catalog,
                    independent_source_links=True)
    if packet.get('signal_priority_profile') is not None:
        payload['signal_priority'], evidence['signal_priority_trace'] = _signal_priority_context(packet, payload)
    return payload, evidence
