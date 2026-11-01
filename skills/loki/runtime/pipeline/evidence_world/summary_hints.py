"""Optional fresh Loom hints, mapped back to immutable original records.

The existing producer validates publications/revisions. Hints never supply a
VOD clock, read completion, coverage completion, or generation authority.
"""

from __future__ import annotations

import json

import os

from pathlib import Path

import re

import shutil

import sqlite3

import subprocess

import sys

from .contracts import EvidenceError, canonical_json, digest, native_path

from .index import atomic_json

from .temporal_source import resolve_session

PROFILE = 'loom_summary_hints_v1'

_HISTORICAL_UNCERTAINTY = ('Historical model interpretation, not current evidence, word meaning, '
    'permanent channel traits or permission. Preserve negation, quotes and game context. '
    'Omitted material remains unknown.')

def vod_hint_configuration(srt_path, video_no, duration_sec):
    root = os.environ.get('CHZZ_LIVE_RUNTIME_ROOT')
    if not root:
        return None
    from ..collected_vod_media import collected_clock
    if collected_clock(srt_path, video_no, duration_sec) is None:
        return None
    return dict(profile=PROFILE, generation_root=root,
        **(dict(cold_archive_root=os.environ['CHZZ_COLD_ARCHIVE_ROOT'])
           if os.environ.get('CHZZ_COLD_ARCHIVE_ROOT') else {}))

def _context_as_of_ms(service):
    """Bound historical background to this immutable world's original clocks."""
    clock = getattr(service, 'summary_hints_config', {}).get('primary_clock')
    with service.open() as db:
        if clock:
            row = db.execute("SELECT max(json_extract(payload_json,'$.time_index.end_us')) "
                "FROM records WHERE family='stt'").fetchone()
            end = row[0] if row else None
            start = clock.get('origin_ms', clock.get('broadcast_start_at_ms'))
            value = start + end // 1000 if type(start) is int and type(end) is int else None
        else:
            row = db.execute("SELECT max(coalesce(json_extract(payload_json,'$.raw_time.end'), "
                "json_extract(payload_json,'$.raw_time.start'))) FROM records "
                "WHERE json_extract(payload_json,'$.raw_time.unit')='utc_epoch_ms'").fetchone()
            value = row[0] if row else None
    return value if type(value) is int and 0 < value <= 2**53 - 1 else None

def _historical_context(service, value):
    config = getattr(service, 'summary_hints_config', {})
    context = value.get('historical_context')
    unavailable = (isinstance(context, dict) and set(context) == {'status','reason'}
                   and context.get('status') == 'unavailable')
    return historical_context_projection(value, producer_source_id=config.get('producer_source_id'),
        channel_uid=config.get('channel_uid'),
        as_of_ms=None if context is None or unavailable else _context_as_of_ms(service))

def historical_context_projection(value, *, producer_source_id, channel_uid, as_of_ms):
    """Validate background independently; it never creates current-world cards."""
    base = dict(raw_read=False, coverage_complete=False, meaning_complete=False,
        vod_seek_verified=False, primary_evidence=False, data_is_untrusted=True,
        policy='Historical model interpretation for background only, not channel permission or current-event evidence. '
               'Ready means the background projection is available; read original evidence independently. '
               'Partial and rechecked historical rows do not establish a fresh ready snapshot.')
    unavailable = lambda reason: {**base, 'status':'unavailable', 'reason':reason}
    context = value.get('historical_context')
    if context is None:
        return unavailable('historical_context_not_provided')
    if isinstance(context, dict) and set(context) == {'status', 'reason'} and context['status'] == 'unavailable':
        reason = context['reason']
        return unavailable(reason if isinstance(reason, str) and re.fullmatch('[a-z][a-z0-9_]{1,100}', reason)
                           else 'historical_context_unavailable')
    expected_source, as_of = producer_source_id, as_of_ms
    positive = lambda number: type(number) is int and 0 < number <= 2**53 - 1
    sha = lambda text: isinstance(text, str) and re.fullmatch('[a-f0-9]{64}', text)
    try:
        if (not isinstance(context, dict) or set(context) != {'schemaVersion','channelId','broadcastId',
                'asOfMs','preparedAtMs','status','scopes','omitted','unavailable','dataIsUntrusted',
                'identityMatching','automaticSanctions','digest'} or type(context.get('schemaVersion')) is not int
                or context['schemaVersion'] != 1
                or context.get('channelId') != channel_uid
                or context.get('broadcastId') != expected_source or context.get('asOfMs') != as_of
                or not positive(as_of) or not positive(context.get('preparedAtMs'))
                or context['preparedAtMs'] < as_of or context.get('status') not in {'ready','partial','empty'}
                or context.get('dataIsUntrusted') is not True or context.get('identityMatching') is not False
                or context.get('automaticSanctions') is not False
                or not sha(context.get('digest')) or type(context.get('omitted')) is not int
                or context['omitted'] < 0 or not isinstance(context.get('unavailable'), list)
                or len(context['unavailable']) > 8):
            raise ValueError('historical_context_invalid')
        encoded = canonical_json(context)
        if len(encoded.encode('utf-16-le')) > 24000:
            raise ValueError('historical_context_unbounded')
        if digest({key:item for key,item in context.items() if key != 'digest'}) != context['digest']:
            raise ValueError('historical_context_digest_mismatch')
        scopes = context['scopes']
        if not isinstance(scopes, dict) or set(scopes) != {'time','broadcast','channel'}:
            raise ValueError('historical_context_scope_invalid')
        count = 0
        for scope, group in scopes.items():
            if not isinstance(group, dict) or set(group) != {'items'} or not isinstance(group['items'], list):
                raise ValueError('historical_context_scope_invalid')
            for item in group['items']:
                count += 1
                archive = item['archiveId']
                window = item['window']
                if (not isinstance(archive, str) or not re.fullmatch('[a-f0-9]{32}-[0-9]{1,16}', archive)
                        or archive[:32] != context['channelId']
                        or (scope != 'channel' and 'chzzk:' + archive.replace('-', ':', 1) != expected_source)
                        or item.get('kind') not in {'topic','reaction','current','previous_broadcast_end'}
                        or not isinstance(item.get('text'), str) or not item['text'].strip() or len(item['text']) > 600
                        or not positive(item.get('reportId')) or not positive(item.get('revision'))
                        or not positive(window.get('startMs')) or not positive(window.get('endMs'))
                        or not window['startMs'] < window['endMs'] <= as_of
                        or item.get('evidenceState') not in {'source_bound_saved_snapshot','historical_source_rechecked_model_observation'}
                        or not isinstance(item.get('coverageState'), str) or not re.fullmatch('[a-z][a-z0-9_]{0,80}', item['coverageState'])
                        or item.get('interpretation') != 'model_interpreted'
                        or item.get('scope') != 'historical_broadcast_observation_not_channel_rule'
                        or not sha(item.get('sourceToken')) or not isinstance(item.get('sourceRefs'), list)
                        or not 1 <= len(item['sourceRefs']) <= 16):
                    raise ValueError('historical_context_item_invalid')
                for ref in item['sourceRefs']:
                    if (ref.get('sourceKind') not in {'chat','transcript'} or not positive(ref.get('sourceRowId'))
                            or not positive(ref.get('eventAtMs')) or ref['eventAtMs'] > as_of
                            or not isinstance(ref.get('quote'), str) or not ref['quote'].strip()
                            or len(ref['quote']) > 240 or not isinstance(ref.get('context',ref['quote']), str)
                            or ref['quote'] not in ref.get('context',ref['quote']) or not sha(ref.get('contentSha256'))):
                        raise ValueError('historical_context_source_invalid')
                    if ref['sourceKind'] == 'transcript':
                        if not positive(ref.get('endMs')) or not ref['eventAtMs'] < ref['endMs'] <= window['endMs']:
                            raise ValueError('historical_context_source_clock_invalid')
                    elif not window['startMs'] <= ref['eventAtMs'] < window['endMs']:
                        raise ValueError('historical_context_source_clock_invalid')
        if count > 12 or context['status'] == 'empty' and count or context['status'] == 'ready' and not count:
            raise ValueError('historical_context_count_invalid')
        projected = value['historical_context_for_input']
        if (not isinstance(projected, dict) or set(projected) != {'projection','sourceContextDigest',
                'channelId','asOfMs','scopes','omitted','uncertainty'}
                or projected['projection'] != 'bounded_historical_observations_v1'
                or projected['sourceContextDigest'] != context['digest']
                or projected['channelId'] != context['channelId'] or projected['asOfMs'] != as_of
                or not isinstance(projected['scopes'], dict) or set(projected['scopes']) != set(scopes)
                or type(projected['omitted']) is not int
                or projected['uncertainty'] != _HISTORICAL_UNCERTAINTY
                or len(canonical_json(projected).encode('utf-16-le')) > 3200):
            raise ValueError('historical_context_projection_invalid')
        projected_count = 0
        keys = ('kind','text','archiveId','window','evidenceState','coverageState')
        for scope, items in projected['scopes'].items():
            if not isinstance(items, list):
                raise ValueError('historical_context_projection_invalid')
            available = [{**{key:item[key] for key in keys},
                'sourceQuotes':list(dict.fromkeys(ref.get('context',ref['quote']) for ref in item['sourceRefs']))}
                for item in scopes[scope]['items']]
            for item in items:
                if item not in available:
                    raise ValueError('historical_context_projection_mismatch')
                available.remove(item)
                projected_count += 1
        if projected['omitted'] != context['omitted'] + count - projected_count:
            raise ValueError('historical_context_projection_omissions_mismatch')
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, RecursionError):
        return unavailable('historical_context_validation_failed')
    return {**base, 'status':context['status'], 'background':projected,
            'source_digest':context['digest'], 'clock_domain':'utc_epoch_ms'}

def configure(service, *, generation_root, node_executable=None, cold_archive_root=None, finalized_vod=None):
    if finalized_vod is not None:


        service.summary_hints_config = dict(generation_root=str(generation_root),
            node_executable=node_executable or shutil.which('node'),
            cold_archive_root=None if cold_archive_root is None else str(cold_archive_root),
            finalized_vod=finalized_vod)
        return
    match = re.fullmatch(r'chzzk:([a-f0-9]{32}):(\d+)', service.source_id)
    primary_clock = None
    if not match:
        vod = re.fullmatch(r'chzzk:(\d+)', service.source_id)
        live = re.fullmatch(r'chzzk-live:([a-f0-9]{32})-(\d+)', service.source_id)
        stt = next((a for a in service.assets.values() if a.family == 'stt'), None)
        if vod and stt and service.duration is not None:
            from ..collected_vod_media import collected_clock
            clock = collected_clock(stt.source_locator['path'], vod[1], service.duration)
            if clock is None:
                raise EvidenceError('loom_hint_verified_vod_clock_required')
            match = re.fullmatch(r'([a-f0-9]{32})-(\d+)', clock['archive'])
            primary_clock = dict(kind='vod', broadcast_start_at_ms=clock['mapping']['originMs'],
                duration_sec=service.duration, proof_sha256=clock['proof_sha256'])
        elif not live or not stt or stt.clock_authority.get('kind') != 'live':
            raise EvidenceError('loom_hints_require_native_live_world')
        else:
            from ..live_editorial_source import load_live_source_clock, clock_summary
            primary_clock = clock_summary(load_live_source_clock(stt.source_locator['path'],
                video_no=stt.video_no, duration_sec=service.duration))
            if not primary_clock or primary_clock['source_id'] != service.source_id:
                raise EvidenceError('loom_hint_primary_clock_mismatch')
            match = live
    elif service.duration is not None:
        raise EvidenceError('loom_hints_require_native_live_world')
    executable = node_executable or shutil.which('node')
    if not executable:
        raise EvidenceError('loom_hints_node_unavailable')
    if cold_archive_root is not None and not Path(cold_archive_root).is_absolute():
        raise EvidenceError('storage_root_absolute_required')
    root, _, _ = resolve_session(generation_root=generation_root,
        channel_uid=match[1], live_id=match[2], cold_archive_root=cold_archive_root,
        node_executable=executable)
    service.summary_hints_config = dict(generation_root=str(root),
        channel_uid=match[1], live_id=match[2], node_executable=str(executable),
        producer_source_id=f'chzzk:{match[1]}:{match[2]}', primary_clock=primary_clock,
        cold_archive_root=None if cold_archive_root is None else str(cold_archive_root))

def _fetch(service, query):
    config = getattr(service, 'summary_hints_config', None)
    if not config:
        raise EvidenceError('loom_hints_not_configured')
    if config.get('finalized_vod') is not None:
        from ..live_vod_finalize import verified_live_transcript
        transcript = verified_live_transcript(config['finalized_vod'], service.video_no, service.duration)
        stt = next((a for a in service.assets.values() if a.family == 'stt'), None)
        if (not transcript or stt is None
                or native_path(transcript).resolve() != native_path(stt.source_locator['path']).resolve()):
            raise EvidenceError('loom_hint_finalized_source_mismatch')
        proof = json.loads(native_path(transcript).with_suffix('.live-vod-source.json').read_text(encoding='utf-8'))
        archive = proof['archive']
        channel, live_id = archive.split('-', 1)
        root, _, _ = resolve_session(generation_root=config['generation_root'], channel_uid=channel,
            live_id=live_id, cold_archive_root=config['cold_archive_root'], node_executable=config['node_executable'])
        config.update(generation_root=str(root), channel_uid=channel, live_id=live_id,
            producer_source_id=f'chzzk:{channel}:{live_id}',
            primary_clock=dict(kind='finalized_vod', archive_id=archive, origin_ms=proof['mapping']['originMs'],
                source_revision=proof['mapping']['sourceRevision'], srt_sha256=proof['srt_sha256']),
            finalized_vod_origins=proof['origins'])
    query = {**query, 'as_of_ms': _context_as_of_ms(service)}
    return _fetch_source(config, query)

def _fetch_source(config, query):
    source_id = config['producer_source_id']
    try:
        completed = subprocess.run([config['node_executable'],
            str(Path(__file__).with_name('loom_hints.mjs')), config['generation_root'],
            f"{config['channel_uid']}-{config['live_id']}", sys.executable, canonical_json(query),
            config['cold_archive_root'] or ''],
            capture_output=True, timeout=30, check=False)
        if len(completed.stdout) > 250000:
            raise EvidenceError('loom_hint_source_unbounded')
        value = json.loads(completed.stdout.decode('utf-8'))
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
        raise EvidenceError('loom_hint_source_unavailable') from exc
    if completed.returncode or not isinstance(value, dict) or value.get('error'):
        reason = value.get('error') if isinstance(value, dict) else None
        raise EvidenceError(reason if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{1,80}', reason)
                            else 'loom_hint_source_unavailable')
    if value.get('schema_version') != 'ew.loom-hint-source.v1' or value.get('source_id') != source_id:
        raise EvidenceError('loom_hint_source_mismatch')
    return value

def _detail(service, value, families):
    detail = value['detail']
    base = dict(report_id=detail['report_id'], report_revision=detail['revision'],
        status=detail['status'], reason=detail['reason'], window=detail['window'],
        clock_domain='utc_epoch_ms', coverage_state=detail['coverage_state'],
        cards=[], original_read=False, meaning_complete=False, vod_seek_verified=False)
    if detail.get('saved_summary'):
        base['saved_summary'] = dict(text=detail['saved_summary'], derived=True,
            current_source_validated=detail['status']=='ready', original_read=False,
            policy='Stored model interpretation; reread the current VOD originals. Source invalidation does not erase saved text.')
    if detail['status'] != 'ready':
        clock = getattr(service,'summary_hints_config',{}).get('primary_clock')
        if clock and clock.get('kind') in ('vod', 'finalized_vod') and detail.get('saved_summary'):
            window = detail['window']
            historical = dict(detail,navigation=dict(scenes=[dict(kind='saved_summary',
                text=detail['saved_summary'].splitlines()[0],startMs=window['startMs'],endMs=window['endMs'])]))
            return _vod_window_detail(service,historical,base,clock,families)
        return base
    publication = detail.get('publication')
    if not publication or publication['report_revision'] != detail['revision']:
        return {**base, 'status':'unavailable', 'reason':'loom_hint_publication_missing'}
    base['publication'] = publication
    base['projection_hash'] = digest(detail)
    primary_clock = getattr(service, 'summary_hints_config', {}).get('primary_clock')
    if primary_clock:
        if primary_clock.get('kind') == 'vod':
            return _vod_window_detail(service, detail, base, primary_clock, families)
        return _live_window_detail(service, detail, base, primary_clock, families)
    from .signal_view import _catalog
    _, signals = _catalog(service)
    base['signal_projection_hash'] = digest(signals)
    base['signal_family_status'] = {f:service.family_status.get(f,'excluded_by_policy')
        for f in ('chat','audio_features','live_metadata')}
    originals = {(r['source_kind'], r['id']):r for r in detail['originals']}
    with service.open() as db:
        for scene in detail.get('navigation', {}).get('scenes', []):
            ids, mapped_families, missing = [], set(), []
            for ref in scene.get('sourceRefs', []):
                family = {'transcript':'stt', 'chat':'chat'}.get(ref.get('sourceKind'))
                if family not in families:
                    missing.append('unsupported_or_unselected_family')
                    continue
                raw = originals.get((ref['sourceKind'], ref['sourceRowId']))
                table = 'transcript_segments' if family == 'stt' else 'chat_messages'
                rows = list(db.execute("SELECT payload_json FROM records WHERE family=? "
                    "AND json_extract(payload_json,'$.native_locator.kind')='temporal_row' "
                    "AND json_extract(payload_json,'$.native_locator.table')=? "
                    "AND json_extract(payload_json,'$.native_locator.primary_key')=?",
                    (family, table, ref['sourceRowId'])))
                if raw is None or len(rows) != 1:
                    missing.append('original_not_in_current_world')
                    continue
                record = json.loads(rows[0][0]); clock = record.get('raw_time') or {}
                if (record['source_id'] != service.source_id or record['raw_text'] != raw['text']
                        or clock.get('unit') != 'utc_epoch_ms' or clock.get('start') != raw['start']
                        or clock.get('end') != raw['end']
                        or isinstance(ref.get('quote'), str) and ref['quote'] not in raw['text']):
                    missing.append('original_revision_mismatch')
                    continue
                ids.append(record['record_id']); mapped_families.add(family)
            ids = list(dict.fromkeys(ids))
            card = dict(kind=scene.get('kind', 'scene'), text=scene['text'],
                source_basis=scene.get('sourceBasis'),
                interval_utc_ms=[scene.get('startMs'),scene.get('endMs')],
                record_ids=ids, missing_references=missing, derived=True,
                original_read=False, coverage_complete=False, meaning_complete=False)


            if ids and not missing:
                card['status'] = 'source_addresses_verified'
                card['next_action'] = dict(action='read_evidence', args=dict(record_ids=ids,
                    families=sorted(mapped_families), context_cues=2, context_order='source_order'))
                card['phrases_action'] = dict(action='browse_evidence', args=dict(view='phrases',
                    record_ids=ids, families=sorted(mapped_families), limit=10))
                span=card['interval_utc_ms']; overlapping=[]
                if all(type(t) is int for t in span) and span[0]<span[1]:
                    overlapping=[s for s in signals['items'] if s.get('interval_utc_ms')
                        and s['interval_utc_ms'][0]<span[1] and s['interval_utc_ms'][1]>span[0]]
                card['signal_refs']=[]; counts={}
                for signal in overlapping:
                    counts[signal['kind']]=counts.get(signal['kind'],0)+1
                    if any(s['kind']==signal['kind'] for s in card['signal_refs']):continue
                    projected={k:signal[k] for k in ('hint_ref','kind','family','interval_utc_ms',
                        'stats','raw','baseline','relative','quality_flags','interpretation') if k in signal}
                    projected.update(derived=True,original_read=False,coverage_complete=False,
                        selection='first_overlapping_native_interval_per_kind_not_value_rank')
                    if signal['family']=='chat':
                        projected['next_action']=dict(action='browse_chat',args=dict(
                            interval_utc_ms=signal['interval_utc_ms'],limit=3,max_bytes=6000))
                    else:
                        projected['next_action']=dict(action='read_evidence',args=dict(
                            record_ids=signal.get('source_record_ids') or [signal['record_id']],
                            families=[signal['family']],context_cues=0))
                    card['signal_refs'].append(projected)
                card['overlapping_signal_counts']=counts
                card['signal_projection_hash']=base['signal_projection_hash']
                card['signals_omitted']=sum(counts.values())>len(card['signal_refs'])
            else:
                card['status'] = 'excluded_unmatched_source'
            base['cards'].append(card)
    base['references_omitted'] = detail.get('references_omitted', False)
    return base

def _vod_window_detail(service, detail, base, clock, families):
    """Five-minute interpretation locates VOD originals; it is never a citation."""
    base.update(navigation_scope='verified_vod_window_not_original_equivalence')
    if clock.get('kind') == 'finalized_vod':
        origin_ms = clock['origin_ms']
        base['primary_source_clock'] = clock
    else:
        origin_ms = clock['broadcast_start_at_ms']
        base['clock_proof_sha256'] = clock['proof_sha256']
    for scene in detail.get('navigation', {}).get('scenes', []):
        start, end = scene.get('startMs'), scene.get('endMs')
        if type(start) is not int or type(end) is not int or start >= end:
            continue
        a = max(0, (start-origin_ms)/1000)
        b = min(service.duration, (end-origin_ms)/1000)
        card = dict(kind=scene.get('kind','scene'), text=scene['text'], derived=True,
            interval_utc_ms=[start,end], original_read=False, meaning_complete=False,
            original_equivalence=False, status='outside_vod_source')
        if a < b:
            card.update(status='verified_vod_window_navigation_only', interval_sec=[a,b],
                next_action=dict(action='read_evidence',args=dict(intervals_sec=[[a,b]],families=families,context_cues=2)))
        base['cards'].append(card)
    return base

def _live_window_detail(service, detail, base, clock, families):
    """Locate verified F2 cues, without aliasing raw ASR PKs or quotations.

    Same broadcast/audio/start/overlap is a navigation relationship only. The
    safe SRT and raw transcription may have different text and revisions.
    """
    base.update(navigation_scope='same_live_audio_window_not_original_equivalence',
        primary_source_clock=clock, references_omitted=detail.get('references_omitted', False),
        policy='Read the current source cues independently. Raw ASR row IDs/quotes are not primary citations; hint windows are not edit boundaries.')
    originals = {(r['source_kind'], r['id']):r for r in detail['originals']}
    with service.open() as db:
        records = [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM records WHERE family='stt' ORDER BY ordinal")]
    vod_origins = {(row['audio_id'], row['start_ms'] * 1000, row['end_ms'] * 1000)
        for row in getattr(service, 'summary_hints_config', {}).get('finalized_vod_origins', [])}
    for scene in detail.get('navigation', {}).get('scenes', []):
        ids, missing, addresses = [], [], []
        refs = scene.get('sourceRefs', [])
        if not refs:
            missing.append('source_reference_missing')
        for ref in refs:
            raw = originals.get((ref.get('sourceKind'), ref.get('sourceRowId')))
            if ref.get('sourceKind') != 'transcript' or 'stt' not in families or not raw:
                missing.append('unsupported_or_unselected_family'); continue
            if (any(type(raw.get(k)) is not int for k in ('audio_id','audio_start_at_ms','start','end'))
                    or raw['start'] >= raw['end'] or not isinstance(raw.get('text'), str)
                    or isinstance(ref.get('quote'), str) and ref['quote'] not in raw['text']):
                missing.append('original_address_or_quote_invalid'); continue
            matches = []
            for record in records:
                timing = record.get('time_index')
                if not timing:
                    continue
                if clock.get('kind') == 'finalized_vod':
                    if (raw['audio_id'], timing['start_us'], timing['end_us']) not in vod_origins:
                        continue
                    origin_ms = clock['origin_ms']
                else:
                    origin = record['native_locator'].get('original_live_occurrence') or {}
                    if (origin.get('audio_id') != raw['audio_id']
                            or origin.get('audio_start_at_ms') != raw['audio_start_at_ms']):
                        continue
                    origin_ms = clock['broadcast_start_at_ms']
                start = origin_ms + timing['start_us'] / 1000
                end = origin_ms + timing['end_us'] / 1000
                if start < raw['end'] and end > raw['start']:
                    matches.append(record['record_id'])
            if not matches:
                missing.append('outside_verified_f2_coverage'); continue
            ids.extend(matches)
            addresses.append(dict(source_kind='transcript', source_row_id=raw['id'],
                audio_id=raw['audio_id'], interval_utc_ms=[raw['start'],raw['end']],
                primary_citation=False, original_equivalence=False))
        ids = list(dict.fromkeys(ids))
        if len(ids) > 32:
            ids = []; missing.append('f2_window_unbounded')
        card = dict(kind=scene.get('kind','scene'), text=scene['text'],
            source_basis=scene.get('sourceBasis'), interval_utc_ms=[scene.get('startMs'),scene.get('endMs')],
            record_ids=ids, hint_source_addresses=addresses, missing_references=missing,
            derived=True, original_read=False, coverage_complete=False, meaning_complete=False,
            status='live_window_navigation_only' if ids and not missing else 'excluded_unmatched_source')
        if ids and not missing:
            card['next_action'] = dict(action='read_evidence',args=dict(record_ids=ids,
                families=['stt'],context_cues=2,context_order='source_order'))
        base['cards'].append(card)
    return base

def browse_hints(service, *, question_id, families=None, cursor=0, limit=20, max_bytes=12000,
                 report_id=None, report_revision=None):
    if (not isinstance(question_id,str) or not question_id or type(limit) is not int
            or not 1 <= limit <= 20 or type(max_bytes) is not int or not 2000 <= max_bytes <= 128000):
        raise EvidenceError('invalid_loom_hint_browse')
    families = service._families(families or [f for f in ('stt','chat')
        if f in service.policy['allowed'] and service.family_status.get(f) in {'present','partial'}])
    if not set(families) <= {'stt','chat'}:
        raise EvidenceError('loom_hint_family_unsupported')
    query, native_cursor = {}, None
    binding = [service.source_id, service.world_revision, question_id, families, limit, max_bytes]
    if report_id is not None or report_revision is not None:
        if (type(report_id) is not int or report_id < 1 or type(report_revision) is not int
                or report_revision < 1 or cursor != 0):
            raise EvidenceError('invalid_loom_hint_detail')
        query = dict(report_id=report_id, report_revision=report_revision)
    elif not (type(cursor) is int and cursor == 0):
        if not isinstance(cursor,str) or not re.fullmatch('[a-f0-9]{64}', cursor):
            raise EvidenceError('loom_hint_cursor_mismatch')
        path = service.run_root/'loom-hint-cursors'/ (cursor+'.json')
        if not path.is_file():
            raise EvidenceError('loom_hint_cursor_mismatch')
        saved = json.loads(path.read_text(encoding='utf-8'))
        if digest(saved) != cursor or saved['binding'] != binding:
            raise EvidenceError('loom_hint_cursor_mismatch')
        native_cursor = saved['native_cursor']
        query['cursor'] = native_cursor
    if report_id is None and limit < 10:
        raise EvidenceError('loom_hint_index_requires_limit_at_least_10')

    with service.open():
        pass
    try:
        value = _fetch(service, query)
    except (EvidenceError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        if getattr(service, 'summary_hints_config', {}).get('finalized_vod') is None:
            raise

        return dict(schema_version='ew.loom-hints.v1', profile=PROFILE,
            source_id=service.source_id, world_revision=service.world_revision, question_id=question_id,
            status='unavailable', reason='loom_hint_finalized_source_unavailable',
            online_status=dict(status='unavailable', reason='loom_hint_finalized_source_unavailable'),
            index=[], gaps=[], gaps_omitted=False, next_cursor=None,
            **(dict(detail=dict(report_id=report_id, report_revision=report_revision,
                status='unavailable', cards=[], original_read=False, vod_seek_verified=False))
                if report_id is not None else {}),
            raw_read=False, coverage_complete=False, meaning_complete=False,
            producer_writes=0, provider_calls=0, data_is_untrusted=True)
    result = dict(schema_version='ew.loom-hints.v1', profile=PROFILE,
        source_id=service.source_id, world_revision=service.world_revision,
        question_id=question_id, source_projection_hash=digest(value),
        online_status=value['online_status'], index=[], gaps=value['gaps'],
        gaps_omitted=value['gaps_omitted'], next_cursor=None,
        raw_read=False, coverage_complete=False, meaning_complete=False,
        producer_writes=0, provider_calls=0, data_is_untrusted=True,
        policy='Stored model hints guide discovery and require current original reads. Missing hints are not missing events; native UTC is not a VOD cut clock.')
    if report_id is not None:
        if value['detail']['report_id'] != report_id or value['detail']['revision'] != report_revision:
            raise EvidenceError('loom_hint_report_mismatch')
        result['detail'] = _detail(service, value, families)
    else:
        def index_item(row):
            item = {k:row[k] for k in ('reportId','revision','window','status','reason','coverageState','omissionCount','itemCount')}
            item['summary_hint'] = row.get('summary','')[:240]
            item['next_action'] = dict(action='browse_evidence', args=dict(view='hints',
                report_id=row['reportId'], report_revision=row['revision'], families=families,max_bytes=48000))
            return item
        result['index'] = [index_item(row) for row in value['index']]
        if 'ready_index' in value:
            result['ready_index'] = [index_item(row) for row in value['ready_index']]
            ready_ids = {(row['reportId'], row['revision']) for row in result['ready_index']}
            result['index'] = [row for row in result['index']
                if (row['reportId'], row['revision']) not in ready_ids]
        if value['has_more']:
            issued = dict(binding=binding,native_cursor=value['next_cursor'])
            result['next_cursor'] = digest(issued)
            atomic_json(service.run_root/'loom-hint-cursors'/(result['next_cursor']+'.json'), issued)
        result['index_page_size'] = 10
        result['pagination_scope'] = 'one_producer_index_page_not_complete_broadcast'
        if len(canonical_json(result).encode('utf-8')) > max_bytes:


            items = result['index'] + result.get('ready_index', [])
            summaries = [row['summary_hint'] for row in items]
            low, high = 0, 240
            while low < high:
                middle = (low + high + 1) // 2
                for row, summary in zip(items, summaries):
                    row['summary_hint'] = summary[:middle]
                if len(canonical_json(result).encode('utf-8')) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            for row, summary in zip(items, summaries):
                row['summary_hint'] = summary[:low]
    if len(canonical_json(result).encode('utf-8')) > max_bytes:
        raise EvidenceError('loom_hint_page_bytes_too_small')
    historical = _historical_context(service, value)
    if len(canonical_json({**result, 'historical_context':historical}).encode('utf-8')) <= max_bytes:
        result['historical_context'] = historical
    else:

        omitted = dict(status='unavailable', reason='historical_context_page_budget_exceeded',
                       primary_evidence=False, raw_read=False, coverage_complete=False)
        if len(canonical_json({**result, 'historical_context':omitted}).encode('utf-8')) <= max_bytes:
            result['historical_context'] = omitted
    with service.open():
        pass
    return result
