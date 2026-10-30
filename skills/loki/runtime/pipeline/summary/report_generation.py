"""Report artifact generation and atomic transaction owner."""

import hashlib

import logging

import os

import re

from pathlib import Path

from typing import Any, Optional

from datetime import datetime, timedelta, timezone

import copy

from ..config import STRUCTURED_SUMMARY_PIPELINES

from ..config import (
    SUMMARY_PIPELINE_METADATA_SCHEMA_VERSION,
    SUMMARY_PIPELINE_2_5,
    SUMMARY_PIPELINE_LEGACY,
    SUMMARY_PIPELINE_VERSIONS,
    normalize_summary_pipeline_version,
    summary_pipeline_manager_label,
    summary_pipeline_release_identity,
)

from ..summary_input_readiness import (
    build_summary_input_diagnostics,
    finalize_summary_input_readiness,
)

from .payload import _build_rendered_summary_evidence_payload

from .report_markdown import (
    ReportMarkdownPreparationRequest,
    prepare_summary_report_markdown,
)

from .report_html import (
    ReportHtmlRenderRequest,
    render_summary_report_html,
)

from .report_timeline_evidence import (
    ReportTimelineEvidenceRequest,
    build_report_timeline_evidence_shadow,
)

from .report import SEGMENT_LEDGER_SCHEMA_VERSION

from ..models import VODInfo

from .render import _build_source_vod_url, _build_summary_generation_quality, _extract_audio_peak_secs, _inject_summary_evidence_payload_html, _inject_summary_quote_citations_payload_html, _inject_timeline_evidence_ux_payload_html, _source_id, _source_platform

from ..models import UserClipsBundle

from ..user_clips_filter import is_eligible_in_vod_clip

from .payload import _MERGE_PROMPT_PATH

logger = logging.getLogger("pipeline")

KST = timezone(timedelta(hours=9))

def _viewer_clip_window_provenance(
    user_clips: Optional[UserClipsBundle],
    vod_info: VODInfo,
) -> dict[str, object]:
    """Return raw-free, manager/QA-readable viewer-clip selection evidence."""
    bundle = user_clips
    clips = list((bundle.clips if bundle is not None else None) or [])
    window_selected = [
        clip
        for clip in clips
        if (getattr(clip, "creation_window_status", "") or "") == "in_window"
    ]
    verified = [
        clip
        for clip in window_selected
        if is_eligible_in_vod_clip(clip, str(vod_info.video_no))
    ]
    acquisition_status = str(getattr(bundle, "status", "") or "missing")
    window_status = str(getattr(bundle, "window_status", "") or "unavailable")
    invalid_count = int(getattr(bundle, "excluded_invalid_date_count", 0) or 0)
    window_candidate_count = int(
        getattr(bundle, "window_candidate_count", 0) or 0
    )
    if bundle is None or window_status != "ok":
        status = "missing"
    elif acquisition_status == "partial":
        status = "partial"
    elif verified:
        status = "present"
    elif invalid_count > 0:
        status = "invalid"
    elif window_candidate_count > 0 or window_selected:
        status = "excluded"
    elif acquisition_status in {"ok", "no_clips"}:
        status = "present_zero"
    else:
        status = "missing"
    coverage_bucket_count = int(
        getattr(bundle, "coverage_bucket_count", 0) or 0
    )
    nonempty_bucket_count = int(
        getattr(bundle, "coverage_nonempty_bucket_count", 0) or 0
    )
    coverage_hole_count = int(getattr(bundle, "coverage_hole_count", 0) or 0)
    return {
        "schema_version": "chzz.viewer_clip_window.v1",
        "status": status,
        "acquisition_status": acquisition_status,
        "order_type": str(getattr(bundle, "order_type", "") or ""),
        "window_start_at": str(getattr(bundle, "window_start_at", "") or ""),
        "window_end_at": str(getattr(bundle, "window_end_at", "") or ""),
        "post_live_grace_hours": 1,
        "raw_clip_count": int(getattr(bundle, "raw_clip_count", 0) or 0),
        "window_candidate_count": window_candidate_count,
        "selected_clip_count": len(window_selected),
        "verified_same_vod_count": len(verified),
        "preserved_unverified_clip_count": len(clips) - len(window_selected),
        "excluded_out_of_window_count": int(
            getattr(bundle, "excluded_out_of_window_count", 0) or 0
        ),
        "excluded_invalid_date_count": invalid_count,
        "excluded_unverified_count": int(
            getattr(bundle, "excluded_unverified_count", 0) or 0
        ),
        "popularity": {
            "order_type": str(getattr(bundle, "order_type", "") or ""),
            "engagement_available": any(
                getattr(clip, "play_count", None) is not None
                or getattr(clip, "like_count", None) is not None
                for clip in verified
            ),
        },
        "temporal_coverage": {
            "selection_policy": str(getattr(bundle, "selection_policy", "") or ""),
            "bucket_count": coverage_bucket_count,
            "nonempty_bucket_count": nonempty_bucket_count,
            "hole_count": coverage_hole_count,
            "complete": status in {"present", "present_zero"}
            and coverage_hole_count == 0,
        },
        "selected_offsets": [
            {
                "offset_sec": int(clip.offset_sec),
                "popularity_rank": getattr(clip, "popularity_rank", None),
                "coverage_bucket_index": getattr(
                    clip, "coverage_bucket_index", None
                ),
                "selection_reason": str(
                    getattr(clip, "selection_reason", "") or ""
                ),
            }
            for clip in verified
        ],
        "raw_clip_payload_included": False,
    }

def _is_runtime_candidate_output_dir(output_dir: str) -> bool:
    try:
        parts = {part.casefold() for part in Path(output_dir).resolve().parts}
    except OSError:
        parts = {part.casefold() for part in Path(output_dir).parts}
    return ".runtime" in parts and "candidate-output" in parts

def _runtime_candidate_report_alias_path(
    output_dir: str,
    html_path: str,
) -> str:
    if not _is_runtime_candidate_output_dir(output_dir):
        return ""
    alias_path = os.path.join(output_dir, "report.html")
    if os.path.abspath(alias_path) == os.path.abspath(html_path):
        return ""
    return alias_path

def _repo_relative_output_path(repo_root: Path, path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root)).replace("\\", "/")
    except (OSError, ValueError):
        return ""

def _resolve_artifact_repo_root(output_dir: str | Path) -> Path:
    resolved_output = Path(output_dir).resolve()
    for candidate in [resolved_output, *resolved_output.parents]:
        if (candidate / ".git").exists() or ((candidate / "pipeline").is_dir() and (candidate / "AGENTS.md").is_file()):
            return candidate
    return resolved_output.parent

def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _register_generated_report_artifacts(
    *,
    output_dir: str,
    vod_info: VODInfo,
    md_path: str,
    html_path: str,
    meta_path: str,
    srt_path: str = "",
    summary_source_manifest: Optional[dict] = None,
    producer_code_version: str = "",
) -> None:
    """Record writer contract metadata for newly generated report files."""
    try:
        from ..artifact_registry import (
            artifact_id,
            register_artifact,
            register_provenance,
            relative_artifact_path,
        )
        from ..summary_run_ledger import run_id_from_metadata_path

        repo_root = _resolve_artifact_repo_root(output_dir)
        manifest = summary_source_manifest if isinstance(summary_source_manifest, dict) else {}
        run_id = run_id_from_metadata_path(meta_path)
        output_paths: list[str] = []
        metadata_artifact_id = ""
        for candidate in (md_path, html_path, meta_path):
            if not candidate:
                continue
            try:
                rel = relative_artifact_path(repo_root, candidate)
                registered = register_artifact(
                    repo_root,
                    rel,
                    producer="summarizer.generate_reports",
                    source_run_id=run_id,
                )
                output_paths.append(rel)
                if rel.endswith("_metadata.json"):
                    metadata_artifact_id = str(registered.get("artifact_id") or artifact_id(rel))
            except (OSError, ValueError) as exc:
                logger.warning(f"artifact registry report registration skipped for {candidate}: {exc}")

        if not output_paths:
            return

        effective_model = str(manifest.get("effective_llm_provider_model") or "")
        provider = ""
        model = effective_model
        if "/" in effective_model:
            provider, model = effective_model.split("/", 1)
        prompt_file = _repo_relative_output_path(repo_root, _MERGE_PROMPT_PATH)
        prompt_sha = _file_sha256(_MERGE_PROMPT_PATH)
        srt_rel = _repo_relative_output_path(repo_root, srt_path) if srt_path else ""
        srt_hash = str(manifest.get("srt_hash") or "")
        if srt_path and not srt_hash:
            srt_hash = _file_sha256(Path(srt_path))
        input_paths = [path for path in [srt_rel] if path]
        input_hashes = {"srt_hash": srt_hash} if srt_hash else {}
        video_no = str(vod_info.video_no or "")
        provenance_record = {
            "artifact_id": metadata_artifact_id or artifact_id(output_paths[-1]),
            "source_video_no": video_no,
            "source_url": f"https://chzzk.naver.com/video/{video_no}" if video_no else "",
            "source_api_url": f"https://api.chzzk.naver.com/service/v2/videos/{video_no}" if video_no else "",
            "pipeline_run_id": run_id,
            "producer_stage": "report_generation",
            "provider": provider,
            "model": model,
            "model_version": effective_model,
            "prompt_file": prompt_file,
            "prompt_sha256": prompt_sha,
            "prompt_label": "summary_merge_prompt",
            "stt_engine": str(manifest.get("stt_engine") or manifest.get("transcription_engine") or ""),
            "stt_model": str(manifest.get("stt_model") or manifest.get("transcription_model") or ""),
            "stt_language": str(manifest.get("stt_language") or manifest.get("language") or "ko-KR"),
            "stt_settings": manifest.get("stt_settings") if isinstance(manifest.get("stt_settings"), dict) else {},
            "transcript_source": "srt" if srt_hash or srt_rel else "",
            "input_artifact_paths": input_paths,
            "input_artifact_hashes": input_hashes,
            "output_artifact_paths": output_paths,
            "media_redownload_pointer_id": f"{video_no}:chzzk" if video_no else "",
            "learning_dataset_refs": [
                "data/highlight_learning",
                "data/alignment_review",
                "data/summary_edit_pair_recovery",
            ],
            "llm": {
                "effective_llm_provider_model": effective_model,
                "provider_order": manifest.get("provider_order") or [],
            },
        }
        if producer_code_version:



            if re.fullmatch(r"[0-9a-f]{40}", producer_code_version):
                provenance_record["pipeline_git_commit"] = producer_code_version
            else:


                provenance_record["pipeline_git_commit"] = ""
            provenance_record["code_version"] = producer_code_version
        register_provenance(repo_root, provenance_record)
    except Exception as exc:
        logger.warning(f"artifact provenance registration skipped: {exc}")

def _commit_generated_report_artifacts(
    *,
    output_dir: str,
    vod_info: VODInfo,
    md_path: str,
    html_path: str,
    meta_path: str,
    report_markdown: str,
    html_content: str,
    metadata: dict[str, Any],
    authoritative_broadcast_map: Optional[dict] = None,
    outline_source_path: str = "",
    summary_source_manifest: Optional[dict] = None,
    timed_evidence_manifest: Optional[dict] = None,
    finalized_summary_input_readiness: Optional[dict] = None,
    summary_input_readiness: Optional[dict] = None,
    summary_provider_order: Optional[list[str]] = None,
    llm_provider_provenance: Optional[dict] = None,
    failure_digest_id: str = "",
    srt_path: str = "",
    producer_code_version: str = "",
    summary_local_vector_index: Optional[dict] = None,
    work_dir: str | Path | None = None,
) -> tuple[str, str, str]:
    """Commit the generated report family and its best-effort side records."""

    try:
        from ..summary_run_ledger import (
            SCHEMA_VERSION as SUMMARY_RUN_LEDGER_SCHEMA_VERSION,
            default_summary_run_ledger_path,
            run_id_from_metadata_path,
        )

        ledger_path = default_summary_run_ledger_path(output_dir)
        metadata["summary_run_ledger"] = {
            "schema_version": SUMMARY_RUN_LEDGER_SCHEMA_VERSION,
            "run_id": run_id_from_metadata_path(meta_path),
            "path_ref": f"output:{ledger_path.name}",
        }
    except Exception as e:
        logger.warning(f"summary run ledger 참조 생성 실패 (metadata 는 계속): {e}")

    from ..report_artifact_family import commit_next_report_artifact_family

    authority_revision_id = (
        str(authoritative_broadcast_map.get("revision_id") or "")
        if isinstance(authoritative_broadcast_map, dict)
        else ("bootstrap" if outline_source_path else "")
    )
    generation_id = commit_next_report_artifact_family(
        video_no=vod_info.video_no,
        markdown_path=md_path,
        html_path=html_path,
        metadata_path=meta_path,
        candidate_alias_path=_runtime_candidate_report_alias_path(
            output_dir,
            html_path,
        ),
        markdown_text=report_markdown,
        html_text=html_content,
        metadata=metadata,
        authority_revision_id=authority_revision_id,
    )
    logger.info(
        "리포트 산출물 세대 저장 완료: generation_id=%s md=%s html=%s metadata=%s",
        generation_id,
        md_path,
        html_path,
        meta_path,
    )
    try:
        from ..summary_run_ledger import record_summary_run_ledger_entry

        record_summary_run_ledger_entry(
            output_dir=output_dir,
            vod_info=vod_info,
            summary_md_path=md_path,
            summary_html_path=html_path,
            metadata_path=meta_path,
            source_manifest=summary_source_manifest,
            timed_evidence_manifest=timed_evidence_manifest,
            summary_input_readiness=finalized_summary_input_readiness or summary_input_readiness,
            provider_order=summary_provider_order or [],
            llm_provider_provenance=llm_provider_provenance,
            failure_digest_id=failure_digest_id,
        )
    except Exception as e:
        logger.warning(f"summary run ledger 저장 실패 (md/html/metadata 는 계속): {e}")

    _register_generated_report_artifacts(
        output_dir=output_dir,
        vod_info=vod_info,
        md_path=md_path,
        html_path=html_path,
        meta_path=meta_path,
        srt_path=srt_path,
        summary_source_manifest=summary_source_manifest,
        producer_code_version=producer_code_version,
    )




    if work_dir:
        try:
            from ..vod_search_index import default_database_path, register_report
            database = default_database_path(work_dir, vod_info.video_no)
            work_root = database.parent.parent
            stored = register_report(database_path=database,
                roots={"work": work_root, "output": Path(output_dir).resolve()},
                metadata_path=Path(meta_path).resolve(), markdown_path=Path(md_path).resolve(),
                html_path=Path(html_path).resolve(), local_vector_index=summary_local_vector_index,
                timed_evidence_manifest=timed_evidence_manifest, srt_path=srt_path or None,
                expected_source_md5=(summary_source_manifest or {}).get("manifest", {}).get("srt_hash"),
                retention_enrollment=(dict(schema_version="vod.retention-enrollment.v1",
                    video_no=str(vod_info.video_no), channel_id=str(vod_info.channel_id),
                    source_platform="chzzk") if _source_platform(vod_info) == "chzzk"
                    and re.fullmatch(r"[a-f0-9]{32}", str(vod_info.channel_id or "")) else None))
        except Exception as error:
            stored = {"schema_version": "vod_search_index.v1", "status": "unavailable",
                      "error_code": str(error) if isinstance(error, ValueError) else type(error).__name__}

            stored["error_code"] = (
                stored["error_code"] if stored["error_code"].startswith(("search_", "summary_vector_"))
                else f"SEARCH_INDEX_{type(error).__name__.upper()}")
            logger.warning("VOD search persistence unavailable (code=%s)", stored["error_code"])
        try:
            from ..vod_search_index import default_database_path, write_status
            write_status(default_database_path(work_dir, vod_info.video_no), Path(md_path).stem, stored)
        except Exception:
            logger.warning("VOD search persistence status unavailable (code=SEARCH_STATUS_WRITE_FAILED)")

    from ..vod_proposals import source_ready
    source_ready(vod_info, "summary", meta_path)
    return md_path, html_path, meta_path

def generate_reports(
    summary: str,
    vod_info: VODInfo,
    highlights: list[dict],
    chats: list[dict],
    output_dir: str,
    community_posts: list = None,
    public_url_base: str = "",
    user_clips: Optional[UserClipsBundle] = None,
    promoted_anchors: Optional[list] = None,
    srt_path: str = "",
    show_community_reaction_summary: bool = False,
    show_community_reaction_cafe: bool = False,
    audio_reaction_metadata: Optional[dict] = None,
    summary_input_readiness: Optional[dict] = None,
    summary_source_manifest: Optional[dict] = None,
    timed_evidence_manifest: Optional[dict] = None,
    pre_generation_evidence_pack: Optional[dict] = None,
    summary_local_vector_index: Optional[dict] = None,
    summary_provider_order: Optional[list[str]] = None,
    llm_provider_provenance: Optional[dict] = None,
    failure_digest_id: str = "",
    comment_timeline_diagnostics: Optional[dict] = None,
    outline_source_path: str | Path | None = None,
    authoritative_broadcast_map: Optional[dict] = None,
    summary_pipeline_requested: str = "",
    summary_pipeline_actual: str = "",
    summary_pipeline_fallback_used: bool = False,
    producer_code_version: str = "",
    timeline_comment_context: Optional[dict] = None,
    editorial_review_packet: Optional[dict] = None,
    viewer_editor_tools_source: Optional[dict] = None,
    report_projection_source: Optional[dict] = None,
    work_dir: str | Path | None = None,
    artifact_discriminator: str = "",
) -> tuple[str, str, str]:
    """Markdown + HTML + metadata JSON 저장"""
    os.makedirs(output_dir, exist_ok=True)
    inferred_pipeline = (
        SUMMARY_PIPELINE_2_5
        if isinstance(authoritative_broadcast_map, dict) or outline_source_path
        else SUMMARY_PIPELINE_LEGACY
    )


    summary_pipeline_requested = summary_pipeline_requested or inferred_pipeline
    summary_pipeline_actual = summary_pipeline_actual or inferred_pipeline
    summary_pipeline_requested = normalize_summary_pipeline_version(
        summary_pipeline_requested
    )
    summary_pipeline_actual = normalize_summary_pipeline_version(
        summary_pipeline_actual
    )
    if not producer_code_version:


        from .._code_version_guard import get_runtime_code_version

        producer_code_version = get_runtime_code_version()
    producer_code_version = str(producer_code_version or "").strip()
    if summary_pipeline_requested not in SUMMARY_PIPELINE_VERSIONS:
        raise ValueError(
            f"unknown requested summary pipeline: {summary_pipeline_requested!r}"
        )
    if summary_pipeline_actual not in SUMMARY_PIPELINE_VERSIONS:
        raise ValueError(
            f"unknown actual summary pipeline: {summary_pipeline_actual!r}"
        )
    if summary_pipeline_requested != summary_pipeline_actual:
        raise ValueError(
            "summary pipeline mismatch: "
            f"requested={summary_pipeline_requested!r}, actual={summary_pipeline_actual!r}"
        )
    if summary_pipeline_fallback_used:
        raise ValueError("cross-engine summary fallback is forbidden")
    if (
        summary_pipeline_actual in STRUCTURED_SUMMARY_PIPELINES
        and not isinstance(authoritative_broadcast_map, dict)
        and not outline_source_path
    ):
        raise ValueError(
            f"{summary_pipeline_manager_label(SUMMARY_PIPELINE_2_5)} report requires "
            "an authoritative BroadcastMap"
        )
    if (
        summary_pipeline_actual == SUMMARY_PIPELINE_LEGACY
        and (authoritative_broadcast_map is not None or outline_source_path)
    ):
        raise ValueError("legacy report must not consume a Loki BroadcastMap")
    summary_pipeline_identity: dict[str, object] = {
        "schema_version": SUMMARY_PIPELINE_METADATA_SCHEMA_VERSION,
        "requested_version": summary_pipeline_requested,
        "actual_version": summary_pipeline_actual,
        **summary_pipeline_release_identity(summary_pipeline_actual),
        "fallback_used": False,
        "producer_code_version": producer_code_version,
    }
    safe_timeline_comment_context: dict = {}
    if isinstance(timeline_comment_context, dict):
        from ..timeline_comment_anchors import build_timeline_comment_context

        safe_timeline_comment_context = build_timeline_comment_context(
            timeline_comment_context.get("anchors") or [],
            source_status=str(
                timeline_comment_context.get("source_status") or "unknown"
            ),
            selected_comment_count=int(
                timeline_comment_context.get("selected_comment_count") or 0
            ),
            source_kind=str(
                timeline_comment_context.get("source_kind") or "unknown"
            ),
            duration_sec=int(getattr(vod_info, "duration", 0) or 0),
        )


        for key in (
            "source_anchor_count",
            "invalid_anchor_count",
            "deduplicated_anchor_count",
        ):
            try:
                original_count = max(0, int(timeline_comment_context.get(key) or 0))
            except (TypeError, ValueError):
                continue
            safe_timeline_comment_context[key] = original_count
        safe_timeline_comment_context["source_anchor_count"] = max(
            int(safe_timeline_comment_context.get("source_anchor_count") or 0),
            int(safe_timeline_comment_context.get("used_anchor_count") or 0),
        )
        if "collaboration_periods" in timeline_comment_context:
            from ..chatgpt_export.contract.collaboration_periods import public_collaboration_periods
            if str(timeline_comment_context.get("collaboration_video_no")) != str(vod_info.video_no):
                raise ValueError("collaboration_period_vod_mismatch")
            safe_timeline_comment_context["collaboration_periods"] = public_collaboration_periods(
                timeline_comment_context["collaboration_periods"], vod_info.duration)
            safe_timeline_comment_context["collaboration_video_no"] = str(vod_info.video_no)
    from ..utils import stable_artifact_key, validate_artifact_path_budget

    date_str = ""
    if vod_info.publish_date:
        try:
            dt = datetime.fromisoformat(vod_info.publish_date.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y%m%d")
        except (ValueError, TypeError):
            date_str = datetime.now(KST).strftime("%Y%m%d")
    else:
        date_str = datetime.now(KST).strftime("%Y%m%d")




    if not isinstance(artifact_discriminator, str):
        raise ValueError("report_artifact_discriminator_invalid")
    base = stable_artifact_key(str(vod_info.video_no), date_str,
        discriminator=artifact_discriminator)
    validate_artifact_path_budget(
        output_dir,
        base,
        suffixes=(
            ".md",
            ".html",
            ".metadata.json",
            ".md.part",
            ".html.part",
            ".metadata.json.part",
        ),
    )






    report_preparation = prepare_summary_report_markdown(
        ReportMarkdownPreparationRequest(
            summary=summary, vod_info=vod_info, output_dir=output_dir, base=base,
            public_url_base=public_url_base, user_clips=user_clips,
            promoted_anchors=promoted_anchors, srt_path=srt_path, chats=chats,
            authoritative_broadcast_map=authoritative_broadcast_map,
            outline_source_path=outline_source_path,
            summary_pipeline_actual=summary_pipeline_actual,
            editorial_review_packet=editorial_review_packet, logger=logger,
        )
    )
    report_markdown = report_preparation.report_markdown
    promoted_anchors_dicts = report_preparation.promoted_anchors_dicts
    structured_outline = report_preparation.structured_outline
    manager_markdown = report_preparation.manager_markdown
    youtube_chapter_readiness = report_preparation.youtube_chapter_readiness
    comment_timeline_supplement_learning = (
        report_preparation.comment_timeline_supplement_learning
    )






    report_html = render_summary_report_html(
        ReportHtmlRenderRequest(
            report_markdown=report_markdown,
            vod_info=vod_info,
            output_dir=output_dir,
            base=base,
            highlights=highlights,
            chats=chats,
            community_posts=community_posts,
            public_url_base=public_url_base,
            user_clips=user_clips,
            promoted_anchors_dicts=promoted_anchors_dicts,
            show_community_reaction_summary=show_community_reaction_summary,
            show_community_reaction_cafe=show_community_reaction_cafe,
            audio_reaction_metadata=audio_reaction_metadata,
            youtube_chapter_readiness=youtube_chapter_readiness,
            srt_path=srt_path,
            work_dir=work_dir,
            safe_timeline_comment_context=safe_timeline_comment_context,
            comment_timeline_supplement_learning=comment_timeline_supplement_learning,
            outline_source_path=outline_source_path,
            structured_outline=structured_outline,
            editorial_review_packet=editorial_review_packet,
            summary_pipeline=summary_pipeline_identity,
            viewer_editor_tools_source=viewer_editor_tools_source,
            report_projection_source=report_projection_source,
            review_progress=(authoritative_broadcast_map or {}).get("review_progress"),
        )
    )
    html_content = report_html.html_content
    metadata_sec = report_html.metadata_sec
    md_path = report_html.md_path
    html_path = report_html.html_path

    meta_path = report_html.meta_path
    structured_highlights = metadata_sec.get("highlight_candidates") or []
    segment_ledger = metadata_sec.get("segment_ledger") or []
    finalized_summary_input_readiness = finalize_summary_input_readiness(
        summary_input_readiness,
        segment_ledger=segment_ledger,
    )
    try:
        from ..boundary_diagnostics import build_boundary_diagnostics

        boundary_diagnostics = build_boundary_diagnostics(
            segment_ledger,
            chat_spikes=highlights,
            audio_events=[
                {"sec": sec, "label": "audio reaction peak"}
                for sec in _extract_audio_peak_secs(audio_reaction_metadata)
            ],
        )
    except Exception as e:
        logger.warning(f"boundary diagnostics 실패 (metadata 에 fail 기록): {e}")
        boundary_diagnostics = {
            "schema_version": "boundary_diagnostics.v1",
            "status": "fail",
            "diagnostic_only": True,
            "items": [],
            "error": str(e),
        }
    try:
        from ..edit_export_preview import build_export_preview

        edit_export_preview = build_export_preview(
            segment_ledger,
            source_name=str(vod_info.video_no or "vod"),
        )
    except Exception as e:
        logger.warning(f"edit export preview 실패 (metadata 에 fail 기록): {e}")
        edit_export_preview = {
            "schema_version": "edit_export_preview.v1",
            "status": "fail",
            "source": "segment_ledger",
            "clip_count": 0,
            "error": str(e),
        }
    summary_evidence_pack: dict = {}
    if isinstance(timed_evidence_manifest, dict) and timed_evidence_manifest:
        try:
            from ..summary_evidence_pack import build_summary_evidence_pack

            summary_evidence_pack = build_summary_evidence_pack(
                segment_ledger=segment_ledger,
                timed_evidence_manifest=timed_evidence_manifest,
                summary_local_vector_index=summary_local_vector_index,
            )
        except Exception as e:
            logger.warning(f"summary evidence pack 생성 실패 (metadata 에 fail 기록): {e}")
            summary_evidence_pack = {
                "schema_version": "summary_evidence_pack.v1",
                "status": "fail",
                "raw_content_included": False,
                "error": str(e),
                "items": [],
            }
    timeline_evidence_result = build_report_timeline_evidence_shadow(
        ReportTimelineEvidenceRequest(
            vod_info=vod_info,
            srt_path=srt_path,
            chats=chats,
            user_clips=user_clips,
            segment_ledger=segment_ledger,
        )
    )
    timeline_evidence_shadow = timeline_evidence_result.timeline_evidence_shadow
    timeline_evidence_shadow_verifier = (
        timeline_evidence_result.timeline_evidence_shadow_verifier
    )
    try:
        from ..evidence_verifier import verify_summary_sections

        evidence_verifier = verify_summary_sections(
            metadata_sec,
            chapter_readiness=youtube_chapter_readiness,
            summary_evidence_pack=summary_evidence_pack if summary_evidence_pack else None,
            duration_sec=vod_info.duration,
        )
    except Exception as e:
        logger.warning(f"evidence verifier 실패 (metadata 에 fail 기록): {e}")
        evidence_verifier = {
            "schema_version": "evidence_verifier.v1",
            "status": "fail",
            "summary": {
                "checked_segments": 0,
                "failure_count": 1,
                "warning_count": 0,
            },
            "findings": [
                {
                    "code": "VERIFIER_RUNTIME_ERROR",
                    "severity": "error",
                    "message": str(e),
                }
            ],
        }
    rendered_summary_evidence = _build_rendered_summary_evidence_payload(summary_evidence_pack)
    rendered_quote_citations: dict = {}
    if rendered_summary_evidence:
        try:
            html_content = _inject_summary_evidence_payload_html(
                html_content,
                rendered_summary_evidence,
            )
            logger.info("HTML 리포트 summary evidence payload 준비 완료")
        except OSError as e:
            logger.warning(f"summary evidence payload HTML 주입 실패 (metadata 는 계속): {e}")
            rendered_summary_evidence = {
                "schema_version": "rendered_summary_evidence_payload.v1",
                "status": "fail",
                "raw_content_included": False,
                "error": str(e),
            }
    if segment_ledger and srt_path:
        try:
            from ..public_quote_citations import build_public_quote_citation_payload

            srt_parent = Path(srt_path).parent
            quote_work_dir = Path(work_dir) if work_dir else (
                srt_parent.parent if srt_parent.name == str(vod_info.video_no or "") else srt_parent
            )
            rendered_quote_citations = build_public_quote_citation_payload(
                segment_ledger=segment_ledger,
                work_dir=quote_work_dir,
                video_no=str(vod_info.video_no or ""),
                chat_records=[row for row in chats if isinstance(row, dict)],
            )
            if rendered_quote_citations.get("items"):
                html_content = _inject_summary_quote_citations_payload_html(
                    html_content,
                    rendered_quote_citations,
                )
                logger.info("HTML 리포트 quote citation payload 준비 완료")
        except Exception as e:
            logger.warning(f"quote citation payload HTML 주입 실패 (metadata 는 계속): {e}")
            rendered_quote_citations = {
                "schema_version": "public_quote_citations.v2",
                "status": "fail",
                "raw_transcript_content_included": False,
                "public_quote_text_included": False,
                "error": str(e),
                "items": [],
            }
    try:
        from ..timeline_evidence_ux import build_timeline_evidence_ux_payload

        timeline_evidence_ux_payload = build_timeline_evidence_ux_payload(
            timeline_evidence_shadow,
            video_no=str(vod_info.video_no or ""),
            verifier=timeline_evidence_shadow_verifier,
        )
        if timeline_evidence_ux_payload.get("items"):
            html_content = _inject_timeline_evidence_ux_payload_html(
                html_content,
                timeline_evidence_ux_payload,
            )
            logger.info("HTML 리포트 timeline evidence UX payload 준비 완료")
    except Exception as e:
        logger.warning(
            "timeline evidence UX payload 주입 실패 "
            f"(raw-free diagnostic code=TIMELINE_EVIDENCE_UX_{type(e).__name__.upper()})"
        )
    highlight_metadata_pairs = sorted(
        zip(highlights, structured_highlights),
        key=lambda pair: int((pair[0] or {}).get("sec") or 0),
    )
    metadata = {
        "video_no": vod_info.video_no,
        "title": vod_info.title,
        "channel": vod_info.channel_name,
        "channel_id": vod_info.channel_id,
        "streamer_id": vod_info.streamer_id,
        "platform": _source_platform(vod_info),
        "source_platform": _source_platform(vod_info),
        "source_id": _source_id(vod_info),
        "source_url": _build_source_vod_url(vod_info),
        "duration": vod_info.duration,
        "publish_date": vod_info.publish_date,
        "category": vod_info.category,
        "thumbnail_url": vod_info.thumbnail_url or None,
        "total_chats": metadata_sec["report_projection"]["total_chats"],
        "highlight_count": metadata_sec["report_projection"]["signal_scene_count"],
        "report_projection": copy.deepcopy(metadata_sec["report_projection"]),
        **({"collaboration_periods": copy.deepcopy(metadata_sec["collaboration_periods"])}
           if "collaboration_periods" in metadata_sec else {}),
        **({"collaboration_diagnostics": copy.deepcopy(metadata_sec["collaboration_diagnostics"])}
           if "collaboration_diagnostics" in metadata_sec else {}),
        **({"collaboration_candidates": copy.deepcopy(metadata_sec["collaboration_candidates"])}
           if "collaboration_candidates" in metadata_sec else {}),
        **({"collaboration_replays": copy.deepcopy(metadata_sec["collaboration_replays"])}
           if "collaboration_replays" in metadata_sec else {}),
        "optional_inputs": (
            {
                "chat": {"status": "not_available_for_source", "items": []},
                "community": {"status": "not_available_for_source", "items": []},
                "viewer_clips": {"status": "not_available_for_source", "items": []},
                "fabricated": False,
            }
            if _source_platform(vod_info) == "youtube"
            else None
        ),
        "viewer_clip_window": _viewer_clip_window_provenance(
            user_clips, vod_info
        ),
        "highlights": [
            {
                "sec": h.get("sec"),
                "composite": h.get("composite"),
                "count": h.get("count"),
                "source_signals": h.get("source_signals") or [],
                "laughter_burst": bool(h.get("laughter_burst")),
                "segment_id": structured.get("segment_id"),
                "sources": structured.get("sources", []),
                "risk_flags": structured.get("risk_flags", []),
            }
            for h, structured in highlight_metadata_pairs
        ],
        "segment_ledger_schema_version": SEGMENT_LEDGER_SCHEMA_VERSION,
        "segment_ledger": segment_ledger,
        "boundary_diagnostics": boundary_diagnostics,
        "edit_export_preview": edit_export_preview,
        "processed_at": datetime.now(KST).isoformat(),
        "summary_pipeline": summary_pipeline_identity,
        "youtube_chapter_readiness": youtube_chapter_readiness,
        "evidence_verifier": evidence_verifier,
        "timeline_evidence_shadow": timeline_evidence_shadow,
        "timeline_evidence_shadow_verifier": timeline_evidence_shadow_verifier,
        "rendered_summary_evidence": {
            "schema_version": rendered_summary_evidence.get("schema_version"),
            "render_target": rendered_summary_evidence.get("render_target", ""),
            "raw_content_included": bool(rendered_summary_evidence.get("raw_content_included") or False),
            "item_count": len(rendered_summary_evidence.get("items") or []),
            "status": "ready" if rendered_summary_evidence.get("items") else rendered_summary_evidence.get("status", "empty"),
        },
        "summary_quote_citations": {
            "schema_version": rendered_quote_citations.get("schema_version"),
            "render_target": rendered_quote_citations.get("render_target", ""),
            "raw_transcript_content_included": bool(rendered_quote_citations.get("raw_transcript_content_included") or False),
            "public_quote_text_included": bool(rendered_quote_citations.get("public_quote_text_included") or False),
            "subtitle_cue_count": int(rendered_quote_citations.get("subtitle_cue_count") or 0),
            "item_count": len(rendered_quote_citations.get("items") or []),
            "status": "ready" if rendered_quote_citations.get("items") else rendered_quote_citations.get("status", "empty"),
        },
    }
    if safe_timeline_comment_context.get("anchors"):


        metadata["timeline_comment_context"] = safe_timeline_comment_context
        metadata["timeline_comment_provenance"] = {
            key: value
            for key, value in safe_timeline_comment_context.items()
            if key != "anchors"
        }
    if structured_outline is not None:
        metadata["structured_outline"] = structured_outline
        metadata["manager_markdown"] = manager_markdown
        review_progress = (authoritative_broadcast_map or {}).get("review_progress")
        if isinstance(review_progress, dict):
            metadata["manager_outline_review_progress"] = copy.deepcopy(review_progress)
        metadata["manager_outline_artifacts"] = {
            "source": (
                str(authoritative_broadcast_map.get("source_path") or "")
                if isinstance(authoritative_broadcast_map, dict)
                else Path(str(outline_source_path or "")).name
            ),
            "authority_kind": (
                str(authoritative_broadcast_map.get("source_kind") or "")
                if isinstance(authoritative_broadcast_map, dict)
                else "bootstrap"
            ),
            "authority_revision_id": (
                str(authoritative_broadcast_map.get("revision_id") or "")
                if isinstance(authoritative_broadcast_map, dict)
                else "bootstrap"
            ),
            "markdown": f"{vod_info.video_no}_manager_outline.md",
            "html": f"{vod_info.video_no}_manager_outline.html",
        }
    if editorial_review_packet is not None:
        metadata["editorial_review_packet"] = editorial_review_packet
    if isinstance(timed_evidence_manifest, dict) and timed_evidence_manifest:
        try:
            from ..timed_evidence import compact_timed_evidence_manifest

            metadata["timed_evidence_manifest"] = compact_timed_evidence_manifest(
                timed_evidence_manifest
            )
            metadata["summary_evidence_pack"] = summary_evidence_pack
            if isinstance(pre_generation_evidence_pack, dict) and pre_generation_evidence_pack:
                metadata["summary_pre_generation_evidence_pack"] = pre_generation_evidence_pack
            if isinstance(summary_local_vector_index, dict) and summary_local_vector_index:
                from ..summary_local_vector_index import compact_summary_local_vector_index

                metadata["summary_local_vector_index"] = compact_summary_local_vector_index(
                    summary_local_vector_index
                )
        except Exception as e:
            logger.warning(f"timed evidence manifest 압축 실패 (metadata 는 계속): {e}")
    if isinstance(comment_timeline_diagnostics, dict) and comment_timeline_diagnostics:
        metadata["comment_timeline_diagnostics"] = comment_timeline_diagnostics
    if isinstance(llm_provider_provenance, dict) and llm_provider_provenance:
        metadata["llm_provider_provenance"] = llm_provider_provenance
    if comment_timeline_supplement_learning:
        metadata["comment_timeline_supplement_learning"] = comment_timeline_supplement_learning
    summary_generation_quality = _build_summary_generation_quality(
        report_markdown,
        comment_timeline_supplement_learning=comment_timeline_supplement_learning,
    )
    if summary_generation_quality is not None:
        metadata["summary_generation_quality"] = summary_generation_quality
        metadata["summary_quality_status"] = summary_generation_quality["status"]
    if finalized_summary_input_readiness is not None:
        metadata["summary_input_readiness"] = finalized_summary_input_readiness
        summary_input_diagnostics = build_summary_input_diagnostics(finalized_summary_input_readiness)
        if summary_input_diagnostics is not None:
            metadata["summary_input_diagnostics"] = summary_input_diagnostics
    if isinstance(audio_reaction_metadata, dict) and audio_reaction_metadata:
        metadata.update(audio_reaction_metadata)
    return _commit_generated_report_artifacts(
        output_dir=output_dir,
        vod_info=vod_info,
        md_path=md_path,
        html_path=html_path,
        meta_path=meta_path,
        report_markdown=report_markdown,
        html_content=html_content,
        metadata=metadata,
        authoritative_broadcast_map=authoritative_broadcast_map,
        outline_source_path=outline_source_path,
        timed_evidence_manifest=timed_evidence_manifest,
        finalized_summary_input_readiness=finalized_summary_input_readiness,
        summary_input_readiness=summary_input_readiness,
        summary_provider_order=summary_provider_order,
        llm_provider_provenance=llm_provider_provenance,
        failure_digest_id=failure_digest_id,
        srt_path=srt_path,
        summary_source_manifest=summary_source_manifest,
        producer_code_version=producer_code_version,
        summary_local_vector_index=summary_local_vector_index,
        work_dir=work_dir,
    )
