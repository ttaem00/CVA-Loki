"""Final report HTML rendering lifecycle owner."""
import copy
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..config import STRUCTURED_SUMMARY_PIPELINES
from ..config import LOKI_LABEL
from .parse import _parse_summary_sections
from .render import _build_viewer_editor_tools_payload, _inject_summary_pipeline_identity_html, _viewer_editor_json, _viewer_outline_projection_rows
from .report import _attach_segment_metadata, _build_report_projection_snapshot, _generate_html, _strip_community_reaction_sections
from .viewer_editor import _project_public_viewer_editor_tools_payload
logger = logging.getLogger('pipeline')

@dataclass(frozen=True)
class ReportHtmlRenderRequest:
    report_markdown: str
    vod_info: Any
    output_dir: str
    base: str
    highlights: list[dict]
    chats: list[dict]
    community_posts: list[Any]
    public_url_base: str
    user_clips: Any
    promoted_anchors_dicts: list[dict]
    show_community_reaction_summary: bool
    show_community_reaction_cafe: bool
    audio_reaction_metadata: Any
    youtube_chapter_readiness: dict
    srt_path: str
    safe_timeline_comment_context: dict
    comment_timeline_supplement_learning: dict
    outline_source_path: Any
    structured_outline: dict | None
    editorial_review_packet: Any
    summary_pipeline: dict[str, Any]
    viewer_editor_tools_source: dict[str, Any] | None = None
    report_projection_source: dict[str, Any] | None = None
    review_progress: dict[str, Any] | None = None
    work_dir: str | Path | None = None

@dataclass(frozen=True)
class ReportHtmlRenderResult:
    html_content: str
    metadata_sec: dict
    md_path: str
    html_path: str
    meta_path: str
from public_ui import attributed_html as _loki_attributed_html

def render_summary_report_html(request: ReportHtmlRenderRequest) -> ReportHtmlRenderResult:
    """Render the report HTML and its canonical metadata/viewer inputs."""
    r = request
    md_path = os.path.join(r.output_dir, f'{r.base}.md')
    metadata_sec = _attach_segment_metadata(_parse_summary_sections(r.report_markdown if r.show_community_reaction_summary else _strip_community_reaction_sections(r.report_markdown)), r.vod_info, highlights=r.highlights, audio_reaction_metadata=r.audio_reaction_metadata, promoted_anchors=r.promoted_anchors_dicts, comment_timeline_supplement_learning=r.comment_timeline_supplement_learning)
    if isinstance(r.audio_reaction_metadata, dict):
        if isinstance(r.audio_reaction_metadata.get('audio_waveform'), dict):
            metadata_sec['audio_waveform'] = r.audio_reaction_metadata['audio_waveform']
        if isinstance(r.audio_reaction_metadata.get('audio_reaction_peaks'), list):
            metadata_sec['audio_reaction_peaks'] = r.audio_reaction_metadata['audio_reaction_peaks']
    if r.safe_timeline_comment_context.get('anchors'):
        metadata_sec['timeline_comment_context'] = r.safe_timeline_comment_context
    viewer_editor_tools_payload = copy.deepcopy(r.viewer_editor_tools_source) if isinstance(r.viewer_editor_tools_source, dict) else _build_viewer_editor_tools_payload(dict(metadata_sec, highlights=r.highlights), r.vod_info, r.chats, srt_path=r.srt_path, work_dir=r.work_dir, evidence_video_no=str(r.vod_info.video_no or ''), user_clips=r.user_clips)
    if isinstance(r.editorial_review_packet, dict) and 'collaboration_periods' in r.editorial_review_packet:
        from ..chatgpt_export.contract.collaboration_periods import recover_collaboration_periods
        if r.editorial_review_packet.get('source_vod_id') is not None and str(r.editorial_review_packet['source_vod_id']) != str(r.vod_info.video_no or ''):
            raise ValueError('collaboration_period_vod_mismatch')
        projection = recover_collaboration_periods(r.editorial_review_packet['collaboration_periods'], r.vod_info.duration)
        metadata_sec['collaboration_periods'] = projection['periods']
        viewer_editor_tools_payload['collaboration_periods'] = copy.deepcopy(projection['periods'])
        if projection['diagnostics']:
            metadata_sec['collaboration_diagnostics'] = projection['diagnostics']
        elif r.editorial_review_packet.get('collaboration_diagnostics'):
            metadata_sec['collaboration_diagnostics'] = copy.deepcopy(r.editorial_review_packet['collaboration_diagnostics'])
    elif isinstance(r.structured_outline, dict) and 'collaboration_periods' in r.structured_outline:
        from ..chatgpt_export.contract.collaboration_periods import recover_collaboration_periods
        projection = recover_collaboration_periods(r.structured_outline['collaboration_periods'], r.vod_info.duration)
        metadata_sec['collaboration_periods'] = projection['periods']
        viewer_editor_tools_payload['collaboration_periods'] = copy.deepcopy(projection['periods'])
        if projection['diagnostics']:
            metadata_sec['collaboration_diagnostics'] = projection['diagnostics']
        elif r.structured_outline.get('collaboration_diagnostics'):
            metadata_sec['collaboration_diagnostics'] = copy.deepcopy(r.structured_outline['collaboration_diagnostics'])
    elif 'collaboration_periods' not in viewer_editor_tools_payload:
        from ..collaboration_candidates import load_comment_collaboration_periods
        periods = load_comment_collaboration_periods(r.vod_info.video_no, r.work_dir, r.vod_info.duration, host_name=str(getattr(r.vod_info, 'channel_name', '') or ''))
        if not periods and r.safe_timeline_comment_context.get('collaboration_periods'):
            from ..chatgpt_export.contract.collaboration_periods import public_collaboration_periods
            if str(r.safe_timeline_comment_context.get('collaboration_video_no')) != str(r.vod_info.video_no):
                raise ValueError('collaboration_period_vod_mismatch')
            periods = public_collaboration_periods(r.safe_timeline_comment_context['collaboration_periods'], r.vod_info.duration)
        if periods:
            metadata_sec['collaboration_periods'] = copy.deepcopy(periods)
            viewer_editor_tools_payload['collaboration_periods'] = periods
    from ..collaboration_candidates import load_collaboration_candidates, merge_period_roster_candidates
    candidates = load_collaboration_candidates(r.vod_info.video_no, r.work_dir, host_name=str(getattr(r.vod_info, 'channel_name', '') or ''), title=str(r.vod_info.title or ''))
    candidates = merge_period_roster_candidates(candidates, metadata_sec.get('collaboration_periods', []))
    if candidates:
        metadata_sec['collaboration_candidates'] = copy.deepcopy(candidates)
        viewer_editor_tools_payload['collaboration_candidates'] = candidates
    from ..collaboration_replays import load_replays
    replays = load_replays(str(r.vod_info.video_no), duration_sec=r.vod_info.duration, channel_id=r.vod_info.channel_id)
    if replays:
        metadata_sec['collaboration_replays'] = replays
        viewer_editor_tools_payload['collaboration_replays'] = copy.deepcopy(replays)
    viewer_editor_tools_payload = _project_public_viewer_editor_tools_payload(viewer_editor_tools_payload)
    report_projection = _build_report_projection_snapshot(chats=None if isinstance(r.report_projection_source, dict) else r.chats, highlights=None if isinstance(r.report_projection_source, dict) else r.highlights, viewer_editor_tools_payload=viewer_editor_tools_payload, report_projection_source=r.report_projection_source)
    metadata_sec['report_projection'] = copy.deepcopy(report_projection)
    viewer_editor_tools_payload['report_projection'] = copy.deepcopy(report_projection)
    html_path = os.path.join(r.output_dir, f'{r.base}.html')
    try:
        html_content = _generate_html(r.report_markdown, r.vod_info, None if isinstance(r.report_projection_source, dict) else r.highlights, None if isinstance(r.report_projection_source, dict) else r.chats, r.community_posts, r.public_url_base, user_clips=r.user_clips, promoted_anchors=r.promoted_anchors_dicts, show_community_reaction_summary=r.show_community_reaction_summary, show_community_reaction_cafe=r.show_community_reaction_cafe, audio_reaction_metadata=r.audio_reaction_metadata, youtube_chapter_readiness=r.youtube_chapter_readiness, srt_path=r.srt_path, work_dir=r.work_dir, evidence_video_no=str(r.vod_info.video_no or ''), viewer_editor_tools_source=viewer_editor_tools_payload, outline_source_path=r.outline_source_path, structured_outline=r.structured_outline, editorial_review_packet=r.editorial_review_packet, report_projection_source=report_projection, review_progress=r.review_progress)
    except Exception as e:
        if str(r.summary_pipeline.get('actual_version') or '') in STRUCTURED_SUMMARY_PIPELINES and isinstance(r.structured_outline, dict):
            logger.warning('%s HTML 렌더 실패 — 권한 payload 보존 fallback: %s', LOKI_LABEL, e)
            import html as _html
            fallback_viewer_payload = copy.deepcopy(viewer_editor_tools_payload)
            fallback_viewer_payload['outline_projection'] = _viewer_outline_projection_rows(r.structured_outline, _parse_summary_sections(r.report_markdown))
            fallback_viewer_json = _viewer_editor_json(fallback_viewer_payload)
            html_content = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>{_html.escape(r.vod_info.title)}</title></head><body><h1>{_html.escape(r.vod_info.title)}</h1><p><em>HTML 렌더링 실패 — 승인된 BroadcastMap 텍스트를 표시합니다.</em></p><pre style='white-space:pre-wrap'>{_html.escape(r.report_markdown)}</pre><script id="viewerEditorToolsData" type="application/json">{fallback_viewer_json}</script></body></html>"""
        else:
            logger.warning(f'HTML 렌더링 실패 — minimal fallback 사용: {e}')
            import html as _html
            fallback_viewer_payload = copy.deepcopy(viewer_editor_tools_payload)
            fallback_viewer_json = _viewer_editor_json(fallback_viewer_payload)
            html_content = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>{_html.escape(r.vod_info.title)}</title></head><body><h1>{_html.escape(r.vod_info.title)}</h1><p><em>HTML 렌더링 실패 — 원본 Markdown 을 보세요: <code>{_html.escape(os.path.basename(md_path))}</code></em></p><pre style='white-space:pre-wrap'>{_html.escape(_strip_community_reaction_sections(r.report_markdown) if not r.show_community_reaction_summary else r.report_markdown)}</pre><script id="viewerEditorToolsData" type="application/json">{fallback_viewer_json}</script></body></html>"""
    html_content = _inject_summary_pipeline_identity_html(html_content, identity=r.summary_pipeline, editorial_review_packet=r.editorial_review_packet)
    meta_path = os.path.join(r.output_dir, f'{r.base}_metadata.json')
    return ReportHtmlRenderResult(html_content=_loki_attributed_html(html_content), metadata_sec=metadata_sec, md_path=md_path, html_path=html_path, meta_path=meta_path)
