"""Read and freeze normal source projections for the current conversation.

This is a consumer of existing collectors, samplers and report contracts. It
never fetches, rewrites a cache, adopts a prior author, or supplies old decisions.
"""

from copy import deepcopy

from dataclasses import asdict

import json

from .evidence_world.contracts import file_hash, native_path

NAVIGATION_PROFILE = 'source_bound_navigation_v1'

EXECUTION_MODES = ('lite', 'normal', 'full')

MODE_DEFAULT_AUTHORING_MINUTES = {'lite': 15, 'normal': 30, 'full': 60}

LITE_WARM_VECTOR_MAX_UNITS = 4000

def should_use_cached_only_navigation(execution_mode, *, service, runtime):
    """In LITE mode, reuse settled caches or allow fast warm-GPU small VODs; never cold-encode."""
    if execution_mode != 'lite':
        return False
    if not isinstance(runtime, dict):
        return True
    from .editorial_multimodal_navigation import _local_runtime_assets
    _, missing_asset = _local_runtime_assets(runtime)
    if missing_asset is not None:
        return True
    device = str(runtime.get('device') or '').strip().lower()
    if not (device.startswith('cuda') or device.startswith('mps')):
        return True
    from .editorial_embedding_index import document_unit_count
    with service.open() as db:
        unit_count = document_unit_count(db)
    return unit_count > LITE_WARM_VECTOR_MAX_UNITS

def build_local_chunk_hints(chunks, duration_sec, *, window_sec=300):
    """Build deterministic zero-LLM time-bucket hints for FULL mode when live hints are absent."""
    duration = max(0, int(duration_sec or 0))
    step = max(60, int(window_sec))
    buckets = {}
    for row in chunks or []:
        if isinstance(row, dict):
            start = float(row.get('start_sec', row.get('start', 0)) or 0)
            text = str(row.get('text') or '').strip()
        else:
            start = float(getattr(row, 'start_sec', getattr(row, 'start', 0)) or 0)
            text = str(getattr(row, 'text', '') or '').strip()
        if not text:
            continue
        idx = int(max(0.0, start) // step)
        entry = buckets.setdefault(idx, {'texts': [], 'count': 0})
        entry['count'] += 1
        if len(entry['texts']) < 3:
            entry['texts'].append(text[:120])
    windows = []
    for idx in sorted(buckets):
        start_sec = idx * step
        end_sec = min(duration, start_sec + step) if duration > 0 else start_sec + step
        windows.append(dict(
            start_sec=start_sec,
            end_sec=end_sec,
            cue_count=buckets[idx]['count'],
            sample_lines=buckets[idx]['texts'],
        ))
    return dict(
        schema_version='chzz.local_chunk_hints.v1',
        window_sec=step,
        derived=True,
        original_read=False,
        authority='deterministic_orientation_only; read originals before citing',
        windows=windows,
    )

def prepare_local_source_files(*, vod, spec, source_dir, root):
    """Bind acquired local originals and copy only available support snapshots."""
    import shutil
    from .chunker import chunk_srt
    from .editorial_source_timing import prepare_source_timing
    from .live_vod_chat import is_bound_chat, descriptor_status
    from .source_platforms import source_from_fields

    reference = source_from_fields(str(vod.video_no), vod.source_platform, vod.source_id, vod.source_url)
    media_path = spec.get('media_path')
    if media_path:
        media = native_path(media_path).resolve()
        if not media.is_relative_to(source_dir):
            raise ValueError('current_conversation_media_outside_source')
        from .downloader import _is_valid_media_file
        if not media.is_file() or not _is_valid_media_file(str(media), expected_duration_sec=vod.duration):
            raise ValueError('source_unavailable_before_summary:media')
    chunks = chunk_srt(str(native_path(spec['srt_path'])))
    source = prepare_source_timing(video_no=reference.work_id, duration_sec=vod.duration,
        work_dir=source_dir, chunks=chunks, srt_path=spec['srt_path'],
        source_platform=reference.platform, source_native_id=reference.native_id)
    if source['asset'].get('clock_authority', {}).get('kind') == 'live':
        raise ValueError('current_conversation_requires_confirmed_vod_clock')
    chat_path = source_dir / f'{reference.work_id}_chat.log.json'
    if chat_path.is_file() and is_bound_chat(chat_path):
        descriptor_status(chat_path)
    comment_dir, clip_dir = root / 'comment-source', root / 'clip-source'
    for directory, filename in ((comment_dir, 'timeline_comments.json'), (clip_dir, 'user_clips.json')):
        directory.mkdir(parents=True, exist_ok=True)
        previous = source_dir / filename
        if previous.is_file() and not (directory / filename).exists():
            shutil.copyfile(previous, directory / filename)
    return (chunks, source, media_path, chat_path if chat_path.is_file() else None,
            comment_dir, clip_dir / 'user_clips.json')

def prepare_navigation_features(*, service, source_dir, runtime, derived_root,
                                retrieval=None, hierarchy=None, read_index_root=None,
                                retained_index_root=None, cached_only=False):
    """Prepare one new owner's navigation without writing retained source caches."""
    from .evidence_world.contracts import EvidenceError, digest
    from .evidence_world.index import atomic_json, build_lock
    from .editorial_embedding_index import (
        document_navigation_admission, document_unit_count,
        prepare, prepare_optional, read_saved_documents,
    )
    from .evidence_world.vector_clusters import build_navigation, DOCUMENT_COSINE_NAVIGATION
    from pathlib import Path

    root = native_path(derived_root).resolve()
    strict_retrieval = retrieval is not None or read_index_root is not None
    selected_manifest = None
    retained_validation_error = None
    cache_data_errors = {'gemma_index_member_changed', 'gemma_index_vector_changed'}

    def read_candidate(config, path, *, materialize, strict, batch_rows=None):
        with service.open() as db:
            try:
                if materialize:
                    return read_saved_documents(config, db=db, index_root=path, max_units=None, batch_rows=batch_rows), None
                read_saved_documents(config, db=db, index_root=path, max_units=None,
                                     batch_rows=batch_rows, materialize=False)
                return True, None
            except EvidenceError as exc:
                if strict or str(exc) not in cache_data_errors:
                    raise
                return None, str(exc)
            except MemoryError:
                if strict or materialize:
                    raise
                return None, 'document_index_validation_memory'

    index_root = native_path(source_dir) / 'embedding-index'
    if read_index_root is not None:
        if not isinstance(read_index_root, str) or not read_index_root.strip():
            raise ValueError('navigation_read_index_root_invalid')
        index_root = native_path(read_index_root).resolve()
        if (index_root.name != 'embedding-index'
                or not index_root.is_relative_to(native_path(source_dir).resolve() / 'current-conversation')
                or index_root.is_relative_to(root.parent)):
            raise ValueError('navigation_read_index_root_outside_source')
        if runtime is None:
            raise ValueError('navigation_read_index_runtime_required')

        verified = prepare(service, source_dir=source_dir, runtime=runtime,
                           cached_only=True, index_root=index_root)
        if verified is None:
            raise ValueError('navigation_read_index_not_prepared')
        if retrieval is not None and retrieval != verified:
            raise ValueError('navigation_read_index_config_changed')
        retrieval = verified
        manifest_path = index_root / 'manifests' / (retrieval['table'][6:] + '.json')
        selected_manifest = dict(path=str(manifest_path), sha256=file_hash(manifest_path))
    if retained_index_root is not None:
        from .editorial_multimodal_navigation import _sha
        retained = native_path(retained_index_root).resolve()
        prior_job = retained.parent.parent
        if (read_index_root is not None or retained.name != 'embedding-index' or retained.parent.name != 'derived'
                or prior_job.parent != root.parent.parent or prior_job == root.parent
                or not _sha(prior_job.name) or not _sha(root.parent.name)
                or not retained.is_relative_to(native_path(source_dir).resolve())):
            raise ValueError('navigation_retained_index_outside_owner')
        if runtime is not None and retrieval is None:
            verified = prepare_optional(service, source_dir=source_dir, runtime=runtime, cached_only=True,
                                        index_root=retained, report_status=False)
            if verified is not None:
                manifest_path = retained / 'manifests' / (verified['table'][6:] + '.json')
                manifest_hash = file_hash(manifest_path)
                valid, retained_validation_error = read_candidate(verified, retained, materialize=False, strict=False)
                if valid:
                    if file_hash(manifest_path) != manifest_hash:
                        raise ValueError('navigation_read_index_manifest_changed')
                    index_root, retrieval = retained, verified
                    selected_manifest = dict(path=str(manifest_path), sha256=manifest_hash)
    identity = dict(profile=NAVIGATION_PROFILE, source_id=service.source_id,
        world_revision=service.world_revision, runtime=runtime,
        vector_role='document', dimension=768, normalization='l2',
        cluster_role='document_cosine_navigation', cluster_policy=DOCUMENT_COSINE_NAVIGATION,
        generator_sha256={name: file_hash(Path(__file__).with_name(name)) for name in
            ('editorial_embedding_index.py', 'editorial_multimodal_navigation.py')},
        adapter_sha256=file_hash(Path(__file__).with_name('evidence_world') / 'vector_clusters.py'),
        original_read=False)
    from .evidence_world.cluster_navigation import PROFILE as READER_PROFILE, descriptor
    identity['cluster_reader'] = dict(profile=READER_PROFILE,
        reader_sha256=file_hash(Path(__file__).with_name('evidence_world') / 'cluster_navigation.py'))
    if selected_manifest is not None:
        identity['read_index_binding'] = dict(index_root=str(index_root), manifest=selected_manifest)
    marker = root / 'navigation-identity.json'
    if root.exists() and not marker.is_file() and any(root.iterdir()):
        raise ValueError('navigation_derived_root_not_owned')
    with build_lock(root):
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8')) != identity:
                raise ValueError('navigation_derived_identity_changed')
        else:
            atomic_json(marker, identity)
    clusters = None
    cluster_reader = None
    grouping_complete = None
    comparison_scope = {}
    owned_index = root / 'embedding-index'
    if runtime is not None and retrieval is None:


        for candidate in (index_root, owned_index):
            if candidate.is_dir():
                retrieval = prepare_optional(service, source_dir=source_dir, runtime=runtime,
                    index_root=candidate, status_root=root / 'status', cached_only=True)
                if retrieval is not None:
                    index_root = candidate
                    break
    if cached_only:
        from .editorial_production_job import SCHEMA as PRODUCTION_SCHEMA
        if retrieval is not None and (selected_manifest is None or read_index_root is not None):
            _, invalid_cache = read_candidate(retrieval, index_root, materialize=False,
                strict=strict_retrieval or selected_manifest is not None)
            if invalid_cache:
                retrieval = None
        candidates = [root]
        if retained_index_root is not None:
            candidates.append(native_path(retained_index_root).resolve().parent)
        cached_group_error = False
        for prepared_root in candidates:
            budget_path = prepared_root.parent / 'budget' / 'budget.json'
            if not budget_path.is_file():
                continue
            try:
                budget = json.loads(budget_path.read_text(encoding='utf-8'))
                state = budget['budget']
                binding = state['identity']
                feature_ref = binding.get('source_scope', {}).get('editorial_navigation_features')
                if feature_ref is None:
                    continue
                feature_path = prepared_root.parent / 'navigation-features.json'
                if (budget.get('sha256') != digest(state)
                        or binding.get('schema_version') != PRODUCTION_SCHEMA
                        or binding.get('video_no') != service.video_no
                        or native_path(binding['root']).resolve() != budget_path.parent
                        or native_path(feature_ref['path']).resolve() != feature_path
                        or file_hash(feature_path) != feature_ref['sha256']):
                    raise ValueError('navigation_read_features_changed')
                saved = json.loads(feature_path.read_text(encoding='utf-8'))
                saved_identity = json.loads((prepared_root / 'navigation-identity.json').read_text(encoding='utf-8'))
                if saved.get('identity_sha256') != digest(saved_identity):
                    raise ValueError('navigation_derived_identity_changed')
                current_basis = {key: item for key, item in identity.items() if key != 'read_index_binding'}
                saved_basis = {key: item for key, item in saved_identity.items() if key != 'read_index_binding'}
                if saved_basis != current_basis:
                    continue
                reader = saved.get('cluster_reader')
                if reader is None:
                    continue
                cluster_path = prepared_root / 'document-navigation.json'
                if (native_path(reader['artifact']['path']).resolve() != cluster_path
                        or saved['clusters'] != dict(path=str(cluster_path), sha256=reader['artifact']['sha256'],
                            profile=DOCUMENT_COSINE_NAVIGATION['profile'], original_read=False)):
                    raise ValueError('navigation_clusters_changed')
                if not cluster_path.is_file():
                    continue
                groups = json.loads(cluster_path.read_text(encoding='utf-8'))
                saved_grouping_complete = groups['grouping_complete']
                selected_hierarchy = saved['hierarchy']
                service.configure_cluster_reader(reader, defer_details=True)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):

                cached_group_error = True
                continue
            clusters, cluster_reader = saved['clusters'], reader
            grouping_complete = saved_grouping_complete
            comparison_scope = {key: groups[key] for key in ('comparison_scope', 'window_count') if key in groups}
            if hierarchy is None:
                hierarchy = selected_hierarchy
            break
        if selected_manifest is not None and file_hash(selected_manifest['path']) != selected_manifest['sha256']:
            raise ValueError('navigation_read_index_manifest_changed')
        reason = 'cached_document_groups_unavailable' if cached_group_error else 'document_groups_not_prepared'
        if clusters:
            reason = 'cluster_comparison_limit' if grouping_complete is False else None
        atomic_json(root / 'status' / 'document-navigation-status.json', dict(
            status='partial' if grouping_complete is False else 'ready' if clusters else 'deferred',
            reason=reason,
            admitted=False, grouping_complete=grouping_complete, **comparison_scope,
            retrieval_configured='dense_and_originals' if retrieval is not None else 'lexical_and_originals'))
        return dict(profile=NAVIGATION_PROFILE, derived_root=str(root),
            index_root=str(index_root) if retrieval is not None else None,
            query_root=str(root / 'queries'), retrieval=retrieval, hierarchy=hierarchy,
            clusters=clusters, identity_sha256=digest(identity),
            **(dict(cluster_reader=cluster_reader) if cluster_reader is not None else {}),
            **(dict(selected_index_manifest=selected_manifest) if selected_manifest is not None else {}))
    if runtime is not None or retrieval is not None:
        with service.open() as db:
            unit_count = document_unit_count(db)
        cold_index = retrieval is None
        cpu_threads = (runtime or {}).get('limits', {}).get('cpu_threads', 1)
        unavailable = None
        if cold_index:
            from .editorial_multimodal_navigation import _local_runtime_assets
            _, unavailable = _local_runtime_assets(runtime)
        with document_navigation_admission(source_dir, unit_count,
                max_units=None, cpu_threads=cpu_threads,
                unavailable=unavailable, cold_index=cold_index) as admission:
            reason = admission['reason']
            if reason is None and retrieval is None:
                index_root = owned_index
                retrieval = prepare_optional(service, source_dir=source_dir, runtime=runtime,
                    index_root=index_root, status_root=root / 'status', cached_only=False)
            if retrieval is not None and reason is None:
                try:
                    documents, invalid_cache = read_candidate(retrieval, index_root, materialize=True,
                        strict=strict_retrieval or selected_manifest is not None, batch_rows=admission['batch_rows'])
                    if invalid_cache:
                        retrieval, reason = None, invalid_cache
                    else:
                        groups = build_navigation(documents['rows'], documents['vectors'],
                            source_id=service.source_id, world_revision=service.world_revision)
                        cluster_path = root / 'document-navigation.json'
                        with build_lock(root):
                            if cluster_path.is_file():
                                if json.loads(cluster_path.read_text(encoding='utf-8')) != groups:
                                    raise ValueError('navigation_clusters_changed')
                            else:
                                atomic_json(cluster_path, groups)
                        cluster_reader = descriptor(service, cluster_path)
                        if hierarchy is None:
                            hierarchy = groups['hierarchy']
                        grouping_complete = groups['grouping_complete']
                        comparison_scope = {key: groups[key] for key in ('comparison_scope', 'window_count') if key in groups}
                        clusters = dict(path=str(cluster_path), sha256=cluster_reader['artifact']['sha256'],
                                        profile=groups['profile'], original_read=False)
                except MemoryError:
                    reason = 'cluster_memory_pressure'
                finally:
                    documents = None
            if (clusters is None and retrieval is not None
                    and (selected_manifest is None or read_index_root is not None)):



                _, invalid_cache = read_candidate(retrieval, index_root, materialize=False,
                    strict=strict_retrieval, batch_rows=admission['batch_rows'])
                if invalid_cache:
                    retrieval, reason = None, invalid_cache
            if selected_manifest is not None and file_hash(selected_manifest['path']) != selected_manifest['sha256']:
                raise ValueError('navigation_read_index_manifest_changed')
            if grouping_complete is False:
                reason = 'cluster_comparison_limit'
            elif clusters is None and reason is None:
                reason = 'document_index_not_prepared'
            if reason in cache_data_errors or reason == 'document_index_validation_memory':
                atomic_json(root / 'status' / 'preparation-status.json',
                    dict(status='unavailable', reason=reason, original_search_available=True))
            atomic_json(root / 'status' / 'document-navigation-status.json',
                {**admission, 'status': 'partial' if grouping_complete is False else 'ready' if clusters else 'deferred',
                 'reason': reason, 'grouping_complete': grouping_complete, **comparison_scope,
                 **({'retained_index_validation': retained_validation_error} if retained_validation_error else {}),
                 'retrieval_configured': 'dense_and_originals' if retrieval is not None else 'lexical_and_originals'})
    return dict(profile=NAVIGATION_PROFILE, derived_root=str(root),
        index_root=str(index_root) if retrieval is not None else None,
        query_root=str(root / 'queries'), retrieval=retrieval, hierarchy=hierarchy,
        clusters=clusters, identity_sha256=digest(identity),
        **(dict(cluster_reader=cluster_reader) if cluster_reader is not None else {}),
        **(dict(selected_index_manifest=selected_manifest) if selected_manifest is not None else {}))

def frozen_source_snapshot(spec, *, source_dir, root):
    """Admit pinned source bytes for a new owner, without reopening the old job."""
    import shutil
    from .chunker import chunk_srt
    from .evidence_world.contracts import AssetBinding, digest
    from .evidence_world.records import assert_asset_fresh
    from .live_vod_chat import is_bound_chat, descriptor_status

    binding = spec['source_snapshot']
    if not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}:
        raise ValueError('current_conversation_snapshot_binding_shape')
    path = native_path(binding['path']).resolve()
    if (path.name != 'prepared.json' or not path.is_relative_to(source_dir / 'current-conversation')
            or path.parent == root or file_hash(path) != binding['sha256']):
        raise ValueError('current_conversation_snapshot_identity_changed')
    saved = json.loads(path.read_text(encoding='utf-8-sig'))
    if (saved.get('schema_version') != 'chzz.current_conversation_prepared.v1'
            or digest({k: v for k, v in saved.items() if k != 'prepared_revision'}) != saved.get('prepared_revision')
            or saved['vod_info'] != spec['vod_info']
            or native_path(saved['identity']['run_root']).resolve() != path.parent
            or file_hash(path.parent / 'author-input.json') != saved['input_sha256']
            or digest(saved['source']) != saved['source_sha256']
            or native_path(saved['source']['asset']['source_locator']['path']).resolve()
                != native_path(spec['srt_path']).resolve()):
        raise ValueError('current_conversation_snapshot_source_identity_changed')
    assets = saved['identity']['source_assets'] + saved['source_bundle']['frozen_assets']
    for asset in assets:
        location = native_path(asset['path']).resolve()
        if not location.is_relative_to(source_dir):
            raise ValueError('current_conversation_snapshot_source_bytes_changed')
        retained = None
        if asset['family'] == 'media':
            from .live_original_vod_archive import resolve_saved_original
            retained = resolve_saved_original(location, video_no=saved['identity']['video_no'],
                work_dir=source_dir.parent, expected_sha256=asset['sha256'])
        if retained is None and file_hash(location) != asset['sha256']:
            raise ValueError('current_conversation_snapshot_source_bytes_changed')
        if asset['family'] == 'chat' and is_bound_chat(location):
            descriptor_status(location)
    try:
        assert_asset_fresh(AssetBinding(**saved['source']['asset']))
    except ValueError as error:
        raise ValueError('current_conversation_snapshot_clock_changed') from error
    by_family = {a['family']: a for a in assets}
    for family, directory, filename in (('clip', 'clip-source', 'user_clips.json'),
                                         ('timeline_comments', 'comment-source', 'timeline_comments.json')):
        if family not in by_family:
            continue
        target = root / directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(native_path(by_family[family]['path']), target)
        if file_hash(target) != by_family[family]['sha256']:
            raise ValueError('current_conversation_snapshot_copy_changed')
    media = saved.get('media_source')
    if spec.get('media_path') and (not media or native_path(spec['media_path']).resolve()
                                  != native_path(media['path']).resolve()):
        raise ValueError('current_conversation_snapshot_media_changed')
    sources = (chunk_srt(spec['srt_path']), deepcopy(saved['source']),
        media['path'] if media else None, by_family.get('chat', {}).get('path'),
        root / 'comment-source', root / 'clip-source' / 'user_clips.json')
    return sources, saved

def frozen_navigation_features(snapshot, *, service, source_dir, derived_root):
    """Reuse a frozen index and available groups with this author's query state."""
    from .editorial_embedding_index import prepare
    from .editorial_multimodal_navigation import _sha

    saved = snapshot.get('navigation_features')
    if not saved or not saved.get('retrieval'):
        return None
    runtime = snapshot['multimodal_runtime']
    index_root = native_path(saved['index_root']).resolve()
    native_sources = source_dir / 'current-conversation'
    if (index_root.name != 'embedding-index'
            or (index_root != source_dir / 'embedding-index' and not index_root.is_relative_to(native_sources))):
        raise ValueError('current_conversation_snapshot_navigation_outside_source')
    features = deepcopy(saved)
    if reader := features.get('cluster_reader'):
        cluster = native_path(reader['artifact']['path']).resolve()
        if cluster.name != 'document-navigation.json' or not cluster.is_relative_to(native_sources):
            raise ValueError('current_conversation_snapshot_navigation_outside_source')
        if not cluster.is_file():
            del features['cluster_reader']
            features.update(clusters=None, hierarchy=None)
    if not native_path(runtime['model_ref']['path']).is_file():
        return None
    retrieval = saved['retrieval']
    table = retrieval.get('table')
    if not isinstance(table, str) or not table.startswith('units_') or not _sha(table[6:]):
        raise ValueError('navigation_read_index_config_changed')
    manifest = index_root / 'manifests' / (table[6:] + '.json')
    if not manifest.is_file() or not (index_root / 'lance').is_dir():
        return None
    binding = dict(path=str(manifest), sha256=file_hash(manifest))
    if saved.get('selected_index_manifest', binding) != binding:
        raise ValueError('navigation_read_index_manifest_changed')
    if prepare(service, source_dir=source_dir, runtime=runtime, cached_only=True, index_root=index_root) != retrieval:
        raise ValueError('navigation_read_index_config_changed')
    if file_hash(manifest) != binding['sha256']:
        raise ValueError('navigation_read_index_manifest_changed')
    return dict(features, derived_root=str(derived_root), query_root=str(derived_root / 'queries'),
                selected_index_manifest=binding)

def assert_snapshot_sources(saved, assets, bundle, world_assets):
    """Reject newly discovered or changed support before delivering author input."""
    expected = saved['identity']['source_assets'] + saved['source_bundle']['frozen_assets']
    actual = assets + bundle['frozen_assets']
    signature = lambda rows: sorted((r['family'], r['sha256']) for r in rows)
    manifest = native_path(saved['world_manifest_path'])
    if file_hash(manifest) != saved['world_manifest_sha256']:
        raise ValueError('current_conversation_snapshot_world_changed')
    prior_assets = json.loads(manifest.read_text(encoding='utf-8'))['identity']['assets']
    by_family = lambda rows: sorted(rows, key=lambda row: row['family'])
    if by_family(prior_assets) != by_family(world_assets):
        raise ValueError('current_conversation_snapshot_binding_changed')
    if signature(expected) != signature(actual):
        raise ValueError('current_conversation_snapshot_support_changed')

def prepare_sources(*, service, source_dir, vod, base_path, chunks, comment_source_dir=None):
    from .evidence_world.records import chat_analysis_rows
    from .chat_analyzer import find_edit_points
    from .chat_semantic_sampler import sample_semantic_chat_rows_with_stats
    from .chzzk_user_clips import _bundle_from_json
    from .editorial_supplemental_sources import (
        CONFIG_KEY, PRODUCTION_PROFILE, build_supplemental_context)
    from .timeline_comment_collector import load_timeline_comment_bundle
    from .timed_evidence import build_timed_evidence_manifest
    from .summary_local_vector_index import build_summary_local_vector_index
    from .evidence_world.signal_view import browse_signals, PROFILE

    video = str(vod.video_no)
    source_url = f'https://chzzk.naver.com/video/{video}'
    if getattr(vod, 'source_platform', 'chzzk') in {'youtube', 'soop'}:
        from .source_platforms import source_from_fields
        source_url = source_from_fields(video, vod.source_platform, vod.source_id, vod.source_url).url
    frozen = []
    assets = {a.family: a for a in service.assets.values()}
    chats = list(chat_analysis_rows(service)) if assets['chat'].availability in {'present', 'partial'} else []
    signals = find_edit_points(chats, duration_sec=vod.duration) if chats else []
    sampled = sample_semantic_chat_rows_with_stats(chats, include_source_index=True)


    selected = [chats[r['source_index']] for r in sampled.rows]
    chat_rows = [dict(ref='chat-original:' + r['record_id'], time_sec=r['ms']/1000,
                      text=r['msg']) for r in selected]
    clips = None
    if assets['clip'].availability in {'present', 'partial', 'absent_confirmed'}:
        clips = _bundle_from_json(json.loads(native_path(
            assets['clip'].source_locator['path']).read_text(encoding='utf-8-sig')))
        if clips is not None and str(clips.video_no) != video:
            raise ValueError('current_conversation_clips_wrong_source')
    audio = None
    if assets['audio_features'].availability in {'present', 'partial'}:
        audio_asset = assets['audio_features']
        if audio_asset.source_locator.get('kind') != 'ast_audio_only':
            audio = json.loads(native_path(audio_asset.source_locator['path']).read_text(encoding='utf-8-sig'))
        for path_key, hash_key in (('audio_event_path', 'audio_event_sha256'),
                                   ('audio_event_timing_path', 'audio_event_timing_sha256')):
            if audio_asset.source_locator.get(path_key):
                frozen.append(dict(family='audio_events', path=audio_asset.source_locator[path_key],
                                   sha256=audio_asset.source_locator[hash_key]))

    support_assets, availability = [], {}
    for family, path in (('manager_context', source_dir / f'{video}_context.md'),
                         ('timeline_comments', (comment_source_dir or source_dir) / 'timeline_comments.json')):
        available = path.is_file()
        if family == 'timeline_comments' and available:
            available = load_timeline_comment_bundle(video, path.parent, cache_ttl_hours=0,
                expected_source_url=source_url) is not None
        availability[family] = 'available' if available else 'not_collected'
        if available:
            row = dict(family=family, path=str(path), sha256=file_hash(path))
            support_assets.append(row)
            frozen.append(row)
    supplemental = build_supplemental_context(dict(summary_pipeline_version='aemeth_3_0_2',
        **{CONFIG_KEY: dict(profile=PRODUCTION_PROFILE, video_no=video,
                            assets=support_assets, availability=availability)}),
        video_no=video, work_dir=source_dir, duration_sec=vod.duration)

    page = browse_signals(service, question_id='current-author-source-overview',
                          families=['stt', 'chat', 'clip', 'audio_features'])
    navigation = dict(source_id=service.source_id, world_revision=service.world_revision,
        signal_orientation=dict(profile=PROFILE, first_page=page,
            remaining_cursors=[dict(question_id=page['question_id'], next_action=page['next_action'])]
                if page.get('next_cursor') else [], original_read=False, meaning_complete=False))
    packet = dict(chat_rows=chat_rows, chat_source_revision=service.world_revision,
                  support_proposals=signals, supplemental_sources=supplemental,
                  navigation_source_reads=navigation)
    from .editorial_supplemental_sources import build_elio_context
    elio = build_elio_context(supplemental, vod_title=vod.title)
    if elio is not None:
        packet['elio_story_context'] = elio
        frozen.extend(elio['assets'])
    report = dict(chats=chats, signal_highlights=signals,
                  user_clips=asdict(clips) if clips is not None else None,
                  audio_reaction_metadata=audio,
                  timeline_comment_context=supplemental['timeline_comment_context'],
                  timed_evidence_manifest=build_timed_evidence_manifest(vod_info=vod,
                      chunks=chunks, srt_path=assets['stt'].source_locator['path'],
                      work_dir=source_dir))
    report['summary_local_vector_index'] = build_summary_local_vector_index(
        chunks=chunks, timed_evidence_manifest=report['timed_evidence_manifest'])


    report['summary_local_vector_index']['source_sha256'] = assets['stt'].bytes_sha256
    retained = {}
    meta_path = base_path.with_name(base_path.stem + '_metadata.json')
    if meta_path.is_file():
        sha = file_hash(meta_path)
        meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
        if str(meta.get('video_no')) != video:
            raise ValueError('current_conversation_base_metadata_wrong_source')


        prior_map = (meta.get('editorial_review_packet') or {}).get('broadcast_map') or {}
        for key in ('collaboration_periods', 'collaboration_diagnostics'):
            if key in prior_map or key in meta:
                retained[key] = deepcopy(prior_map[key] if key in prior_map else meta[key])
        if 'collaboration_periods' in retained:
            from .chatgpt_export.contract.collaboration_periods import (
                recover_collaboration_periods, validate_collaboration_periods)
            projection = recover_collaboration_periods(retained['collaboration_periods'], vod.duration)
            retained['collaboration_periods'] = projection['periods']
            if projection['diagnostics']:
                retained['collaboration_diagnostics'] = projection['diagnostics']
            validate_collaboration_periods(retained['collaboration_periods'], vod.duration)
        frozen.append(dict(family='base_metadata', path=str(meta_path), sha256=sha))
    return dict(packet=packet, report=report, retained_collaboration=retained,
                frozen_assets=frozen, availability=dict(service.family_status,
                    audio_events=assets['audio_features'].collection_status.get('audio_events',
                        dict(status='not_collected', audio_listened=False, unprocessed_audio_is_unknown=True))),
                policy='Available source, overview delivery and actual original reads are distinct.')

def report_kwargs(bundle):
    """Restore the normal typed clip object only at the report consumer."""
    report = deepcopy(bundle['report'])
    clips = report.pop('user_clips')


    if clips is not None:
        from .models import UserClip, UserClipsBundle
        clips['clips'] = [UserClip(**row) for row in clips['clips']]
        report['user_clips'] = UserClipsBundle(**clips)
    else:
        report['user_clips'] = None
    return report
