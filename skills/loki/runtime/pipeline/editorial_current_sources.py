"""Read and freeze normal source projections for the current conversation.

This is a consumer of existing collectors, samplers and report contracts. It
never fetches, rewrites a cache, adopts a prior author, or supplies old decisions.
"""

from copy import deepcopy

from dataclasses import asdict

import json

from .evidence_world.contracts import file_hash, native_path

def prepare_sources(*, service, source_dir, vod, base_path, chunks):
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
                         ('timeline_comments', source_dir / 'timeline_comments.json')):
        available = path.is_file()
        if family == 'timeline_comments' and available:
            available = load_timeline_comment_bundle(video, source_dir, cache_ttl_hours=0,
                expected_source_url=f'https://chzzk.naver.com/video/{video}') is not None
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
            from .chatgpt_export.contract.collaboration_periods import validate_collaboration_periods
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
