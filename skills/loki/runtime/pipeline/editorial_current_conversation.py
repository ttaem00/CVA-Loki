"""Prepared-source authoring by the current conversation, without a transport.

prepare/read/submit are local adapters, not provider calls. Submit creates a new
owned report family; manager save and public publication remain separate actions.
"""

from __future__ import annotations

from copy import deepcopy

import hashlib

import json

from pathlib import Path

import re

import time

from .evidence_world.contracts import SourcePolicy, canonical_json, digest, file_hash, native_path

from .evidence_world.index import build_lock

from .evidence_world.records import assert_asset_fresh

from .evidence_world.service import EvidenceWorldService

from .editorial_production_job import (
    bind_current_conversation_job, current_conversation_job_binding,
    current_conversation_author_owner,
)

SCHEMA = "chzz.current_conversation_prepared.v1"

_CONTEXT_NOTE_FIELDS = {
    'question_id': 'string: the question being tracked',
    'text': 'string: a working hypothesis, not original evidence',
    'read_ids': 'nonempty distinct list of actual read_id values returned by this job',
}

class SubmissionRejected(ValueError):
    """A repairable validation failure, with the original error code intact."""
    def __init__(self, rejection):
        super().__init__(rejection['error'])
        self.rejection = rejection

def _json(path):
    raw = native_path(path).read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if raw != native_path(path).read_bytes():
        raise ValueError("current_conversation_input_changed_during_read")
    return value, hashlib.sha256(raw).hexdigest()

def _create(path, value):
    path = native_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (canonical_json(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("current_conversation_immutable_artifact_changed")
        return
    with path.open("xb") as stream:
        stream.write(data)

def _code_hashes(prompt_path=None):
    files = [Path(__file__).with_name(name) for name in (
        "editorial_current_conversation.py", "editorial_current_sources.py",
        "editorial_full_author.py", "editorial_production_job.py",
        "editorial_supplemental_sources.py", "editorial_embedding_index.py",
        "editorial_context_author.py", "editorial_signal_scoring.py", "highlight_prompt_assets.py")]
    files.append(Path(prompt_path) if prompt_path is not None else
        Path(__file__).resolve().parents[1] / 'prompts/highlight/editorial_full_author_302.md')
    navigation = Path(__file__).with_name('editorial_multimodal_navigation.py')
    if navigation.is_file():
        files.append(navigation)
    files.extend(path for name in ('service.py', 'vector_clusters.py', 'pageindex_tree.py', 'cluster_navigation.py')
                 if (path := Path(__file__).with_name('evidence_world') / name).is_file())
    return {str(p): file_hash(p) for p in files}

def _mint(prepared):
    i = prepared["identity"]
    return bind_current_conversation_job(work_root=i["work_root"], run_root=i["run_root"],
        video_no=i["video_no"], source_assets=i["source_assets"], job_id=i["job_id"],
        code_version=i["code_version"], base_revision=i["base_revision"], author=i["author"],
        selected_pipeline=i["selected_pipeline"])

def _load(job_root):
    root = native_path(job_root).resolve()
    prepared, _ = _json(root / "prepared.json")
    body = {k: v for k, v in prepared.items() if k != "prepared_revision"}
    if (prepared.get("schema_version") != SCHEMA or digest(body) != prepared.get("prepared_revision")
            or native_path(prepared["identity"]["run_root"]).resolve() != root
            or not (root / 'author-input.json').is_file()
            or file_hash(root / 'author-input.json') != prepared.get('input_sha256')):
        raise ValueError("current_conversation_prepared_changed")
    if file_hash(prepared["base_path"]) != prepared["identity"]["base_revision"]:
        raise ValueError("current_conversation_base_changed")
    job = _mint(prepared)
    if current_conversation_job_binding(job) != prepared["identity"]:
        raise ValueError("current_conversation_identity_changed")
    if digest(prepared["source"]) != prepared["source_sha256"]:
        raise ValueError("current_conversation_native_snapshot_changed")
    if prepared.get('authoring_time'):
        _authoring_time(root, prepared)
    for artifact in prepared.get("orientation_artifacts", []):
        if file_hash(artifact["path"]) != artifact["sha256"]:
            raise ValueError("current_conversation_orientation_changed")
    for asset in prepared.get('source_bundle', {}).get('frozen_assets', []):
        if asset['family'] != 'media' and file_hash(asset['path']) != asset['sha256']:
            raise ValueError('current_conversation_support_source_changed')
    from .live_vod_chat import is_bound_chat, descriptor_status
    for asset in prepared['identity']['source_assets']:
        if asset['family'] == 'chat' and is_bound_chat(asset['path']):
            descriptor_status(asset['path'])
    from .evidence_world.contracts import AssetBinding
    assert_asset_fresh(AssetBinding(**prepared["source"]["asset"]))
    return root, prepared, job

def _service(root, prepared):
    manifest, sha = _json(prepared["world_manifest_path"])
    if sha != prepared["world_manifest_sha256"] or manifest["world_revision"] != prepared["world_revision"]:
        raise ValueError("current_conversation_world_changed")
    service = EvidenceWorldService(root=native_path(prepared["world_manifest_path"]).parent,
        manifest=manifest, run_root=root / "source-reads",
        owner=current_conversation_author_owner(prepared["identity"]["author"]))


    service.configure_read(prepared.get('read_page_policy', 'family_serial_v1'))
    for asset in service.assets.values():
        assert_asset_fresh(asset)
    features = prepared.get('navigation_features')
    service.configure_retrieval(prepared.get('retrieval'), search_mode=prepared.get('search_mode', 'lexical'),
        **(dict(runtime=prepared.get('multimodal_runtime'), index_root=features['index_root'],
                query_root=features['query_root']) if features else {}))
    service.configure_hierarchy(prepared.get('hierarchy'))
    if features and features.get('cluster_reader'):
        service.configure_cluster_reader(features['cluster_reader'],defer_details=True)
    if prepared.get('summary_hints'):
        service.configure_summary_hints(**{k:v for k,v in prepared['summary_hints'].items() if k != 'profile'})
    return service

def _orientation_binding(value, path, source, source_dir):
    """Check same-source context without turning a prior judgment into authority."""
    binding = value.get("binding") or value.get("native_timing") or {}
    existing_source = value.get("source")
    if isinstance(existing_source, dict) and isinstance(existing_source.get("asset"), dict):
        asset = existing_source["asset"]
        binding = dict(source_id=asset.get("source_id"), stt_sha256=asset.get("bytes_sha256"),
            clock_sha256=digest(asset.get("clock_authority")), source_revision=existing_source.get("sha256"))
        if (str(value.get("video_no", source["asset"]["video_no"])) != source["asset"]["video_no"]
                or not isinstance(value.get("packet"), dict)):
            raise ValueError("current_conversation_normal_input_source_mismatch")
    if isinstance(value.get("source_material"), dict):
        binding = value["source_material"].get("source") or binding
    if not isinstance(binding, dict):
        raise ValueError("current_conversation_orientation_binding_shape")
    if binding.get("source_id") is not None and binding["source_id"] != source["asset"]["source_id"]:
        raise ValueError("current_conversation_orientation_wrong_source")
    stt_sha = binding.get("stt_sha256") or (binding.get("source_revision")
        if "native_timing" in value or "source_material" in value else None)
    if stt_sha is not None and stt_sha != source["asset"]["bytes_sha256"]:
        raise ValueError("current_conversation_orientation_wrong_transcript")
    if binding.get("clock_sha256") is not None and binding["clock_sha256"] != digest(source["asset"]["clock_authority"]):
        raise ValueError("current_conversation_orientation_wrong_clock")
    if not native_path(path).resolve().is_relative_to(source_dir) and (
            binding.get("source_id") != source["asset"]["source_id"] or stt_sha is None):
        raise ValueError("current_conversation_orientation_source_binding_required")
    return dict(source_id=source["asset"]["source_id"], stt_sha256=source["asset"]["bytes_sha256"],
        clock_sha256=digest(source["asset"]["clock_authority"]),
        original_binding=deepcopy(binding), prior_world_revision_is_not_current_read_receipt=True)

def prepare(spec):
    """Refresh public support into this job; reuse STT without ASR or model calls."""
    from .editorial_full_author import build_broadcast_flow_context, full_source_context, RESPONSE_FIELDS, OPTIONAL_RESPONSE_FIELDS
    from .models import VODInfo
    required = {"vod_info", "work_root", "source_work_dir", "srt_path", "base_path", "job_id",
                "code_version", "author"}
    if (not isinstance(spec, dict) or not required <= set(spec)
            or set(spec) - required - {"summary_pipeline_version", "packet_path", "orientation_paths", "hierarchy", "navigation_path", "media_path", "multimodal_runtime", "summary_hints", "source_output_dir", "source_data_dir", "retrieval", "read_page_policy", "search_mode", "authoring_minutes", "signal_priority_profile", "source_snapshot", "author_prompt_profile", "feature_profile", "read_index_root"}):
        raise ValueError("current_conversation_prepare_spec_shape")
    spec_sha256 = digest(spec)
    minutes = spec.get('authoring_minutes')
    if 'authoring_minutes' in spec and (type(minutes) is not int or minutes <= 0):
        raise ValueError('current_conversation_authoring_minutes_must_be_positive_integer')
    author_owner = current_conversation_author_owner(spec["author"])
    vod = VODInfo(**spec["vod_info"])
    video = str(vod.video_no)
    source_dir = native_path(spec["source_work_dir"]).resolve()
    work = native_path(spec["work_root"]).resolve()
    if not source_dir.is_relative_to(work / video):
        raise ValueError("current_conversation_source_outside_vod")

    if (not isinstance(spec["job_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", spec["job_id"])):
        raise ValueError("current_conversation_job_id_path_invalid")
    root = (work / video / "current-conversation" / spec["job_id"]).resolve()
    if not root.is_relative_to((work / video).resolve()):
        raise ValueError("current_conversation_job_root_outside_vod")


    from .utils import stable_artifact_key, validate_artifact_path_budget
    validate_artifact_path_budget(root / "report", stable_artifact_key(video, "00000000"),
        suffixes=(".md", ".html", ".metadata.json", ".md.part", ".html.part", ".metadata.json.part"))
    base = native_path(spec["base_path"]).resolve()
    base_revision = file_hash(base)
    with build_lock(root):
        if (root / "prepared.json").exists():
            _, saved, _ = _load(root)
            if saved["spec_sha256"] != spec_sha256:
                raise ValueError("current_conversation_prepare_identity_changed")
            return _prepare_receipt(root, saved)
        preparation_started = time.time()
        from .loki_version_policy import new_job_defaults, select_new_pipeline
        selected_pipeline = select_new_pipeline(spec.get("summary_pipeline_version", "latest"))
        spec = dict(spec)
        for key, value in new_job_defaults(selected_pipeline).items():
            spec.setdefault(key, value)
        from .editorial_current_sources import NAVIGATION_PROFILE
        feature_profile = spec.get('feature_profile')
        if feature_profile is not None and feature_profile != NAVIGATION_PROFILE:
            raise ValueError('current_conversation_feature_profile')
        if 'read_index_root' in spec:
            if (feature_profile != NAVIGATION_PROFILE or not isinstance(spec['read_index_root'], str)
                    or not spec['read_index_root'].strip()):
                raise ValueError('current_conversation_read_index_root_invalid')
            read_root = native_path(spec['read_index_root']).resolve()
            if (read_root.name != 'embedding-index'
                    or not read_root.is_relative_to(source_dir / 'current-conversation')
                    or read_root.is_relative_to(root)):
                raise ValueError('current_conversation_read_index_root_outside_source')
        from .highlight_prompt_assets import load_full_author_prompt
        prompt_asset = load_full_author_prompt(spec.get('author_prompt_profile'))
        if spec.get('signal_priority_profile') is not None:
            from .editorial_context_author import SIGNAL_PRIORITY_PROFILE
            if spec['signal_priority_profile'] != SIGNAL_PRIORITY_PROFILE:
                raise ValueError('unknown_signal_priority_profile')
        from .evidence_world.read_pages import POLICIES
        read_page_policy = spec.get('read_page_policy', 'balanced_compact_v1')
        if not isinstance(read_page_policy, str) or read_page_policy not in POLICIES:
            raise ValueError('unknown_read_page_policy')
        search_mode = spec.get('search_mode', 'lexical')
        if not isinstance(search_mode, str) or search_mode not in {'lexical', 'hybrid_branch_v1', 'hybrid_dbsf_v1'}:
            raise ValueError('unsupported_default_search_mode')
        if not vod.duration or vod.duration <= 0:
            raise ValueError("current_conversation_duration_required")
        from .vod.process import _prepare_current_conversation_sources
        snapshot = None
        if 'source_snapshot' in spec:
            from .editorial_current_sources import frozen_source_snapshot
            sources, snapshot = frozen_source_snapshot(spec, source_dir=source_dir, root=root)
        else:
            sources = _prepare_current_conversation_sources(vod=vod, spec=spec, source_dir=source_dir,
                root=root, work=work, selected_pipeline=selected_pipeline)
        chunks, source, prepared_media_path, chat_source_path, comment_dir, clip_path = sources
        _create(root / "source-identity.json", spec["vod_info"])
        service = EvidenceWorldService.prepare(video_no=video, srt_path=spec["srt_path"],
            source_work_dir=source_dir, cache_root=root / "evidence-world-cache",
            run_root=root / "source-reads", duration_sec=vod.duration,
            policy=SourcePolicy(allowed_families=("stt", "chat", "clip", "audio_features"), required_families=("stt",)),
            owner=author_owner, clip_source_path=clip_path,
            chat_source_path=chat_source_path)
        service.configure_read(read_page_policy)
        service.configure_hierarchy(spec.get('hierarchy'))
        from .evidence_world.summary_hints import vod_hint_configuration
        summary_hints = (deepcopy(spec['summary_hints']) if spec.get('summary_hints') is not None
                         else vod_hint_configuration(spec['srt_path'], video, vod.duration))
        if summary_hints:
            service.configure_summary_hints(**{k:v for k,v in summary_hints.items() if k != 'profile'})
        assets = [dict(family=a.family, path=a.source_locator["path"], sha256=a.bytes_sha256)
                  for a in service.assets.values() if a.bytes_sha256 is not None]
        assets.append(dict(family="metadata", path=str(root / "source-identity.json"),
                           sha256=file_hash(root / "source-identity.json")))
        job = bind_current_conversation_job(work_root=work, run_root=root, video_no=video,
            source_assets=assets, job_id=spec["job_id"], code_version=spec["code_version"],
            base_revision=base_revision, author=spec["author"], selected_pipeline=selected_pipeline)
        identity = current_conversation_job_binding(job)
        orientation_artifacts, orientations = [], []
        from .editorial_current_sources import prepare_sources
        bundle = prepare_sources(service=service, source_dir=source_dir, vod=vod,
                                 base_path=base, chunks=chunks, comment_source_dir=comment_dir)
        media = None
        media_path = prepared_media_path
        if media_path:
            media_path = native_path(media_path).resolve()
            if not media_path.is_relative_to(source_dir) or not media_path.is_file():
                raise ValueError('current_conversation_media_outside_source')
            media = dict(path=str(media_path), sha256=file_hash(media_path))
            bundle['frozen_assets'].append(dict(family='media', **media))
        if snapshot is not None:
            from .editorial_current_sources import assert_snapshot_sources
            assert_snapshot_sources(snapshot, assets, bundle,
                [a.public() for a in service.assets.values()])
            orientation_artifacts.append(dict(kind='frozen_source_snapshot', **deepcopy(spec['source_snapshot'])))
        packet = deepcopy(bundle['packet'])
        existing_broadcast_map = None
        if spec.get("packet_path"):
            packet, packet_sha = _json(spec["packet_path"])
            if not isinstance(packet, dict):
                raise ValueError("current_conversation_packet_shape")
            packet_binding = _orientation_binding(packet, spec["packet_path"], source, source_dir)
            if isinstance(packet.get("source"), dict) and isinstance(packet.get("packet"), dict):
                existing_broadcast_map = deepcopy(packet.get("outline"))
                packet = packet["packet"]
            elif isinstance(packet.get("source_material"), dict):
                material = packet["source_material"]
                packet = dict(chat_rows=material.get("chat") or [],
                    support_proposals=material.get("discovery_signals") or [],
                    navigation_source_reads=material.get("navigation_context"))
                for field in ('supplemental_sources', 'live_manager_context', 'live_manager_context_clock', 'elio_story_context'):
                    if field in material:
                        packet[field] = deepcopy(material[field])
            packet = deepcopy(packet)


            packet = {**deepcopy(bundle['packet']), **packet}


            if 'elio_story_context' in bundle['packet']:
                packet['elio_story_context'] = deepcopy(bundle['packet']['elio_story_context'])
            else:
                packet.pop('elio_story_context', None)

            for field in ("_media_frames", "_delivered_media_frames"):
                packet.pop(field, None)
            orientation_artifacts.append(dict(kind="existing_author_packet",
                path=str(native_path(spec["packet_path"]).resolve()), sha256=packet_sha,
                source_binding=packet_binding))
        paths = spec.get("orientation_paths", [])
        if not isinstance(paths, list):
            raise ValueError("current_conversation_orientation_paths_shape")
        for path in paths:
            orientation, sha = _json(path)
            if not isinstance(orientation, dict):
                raise ValueError("current_conversation_orientation_shape")
            binding = dict(kind="existing_source_orientation", path=str(native_path(path).resolve()), sha256=sha,
                source_binding=_orientation_binding(orientation, path, source, source_dir))
            orientation_artifacts.append(binding)
            orientations.append(dict(binding=binding, content=orientation,
                authority="source-bound orientation only; prior author/model judgments are not current decisions",
                current_writer_pixels_seen="UNVERIFIED"))
        flow_context = build_broadcast_flow_context(source=source, base_revision=base_revision,
            existing_broadcast_map=existing_broadcast_map, orientations=orientations)
        packet['_broadcast_flow_context'] = flow_context
        if spec.get('signal_priority_profile') is not None:
            packet['signal_priority_profile'] = spec['signal_priority_profile']
        else:
            packet.pop('signal_priority_profile', None)
        view, source_evidence = full_source_context(source=source, packet=packet)
        prompt_path = Path(__file__).resolve().parents[1] / prompt_asset.relative_path
        prompt_text = prompt_path.read_text(encoding='utf-8-sig')
        prompt_header = json.loads(prompt_text.splitlines()[1])
        prompt = dict(path=str(prompt_path), sha256=file_hash(prompt_path),
                      version=prompt_header['version'], text=prompt_text)
        navigation = None
        if spec.get('navigation_path'):
            navigation, sha = _json(spec['navigation_path'])
            from .editorial_multimodal_navigation import validate_navigation
            _, evidence = full_source_context(source=source, packet=packet)
            navigation = validate_navigation(navigation, source=source,
                evidence_catalog=evidence['evidence_catalog'], world_revision=service.world_revision,
                media_hashes={media['sha256']} if media else None)
            orientation_artifacts.append(dict(kind='multimodal_navigation',
                path=str(native_path(spec['navigation_path']).resolve()), sha256=sha))
        multimodal_runtime = None
        multimodal_context = None
        from .editorial_multimodal_navigation import resolve_local_runtime, LocalNavigationSession
        frozen_features = snapshot is not None and feature_profile is None
        if frozen_features and any(key in spec and spec[key] != snapshot.get(key)
                for key in ('multimodal_runtime', 'retrieval')):
            raise ValueError('current_conversation_snapshot_runtime_changed')
        multimodal_runtime = (deepcopy(snapshot.get('multimodal_runtime'))
            if frozen_features else resolve_local_runtime(spec.get('multimodal_runtime')))
        retrieval = (deepcopy(snapshot.get('retrieval')) if frozen_features
                     else spec.get('retrieval'))
        hierarchy = deepcopy(spec.get('hierarchy'))
        features = None
        if feature_profile is not None:
            from .editorial_current_sources import prepare_navigation_features
            features = prepare_navigation_features(service=service, source_dir=source_dir,
                runtime=multimodal_runtime, derived_root=root / 'derived',
                retrieval=retrieval, hierarchy=hierarchy,
                **(dict(read_index_root=spec['read_index_root']) if 'read_index_root' in spec else {}))
            retrieval, hierarchy = features['retrieval'], features['hierarchy']
            service.configure_hierarchy(hierarchy)
            if features.get('cluster_reader'):
                service.configure_cluster_reader(features['cluster_reader'])
            if features.get('selected_index_manifest'):
                orientation_artifacts.append(dict(kind='cached_document_index_manifest',
                    **features['selected_index_manifest']))
            if features['clusters']:
                orientation_artifacts.append(dict(kind='document_vector_navigation',
                    path=features['clusters']['path'], sha256=features['clusters']['sha256']))
        elif retrieval is None and multimodal_runtime is not None and snapshot is None:
            from .editorial_embedding_index import prepare_optional
            from .editorial_multimodal_navigation import _local_runtime_assets



            _, missing_asset = _local_runtime_assets(multimodal_runtime)
            retrieval=prepare_optional(service,source_dir=source_dir,runtime=multimodal_runtime,
                                       cached_only=missing_asset is not None)
        service.configure_retrieval(retrieval, search_mode=search_mode,
            **(dict(runtime=multimodal_runtime, index_root=features['index_root'],
                    query_root=features['query_root']) if features else {}))
        if multimodal_runtime is not None:
            multimodal_context = LocalNavigationSession(source=source, service=service, media_ref=media,
                runtime=multimodal_runtime, root=root / 'multimodal',
                **(dict(cache_root=features['derived_root']) if features else {})).snapshot()
        _create(root / "author-input.json", dict(base_revision=base_revision, source_material=view,
            response_fields=sorted(RESPONSE_FIELDS), source_id=service.source_id,
            response_optional_fields=dict(collaboration_periods='Optional source_evidence/provisional periods '
                'with actual original evidence_refs. Never claim manager/verified authority or invent member_vods.'),
            editorial_priority_contract='Point/proposal editorial_priority is optional: '
                '{tier:focus|normal|later,reason:<source-grounded review reason>,basis:author}. '
                'It does not select content, change cut boundaries or reorder the list. Story has no independent priority.',
            world_revision=service.world_revision, availability=service.admission(),
            **(dict(navigation_features=dict(profile=feature_profile,
                clusters=features['clusters'], derived=True, original_read=False,
                outside_navigation='source_order',
                **(dict(cluster_reader=features['cluster_reader']) if features.get('cluster_reader') else {}),
                guidance='Search the current question, follow a hit related_group_action when available, '
                         'then member original read actions. Follow returned continuation for unread members. '
                         'Source-order and optional hierarchy remain available. '
                         'Document cosine groups are navigation, not events, cut boundaries or importance. '
                         'Source-order, low-signal and unindexed originals remain available. '
                         'Preview grids sample only preserved media coverage in windows of at most120seconds; '
                         'scene/ocr keep their stated resolution requirements. No provider acquisition fallback. '
                         'Open the actual returned grid image, then individual precision frames if needed.')) if features else {}),
            source_read_contract=("Native CLI --action requires a complete EW envelope: "
                + canonical_json(dict(schema_version='ew.tool.v1', source_id=service.source_id,
                    world_revision=service.world_revision, question_id='<your question ID>',
                    action='<action>', args={}))
                + " Set your action and args inside this binding. For read_evidence use "
                "args={intervals_sec:[[start_sec,end_sec],...],families:['stt' or 'chat'],context_cues:2}, "
                "or exact record_ids returned by a tool; citation refs such as e1 are not record_ids. "
                "Input STT rows contain original text and help locate content, but reading those rows "
                "does not enter this job's read ledger. Before adopting/citing a claim in the first complete "
                "JSON, genuinely read its needed originals through this job and check their meaning; "
                "do not postpone all writing until all originals are read. Do not collect receipts without "
                "reading or delete supported citations merely to pass. Wait for a running command on the "
                "same job to finish before dependent read/frame/draft commands; independent jobs are separate. "
                "Original read receipts record delivery only, not understanding. "
                "The CLI sends ew.owner_read.v1 for compact pages: merge record_defaults with "
                "zip(record_columns, each record_rows entry) to recover exact original text, IDs and clocks. "
                "Full provenance remains in the immutable read_id artifact. A next_cursor means unread "
                "originals remain; call continuation --read-id <returned read_id> with this job root and "
                "expected revision, then execute its next_original_action unchanged. It preserves the "
                "same read selector, families, question, page budget and cursor. Do not assemble a cursor-only action. "
                "A page boundary never establishes meaning completeness."),
            working_draft_contract=dict(command='draft', fields_argument='--fields',
                fields_optional=True, field_names=sorted(RESPONSE_FIELDS | OPTIONAL_RESPONSE_FIELDS),
                provisional=True, original_evidence=False,
                policy='Optional same-writer partial author fields; omit --fields to reopen. '
                    'Supplied fields replace the whole field, including explicit []; omitted fields retain. '
                    'Notes never apply to draft fields. Submit still requires your complete final JSON '
                    'and never adds or merges draft content automatically.'),
            read_page_policy=read_page_policy,
            search_mode=search_mode,
            **(dict(authoring_time_contract=dict(minutes=minutes,
                control='advisory_current_conversation', status_command='status',
                arguments=['--job-root', '--expected-revision'],
                policy='The requested minutes replace legacy 10-15 minute examples for this job. '
                    'Writing starts after this input is ready. Query status for current time; '
                    'on overrun finish preserved work, keeping unsupported work pending. '
                    'No model cancellation or transport budget reset is available.')) if minutes is not None else {}),
            existing_orientation=orientations, broadcast_flow_context=flow_context, author_prompt=prompt,
            retrieval_contract=service.retrieval_contract(), multimodal_navigation=navigation,
            **(dict(source_search_contract=("For this job's STT/chat topic searches, omit query.mode to use "
                "retrieval_contract.text_default_mode, including when an older author prompt illustrates "
                "explicit hybrid_branch_v1. Explicit exact/lexical remains available for literal wording. "
                "Mixed media families keep the full lexical scope; search hits still require original reading."))
               if service.search_mode != 'lexical' else {}),
            **(dict(multimodal_context=multimodal_context) if multimodal_runtime is not None else {}),
            **(dict(multimodal_final_review=dict(policy='consume_or_defer_v1',
                response_optional_field='multimodal_decision',
                instruction="When ready, consume a source-bound multimodal result and compare useful originals before final writing. Otherwise include multimodal_decision={decision:'defer',reason:<specific reason>}; availability is not use."))
                if multimodal_runtime is not None else {}),
            media_source=media,
            media_read_contract=dict(actions=['read_frames', 'read_frame_page'],
                image_selection=dict(frame_ref='a returned frame ref', alt='short visible description', caption='image caption'),
                policy="Use the same complete EW envelope. read_frames args={start_sec,duration_sec,"
                    "inspection_mode:'scene'|'ocr'|'preview'} uses numeric seconds for one continuous window, "
                    "not intervals_sec. read_frame_page args={cursor:returned_cursor} uses its returned cursor. "
                    "Availability and window limits follow the actual selected media profile. Select only "
                    "inspected same-source pixels inside the authored range or source span; returned "
                    "locators alone do not prove viewing."),
            retained_collaboration=deepcopy(bundle['retained_collaboration']),
            context_memory_contract=dict(commands=['remember', 'memory'],
                notes_are_hypotheses=True, original_reads_required=True,
                expected_revision_argument='--expected-revision',
                expected_revision_from='prepare.prepared_revision',
                remember=dict(note_argument='--note', note_shape=_CONTEXT_NOTE_FIELDS),
                memory=dict(query_argument='--query', query_optional=True))))
        manifest_path = service.root / "manifest.json"
        prepared = dict(schema_version=SCHEMA, identity=identity, spec_sha256=spec_sha256,
            code_hashes=_code_hashes(prompt_path), source=source, source_sha256=digest(source),
            base_path=str(base), vod_info=deepcopy(spec["vod_info"]), packet=packet,
            source_bundle=bundle, retrieval=deepcopy(retrieval), read_page_policy=read_page_policy,
            search_mode=search_mode,
            **(dict(summary_hints=summary_hints) if summary_hints else {}),
            hierarchy=hierarchy, multimodal_navigation=navigation, media_source=media,
            **(dict(feature_profile=feature_profile, navigation_features=features) if features else {}),
            **(dict(multimodal_runtime=multimodal_runtime) if multimodal_runtime is not None else {}),
            **(dict(multimodal_final_review=dict(policy='consume_or_defer_v1',
                initial_context=deepcopy(multimodal_context))) if multimodal_runtime is not None else {}),
            orientation_artifacts=orientation_artifacts,
            world_manifest_path=str(manifest_path), world_manifest_sha256=file_hash(manifest_path),
            world_revision=service.world_revision, input_sha256=file_hash(root / "author-input.json"))
        if 'signal_priority_trace' in source_evidence:
            prepared['signal_priority_trace'] = source_evidence['signal_priority_trace']
        if spec.get('author_prompt_profile') is not None:
            prepared['author_prompt_profile'] = spec['author_prompt_profile']
        if minutes is not None:
            clock_path = root / 'authoring-time.json'
            if clock_path.exists():
                clock, _ = _json(clock_path)
                if any(clock.get(key) != value for key, value in dict(minutes=minutes,
                        spec_sha256=spec_sha256, input_sha256=prepared['input_sha256']).items()):
                    raise ValueError('current_conversation_authoring_time_identity_changed')
            else:
                started = time.time()
                _create(clock_path, dict(minutes=minutes, spec_sha256=spec_sha256,
                    input_sha256=prepared['input_sha256'], started_at=started,
                    preparation_seconds=max(0, started - preparation_started)))
            prepared['authoring_time'] = dict(path=str(clock_path), sha256=file_hash(clock_path))
        prepared["prepared_revision"] = digest(prepared)
        _create(root / "prepared.json", prepared)
        return _prepare_receipt(root, prepared)

def _prepare_receipt(root, prepared):
    receipt = dict(job_root=str(root), prepared_revision=prepared["prepared_revision"],
        base_revision=prepared["identity"]["base_revision"], author_input_path=str(root / "author-input.json"),
        author_input_sha256=prepared["input_sha256"], world_revision=prepared["world_revision"],
        source_id=prepared["source"]["asset"]["source_id"], external_transport_calls=0)
    if prepared.get('authoring_time'):
        receipt['authoring_time'] = _authoring_time(root, prepared)
    return receipt

def _authoring_time(root, prepared):
    """Observe the opt-in clock; no deadline, read authority or budget is changed."""
    binding = prepared.get('authoring_time')
    if not binding:
        return None
    path = root / 'authoring-time.json'
    if native_path(binding['path']).resolve() != path.resolve() or file_hash(path) != binding['sha256']:
        raise ValueError('current_conversation_authoring_time_changed')
    terminal = root / 'submission-result.json'
    if terminal.exists():
        return _json(terminal)[0].get('authoring_time')
    clock, _ = _json(path)
    observed = time.time()
    elapsed = max(0, observed - clock['started_at'])
    allowance = clock['minutes'] * 60
    return dict(minutes=clock['minutes'], started_at=clock['started_at'], observed_at=observed,
        preparation_seconds=clock['preparation_seconds'], elapsed_seconds=elapsed,
        remaining_seconds=max(0, allowance - elapsed), overrun_seconds=max(0, elapsed - allowance),
        phase='overdue' if elapsed >= allowance else 'writing',
        control='advisory_current_conversation', can_cancel_inflight_generation=False,
        clock_basis='unix_wall_clock; system clock adjustments affect observation',
        tool_wait_seconds=None, repair_seconds=None, connection_recovery_seconds=None,
        manager_saved=False)

def status(job_root, *, expected_revision):
    """Observe this job without rewriting its input or bounded original-read wire."""
    root, prepared, _ = _load(job_root)
    with build_lock(root):
        if expected_revision != prepared['prepared_revision']:
            raise ValueError('current_conversation_prepare_conflict')
        state, clock = 'not_submitted', _authoring_time(root, prepared)
        if (root / 'submission.json').exists():
            state = 'accepted_render_pending'
            if (root / 'submission-result.json').exists():
                state = 'terminal_record_present'
        return dict(prepared_revision=prepared['prepared_revision'], submission_state=state, authoring_time=clock)

def read(job_root, action, *, expected_revision):
    root, prepared, _ = _load(job_root)
    with build_lock(root):
        if expected_revision != prepared["prepared_revision"]:
            raise ValueError("current_conversation_prepare_conflict")
        if (root / "submission.json").exists():
            raise ValueError("current_conversation_submission_frozen")
        service = _service(root, prepared)

        from .evidence_world.context_session import act
        if isinstance(action, dict) and action.get('action') in {'read_frames', 'read_frame_page'}:
            if (set(action) != {'schema_version', 'source_id', 'world_revision', 'question_id', 'action', 'args'}
                    or action['schema_version'] != 'ew.tool.v1'
                    or action['source_id'] != service.source_id or action['world_revision'] != service.world_revision
                    or not isinstance(action['question_id'], str) or not action['question_id'].strip()):
                raise ValueError('author_media_action_invalid')
            media, _ = _frame_session(root, prepared)
            if media is None:
                return dict(status='pending', error_code='media_not_collected', question_id=action['question_id'],
                    frame_refs=[], frames=[], pixels_seen='UNVERIFIED', audio_listened=False)
            result = _frame_page_result(media, media.dispatch(action), include_grid=bool(prepared.get('feature_profile')))
        else:
            result = act(service, action)
        _create(root / "action-results" / (digest([action, result]) + ".json"),
                dict(action=action, result=result, result_sha256=digest(result)))
        return result

def _frame_page_result(session, result, *, include_grid=False):
    """Return this page's actual owned image locators for the current writer."""
    frames = [dict(frame_ref=ref, source_sec=session.active[ref]['source_sec'],
                   image=deepcopy(session.active[ref]['image']),
                   **{key:deepcopy(session.active[ref][key]) for key in ('time_binding', 'source_interval_sec')
                      if key in session.active[ref]}) for ref in result.get('frame_refs', [])]
    return dict(result, frames=frames,
                **(dict(preview_grid=deepcopy(session.active_grid))
                   if include_grid and session.active_grid else {}),
                pixels='Inspect the returned local images; paths are not a visual interpretation.',
                pixels_seen='UNVERIFIED', audio_listened=False)

def _frame_session(root, prepared):
    """Reuse the source media owner and replay only pages returned by this job."""
    from .editorial_media_request import current_conversation_media_session
    session = current_conversation_media_session(root=root, prepared=prepared)
    delivered = {}
    if session is None:
        return None, delivered
    for path in sorted((root / 'action-results').glob('*.json')):
        entry, _ = _json(path)
        action = entry['action']
        if action.get('action') not in {'read_frames', 'read_frame_page'}:
            continue
        if digest(entry['result']) != entry['result_sha256']:
            raise ValueError('current_conversation_read_changed')
        result = _frame_page_result(session, session.dispatch(action), include_grid=bool(prepared.get('feature_profile')))
        if result != entry['result']:
            raise ValueError('current_conversation_frame_read_changed')
        delivered.update({ref:deepcopy(session.active[ref]) for ref in result.get('frame_refs', [])})
    return session, delivered

def remember(job_root, note, *, expected_revision):
    root, prepared, _ = _load(job_root)
    with build_lock(root):
        if expected_revision != prepared['prepared_revision'] or (root / 'submission.json').exists():
            raise ValueError('current_conversation_note_conflict')
        if not isinstance(note, dict) or set(note) != _CONTEXT_NOTE_FIELDS.keys():
            raise ValueError('current_conversation_note_shape')
        from .evidence_world.context_session import remember as retain
        return retain(_service(root, prepared), **note)

def memory(job_root, *, expected_revision, query=None):
    root, prepared, _ = _load(job_root)
    if expected_revision != prepared['prepared_revision']:
        raise ValueError('current_conversation_memory_conflict')
    from .evidence_world.context_session import memory as recall
    return recall(_service(root, prepared), query=query)

def continuation(job_root, *, read_id, expected_revision):
    """Recover an exact delivered query without enlarging or mutating read pages."""
    root, prepared, _ = _load(job_root)
    if expected_revision != prepared['prepared_revision']:
        raise ValueError('current_conversation_prepare_conflict')
    page = _service(root, prepared).load_read(read_id)
    original = None
    for path in sorted((root / 'action-results').glob('*.json')):
        entry, _ = _json(path)
        result, action = entry['result'], entry['action']
        if result.get('read_id') != read_id:
            continue
        if (digest(result) != entry['result_sha256'] or digest(result) != digest(page)
                or path.stem != digest([action, result])):
            raise ValueError('current_conversation_read_changed')
        if (action.get('action') == 'read_evidence' and 'read_id' not in action.get('args', {})
                and any(key in action.get('args', {}) for key in ('record_ids', 'intervals_sec', 'intervals_utc_ms'))):
            original = action
    if original is None:
        raise ValueError('current_conversation_original_read_action_missing')
    from .editorial_author_source import _read_continuation_action
    return dict(read_id=read_id, original_evidence=False,
                next_original_action=_read_continuation_action(page, original, root))

def draft(job_root, fields=None, *, expected_revision):
    """Retain this writer's provisional fields; submit never consumes this file."""
    from .editorial_full_author import RESPONSE_FIELDS, OPTIONAL_RESPONSE_FIELDS
    from .evidence_world.index import atomic_json
    root, prepared, _ = _load(job_root)
    with build_lock(root):
        if (expected_revision != prepared['prepared_revision']
                or fields is not None and (root / 'submission.json').exists()):
            raise ValueError('current_conversation_draft_conflict')
        if fields is not None and (not isinstance(fields, dict)
                or set(fields) - RESPONSE_FIELDS - OPTIONAL_RESPONSE_FIELDS):
            raise ValueError('current_conversation_draft_fields')
        path = root / 'current-draft.json'
        prior = {}
        if path.exists():
            saved, _ = _json(path)
            body = {key:value for key, value in saved.items() if key != 'draft_revision'}
            if (saved.get('schema_version') != 'chzz.current_writer_draft.v1'
                    or saved.get('prepared_revision') != expected_revision
                    or saved.get('draft_revision') != digest(body) or not isinstance(saved.get('fields'), dict)):
                raise ValueError('current_conversation_draft_changed')
            prior = saved['fields']
        updates = deepcopy(fields) if fields is not None else {}
        application = dict(applied_fields=sorted(updates),
            changed_fields=sorted(key for key in updates if key not in prior or updates[key] != prior[key]),
            retained_fields=sorted(set(prior) - set(updates)), notes_apply_to_draft=False)
        body = dict(schema_version='chzz.current_writer_draft.v1', prepared_revision=expected_revision,
                    fields={**prior, **updates})
        revision = digest(body)
        if fields is not None:
            atomic_json(path, dict(body, draft_revision=revision))
        return dict(draft=body['fields'], draft_revision=revision, provisional=True,
                    original_evidence=False, meaning_complete=False, draft_application=application)

def _navigation_media_hashes(prepared):
    media = prepared.get('media_source')
    if media is None:
        return None
    path = native_path(media['path'])
    if not path.is_file() or file_hash(path) != media['sha256']:
        raise ValueError('current_conversation_support_source_changed')
    return {media['sha256']}

def multimodal(job_root, action, *, expected_revision):
    """Use the same bounded local media retrieval tool as the normal author."""
    from .editorial_multimodal_navigation import LocalNavigationSession, unavailable_navigation
    root, prepared, _ = _load(job_root)
    with build_lock(root):
        if expected_revision != prepared['prepared_revision'] or (root / 'submission.json').exists():
            raise ValueError('current_conversation_navigation_conflict')
        if prepared.get('multimodal_runtime') is None:
            raise ValueError('current_conversation_multimodal_runtime_not_configured')
        session = LocalNavigationSession(source=prepared['source'], service=_service(root, prepared),
            media_ref=prepared.get('media_source'), runtime=prepared['multimodal_runtime'],
            root=root / 'multimodal',
            **(dict(cache_root=prepared['navigation_features']['derived_root'])
               if prepared.get('navigation_features') else {}))
        try:
            result = session.execute(action)
        except ValueError as exc:
            result = unavailable_navigation(exc, session)
        entry = dict(prepared_revision=expected_revision, result=result,
            **(dict(action=deepcopy(action)) if result.get('status') == 'unavailable' else {}),
            authority='retrieval candidates only; actual original reads required')
        _create(root / 'navigation-results' / (digest(entry) + '.json'), entry)
        return result

def _navigation_packet(root, prepared):


    packet = deepcopy(prepared['packet'])
    _, _, chats = _reads(root, prepared)
    rows = {r['ref']: r for r in packet.get('chat_rows', [])}
    rows.update({r['ref']: r for r in chats})
    packet['chat_rows'] = list(rows.values())
    return packet

def _navigation_receipts(root, prepared):
    from .editorial_full_author import full_source_context
    from .editorial_multimodal_navigation import validate_navigation, RECOVERABLE_LOCAL_ERRORS
    _, evidence = full_source_context(source=prepared['source'], packet=_navigation_packet(root, prepared))
    if prepared.get('multimodal_navigation'):
        _navigation_media_hashes(prepared)
    receipts = []
    for path in sorted((root / 'navigation-results').glob('*.json')):
        entry, sha = _json(path)
        if path.stem != digest(entry) or entry.get('prepared_revision') != prepared['prepared_revision']:
            raise ValueError('current_conversation_navigation_changed')
        result = entry['result']
        if result.get('status') == 'unavailable':
            action = entry.get('action') or {}
            context = result.get('multimodal_context') or {}
            if (set(result) != {'status', 'reason', 'multimodal_context', 'read_actions', 'original_read'}
                    or result['reason'] not in RECOVERABLE_LOCAL_ERRORS
                    or result['original_read'] is not False or result['read_actions'] != []
                    or any(context.get(k) is not False for k in
                           ('original_read', 'writer_pixels_seen', 'audio_listened', 'meaning_complete'))
                    or action.get('schema_version') != 'ew.tool.v1'
                    or action.get('action') != 'navigate_multimodal'
                    or action.get('source_id') != prepared['source']['asset']['source_id']
                    or action.get('world_revision') != prepared['world_revision']):
                raise ValueError('current_conversation_unavailable_navigation_changed')
            receipts.append(dict(path=str(path), sha256=sha, status='unavailable',
                reason=result['reason'], navigation_pages=[], original_read=False))
            continue
        pages = result.get('navigation_pages') or [result['navigation']]
        media_hashes = _navigation_media_hashes(prepared)


        chat_ids = sorted({original['ref'][len('chat-original:'):]
            for page in pages for candidate in page.get('candidates', [])
            for original in candidate.get('originals', [])
            if isinstance(original.get('ref'), str) and original['ref'].startswith('chat-original:')})
        catalog = dict(evidence['evidence_catalog'])
        service = _service(root, prepared)
        with service.open() as db:
            for offset in range(0, len(chat_ids), 256):
                batch = chat_ids[offset:offset + 256]
                placeholders = ','.join('?' for _ in batch)
                for row in db.execute("SELECT payload_json FROM records WHERE family='chat' AND record_id IN ("
                                      + placeholders + ')', batch):
                    record = json.loads(row[0])
                    if record['source_id'] != service.source_id:
                        raise ValueError('current_conversation_navigation_wrong_chat_source')
                    projected = dict(ref='chat-original:' + record['record_id'], text=record['raw_text'],
                        time_sec=record['time_index']['start_us'] / 1_000_000 if record.get('time_index') else None)
                    _, native = full_source_context(source=prepared['source'], packet=dict(chat_rows=[projected]))
                    catalog.update(native['evidence_catalog'])
        for page in pages:
            validate_navigation(page, source=prepared['source'],
                evidence_catalog=catalog, world_revision=prepared['world_revision'],
                media_hashes=media_hashes)
        receipts.append(dict(path=str(path), sha256=sha, navigation_pages=pages))
    return receipts

def _reads(root, prepared):
    service = _service(root, prepared)
    receipts, complete_ids, chats, fragments = [], set(), {}, {}
    for path in sorted((root / "action-results").glob("*.json")):
        entry, sha = _json(path)
        page = entry["result"]
        if digest(page) != entry["result_sha256"]:
            raise ValueError("current_conversation_read_changed")
        if not page.get("read_id"):
            continue
        saved = service.load_read(page["read_id"])
        if digest(saved) != digest(page):
            raise ValueError("current_conversation_read_receipt_mismatch")
        receipts.append(dict(read_id=page["read_id"], sha256=sha, result_sha256=digest(page), path=str(path)))
        for row in page["records"]:
            fragments.setdefault(row["record_id"], []).append(row)
    native = {r["record_id"]: r for r in prepared["source"]["records"]}
    for record_id, pieces in fragments.items():
        text, end = "", 0
        total = pieces[0]["raw_char_total"]
        for row in sorted(pieces, key=lambda r: (r["raw_char_start"], r["raw_char_end"])):
            start, finish = row["raw_char_start"], row["raw_char_end"]
            if row["raw_char_total"] != total or start > end:
                break
            if start < end and text[start:min(end, finish)] != row["raw_text"][:min(end, finish)-start]:
                raise ValueError("current_conversation_read_fragment_conflict")
            if finish > end:
                text += row["raw_text"][end-start:]
                end = finish
        if end != total:
            continue
        if record_id in native and text != native[record_id]["raw_text"]:
            raise ValueError("current_conversation_read_native_text_changed")
        complete_ids.add(record_id)
        row = pieces[0]
        if row["family"] == "chat" and row.get("time_index"):
            chats[record_id] = dict(ref="chat-original:" + record_id,
                time_sec=row["time_index"]["start_us"] / 1000000, text=text)
    return receipts, complete_ids, list(chats.values())

def _citation_paths(value, path=()):
    refs = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"summary_evidence_refs", "evidence_refs", "used_evidence_ids",
                       "start_evidence_ref", "end_evidence_ref", "frame_ref"}:
                if isinstance(item, str):
                    refs.setdefault(item, []).append([*path, key])
                elif isinstance(item, list):
                    for index, ref in enumerate(item):
                        if isinstance(ref, str):
                            refs.setdefault(ref, []).append([*path, key, index])
            else:
                for ref, paths in _citation_paths(item, (*path, key)).items():
                    refs.setdefault(ref, []).extend(paths)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            for ref, paths in _citation_paths(item, (*path, index)).items():
                refs.setdefault(ref, []).extend(paths)
    return refs

def _multimodal_final_review(prepared, payload, navigation_receipts):
    """Fresh-job final condition; never impose a new call on an old job."""
    review = prepared.get('multimodal_final_review') or {}
    decision = payload.get('multimodal_decision')
    consumed = bool(navigation_receipts or prepared.get('multimodal_navigation'))
    if decision is not None and (not isinstance(decision, dict)
            or set(decision) != {'decision', 'reason'} or decision['decision'] != 'defer'
            or not isinstance(decision['reason'], str) or not decision['reason'].strip()
            or len(decision['reason'].encode('utf-8')) > 8000):
        raise ValueError('current_conversation_multimodal_defer_invalid')
    if (review.get('policy') == 'consume_or_defer_v1'
            and review.get('initial_context', {}).get('status') == 'ready'
            and not consumed and decision is None):
        raise ValueError('current_conversation_multimodal_use_or_specific_defer_required')
    return dict(policy=review.get('policy'), source_bound_results=len(navigation_receipts),
        prepared_navigation_delivered=bool(prepared.get('multimodal_navigation')),
        decision=deepcopy(decision), initial_status=review.get('initial_context', {}).get('status'),
        semantic_adoption='UNVERIFIED; compare authored output and original reads')

def submit(job_root, response_path, *, expected_revision):
    """Validate one new author response; terminal unknown submissions are never resent."""
    from .editorial_full_author import validate_current_conversation_response
    from .editorial_highlight_projection import build_qg1_preview, validate_qg1_preview_payload
    root, prepared, job = _load(job_root)
    payload, raw_sha = _json(response_path)
    with build_lock(root):
        if expected_revision != prepared["prepared_revision"]:
            raise ValueError("current_conversation_prepare_conflict")
        submission = root / "submission.json"
        terminal = root / "submission-result.json"
        if submission.exists():
            prior, _ = _json(submission)
            if prior["raw_response_sha256"] != raw_sha:
                raise ValueError("current_conversation_submit_conflict")
            if not terminal.exists():
                raise ValueError("current_conversation_submit_unsettled_no_resend")
            report = _json(terminal)[0]
            if (file_hash(root / "response.raw.json") != raw_sha
                    or digest(_json(root / "result.json")[0]) != report["result_sha256"]):
                raise ValueError("current_conversation_submitted_artifact_changed")
            return report
        raw = native_path(response_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != raw_sha:
            raise ValueError("current_conversation_response_changed")
        candidate = root / "response-candidates" / (raw_sha + ".json")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.exists():
            if candidate.read_bytes() != raw:
                raise ValueError("current_conversation_response_archive_changed")
        else:
            with candidate.open("xb") as stream:
                stream.write(raw)
        receipts, complete_ids, chats = _reads(root, prepared)
        ref_ids = {"e" + str(r["ordinal"] + 1): r["record_id"] for r in prepared["source"]["records"]}
        cited = _citation_paths(payload)
        packet = deepcopy(prepared["packet"])
        packet.update(chat_rows=chats, chat_source_revision=digest(prepared["identity"]["source_assets"]))
        try:
            missing = [dict(ref=ref, record_id=ref_ids[ref], paths=cited[ref])
                       for ref in sorted(cited) if ref in ref_ids and ref_ids[ref] not in complete_ids]
            if not receipts or missing:
                error = ValueError('current_conversation_cited_original_not_read')
                error.submission_diagnostics = dict(missing_originals=missing,
                    original_read_required=not bool(receipts),
                    instruction='Read these exact originals through this job, including every page. '
                        'Search, notes and navigation do not grant read authority. Preserve the response '
                        'and unrelated items; resubmit only after genuine reads or a supported local correction.')
                raise error
            if any(ref.startswith('frame:') for ref in cited):
                _, frames = _frame_session(root, prepared)
                packet['_delivered_media_frames'] = frames
            navigation_receipts = _navigation_receipts(root, prepared)
            multimodal_review = _multimodal_final_review(prepared, payload, navigation_receipts)
            semantic_payload = {k:v for k,v in payload.items() if k != 'multimodal_decision'}
            result = validate_current_conversation_response(semantic_payload, job=job, source=prepared["source"],
                packet=packet, duration_sec=prepared["vod_info"]["duration"], raw_sha256=raw_sha,
                source_reads=receipts)
        except ValueError as exc:
            diagnostics = deepcopy(getattr(exc, 'submission_diagnostics', {}))
            for field in ('item_path', 'range_findings', 'flow_findings'):
                if hasattr(exc, field):
                    diagnostics[field] = deepcopy(getattr(exc, field))
            rejection = dict(schema_version='chzz.current_conversation_rejection.v1', status='rejected',
                raw_response_sha256=raw_sha, prepared_revision=expected_revision,
                error=str(exc), diagnostics=diagnostics, external_transport_calls=0)


            _create(root / 'response-rejections' / (raw_sha + '-' + digest(rejection) + '.json'), rejection)
            raise SubmissionRejected(rejection) from exc
        quality = dict(schema_version="chzz.editorial_highlight_quality.v1",
            authority_revision_id=result["authority_revision_id"], cumulative_call_count=0,
            selected_model=prepared["identity"]["author"]["actual_model"],
            model_calls=[], completion_state=result["completion_state"], stable_saved_public_changed=False)
        result['navigation_receipts'] = navigation_receipts
        result['full_author_receipt']['multimodal_final_review'] = multimodal_review


        supplemental = prepared['packet'].get('supplemental_sources', {})
        elio = prepared['packet'].get('elio_story_context', {})
        navigation_pages = [p for r in result['navigation_receipts'] for p in r.get('navigation_pages', [])]
        result['full_author_receipt']['supporting_inputs'] = dict(
            manager_memo=dict(projection_sha256=supplemental.get('projection_sha256'),
                delivered_characters=len(supplemental.get('manager_context', ''))),
            timeline_comments=dict(anchors=len(supplemental.get('timeline_comment_context', {}).get('anchors', [])),
                full_selected_comment_bodies=len(supplemental.get('timeline_comment_context', {}).get('private_comment_semantic_context', {}).get('comments', []))),
            elio=dict(projection_sha256=elio.get('projection_sha256'),
                delivered_pages=[dict(url=p['url'], page_sha256=p.get('page_sha256'), story=p.get('story'), fragment=p.get('fragment'))
                    for p in elio.get('pages', []) if p['status'] == 'available'],
                unavailable_pages=[dict(url=p['url'], reason=p['reason']) for p in elio.get('pages', []) if p['status'] != 'available']),
            multimodal=dict(navigation_receipts=len(result['navigation_receipts']), navigation_pages=len(navigation_pages),
                original_reread_required=True, whole_broadcast=False, pixels_delivered=False, audio_listened=False),
            policy='Input delivery and source-bound navigation only; semantic adoption requires original comparison and author judgment.')
        projected = result["full_author_projection"]
        from .editorial_full_author import bind_collaboration_lane
        retained = prepared['source_bundle']['retained_collaboration']
        bind_collaboration_lane(result, retained=retained.get('collaboration_periods'),
            retained_diagnostics=retained.get('collaboration_diagnostics'),
            duration_sec=prepared['vod_info']['duration'])
        preview = build_qg1_preview(broadcast_map=projected["outline"], editorial_result=result,
            quality=quality, story_selection=projected["story_shadow"]["story_point_selection"],
            source_broadcast_map=projected["outline"], projection_mode="qg1_review_candidate")
        validate_qg1_preview_payload(preview, expected_broadcast_map=projected["outline"])

        raw = native_path(response_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != raw_sha:
            raise ValueError("current_conversation_response_changed")
        output = root / "report"
        if output.exists() and any(output.iterdir()):
            raise ValueError("current_conversation_new_report_only")
        _create(submission, dict(prepared_revision=expected_revision, raw_response_sha256=raw_sha,
            state="accepted_render_pending", author=prepared["identity"]["author"], external_transport_calls=0))
        with (root / "response.raw.json").open("xb") as stream:
            stream.write(raw)
        _create(root / "result.json", result)
        _create(root / "quality.json", quality)
        _create(root / "qg1-review-candidate.json", preview)
        paths = _render(prepared, result, preview, output)
        report = dict(status="new_normal_report_created", prepared_revision=expected_revision,
            raw_response_sha256=raw_sha, result_sha256=digest(result), paths=paths,
            external_transport_calls=0, manager_saved=False, published=False,
            original_report_changed=False, completion_state=result["completion_state"])
        clock = _authoring_time(root, prepared)
        if clock is not None:
            report['authoring_time'] = dict(clock, phase='generated_report')
        _create(terminal, report)
        return report

def _render(prepared, result, preview, output):
    from .models import VODInfo
    from .summary.report_generation import generate_reports
    from .outline.render import project_manager_outline_markdown
    projected = result["full_author_projection"]
    authority = dict(outline=projected["outline"], source_kind="generated_runtime_projection",
        revision_id=result["authority_revision_id"],
        source_path=str(native_path(prepared["identity"]["run_root"]) / "result.json"),
        manager_markdown=project_manager_outline_markdown(projected["outline"],
            duration_sec=prepared["vod_info"]["duration"]))
    paths = generate_reports(projected["summary"], VODInfo(**prepared["vod_info"]),
        prepared['source_bundle']['report']['signal_highlights'],
        prepared['source_bundle']['report']['chats'], str(output),
        srt_path=prepared["source"]["asset"]["source_locator"]["path"],
        authoritative_broadcast_map=authority, editorial_review_packet=preview,
        summary_pipeline_requested=prepared["identity"]["selected_pipeline"],
        summary_pipeline_actual=prepared["identity"]["selected_pipeline"],
        producer_code_version=prepared["identity"]["code_version"],
        artifact_discriminator="current-conversation:" + prepared["prepared_revision"],
        llm_provider_provenance=result["full_author_receipt"],
        work_dir=native_path(prepared["identity"]["work_root"]),
        **_report_source_kwargs(prepared))
    return dict(markdown=paths[0], html=paths[1], metadata=paths[2])

def _report_source_kwargs(prepared):
    from .editorial_current_sources import report_kwargs
    kwargs = report_kwargs(prepared['source_bundle'])
    kwargs.pop('chats')
    kwargs.pop('signal_highlights')
    return kwargs

def cli_result_json(result):
    """Share the exact bounded read wire with the standalone Loki CLI."""
    if isinstance(result, dict):
        if result.get('schema_version') == 'ew.read_result.v1' and result.get('read_page_policy') == 'balanced_compact_v1':
            from .evidence_world.owner_payload import compact_read
            result = compact_read(result)
        if result.get('schema_version') in {'ew.owner_read.v1', 'ew.read_result.v1'}:
            return canonical_json(result)
    return json.dumps(result, ensure_ascii=False, indent=2) + '\n'
