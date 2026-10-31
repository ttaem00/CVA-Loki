"""Final report Markdown preparation lifecycle owner."""

from ..config import FULL_AUTHOR_PIPELINES

import json

import re

from dataclasses import dataclass

from pathlib import Path

from typing import Any

from ..chapter_readiness import validate_youtube_chapter_readiness

from ..config import STRUCTURED_SUMMARY_PIPELINES

from ..config import LOKI_LABEL, SUMMARY_PIPELINE_LEGACY

from ..llm_gateway import SanityGateError

from ..sanity_gate import adaptive_min_body_chars, validate_merge_response

from .. import run_status

from .parse import _parse_summary_sections

from .render import (
    _merge_editorial_review_markdown,
    _normalize_manager_report_markdown,
    _normalize_promoted_anchors_for_inject,
    _postprocess_summary_md,
    _project_official_highlight_candidates_into_markdown,
)

from .report import _attach_segment_metadata

@dataclass(frozen=True)
class ReportMarkdownPreparationRequest:
    summary: str
    vod_info: Any
    output_dir: str
    base: str
    public_url_base: str
    user_clips: Any
    promoted_anchors: Any
    srt_path: str
    chats: list[dict]
    authoritative_broadcast_map: Any
    outline_source_path: Any
    summary_pipeline_actual: str
    editorial_review_packet: Any
    logger: Any

@dataclass(frozen=True)
class ReportMarkdownPreparationResult:
    report_markdown: str
    promoted_anchors_dicts: list[dict]
    structured_outline: dict | None
    manager_markdown: str
    youtube_chapter_readiness: dict
    comment_timeline_supplement_learning: dict

def _structured_outline_chapter_sections(outline: dict | None) -> dict | None:
    """Use the same top-level broadcast ranges shown in structured reports."""

    ranges = outline.get("ranges") if isinstance(outline, dict) else None
    if not isinstance(ranges, list):
        return None
    chapters = [
        {"tc": row.get("start"), "title": row.get("title"),
         "segment_id": row.get("id", "")}
        for row in ranges
        if isinstance(row, dict) and row.get("level") == "D1"
    ]
    return {"timeline": chapters} if chapters else None

def prepare_summary_report_markdown(
    request: ReportMarkdownPreparationRequest,
) -> ReportMarkdownPreparationResult:
    """Prepare the Markdown and data required by later HTML/metadata rendering."""
    r = request
    promoted_anchors_dicts = _normalize_promoted_anchors_for_inject(
        r.promoted_anchors, r.user_clips,
    )
    srt_cues = None
    if r.srt_path:
        try:
            from ..chunker import parse_srt
            srt_cues = parse_srt(r.srt_path)
        except Exception as e:
            r.logger.warning(f"SRT cross-validation 용 파싱 실패 (무시): {e}")

    try:
        _eb_parsed = _parse_summary_sections(r.summary)
        _eb_points = _eb_parsed.get("editor_brief_points") or []
        if _eb_points:
            from .. import editor_brief as _editor_brief_mod
            payload = _editor_brief_mod.build_payload(
                base=r.base,
                video_no=r.vod_info.video_no,
                title=r.vod_info.title,
                channel=r.vod_info.channel_name,
                publish_date=r.vod_info.publish_date,
                points=_eb_points,
            )
            _editor_brief_mod.save_sidecar(r.output_dir, r.base, payload)
            r.logger.info(
                f"편집자 브리프 저장: {len(_eb_points)}개 컷 후보 "
                f"→ {r.base}_editor_brief.json"
            )
    except Exception as e:
        r.logger.warning(f"편집자 브리프 사이드카 저장 실패 (viewer 산출물엔 영향 없음): {e}")

    comment_timeline_supplement_learning: dict = {}
    summary = _postprocess_summary_md(
        r.summary, r.vod_info, r.public_url_base,
        promoted_anchors=promoted_anchors_dicts,
        srt_cues=srt_cues,
        comment_timeline_supplement_sink=comment_timeline_supplement_learning,
    )
    summary = _normalize_manager_report_markdown(summary)
    structured_outline: dict | None = None
    manager_markdown = ""
    if isinstance(r.authoritative_broadcast_map, dict):
        candidate_outline = r.authoritative_broadcast_map.get("outline")
        if not isinstance(candidate_outline, dict):
            raise ValueError("authoritative BroadcastMap has no structured outline")
        structured_outline = json.loads(json.dumps(candidate_outline))
        manager_markdown = str(r.authoritative_broadcast_map.get("manager_markdown") or "")
        summary = _project_official_highlight_candidates_into_markdown(
            summary,
            structured_outline=structured_outline,
            vod_info=r.vod_info,
        )
    elif r.outline_source_path:
        from ..manager_outline import parse_manager_outline, project_manager_outline_markdown

        structured_outline = parse_manager_outline(
            Path(r.outline_source_path).read_text(encoding="utf-8"),
            duration_sec=r.vod_info.duration or None,
        )
        manager_markdown = project_manager_outline_markdown(
            structured_outline,
            duration_sec=r.vod_info.duration or None,
        )
        summary = _project_official_highlight_candidates_into_markdown(
            summary,
            outline_source_path=r.outline_source_path,
            vod_info=r.vod_info,
        )
    try:
        from ..public_quote_citations import load_quote_source_cues
        from ..timed_evidence import build_timeline_source_evidence
        from ..timeline_missing_card_adoption import apply_verified_missing_cards

        nomination_sections = _attach_segment_metadata(
            _parse_summary_sections(summary),
            r.vod_info,
            promoted_anchors=promoted_anchors_dicts,
        )
        nomination_subtitle_cues = []
        if r.srt_path:
            nomination_srt_parent = Path(r.srt_path).parent
            nomination_work_dir = (
                nomination_srt_parent.parent
                if nomination_srt_parent.name == str(r.vod_info.video_no or "")
                else nomination_srt_parent
            )
            nomination_subtitle_cues = load_quote_source_cues(
                nomination_work_dir,
                str(r.vod_info.video_no or ""),
            )
        nomination_source_evidence = build_timeline_source_evidence(
            video_id=str(r.vod_info.video_no or ""),
            duration_sec=r.vod_info.duration,
            subtitle_cues=nomination_subtitle_cues,
            chat_records=[row for row in r.chats if isinstance(row, dict)],
            viewer_clips=list(getattr(r.user_clips, "clips", []) or []),
        )
        summary, _nomination_shadow = apply_verified_missing_cards(
            summary,
            video_no=str(r.vod_info.video_no or ""),
            duration_sec=r.vod_info.duration,
            segment_ledger=[
                row
                for row in (nomination_sections.get("segment_ledger") or [])
                if isinstance(row, dict)
            ],
            timeline_source_evidence=nomination_source_evidence,
        )
    except Exception as e:
        r.logger.warning(
            "viewer clip missing-card adoption skipped "
            f"(raw-free diagnostic code=MISSING_CARD_ADOPTION_{type(e).__name__.upper()})"
        )
    report_markdown = (
        manager_markdown
        if r.summary_pipeline_actual in STRUCTURED_SUMMARY_PIPELINES
        else summary
    )
    if r.summary_pipeline_actual in STRUCTURED_SUMMARY_PIPELINES and not report_markdown.strip():
        raise ValueError(f"{LOKI_LABEL} manager Markdown is empty")
    if (
        r.summary_pipeline_actual in FULL_AUTHOR_PIPELINES
        and isinstance(r.editorial_review_packet, dict)
        and "unresolved_questions" in r.editorial_review_packet
        and isinstance(r.authoritative_broadcast_map, dict)
        and r.authoritative_broadcast_map.get("source_kind") == "generated_runtime_projection"
    ):



        tagged_summary = re.search(
            r"(?ms)^\[(?:방송 전체 이해|짧은 요약)\][ \t]*\n(.*?)(?=^\[[^\]\n]+\][ \t]*(?:\n|\Z)|\Z)",
            r.summary,
        )
        authored_summary = (tagged_summary.group(1) if tagged_summary else r.summary).strip()
        if (
            authored_summary
            and re.match(r"\s*## 방송 흐름[ \t]*\n", report_markdown)
            and not re.search(r"(?m)^## 방송 요약[ \t]*$", report_markdown)
        ):
            report_markdown = "## 방송 요약\n\n" + authored_summary + "\n\n" + report_markdown
    if r.editorial_review_packet is not None:
        report_markdown = _merge_editorial_review_markdown(
            report_markdown,
            r.editorial_review_packet,
            video_no=str(r.vod_info.video_no or ""),
        )
    duration_sec = int(getattr(r.vod_info, "duration", 0) or 0)
    if (
        r.summary_pipeline_actual in STRUCTURED_SUMMARY_PIPELINES
        and isinstance(r.editorial_review_packet, dict)
    ):



        manager_markdown = report_markdown
    if duration_sec >= 7200 and r.summary_pipeline_actual == SUMMARY_PIPELINE_LEGACY:
        try:
            validate_merge_response(
                report_markdown,
                None,
                min_body_chars=adaptive_min_body_chars(duration_sec),
                expected_duration_sec=duration_sec,
            )
        except SanityGateError as e:
            run_status.record_sanity_failure("summarizer.generate_reports", str(e))
            raise
    chapter_sections = _parse_summary_sections(report_markdown)
    if r.summary_pipeline_actual in STRUCTURED_SUMMARY_PIPELINES:
        chapter_sections = _structured_outline_chapter_sections(structured_outline) or chapter_sections
    youtube_chapter_readiness = validate_youtube_chapter_readiness(
        chapter_sections,
        duration_sec=r.vod_info.duration,
    )
    return ReportMarkdownPreparationResult(
        report_markdown=report_markdown,
        promoted_anchors_dicts=promoted_anchors_dicts,
        structured_outline=structured_outline,
        manager_markdown=manager_markdown,
        youtube_chapter_readiness=youtube_chapter_readiness,
        comment_timeline_supplement_learning=comment_timeline_supplement_learning,
    )
