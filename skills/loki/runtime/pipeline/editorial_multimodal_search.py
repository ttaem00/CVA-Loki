"""Private manager search of durable, settled Loki caches. Query encoding only.

The existing job store is the index; optional raw-free descriptors admit known
older jobs. Reports, original clocks and writer ledgers are never changed.
"""

from __future__ import annotations

from copy import deepcopy

import json

import re

import sqlite3

import time

from . import editorial_multimodal_navigation as mm

from .evidence_world.contracts import AssetBinding, SourcePolicy, digest, file_hash, native_path

from .evidence_world.index import atomic_json, build_lock

QUERY_SECONDS = 120

PATTERNS = ('current-conversation/*/multimodal/state.json',
    'editorial/aemeth_3_0_2/*/multimodal/state.json',
    'editorial/aemeth_3_0_2/*/author-source-reads/*/multimodal/state.json')

def _json(path, *, max_bytes=8_000_000):
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('search_cache_missing')
    return json.loads(path.read_text(encoding='utf-8-sig'))

def _inside(ref, root):
    if not native_path(ref['path']).resolve().is_relative_to(native_path(root).resolve()):
        raise ValueError('search_cache_outside_request')
    return mm._bound_json(ref)

def _requests(state_path, *, cold_work_root=None):
    if cold_work_root is not None:
        _cold_path(state_path, cold_work_root)
    stored = _json(state_path)
    state = stored.get('payload')
    if (not isinstance(state, dict) or stored.get('sha256') != digest(state)
            or not isinstance(state.get('requests'), list) or len(state['requests']) > 2):
        raise ValueError('search_session_changed')
    for entry in state['requests']:
        if not isinstance(entry, dict):
            yield None
            continue
        if entry.get('status') != 'settled':
            yield None
            continue
        key = entry.get('request_key')
        if not mm._sha(key):
            raise ValueError('search_request_key')
        folder = state_path.parent / ('request-' + key)
        if cold_work_root is not None:
            for path in (folder, entry['result_ref']['path'], folder / 'spec.json'):
                _cold_path(path, cold_work_root)
        settled, _ = _inside(entry['result_ref'], folder)
        yield dict(binding=state['binding'], cache_ref=settled['cache_ref'],
            spec_ref=dict(path=str(folder/'spec.json'), sha256=file_hash(folder/'spec.json')))

def register_session(session_root, *, work_dir):
    """Explicit, idempotent backfill of a known older job, never a root crawl."""
    folder = native_path(work_dir)/'_multimodal-search'/'sources'
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    for request in _requests(native_path(session_root)/'state.json'):
        if request is None:
            continue
        _load_request(request)
        path = folder/(digest(request)+'.json')
        if path.exists() and _json(path) != request:
            raise ValueError('search_registration_changed')
        if not path.exists():
            atomic_json(path, request)
        count += 1
    return count

def _cold_path(path, work_root):
    from .vod_search_index import _plain
    from pathlib import Path
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise ValueError('search_cold_source_outside_root')
    selected = _plain(native_path(path))
    if not selected.is_relative_to(work_root):
        raise ValueError('search_cold_source_outside_root')
    return selected

def _current_chat_assets(spec_path, source, binding, original_records):
    """Read the closed job's bound world; derived SRT folders are not chat roots."""
    job = spec_path.parent.parent.parent
    prepared_path = job / 'prepared.json'
    if (spec_path.parent.parent.name != 'multimodal'
            or job.parent.name != 'current-conversation' or not prepared_path.is_file()):
        return None
    prepared = _json(prepared_path, max_bytes=64_000_000)
    identity = prepared.get('identity') or {}
    if (prepared.get('schema_version') != 'chzz.current_conversation_prepared.v1'
            or digest({k:v for k,v in prepared.items() if k != 'prepared_revision'})
               != prepared.get('prepared_revision')
            or native_path(identity.get('run_root')).resolve() != job.resolve()
            or identity.get('job_id') != job.name
            or prepared.get('source') != source
            or prepared.get('world_revision') != binding['world_revision']):
        raise ValueError('search_current_world_binding')
    manifest = native_path(prepared['world_manifest_path']).resolve()
    if (not manifest.is_relative_to(job.resolve()) or manifest.name != 'manifest.json'
            or manifest.parent.name != binding['world_revision']
            or file_hash(manifest) != prepared.get('world_manifest_sha256')):
        raise ValueError('search_current_world_binding')
    from .evidence_world.index import open_snapshot
    needed = {r['asset_id'] for r in original_records if r['family'] == 'chat'}
    with open_snapshot(manifest.parent, world_revision=binding['world_revision'],
                       source_id=binding['source_id']) as db:
        primary = db.execute('SELECT binding_json FROM assets WHERE asset_id=?',
                             (source['asset']['asset_id'],)).fetchone()
        primary_public = AssetBinding(**json.loads(primary[0])).public() if primary is not None else {}
        source_public = AssetBinding(**source['asset']).public()


        primary_public.pop('policy_scope', None)
        source_public.pop('policy_scope', None)
        if primary_public != source_public:
            raise ValueError('search_current_world_source')
        assets = []
        for asset_id in sorted(needed):
            row = db.execute('SELECT binding_json FROM assets WHERE asset_id=?', (asset_id,)).fetchone()
            asset = AssetBinding(**json.loads(row[0])) if row is not None else None
            if (asset is None or asset.family != 'chat' or asset.source_id != binding['source_id']
                    or asset.video_no != source['asset']['video_no']):
                raise ValueError('search_current_world_chat')
            assets.append(asset)
    return assets

def _load_request(request, *, cold_work_root=None):
    if cold_work_root is not None:
        for ref in (request['spec_ref'], request['cache_ref']['input'], request['cache_ref']['result']):
            _cold_path(ref['path'], cold_work_root)
        if request.get('origin_provenance') is not None:
            archive = _cold_path(request['origin_provenance']['archive_ref']['path'], cold_work_root)
            for name in ('spec', 'input', 'result', 'model'):
                _cold_path(archive.parent / (name + '.json'), cold_work_root)
            origin = _json(archive)
            if origin.get('job_tie') is not None:
                _cold_path(origin['job_tie']['cold_root'], cold_work_root)
                _cold_path(origin['job_tie']['prepared_ref']['path'], cold_work_root)
    if request.get('origin_provenance') is not None:
        from .editorial_multimodal_search_transport import validate_projection
        validate_projection(request)


    spec, spec_path = mm._bound_json(request['spec_ref'], max_bytes=64_000_000)
    source, binding = deepcopy(spec['source']), request['binding']
    asset = AssetBinding(**source['asset'])
    if cold_work_root is not None:
        for name in ('path', 'meta_path', 'audio_event_path', 'audio_event_timing_path'):
            if asset.source_locator.get(name):
                _cold_path(asset.source_locator[name], cold_work_root)
        _cold_path(binding['media']['path'], cold_work_root)
    if (source['sha256'] != digest({k:v for k,v in source.items() if k != 'sha256'})
            or binding['source_revision'] != source['sha256'] or binding['source_id'] != asset.source_id
            or binding['stt_sha256'] != asset.bytes_sha256
            or binding['clock_sha256'] != digest(asset.clock_authority)
            or not re.fullmatch(r'[0-9]+', asset.video_no) or asset.clock_authority.get('kind') == 'live'):
        raise ValueError('search_source_binding')
    from .evidence_world.records import assert_asset_fresh, iter_srt_records, iter_chat_records, register_assets
    from .editorial_source_timing import bound_live_source_clock
    assert_asset_fresh(asset)
    bound_live_source_clock(source)
    source['records'] = list(iter_srt_records(asset))
    ref = request['cache_ref']
    material, _ = _inside(ref['input'], spec_path.parent/'cache')
    _inside(ref['result'], spec_path.parent/'cache')
    if cold_work_root is not None:
        result, _ = mm._bound_json(ref['result'])
        for stage in result['stages']:
            _cold_path(stage['path'], cold_work_root)
        if material['contract']['media'].get('path'):
            _cold_path(material['contract']['media']['path'], cold_work_root)
    if ref['model'] != binding['runtime']['model_ref'] or ref['model'] != spec['model_ref']:
        raise ValueError('search_model_binding')
    model, _ = mm._bound_json(ref['model'])
    mm._validate_local_model_manifest(model)
    media = binding['media']
    if (spec['media_ref'] != media or not native_path(media['path']).is_file()
            or file_hash(media['path']) != media['sha256']):
        raise ValueError('search_media_changed')
    chats = {}
    if any(r.get('family') == 'chat' for r in material['original_records']):
        if cold_work_root is not None:
            source_root = native_path(asset.source_locator['path']).parent
            for suffix in ('_chat.log.json', '_chat.log.meta.json'):
                _cold_path(source_root / (asset.video_no + suffix), cold_work_root)
        assets = (_current_chat_assets(spec_path, spec['source'], binding, material['original_records'])
                  if cold_work_root is None else None)
        if assets is None:
            assets = register_assets(video_no=asset.video_no, srt_path=asset.source_locator['path'],
                work_dir=native_path(asset.source_locator['path']).parent,
                duration_sec=float(asset.clock_authority['duration_sec_decimal']),
                policy=SourcePolicy(('chat',), ('chat',)))
        chats = {r['record_id']:r for a in assets if a.family == 'chat' for r in iter_chat_records(a)}
    from .editorial_full_author import full_source_context
    selected_chats = [chats[r['record_id']] for r in material['original_records'] if r['family'] == 'chat']
    chat_rows = [dict(
        ref='chat-original:'+r['record_id'], text=r['raw_text'], time_sec=mm._record_interval(r)[0])
        for r in selected_chats if mm._record_interval(r)]
    _, evidence = full_source_context(source=source, packet=dict(chat_rows=chat_rows,
        chat_source_revision=digest(chat_rows)))
    kwargs = dict(source=source, evidence_catalog=evidence['evidence_catalog'],
        world_revision=binding['world_revision'], media_hashes={media['sha256']},
        current_original_projection=True, current_chat_records=chats, top_k=5, context_sec=0)
    mm.prepare_cached_candidates(ref, **kwargs)
    return dict(request=deepcopy(request), ref=ref, material=material, kwargs=kwargs,
        model=model, video_no=asset.video_no)

def _basis(model):


    files = sorted((dict(file=r['file'], sha256=r['sha256']) for r in model['files']),
        key=lambda r: r['file'])
    return dict(model=mm.MODEL, revision=mm.REVISION, dimension=mm.DIMENSION,
        pooling='mean_l2', files_sha256=digest(files))

def _validate_query_vector(vector):
    import numpy as np
    if (not isinstance(vector, np.ndarray) or vector.dtype != np.float32
            or vector.shape != (1, mm.DIMENSION) or not np.isfinite(vector).all()
            or not np.allclose(np.linalg.norm(vector, axis=1), 1, atol=.005)):
        raise ValueError('search_query_vector')

def build_query_cache(*, query, output_root, model_ref, sdk_root, cpu_threads, max_seconds, max_memory_bytes, device='auto'):
    """Same pinned encoder, query only; exclusively in the existing child worker."""
    import os
    import numpy as np
    model, _ = mm._bound_json(model_ref)
    root = native_path(output_root)
    root.mkdir(parents=True, exist_ok=False)
    _, _, free = mm._memory()
    if free < 8*1024**3:
        raise ValueError('search_insufficient_ram')
    os.environ.update(HF_HOME=str(root/'offline-cache'), HF_HUB_OFFLINE='1',
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1', HF_HUB_DISABLE_TELEMETRY='1',
        TRANSFORMERS_OFFLINE='1')
    started = time.monotonic()
    instance = mm._load_local_model(model, sdk_root, cpu_threads, device=device)
    if len(instance[0].processor.tokenizer(query)['input_ids']) > 8192:
        raise ValueError('search_query_context')
    vector = np.asarray(instance.encode_query([query], batch_size=1, show_progress_bar=False,
        convert_to_numpy=True), dtype=np.float32)
    _validate_query_vector(vector)
    resident, peak, _ = mm._memory()
    if max(resident, peak) > max_memory_bytes or time.monotonic()-started > max_seconds:
        raise ValueError('search_query_resource_limit')
    material = dict(query=query, basis=_basis(model))
    mm._write_new(root/'input.json', material)
    path = root/'query.npy'
    np.save(path, vector, allow_pickle=False)
    mm._write_new(root/'result.json', dict(status='settled', input_sha256=digest(material), basis=_basis(model),
        vector=dict(path=str(path), sha256=file_hash(path)), elapsed_seconds=time.monotonic()-started,
        provider_calls=0, media_encoded=False))
    return dict(input=dict(path=str(root/'input.json'), sha256=file_hash(root/'input.json')),
        result=dict(path=str(root/'result.json'), sha256=file_hash(root/'result.json')), model=model_ref)

def _query_vector(query, *, work_dir, profile, runtime=None, allow_encode=True):
    """A separate immutable search receipt; no reset of closed author budgets."""
    import numpy as np
    runtime = mm.resolve_local_runtime() if runtime is None else runtime
    if runtime is None:
        raise ValueError('search_query_runtime_missing')
    model, reason = mm._local_runtime_assets(runtime)
    if reason or _basis(model) != profile:
        raise ValueError('search_query_model_unavailable')
    root = native_path(work_dir)/'_multimodal-search'
    folder = root/('query-'+digest(dict(query=query, basis=profile)))
    if not allow_encode and not (folder/'settled.json').is_file():
        raise ValueError('search_query_unsettled_no_retry')
    with build_lock(root):
        reused = folder.exists()
        receipt = folder/'settled.json'
        if reused and not receipt.is_file():
            raise ValueError('search_query_unsettled_no_retry')
        if not reused:
            folder.mkdir()
            spec = dict(kind='search_query', query=query, output_root=str(folder/'cache'),
                model_ref=runtime['model_ref'], sdk_root=runtime['sdk_root'], device=runtime['device'],
                cpu_threads=min(2,runtime['limits']['cpu_threads']),
                max_seconds=min(QUERY_SECONDS,runtime['limits']['max_seconds']),
                max_memory_bytes=runtime['limits']['max_memory_bytes'])
            mm._write_new(folder/'spec.json', spec)
            mm._write_new(receipt, mm.run_local_spec(folder/'spec.json'))
        ref = _json(receipt)
        if ref.get('origin_provenance') is not None:
            from .editorial_multimodal_search_transport import validate_query_projection
            validate_query_projection(ref)
        if ref['model'] != runtime['model_ref']:
            raise ValueError('search_query_model_changed')
        material, _ = _inside(ref['input'], folder/'cache')
        result, _ = _inside(ref['result'], folder/'cache')
        if (material != dict(query=query,basis=profile) or result.get('status') != 'settled'
                or result.get('input_sha256') != digest(material) or result.get('basis') != profile):
            raise ValueError('search_query_cache_changed')
        path = native_path(result['vector']['path'])
        if (not path.resolve().is_relative_to((folder/'cache').resolve())
                or path.stat().st_size > 10000 or file_hash(path) != result['vector']['sha256']):
            raise ValueError('search_query_cache_changed')
        vector = np.load(path, allow_pickle=False)
        _validate_query_vector(vector)
        return vector, reused

def _report_records(*, output_dir, site_dir, work_dir, canonical_records,
                    cold_database=None, cold_roots=None):
    from .admin_search import _iter_vod_records
    candidates = (_iter_vod_records(output_dir, site_dir) if canonical_records is None
                  else canonical_records)
    records = {}
    roots = [native_path(p).resolve() for p in (output_dir, site_dir, work_dir)]
    cold, cold_work = {}, None
    if cold_database is not None and canonical_records is not None:
        from .vod_search_index import _roots, list_registered_reports
        try:
            trusted = _roots(cold_roots)
            trusted = {name: native_path(path).resolve() for name, path in trusted.items()}
            if ('work' not in trusted or any(not p.is_dir() for p in trusted.values())
                    or any(p.is_relative_to(hot) or hot.is_relative_to(p)
                           for p in trusted.values() for hot in roots)):
                raise ValueError('search_cold_roots_invalid')
            listing = list_registered_reports(database_path=cold_database, roots=trusted)
            if listing['tier'] != 'cold':
                raise ValueError('search_cold_tier_invalid')
            cold_work = native_path(trusted['work']).resolve()
            cold = {(row['video_no'], row['base'], native_path(row['metadata_path']).resolve()): row
                    for row in listing['reports']}
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            cold, cold_work = {}, None
    for row in candidates:
        try:
            video, base = str(row['video_no']), str(row['base'])
            if (not re.fullmatch(r'[0-9]+', video) or base.split('_', 1)[0] != video
                    or any(c in base for c in '/\\\x00')):
                continue
            path = native_path(row.get('metadata_path') or row.get('meta_path')
                               or output_dir / (base + '_metadata.json'))
            location = cold.get((video, base, path.resolve()))
            is_hot = any(path.resolve().is_relative_to(root) for root in roots)
            if path.is_symlink() or not (is_hot or location is not None):
                continue
            revision = file_hash(path)



            meta = _json(path, max_bytes=64_000_000)
            if (not isinstance(meta, dict) or str(meta.get('video_no') or video) != video
                    or file_hash(path) != revision):
                continue
            compact = meta.get('summary_local_vector_index') or {}
            hashes = [v for v in (row.get('source_sha256'),
                                 compact.get('source_sha256') if isinstance(compact, dict) else None)
                      if v is not None]
            if any(not mm._sha(v) for v in hashes) or len(set(hashes)) > 1:
                continue
            markdown = native_path(row.get('markdown_path') or row.get('md_path')
                                   or output_dir / (base + '.md')).resolve()
            if location is not None:
                source_sha = location.get('source_sha256')
                if (markdown != native_path(location['markdown_path']).resolve()
                        or not mm._sha(source_sha) or not hashes
                        or any(value != source_sha for value in hashes)):
                    continue
            selected_work = roots[2] if is_hot else cold_work
            job = markdown.parent.parent
            current = (markdown.parent.name == 'report' and job.parent.name == 'current-conversation'
                       and job.is_relative_to(selected_work / video / 'current-conversation'))
            provenance = meta.get('llm_provider_provenance') or {}
            if (current and isinstance(provenance, dict)
                    and provenance.get('profile') == 'full_broadcast_v302'
                    and provenance.get('actual_provider') in {
                        'current_conversation_codex', 'current_conversation_chatgpt'}
                    and provenance.get('job_id') == job.name):
                from .editorial_production_job import current_conversation_author_mode
                if ('author' in provenance and current_conversation_author_mode(provenance['author'])
                        != provenance['actual_provider']):
                    continue
                if provenance['actual_provider'] == 'current_conversation_chatgpt':
                    prepared = _json(job / 'prepared.json', max_bytes=64_000_000)
                    identity = prepared.get('identity') or {}
                    if (prepared.get('schema_version') != 'chzz.current_conversation_prepared.v1'
                            or digest({k:v for k,v in prepared.items() if k != 'prepared_revision'})
                            != prepared.get('prepared_revision')
                            or native_path(identity.get('run_root')).resolve() != job
                            or identity.get('job_id') != job.name
                            or current_conversation_author_mode(identity.get('author'))
                               != provenance['actual_provider']
                            or identity.get('summary_author_mode') != provenance['actual_provider']
                            or provenance.get('author') != identity['author']
                            or provenance.get('job_identity_sha256') != digest(identity)):
                        continue
                native_sha = provenance.get('native_source_sha256')
                if not mm._sha(native_sha) or any(value != native_sha for value in hashes):
                    continue
                hashes = [native_sha]
            record = dict(row, video_no=video, base=base,
                title=str(meta.get('title') or base), publish_date=str(meta.get('publish_date') or ''),
                streamer_id=str(meta.get('streamer_id') or ''), channel=str(meta.get('channel') or ''),
                _metadata_path=path, _metadata_revision=revision,
                _source_sha256=hashes[0] if hashes else None, _current_job=job if current else None,
                _work_root=selected_work, _cold_work_root=None if is_hot else cold_work)
            records[(video, base)] = record
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            continue
    return records

def _select_report(item, records, *, legacy_default, work_root=None):
    """Keep a scene's report identity; a shared video number is not a job tie."""
    source_sha = item['request']['binding']['stt_sha256']
    current, exact, canonical, legacy = [], [], [], []
    for record in records:
        try:
            if work_root is not None and record['_work_root'] != work_root:
                continue
            if file_hash(record['_metadata_path']) != record['_metadata_revision']:
                continue
            bound_sha = record['_source_sha256']
            if bound_sha is not None and bound_sha != source_sha:
                continue
            job = record['_current_job']
            if job is not None:
                if record['_cold_work_root'] is not None and item['request'].get('origin_provenance') is not None:
                    from .editorial_multimodal_search_transport import projected_job_root
                    if bound_sha == source_sha and projected_job_root(item['request']) == job:
                        current.append(record)
                    continue
                spec = native_path(item['request']['spec_ref']['path']).resolve()
                relative = spec.relative_to(job / 'multimodal')
                if (bound_sha == source_sha and len(relative.parts) == 2
                        and re.fullmatch(r'request-[0-9a-f]{64}', relative.parts[0])
                        and relative.parts[1] == 'spec.json'):
                    current.append(record)
                continue
            legacy.append(record)
            if bound_sha == source_sha:
                exact.append(record)
            if record.get('is_canonical') is True:
                canonical.append(record)
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            continue
    if current:
        return current[0] if len(current) == 1 else None
    if exact:
        preferred = [r for r in exact if r.get('is_canonical') is True]
        return preferred[0] if len(preferred) == 1 else exact[0] if len(exact) == 1 else None
    if len(canonical) == 1:
        return canonical[0]
    return legacy[0] if legacy_default and len(legacy) == 1 else None

def search_saved_multimodal(query, *, output_dir, site_dir, work_dir, query_encoder=None,
                           canonical_records=None, cold_database=None, cold_roots=None):
    """Opt-in semantic candidates with native clocks, not final cuts or answers."""
    query = (query or '').strip()
    if not query or len(query.encode('utf-8')) > 2000:
        raise ValueError('search_query_length')
    records = _report_records(output_dir=output_dir, site_dir=site_dir, work_dir=work_dir,
        canonical_records=canonical_records, cold_database=cold_database, cold_roots=cold_roots)
    by_video = {}
    for record in records.values():
        by_video.setdefault(record['video_no'], []).append(record)
    requests, skipped, seen = [], 0, set()
    locations = {(r['_work_root'], r['_cold_work_root'], r['video_no']) for r in records.values()}
    for root, cold_root, video in sorted(locations, key=lambda row: (str(row[0]), row[2])):
        for pattern in PATTERNS:
            for state in sorted((root/video).glob(pattern)):
                try:
                    if cold_root is not None:
                        _cold_path(state, cold_root)
                    requests.extend((request, root, cold_root) for request in
                                    _requests(state, cold_work_root=cold_root))
                except (ValueError,OSError,KeyError,TypeError,AttributeError):
                    skipped += 1
    for root, cold_root in sorted({(r['_work_root'], r['_cold_work_root']) for r in records.values()},
                                  key=lambda row: str(row[0])):
        for descriptor in sorted((root/'_multimodal-search'/'sources').glob('*.json')):
            try:
                if cold_root is not None:
                    _cold_path(descriptor, cold_root)
                requests.append((_json(descriptor), root, cold_root))
            except (ValueError,OSError,TypeError,AttributeError):
                skipped += 1
    loaded = []
    skipped += max(0, len(requests)-1000)
    for request, root, cold_root in requests[:1000]:
        try:
            if request is None:
                skipped += 1
                continue
            key = digest(request['cache_ref'])
            if key in seen:
                continue
            seen.add(key)
            item = _load_request(request, cold_work_root=cold_root)
            record = _select_report(item, by_video.get(item['video_no'], []),
                                    legacy_default=canonical_records is None, work_root=root)
            if record is None:
                skipped += 1
                continue
            item['report'] = record
            loaded.append(item)
        except (ValueError,OSError,KeyError,TypeError,OverflowError,AttributeError):
            skipped += 1
    status = dict(status='unavailable', message='저장된 분석 구간이 아직 없습니다.',
        indexed_windows=sum(len(r['material']['windows']) for r in loaded),
        skipped_requests=skipped, query_cache_reused=False, whole_broadcast=False)
    empty = dict(query=query,sources=['multimodal'],total_matches=0,vods=[],multimodal_status=status)
    if not loaded:
        return empty
    profile = _basis(loaded[0]['model'])
    loaded = [r for r in loaded if _basis(r['model']) == profile]
    try:
        vector, reused = (query_encoder or _query_vector)(query,work_dir=work_dir,profile=profile)
        _validate_query_vector(vector)
    except (ValueError,OSError,KeyError,TypeError,AttributeError):
        status['message'] = '저장된 구간은 있으나 검색 문장을 계산하지 못했습니다. 운영 상태를 확인해 주세요.'
        return empty
    status.update(status='partial',message='이미 분석한 구간에서 찾은 후보입니다. 방송 전체를 검색한 결과는 아닙니다.',
        query_cache_reused=reused)
    current_cold = {}
    if any(item['report']['_cold_work_root'] is not None for item in loaded):
        current_cold = _report_records(output_dir=output_dir, site_dir=site_dir, work_dir=work_dir,
            canonical_records=canonical_records, cold_database=cold_database, cold_roots=cold_roots)
    matches = {}
    for item in loaded:
        try:



            expected_report = item['report']
            item = _load_request(item['request'], cold_work_root=expected_report['_cold_work_root'])
            candidates = ([record for record in current_cold.values() if record['video_no'] == item['video_no']]
                          if expected_report['_cold_work_root'] is not None
                          else by_video.get(item['video_no'], []))
            record = _select_report(item, candidates,
                legacy_default=canonical_records is None, work_root=expected_report['_work_root'])
            if (record is None or record['base'] != expected_report['base']
                    or record['_metadata_revision'] != expected_report['_metadata_revision']):
                raise ValueError('search_report_binding_changed')
            if _basis(item['model']) != profile:
                raise ValueError('search_query_model_changed')
            ranked = mm.prepare_cached_candidates(item['ref'],questions=[query],query_vectors=vector,
                question_id='manager-search',**item['kwargs'])
        except (ValueError,OSError,KeyError,TypeError,AttributeError):
            status['skipped_requests'] += 1
            continue
        for page in ranked['navigation_pages']:
            for row in page['candidates']:
                a,b = row['interval_sec']
                scores = {k:v['cosine'] for k,v in row['retrieval'].items() if v is not None}
                score = max(scores.values())
                if score <= 0:
                    continue
                h, rem = divmod(int(a),3600)
                m,s = divmod(rem,60)
                catalog = item['kwargs']['evidence_catalog']
                timed = [r for r in row['originals'] if catalog[r['ref']].get('time_sec') is not None]
                speech = [r['text'] for r in timed if catalog[r['ref']]['source_type'] == 'stt']
                chat = [r['text'] for r in timed if catalog[r['ref']]['source_type'] == 'replay_chat'
                    and re.sub(r'\{:[^{}\r\n]+:\}', '', r['text']).strip()]
                timed = [*speech, *chat]
                match = dict(source='multimodal',snippet=' · '.join(timed)[:400],
                    timestamp=f'{h:02}:{m:02}:{s:02}',ts_ms=round(a*1000),interval_sec=[a,b],
                    section='의미가 가까운 구간 · 원문에서 확인',score=score,modalities=sorted(scores),
                    jump_url=f'https://chzzk.naver.com/video/{item["video_no"]}?currentTime={a:g}')
                key = (item['video_no'],record['base'],a,b)
                if key not in matches or score > matches[key]['score']:
                    matches[key] = match
    vods = []
    for (video,base),rec in records.items():
        rows = sorted((r for (v,rbase,_,_),r in matches.items() if v == video and rbase == base),
                      key=lambda r:(-r['score'],r['ts_ms']))[:25]
        if rows:
            vods.append(dict(video_no=video,title=str(rec['title']),base=rec['base'],
                publish_date=str(rec['publish_date']),streamer_id=str(rec['streamer_id']),streamer_name=str(rec['channel']),
                matches=rows,match_count=len(rows)))
    vods.sort(key=lambda v:(-v['matches'][0]['score'],v['video_no']))
    return dict(empty,vods=vods[:200],total_matches=sum(v['match_count'] for v in vods[:200]))
