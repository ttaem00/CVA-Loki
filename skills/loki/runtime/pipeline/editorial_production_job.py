"""Bind an explicitly selected normal 3.0.1/3.0.2 job to its prepared sources.

This is provenance and containment, not a spending grant. The existing central
Gateway remains the only request admission authority. Candidate grants retain
their separate contract and cannot be promoted by deserializing this identity.
"""

from __future__ import annotations

from .config import FULL_AUTHOR_PIPELINES

from dataclasses import dataclass

import json

import re

from .evidence_world.contracts import canonical_json, file_hash, native_path

KEY = "_editorial_production_job"

_MINT = object()

CURRENT_CONVERSATION_MODE = "current_conversation_codex"

@dataclass(frozen=True, slots=True)
class CurrentConversationJob:
    """Source authority for the current conversation, never a gateway grant."""
    identity_json: str
    authority: object

def bind_current_conversation_job(*, work_root, run_root, video_no, source_assets,
                                  job_id, code_version, base_revision, author,
                                  selected_pipeline="aemeth_3_0_2"):
    """Mint after checking prepared originals; serialized handles have no authority."""
    work, root = native_path(work_root).resolve(), native_path(run_root).resolve()
    video_no = str(video_no)
    if (not video_no.isdigit() or root == work / video_no
            or not root.is_relative_to(work / video_no)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", str(job_id))
            or not re.fullmatch(r"[0-9a-f]{64}", str(base_revision))
            or not re.fullmatch(r"[0-9a-f]{40}", str(code_version))):
        raise ValueError("current_conversation_job_identity_invalid")
    if (not isinstance(author, dict) or set(author) != {"thread_id", "turn_id", "actual_model"}
            or not all(isinstance(v, str) and v.strip() for v in author.values())):
        raise ValueError("current_conversation_author_identity_invalid")
    if selected_pipeline not in FULL_AUTHOR_PIPELINES:
        raise ValueError("current_conversation_pipeline_invalid")
    identity = dict(schema_version="chzz.current_conversation_job.v1", job_id=job_id,
        selected_pipeline=selected_pipeline, summary_author_mode=CURRENT_CONVERSATION_MODE,
        admission_authority="current_conversation", external_transport_calls=0,
        code_version=code_version, video_no=video_no, work_root=str(work), run_root=str(root),
        base_revision=base_revision, author=dict(author),
        source_assets=_sources(source_assets, video_no, work))
    return CurrentConversationJob(canonical_json(identity), _MINT)

def current_conversation_job_binding(job):
    if type(job) is not CurrentConversationJob or job.authority is not _MINT:
        raise ValueError("current_conversation_serialized_authority_rejected")
    identity = json.loads(job.identity_json)
    if _sources(identity["source_assets"], identity["video_no"], identity["work_root"]) != identity["source_assets"]:
        raise ValueError("current_conversation_source_changed")
    return identity

_SCOPE_KEYS = ("source_navigation_backend", "evidence_world_source_policy", "manager_outline_enabled",
               "summary_author_mode", "editorial_full_author",
               "editorial_live_source_clock", "editorial_live_manager_context",
               "live_finalize_archive", "live_finalize_source",
               "audio_reaction_peaks_enabled", "chzzk_clips_enabled", "timeline_comments_auto_collect_enabled",
               "editorial_context_media_requests", "evidence_world_media_requests", "editorial_context_audio_reread",
               "editorial_intent_comparison", "editorial_memory_connection", "editorial_codex_session")

_MEDIA_SCOPE_KEYS = ("editorial_context_visual_input", "editorial_context_author_media_page_policy",
    "editorial_context_author_media_page_size", "codex_cli_require_isolated_paths",
    "codex_cli_required_runtime_root", "codex_cli_usage_sink_path", "codex_cli_temp_root")

@dataclass(frozen=True, slots=True)
class _ProductionJob:
    identity_json: str
    value_json: str
    work_root: str
    run_root: str
    authority: object
    recovery: _NormalTransportRecovery | None = None

    def __deepcopy__(self, memo):
        return self

@dataclass(frozen=True, slots=True)
class _NormalTransportRecovery:
    job_root: str
    identity_json: str
    attempts_json: str
    files_json: str
    provenance_json: str
    authority: object

    def __deepcopy__(self, memo):
        return self

def _source_scope(cfg):

    keys = _SCOPE_KEYS + _MEDIA_SCOPE_KEYS if cfg.get("summary_pipeline_version") in FULL_AUTHOR_PIPELINES else tuple(
        key for key in _SCOPE_KEYS if key not in {"summary_author_mode", "editorial_full_author"})
    scope = {key:cfg.get(key) for key in keys if key not in {"live_finalize_archive", "live_finalize_source"}
            or cfg.get(key) is not None}
    if (cfg.get('summary_pipeline_version') in FULL_AUTHOR_PIPELINES
            and cfg.get('evidence_world_read_page_policy') is not None):
        scope['evidence_world_read_page_policy'] = cfg['evidence_world_read_page_policy']
    if (cfg.get('summary_pipeline_version') in FULL_AUTHOR_PIPELINES
            and cfg.get('evidence_world_search_mode') is not None):
        scope['evidence_world_search_mode'] = cfg['evidence_world_search_mode']
    for key in ("editorial_full_author_navigation", "editorial_full_author_source_actions", "editorial_full_author_handoff_policy", "editorial_multimodal_runtime"):
        if cfg.get(key) is not None:
            if cfg.get("summary_pipeline_version") not in FULL_AUTHOR_PIPELINES:
                raise ValueError("full_author_navigation_requires_302")
            if key == "editorial_multimodal_runtime":
                from .editorial_multimodal_navigation import validate_local_runtime
                scope[key] = validate_local_runtime(cfg[key])
            else:
                scope[key] = cfg[key]
    if cfg.get("editorial_production_audio_reread") is not None:
        scope["editorial_production_audio_reread"] = cfg["editorial_production_audio_reread"]
    hints = cfg.get("evidence_world_summary_hints")
    if hints:
        from .evidence_world.summary_hints import PROFILE
        if (not isinstance(hints, dict) or hints.get("profile") != PROFILE
                or set(hints) - {"profile", "generation_root", "node_executable", "cold_archive_root", "finalized_vod"}
                or not hints.get("generation_root")):
            raise ValueError("unknown_author_summary_hints")


        scope["loom_summary_hint_profile"] = PROFILE
    return scope

def _source_paths(assets, video_no, work_root):
    """Validate every declared family and VOD path before reading a source."""
    if not isinstance(assets, list) or not assets:
        raise ValueError("production_job_sources_missing")
    rows, families = [], set()
    for asset in assets:
        if not isinstance(asset, dict) or not {"family", "path", "sha256"} <= set(asset):
            raise ValueError("production_job_source_invalid")
        family = asset["family"]
        if family not in {"stt", "chat", "audio_features", "clip", "metadata"} or family in families:
            raise ValueError("production_job_source_family_invalid")
        path = native_path(asset["path"]).resolve()
        if not path.is_relative_to(native_path(work_root).resolve() / video_no):
            raise ValueError("production_job_source_outside_vod")
        rows.append({**asset, "path": str(path)})
        families.add(family)
    if not {"metadata", "stt"} <= families:
        raise ValueError("production_job_sources_incomplete")
    return sorted(rows, key=lambda row: row["family"])

def _sources(assets, video_no, work_root):
    rows = _source_paths(assets, video_no, work_root)
    for asset in rows:
        if file_hash(native_path(asset["path"])) != asset["sha256"]:
            raise ValueError("production_job_source_changed")
    return rows

def production_media_root(cfg):
    """Containment from the minted owner; this is not spending permission.

    Media readers verify the bound manifest themselves. Serialized/candidate
    roots cannot enter here.
    """
    if KEY not in cfg:
        return None
    job = cfg[KEY]
    if type(job) is not _ProductionJob or job.authority is not _MINT:
        raise ValueError("production_job_serialized_authority_rejected")
    identity = json.loads(job.identity_json)
    if (identity["selected_pipeline"] not in FULL_AUTHOR_PIPELINES or not identity.get("requested_media")
            or identity["source_scope"] != _source_scope(cfg)
            or canonical_json(cfg.get("editorial_candidate_budget")) != job.value_json
            or str(native_path(cfg.get("candidate_output_dir", "")).resolve()) != job.run_root):
        raise ValueError("production_job_media_scope_changed")
    return native_path(job.run_root)
