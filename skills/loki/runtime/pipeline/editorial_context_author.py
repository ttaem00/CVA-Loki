"""Opt-in V22 composition in the existing T4 owner and normal gateway.

Raw/locator receipts stay on disk. Request-local short refs are one-to-one
projections, never replacements for source identity or semantic approvals.
"""

from __future__ import annotations

import json

from .editorial_source_timing import native_author_rows

from .evidence_world.contracts import digest

from .evidence_world.signal_view import LOCAL_REACTION_PROFILE, OBSERVED_DENSITY_PROFILE

CONTRACT = "editorial.context-author.v22"

SIGNAL_PRIORITY_PROFILE = 'weighted_signals_v1'

def _signal_priority_context(packet, view):
    navigation = packet.get('_signal_priority_navigation') or view.get('navigation_context') or {}
    page = (navigation.get('signal_orientation') or {}).get('first_page') or {}
    if page.get('profile') in (LOCAL_REACTION_PROFILE, OBSERVED_DENSITY_PROFILE):
        return _signal_priority_context_v2(packet, view)
    return _signal_priority_context_v1(packet, view)

def _signal_priority_context_v1(packet, view):
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

def _signal_priority_context_v2(packet, view):
    """Project admitted VOD hints; never read originals or change their ranks."""
    import math
    from html import escape
    from .chat_analyzer import WINDOW_SEC
    from .editorial_signal_scoring import DEFAULT_WEIGHTS, MODEL_VERSION, score_candidates

    if packet.get('signal_priority_profile') != SIGNAL_PRIORITY_PROFILE:
        raise ValueError('unknown_signal_priority_profile')
    projected_navigation = packet.get('_signal_priority_navigation')
    navigation = projected_navigation or view.get('navigation_context') or {}
    navigation_path = ('author_source_context.signal_orientation' if projected_navigation
        else 'source_material.navigation_context.signal_orientation.first_page')
    page = (navigation.get('signal_orientation') or {}).get('first_page') or {}
    states = page.get('family_status') or {}
    if navigation and (not navigation.get('world_revision')
            or navigation.get('source_id') != view['source']['source_id']
            or page.get('source_id') != view['source']['source_id']
            or page.get('world_revision') != navigation.get('world_revision')):
        raise ValueError('signal_priority_source_binding')
    scoped = (projected_navigation or {}).get('scoped_page') or {}
    if scoped.get('schema_version') != 'ew.signal_page.v1' or scoped.get('profile') != page.get('profile'):
        scoped = {}
    scoped_binding_mismatch = bool(scoped and (scoped.get('source_id') != page['source_id']
                                    or scoped.get('world_revision') != page['world_revision']))
    if scoped_binding_mismatch:
        scoped = {}
    scoped_path = 'author_source_context.tool_result'

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

    scope = scoped.get('interval_sec') or []
    scope = span(*scope) if len(scope) == 2 else None
    scoped_states = scoped.get('family_status') or {}
    scoped_attention = scoped.get('peak_attention') or {}
    observations, match_intervals = {}, {}
    clips, anchors, candidates, paths = [], [], [], {}
    chat_fields = dict(chat_density='z_count', laughter='laughter_count',
                       question='question_count', surprise='surprise_count')
    duration = number((page.get('chat_baseline') or {}).get('duration_sec'))

    def offer(identifier, interval, path, *, chat_windows=None):
        candidates.append(dict(id=identifier, start_sec=interval[0], end_sec=interval[1], features={}))
        paths[identifier] = path
        match_intervals[identifier] = [interval] if chat_windows is None else chat_windows

    def observed(interval, stats, path):
        interval = span(*interval) if isinstance(interval, (list, tuple)) and len(interval) == 2 else None
        if (interval is None or interval[0] % WINDOW_SEC or interval[0] == interval[1]
                or number(stats.get('count')) is None or stats['count'] <= 0):
            return None
        end = interval[0] + WINDOW_SEC
        if interval[1] != (min(end, duration) if duration is not None else end):
            return None
        features = observations.setdefault(interval, {})
        for feature, key in chat_fields.items():
            value = number(stats.get(key))
            if value is None or (feature != 'chat_density' and not 0 <= value <= stats['count']):
                continue
            strength = max(0.0, value) if feature == 'chat_density' else value / WINDOW_SEC
            features.setdefault(feature, (strength, path))
        return interval

    def observe_members(stats, path):


        members = stats.get('peak_members') or [stats, *(stats.get('family_peaks') or {}).values()]
        windows = []
        for member in members:
            start = number(member.get('sec'))
            if start is not None:
                end = min(start + WINDOW_SEC, duration) if duration is not None else start + WINDOW_SEC
                window = observed((start, end), member, path)
                if window is not None:
                    windows.append(window)
        return windows

    def observe_local_peaks(attention, prefix, path, *, scope=None):
        for index, row in enumerate(attention.get('local_peaks') or []):
            interval = row.get('interval_sec') or []
            interval = span(*interval) if len(interval) == 2 else None
            if interval is None or (scope is not None and not overlaps(interval, scope)):
                continue
            location = f'{path}.peak_attention.local_peaks[{index}]'
            window = observed(interval, row.get('counts') or {}, location)
            if window is not None and window not in local_windows:
                offer(f'{prefix}-{index}', window, location)
                local_windows.add(window)

    # The first page is a frozen overview, not a licence to join a live clock.
    vod_hints = bool(navigation and not view['source'].get('source_clock'))
    local_windows = set()
    if vod_hints and states.get('chat') in {'present', 'partial'}:
        observe_local_peaks(page.get('peak_attention') or {}, 'chat-local', navigation_path)
        proposals = packet.get('support_proposals') or []
        for index, row in enumerate(proposals):
            path = f'source_material.discovery_signals[{index}]'
            windows = observe_members(row, path)
            interval = span(row.get('start_sec'), row.get('end_sec'))
            if not local_windows and interval is not None:
                offer(f'chat-{index}', interval, path, chat_windows=windows)
        for index, row in enumerate(page.get('hints') or []):
            if row.get('family') != 'chat':
                continue
            path = f'{navigation_path}.hints[{index}]'
            if row.get('kind') == 'chat_reaction' and row.get('clock_domain') == 'vod_relative_ms':
                observed(row.get('interval_sec'), row.get('stats') or {}, path)
            elif row.get('kind') == 'chat_peak_cluster' and row.get('clock_domain') == 'vod_relative_ms':
                windows = observe_members(row.get('stats') or {}, path)
                interval = row.get('interval_sec') or []
                interval = span(*interval) if len(interval) == 2 else None
                if not local_windows and not proposals and interval is not None:
                    offer(f'chat-hint-{index}', interval, path, chat_windows=windows)
    if vod_hints and scope is not None and scoped_states.get('chat') in {'present', 'partial'}:
        observe_local_peaks(scoped_attention, 'chat-scoped', scoped_path, scope=scope)
        for index, row in enumerate(scoped.get('hints') or []):
            if row.get('kind') == 'chat_reaction' and row.get('clock_domain') == 'vod_relative_ms':
                interval = row.get('interval_sec') or []
                interval = span(*interval) if len(interval) == 2 else None
                if interval is not None and overlaps(interval, scope):
                    observed(interval, row.get('stats') or {}, f'{scoped_path}.hints[{index}]')
    attention = page.get('clip_attention') or {}
    cue_columns = attention.get('clip_cue_columns') or []
    if vod_hints and states.get('clip') in {'present', 'partial'}:
        for index, row in enumerate(attention.get('regions') or []):
            interval = row.get('interval_sec') or []
            interval = span(*interval) if len(interval) == 2 else None
            count = number(row.get('clip_count'))
            if interval is not None and interval[1] > interval[0] and count is not None and count >= 0:
                identifier = f'clip-{index}'
                locations = {}
                for cue in row.get('clip_cue_rows') or []:
                    fields = dict(zip(cue_columns, cue))
                    ref = fields.get('hint_ref')
                    offset = number(fields.get('offset_sec'))
                    length = number(fields.get('duration'))
                    if isinstance(ref, str) and ref and offset is not None and length is not None and length >= 0:
                        location = span(offset, offset + length)
                        if (location is not None and (duration is None or location[1] <= duration)
                                and overlaps(interval, location)):
                            locations.setdefault(ref, (max(interval[0], location[0]), min(interval[1], location[1])))
                clips.append((identifier, interval, count, locations))
                offer(identifier, interval, f'{navigation_path}.clip_attention.regions[{index}]',
                      chat_windows=locations.values())
    if vod_hints and scope is not None and scoped_states.get('clip') in {'present', 'partial'}:
        located_refs = {ref for _, _, _, locations in clips for ref in locations}
        cues = scoped_attention.get('clip_cues') or {}
        for row in scoped_attention.get('local_peaks') or []:
            bounds = row.get('interval_sec') or []
            bucket = span(*bounds) if len(bounds) == 2 else None
            if bucket is None or bucket[1] <= bucket[0] or not overlaps(bucket, scope):
                continue
            for ref in row.get('clip_hint_refs') or []:
                bounds = (cues.get(ref) or {}).get('interval_sec') or []
                location = span(*bounds) if len(bounds) == 2 else None
                if (ref not in located_refs and location is not None and overlaps(bucket, location)
                        and overlaps(scope, location) and (duration is None or location[1] <= duration)):
                    identifier = f'clip-scoped-{len(located_refs)}'
                    encoded_ref = json.dumps(ref, ensure_ascii=False)
                    clips.append((identifier, bucket, 1, {ref: location}))
                    offer(identifier, location, f'{scoped_path}.peak_attention.clip_cues[{encoded_ref}]')
                    located_refs.add(ref)
    supplemental = view.get('supplemental_sources') or {}
    comments = supplemental.get('timeline_comment_context') or {}
    comments_known = vod_hints and comments.get('source_status') in {'ok', 'no_comments'}
    if comments_known:
        for index, row in enumerate(comments.get('anchors') or []):
            time = number(row.get('time_sec'))
            if time is not None and time >= 0:
                anchors.append(time)
                offer(f'comment-{index}', (time, time), f'source_material.supplemental_sources.timeline_comment_context.anchors[{index}]')
    if vod_hints and duration is not None and supplemental.get('manager_context'):
        from .timeline_llm_provider import extract_context_comment_timeline_items, hms_to_sec
        memo_times = dict.fromkeys(hms_to_sec(row['tc'])
                                  for row in extract_context_comment_timeline_items(supplemental['manager_context']))
        for index, time in enumerate(memo_times):
            if 0 <= time < duration:
                offer(f'memo-{index}', (time, time), 'source_material.supplemental_sources.manager_context')

    chat_sources = {}
    for candidate in candidates:
        interval = (candidate['start_sec'], candidate['end_sec'])
        features = candidate['features']
        related = [values for bucket, values in observations.items()
                   if any(overlaps(target, bucket) for target in match_intervals[candidate['id']])]
        used_paths = set()
        for feature in chat_fields:
            values = [row[feature] for row in related if feature in row]
            if values:
                value, path = max(values, key=lambda item: item[0])
                features[feature] = value
                used_paths.add(path)
        if used_paths:
            chat_sources[candidate['id']] = sorted(used_paths)
        clip_rates = []
        for identifier, other, count, locations in clips:
            observed_count = count if identifier == candidate['id'] else sum(
                overlaps(interval, location) for location in locations.values())
            if observed_count:
                clip_rates.append(observed_count * 60 / (other[1] - other[0]))
        features['clips'] = max(clip_rates) if clip_rates else 0.0 if states.get('clip') == 'absent_confirmed' else None
        features['comments'] = (sum(overlaps(interval, (time, time)) for time in anchors)
            / max(1.0, (interval[1] - interval[0]) / 60)) if comments_known else None

        features['viewers'] = None

    scored = score_candidates(candidates)
    units = dict(chat_density='positive command-filtered density z',
        laughter='filtered messages/second in an overlapping observed 10s bucket',
        question='filtered messages/second in an overlapping observed 10s bucket',
        surprise='filtered messages/second in an overlapping observed 10s bucket',
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
        projected = dict(id=row['id'], start_sec=row['start_sec'], end_sec=row['end_sec'],
            score=None if row['score'] is None else round(row['score'], 1), coverage=row['coverage'],
            source_path=paths[row['id']], summary_html='<strong>' + escape(text) + '</strong>')
        if row['id'] in chat_sources:
            projected['chat_source_paths'] = chat_sources[row['id']]
        rows.append(projected)
    shared = dict(profile=SIGNAL_PRIORITY_PROFILE, model_version=MODEL_VERSION,
        status='ready' if rows else 'empty' if vod_hints else 'source_unavailable',
        source_id=view['source']['source_id'], source_revision=view['source']['source_revision'],
        world_revision=navigation.get('world_revision'), original_read=False, meaning_complete=False,
        scope='Existing delivered source hints only; partial overview remains partial. '
              'Observed support, not editorial value/probability. Missing weight is not redistributed. '
              'Do not exclude quiet information, reorder original hints, or treat this as final selection.')
    trace = {**shared, 'weights': dict(DEFAULT_WEIGHTS), 'feature_units': units, 'candidates': scored}
    if scoped_binding_mismatch:
        trace['scoped_page_omission'] = 'source_binding_mismatch'
    return {**shared, 'rows': rows}, trace

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
