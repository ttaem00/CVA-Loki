"""Report timeline-evidence shadow lifecycle owner."""

import logging

from dataclasses import dataclass

from pathlib import Path

from typing import Any

logger = logging.getLogger("pipeline")

@dataclass(frozen=True)
class ReportTimelineEvidenceRequest:
    vod_info: Any
    srt_path: str
    chats: list[dict]
    user_clips: Any
    segment_ledger: list[dict]

@dataclass(frozen=True)
class ReportTimelineEvidenceResult:
    timeline_evidence_shadow: dict
    timeline_evidence_shadow_verifier: dict

def build_report_timeline_evidence_shadow(
    request: ReportTimelineEvidenceRequest,
) -> ReportTimelineEvidenceResult:
    """Build and verify the raw-free, admin-only timeline evidence shadow."""
    r = request
    try:
        from ..public_quote_citations import load_quote_source_cues
        from ..summary_evidence_pack import build_timeline_evidence_shadow_contract
        from ..timed_evidence import build_timeline_source_evidence

        subtitle_cues = []
        if r.srt_path:
            srt_parent = Path(r.srt_path).parent
            evidence_work_dir = (
                srt_parent.parent
                if srt_parent.name == str(r.vod_info.video_no or "")
                else srt_parent
            )
            subtitle_cues = load_quote_source_cues(
                evidence_work_dir,
                str(r.vod_info.video_no or ""),
            )
        timeline_source_evidence = build_timeline_source_evidence(
            video_id=str(r.vod_info.video_no or ""),
            duration_sec=r.vod_info.duration,
            subtitle_cues=subtitle_cues,
            chat_records=[row for row in r.chats if isinstance(row, dict)],
            viewer_clips=list(getattr(r.user_clips, "clips", []) or []),
        )
        timeline_evidence_shadow = build_timeline_evidence_shadow_contract(
            segment_ledger=r.segment_ledger,
            timeline_source_evidence=timeline_source_evidence,
        )
        from ..evidence_verifier import verify_evidence_payload

        timeline_evidence_shadow_verifier = verify_evidence_payload(
            {
                "schema_version": "evidence_verifier_input.v1",
                "segments": [],
                "timeline_evidence_shadow": timeline_evidence_shadow,
                "duration_sec": r.vod_info.duration,
            }
        )
    except Exception as e:
        error_code = f"TIMELINE_EVIDENCE_SHADOW_BUILD_{type(e).__name__.upper()}"
        logger.warning(
            "timeline evidence shadow 생성 실패 "
            f"(raw-free diagnostic code={error_code})"
        )
        timeline_evidence_shadow = {
            "schema_version": "timeline_evidence_shadow.v1",
            "status": "fail",
            "shadow_only": True,
            "admin_only": True,
            "public_output_changed": False,
            "raw_content_included": False,
            "summary": {
                "timeline_item_count": 0,
                "evidence_ref_count": 0,
                "source_ref_counts": {
                    "subtitle": 0,
                    "chat": 0,
                    "viewer_clip": 0,
                },
                "evidence_less_item_count": 0,
                "timestamp_less_chat_count": 0,
                "viewer_clip_candidate_count": 0,
                "quarantined_out_of_range_count": 0,
                "quarantined_wrong_vod_count": 0,
            },
            "diagnostics": {
                "schema_version": "timeline_evidence_shadow_diagnostics.v1",
                "raw_content_included": False,
                "code_counts": {error_code: 1},
            },
            "items": [],
        }
        timeline_evidence_shadow_verifier = {
            "schema_version": "evidence_verifier.v1",
            "status": "fail",
            "summary": {
                "checked_segments": 0,
                "failure_count": 1,
                "warning_count": 0,
            },
            "findings": [
                {
                    "code": error_code,
                    "severity": "error",
                    "message": "timeline evidence shadow build failed",
                }
            ],
        }
    return ReportTimelineEvidenceResult(
        timeline_evidence_shadow=timeline_evidence_shadow,
        timeline_evidence_shadow_verifier=timeline_evidence_shadow_verifier,
    )
