"""Pure summary evidence HTML rendering helpers.

This module owns deterministic, idempotent injection of public-safe evidence
payloads into already-rendered report HTML. It never imports orchestration.
"""

from __future__ import annotations

import json

import re

from pathlib import Path

from typing import Any, Optional

from ..models import VODInfo

from .parse import _parse_summary_sections

from .render_primitives import _chzzk_video_url_at, _hms_to_seconds, _html_escape

from .viewer_editor import _VIEWER_EDITOR_LANE_LABELS, _viewer_editor_lane_summaries_for_events, _viewer_editor_limit_events_preserving_viewer_clips, _viewer_editor_guidance_from_flags, _viewer_editor_stable_source_identity_key, _scope_viewer_editor_evidence_source, _build_viewer_editor_tools_payload, _dedupe_viewer_editor_viewer_clip_events, _audio_peak_badge_html, _build_chzzk_vod_url, _build_source_vod_url, _build_user_clips_block, _build_viewer_editor_tools_html, _chartjs_script_tag, _generate_youtube_html, _normalize_viewer_clip_title, _project_editorial_review_html, _render_chzzk_comment_text, _render_community_reaction_md, _render_tc_anchor_html, _render_top_vod_link_html, _source_id, _source_platform, _validate_editorial_review_projection, _viewer_clip_badge_html, _viewer_editor_json

from .cafe_projection import _HIGHLIGHT_LEARNING_NOTICE, _render_inline_md, _render_naver_cafe_html

from .merge_recovery import SUMMARY_MERGE_FALLBACK_MARKER, SUMMARY_MERGE_FALLBACK_SCHEMA_VERSION, SUMMARY_MERGE_FALLBACK_TIMELINE_MAX_BLOCKS

from .markdown_postprocess import _postprocess_summary_md, _strip_editorial_review_markdown, _merge_editorial_review_markdown, _normalize_promoted_anchors_for_inject

_VIEWER_EDITOR_TOOLS_JS = (Path(__file__).parent / "viewer.js").read_text(encoding="utf-8")

_VIEWER_EDITOR_TOOLS_CSS = (Path(__file__).parent / "viewer.css").read_text(encoding="utf-8")

_VIEWER_EDITOR_TOOLS_JS += "\n" + (Path(__file__).parent / "result_page.js").read_text(encoding="utf-8")

_VIEWER_EDITOR_TOOLS_CSS += "\n" + (Path(__file__).parent / "result_page.css").read_text(encoding="utf-8")

_REPORT_DOCUMENT_TEMPLATE = (Path(__file__).parent / "report.html").read_text(encoding="utf-8")

_REPORT_DOCUMENT_TOKEN_RE = re.compile(r"@@([A-Z][A-Z0-9_]*)@@")

_REPORT_DOCUMENT_TOKENS = frozenset({
    "REPORT_TITLE", "OG_META", "CHART_SCRIPT", "TOP_VOD_CSS",
    "VIEWER_CLIPS_CSS", "EDITORIAL_HIGHLIGHT_CARD_CSS", "VIEWER_TOOLS_CSS",
    "CHANNEL_NAME", "VOD_ID", "DISPLAY_TITLE", "PUBLISHED_AT", "DURATION",
    "CATEGORY_META", "COLLABORATION_CHIPS", "TOP_VOD_LINK", "EDITOR_JUMP", "HERO_HTML", "BROADCAST_SUMMARY_HTML", "CHAT_COUNT",
    "DURATION_STAT", "SIGNAL_SCENE_COUNT", "EDITORIAL_HIGHLIGHT_STAT",
    "TIMELINE_HTML", "EDITORIAL_REVIEW_HTML",
    "HIGHLIGHTS_HTML", "INJECTION_WARNING_HTML", "COMMENT_TIMELINE_HTML",
    "USER_CLIPS_HTML", "COMMUNITY_REACTION_HTML", "NOTES_HTML", "VIEWER_TOOLS_HTML",
    "FALLBACK_HTML", "CHAT_CHART_STATE", "CHART_LABELS", "CHART_COUNTS", "CHART_BUCKET_SECS",
    "PEAK_SECS", "AUDIO_PEAK_SECS", "VOD_VIDEO_NO", "WINDOW_SEC", "VIEWER_JS",
})

def _render_collaboration_chips_html(candidates) -> str:
    """Small document-roster metadata; never infer attendance at a playhead."""
    from ..collaboration_candidates import public_collaboration_candidates
    names = list(dict.fromkeys(name for row in public_collaboration_candidates(candidates) for name in row["members"]))
    if not names:
        return ""
    chips = ''.join('<span class="collaboration-chip" title="명단 기준 · 참여 시점 미확인">'
                    + _html_escape(name) + '</span>' for name in names)
    return ('<div class="collaboration-chips" role="group" aria-label="합방 명단 · 참여 시점 미확인">'
            '<span class="collaboration-chips-label">합방</span>' + chips + '</div>')

def render_normal_report_document(tokens: dict[str, str]) -> str:
    """Render the fixed normal-report document from its explicit token contract."""
    supplied = set(tokens)
    if supplied != _REPORT_DOCUMENT_TOKENS:
        missing = sorted(_REPORT_DOCUMENT_TOKENS - supplied)
        unexpected = sorted(supplied - _REPORT_DOCUMENT_TOKENS)
        raise ValueError(
            "normal report token contract mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if any(not isinstance(value, str) for value in tokens.values()):
        raise TypeError("normal report tokens must be strings")
    return _REPORT_DOCUMENT_TOKEN_RE.sub(
        lambda match: tokens[match.group(1)],
        _REPORT_DOCUMENT_TEMPLATE,
    )

def _render_broadcast_summary_html(summary_md: str) -> str:
    """Render the authored body separately from the accepted Map's timeline."""
    section = re.search(r"(?ms)^## 방송 요약[ \t]*\n(.*)", summary_md)
    body = section.group(1).strip() if section else ""

    map_headings = list(re.finditer(r"(?m)^## 방송 흐름[ \t]*$", body))
    if map_headings:
        body = body[:map_headings[-1].start()].rstrip()
    if not body:
        return ""
    paragraphs = "".join(
        "<p>" + _render_inline_md(paragraph.strip()) + "</p>"
        for paragraph in re.split(r"\n\s*\n", body)
        if paragraph.strip()
    )
    return (
        '<div class="card broadcast-summary"><div class="card-head"><h2>방송 요약</h2></div>'
        '<div class="card-body"><div class="notes">' + paragraphs + '</div></div></div>'
    )

def _build_og_meta(vod_info: VODInfo, summary_md: str, public_url_base: str,
                   sec: dict) -> str:
    """OG/Twitter 메타 태그 블록 생성 (report.html 용).

    description 은 hashtags + pull_quote 조합 우선, 없으면 summary 첫 200자.
    """
    title = f"{vod_info.title} — 방송 분석 리포트"
    if vod_info.channel_name:
        title = f"[{vod_info.channel_name}] {title}"

    desc_parts = []
    if sec.get("hashtags"):
        desc_parts.append(" ".join(f"#{t}" for t in sec["hashtags"][:6]))
    if sec.get("pull_quote"):
        desc_parts.append(sec["pull_quote"])
    description = " — ".join(desc_parts) if desc_parts else summary_md[:200].replace("\n", " ")
    if len(description) > 300:
        description = description[:297] + "..."

    og_url = ""
    if public_url_base:
        og_url = f"{public_url_base.rstrip('/')}/vods/{vod_info.video_no}/report"

    image_tags = ""
    if vod_info.thumbnail_url:
        image_tags = (
            f'<meta property="og:image" content="{_html_escape(vod_info.thumbnail_url)}">\n'
            f'<meta name="twitter:image" content="{_html_escape(vod_info.thumbnail_url)}">\n'
        )
    card_type = "summary_large_image" if vod_info.thumbnail_url else "summary"

    tags = [
        f'<meta property="og:type" content="article">',
        f'<meta property="og:site_name" content="auto-caption-generator">',
        f'<meta property="og:title" content="{_html_escape(title)}">',
        f'<meta property="og:description" content="{_html_escape(description)}">',
    ]
    if og_url:
        tags.append(f'<meta property="og:url" content="{_html_escape(og_url)}">')
    tags.append(f'<meta name="twitter:card" content="{card_type}">')
    tags.append(f'<meta name="twitter:title" content="{_html_escape(title)}">')
    tags.append(f'<meta name="twitter:description" content="{_html_escape(description)}">')
    return image_tags + "\n".join(tags)

def _extract_audio_peak_secs(source: object) -> list[float]:
    """Return audio reaction peak center seconds from metadata or highlight rows."""
    if not source:
        return []
    raw_peaks: object
    if isinstance(source, dict):
        raw_peaks = source.get("audio_reaction_peaks") or []
    else:
        raw_peaks = source
    if not isinstance(raw_peaks, list):
        return []

    secs: list[float] = []
    for item in raw_peaks:
        sec: object = None
        if isinstance(item, dict):
            sec = item.get("center_sec", item.get("sec"))
            if sec is None and item.get("start_sec") is not None and item.get("end_sec") is not None:
                try:
                    sec = (float(item["start_sec"]) + float(item["end_sec"])) / 2.0
                except (TypeError, ValueError):
                    sec = None
        else:
            sec = item
        try:
            secs.append(float(sec))
        except (TypeError, ValueError):
            continue
    return secs

def _viewer_outline_projection_rows(
    outline_projection: dict,
    summary_sections: dict,
    editorial_review_packet: dict | None = None,
) -> list[dict[str, Any]]:
    summary_scene_seconds = {
        _hms_to_seconds(str(row.get("timestamp") or row.get("tc") or ""))
        for row in (summary_sections.get("timeline") or [])
        if isinstance(row, dict)
        and _hms_to_seconds(str(row.get("timestamp") or row.get("tc") or "")) is not None
    }
    legacy_highlight_seconds = set()
    for row in summary_sections.get("highlights") or []:
        if not isinstance(row, dict):
            continue
        range_text = str(row.get("tc_range") or row.get("timestamp") or "")
        start_text = re.split(r"\s*[-~–]\s*", range_text, maxsplit=1)[0]
        start_sec = _hms_to_seconds(start_text)
        if start_sec is not None:
            legacy_highlight_seconds.add(start_sec)

    def status_labels(level: str, start_sec: int) -> list[str]:
        labels = []
        if level in {"D1", "D2"} and start_sec in summary_scene_seconds:
            labels.append("요약에 포함됨")
        if level == "Candidate" and start_sec in legacy_highlight_seconds:
            labels.append("요약에서도 추천됨")
        return labels

    editorial_view = (
        _validate_editorial_review_projection(editorial_review_packet)
        if isinstance(editorial_review_packet, dict)
        else None
    )
    stories_by_ref = editorial_view["stories_by_ref"] if editorial_view else {}
    projected_rows = []
    for row in outline_projection.get("ranges") or []:
        start_sec = _hms_to_seconds(str(row.get("start") or "")) or 0
        level = str(row.get("level") or "")
        projected_rows.append({**row, "kind": "outline_range", "start_sec": start_sec, "end_sec": _hms_to_seconds(str(row.get("end") or "")) or 0, "status_labels": status_labels(level, start_sec)})
    for row in outline_projection.get("points") or []:
        start_sec = _hms_to_seconds(str(row.get("timestamp") or "")) or 0
        story = stories_by_ref.get(str(row.get("story_ref") or ""), {})
        projected_rows.append({
            **row,
            "kind": "outline_point",
            "level": "Point",
            "start_sec": start_sec,
            "status_labels": status_labels("Point", start_sec),
            **({
                "story_title": str(story.get("title") or ""),
                "story_content": str(story.get("why_notable") or ""),
            } if story else {}),
        })
    for row in outline_projection.get("candidates") or []:
        start_sec = _hms_to_seconds(str(row.get("start") or "")) or 0
        projected_rows.append({**row, "kind": "outline_candidate", "level": "Candidate", "start_sec": start_sec, "end_sec": _hms_to_seconds(str(row.get("end") or "")) or 0, "status_labels": status_labels("Candidate", start_sec)})
    if editorial_view:
        for highlight in editorial_view["highlights"]:
            spans = list(highlight.get("source_spans") or [])
            projected_rows.append(
                {
                    "id": str(highlight.get("highlight_id") or ""),
                    "kind": "editorial_highlight",
                    "level": "Highlight",
                    "title": str(highlight.get("title") or "편집 Highlight"),
                    "content": str(highlight.get("story_summary") or ""),
                    "start_sec": int(float(spans[0]["start_sec"])),
                    "end_sec": int(float(spans[-1]["end_sec"])),
                    "point_refs": [
                        str(value) for value in highlight.get("point_refs") or []
                    ],
                    "source_spans": json.loads(json.dumps(spans)),
                    "status_labels": ["승인된 Highlight"],
                }
            )
    return projected_rows

def _project_official_highlight_candidates_into_markdown(
    summary: str,
    *,
    outline_source_path: str | Path | None = None,
    structured_outline: dict | None = None,
    vod_info: VODInfo,
) -> str:
    """Render BroadcastMap Candidate geometry into the editable Markdown layer.

    Candidate boundaries stay owned by BroadcastMap.  Existing Markdown copy
    is preserved only as an explanation when it starts at the same scene; it
    cannot create a second official range.
    """
    outline = structured_outline
    if outline is None:
        from ..manager_outline import parse_manager_outline

        outline_path = Path(str(outline_source_path or ""))
        if not outline_path.is_file():
            raise FileNotFoundError(f"manager outline source not found: {outline_path}")
        outline = parse_manager_outline(outline_path.read_text(encoding="utf-8"), duration_sec=vod_info.duration or None)
    candidates = [row for row in (outline.get("candidates") or []) if isinstance(row, dict)]
    legacy = _parse_summary_sections(summary).get("highlights") or []
    legacy_by_start: dict[int, dict[str, Any]] = {}
    for row in legacy:
        if not isinstance(row, dict):
            continue
        start_text = str(row.get("tc_range") or "").split("~", 1)[0].strip()
        start_sec = _hms_to_seconds(start_text)
        if start_sec is not None:
            legacy_by_start[int(start_sec)] = row
    rendered: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        start_text = str(candidate.get("start") or "").strip()
        end_text = str(candidate.get("end") or "").strip()
        start_sec = _hms_to_seconds(start_text)
        end_sec = _hms_to_seconds(end_text)
        if start_sec is None or end_sec is None or end_sec <= start_sec:
            continue
        matched = legacy_by_start.get(int(start_sec), {})
        title = str(candidate.get("title") or matched.get("title") or "하이라이트 후보").strip()
        reason = str(matched.get("reason") or candidate.get("content") or "").strip()
        url = _chzzk_video_url_at(str(vod_info.video_no or ""), start_text)
        rendered.append(f"{index}. **[{start_text}~{end_text}]({url}) {title}**\n" + (f"    - 추천 이유: {reason}\n" if reason else ""))
    replacement = "### 🎬 하이라이트 추천 구간\n\n" + "\n".join(rendered).rstrip() + "\n"
    pattern = re.compile(r"^###\s*🎬\s*하이라이트 추천 구간\s*$.*?(?=^###\s|\Z)", flags=re.MULTILINE | re.DOTALL)
    if pattern.search(summary):
        return pattern.sub(replacement + "\n", summary, count=1)
    return summary.rstrip() + "\n\n" + replacement

def inject_summary_pipeline_identity_html(
    html_content: str,
    *,
    identity: dict[str, object],
    editorial_review_packet: dict | None = None,
) -> str:
    """Render the artifact identity supplied by the pipeline without upgrading it."""
    import html as _html

    version = str(identity.get("actual_version") or "unknown").strip()
    label = str(identity.get("label") or "방식 기록 없음 (과거 결과)").strip()
    review_mode = str((editorial_review_packet or {}).get("projection_mode") or "")
    if review_mode == "composed_review_draft":
        display_text = "조합 편집 검토본 · 외부 편집 초안 + 선택된 분석 결과"
    elif review_mode == "external_review_draft":
        display_text = "외부 편집 검토본 · 자동 분석 완료와 구분"
    else:
        display_text = f"요약 방식: {label}"
    identity_attrs = "".join(
        f' data-{name.replace("_", "-")}="{_html.escape(value, quote=True)}"'
        for name, value in (
            ("summary_mechanism", identity.get("mechanism_name", "")),
            ("summary_mechanism_version", identity.get("mechanism_version", "")),
            ("aemeth_version", identity.get("aemeth_version", "")),
            ("review_projection_mode", review_mode),
        )
        if value
    )
    marker = (f'<div id="summaryPipelineIdentity" role="status" ' f'data-summary-pipeline="{_html.escape(version, quote=True)}"{identity_attrs} ' 'style="margin:12px auto;padding:6px 10px;max-width:1180px;' 'font:700 13px/1.4 system-ui,sans-serif;color:#c0caf5">' f'{_html.escape(display_text)}</div>')
    cleaned = re.sub(
        r'''\s*<div\b(?=[^>]*\bid=["']summaryPipelineIdentity["'])[^>]*>.*?</div>''',
        "",
        html_content,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(
        r'\s*<div\b(?=[^>]*data-summary-pipeline=)[^>]*>\s*'
        r'(?:요약 방식:\s*)?(?:Pipeline\s*2\.5|Loki\s*[0-9.]+)\s*</div>',
        "",
        cleaned,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_match = re.search(r"<body\b[^>]*>", cleaned, re.IGNORECASE)
    if body_match:
        index = body_match.end()
        return cleaned[:index] + "\n" + marker + cleaned[index:]
    return marker + "\n" + cleaned

_inject_summary_pipeline_identity_html = inject_summary_pipeline_identity_html

def _build_summary_generation_quality(
    summary: str,
    *,
    comment_timeline_supplement_learning: Optional[dict] = None,
) -> Optional[dict]:
    if SUMMARY_MERGE_FALLBACK_MARKER not in (summary or ""):
        return None
    parsed = _parse_summary_sections(summary)
    supplement = (
        comment_timeline_supplement_learning
        if isinstance(comment_timeline_supplement_learning, dict)
        else {}
    )
    candidate_count = int(supplement.get("candidate_count") or 0)
    inserted_count = int(supplement.get("inserted_count") or 0)
    duplicate_skipped_count = int(supplement.get("duplicate_skipped_count") or 0)
    cap_skipped_count = int(supplement.get("cap_skipped_count") or 0)
    context_anchor_status = "unverified"
    if candidate_count:
        context_anchor_status = "supplement_recorded"
        if inserted_count:
            context_anchor_status = "supplement_inserted"
        elif duplicate_skipped_count:
            context_anchor_status = "nearby_duplicate_skipped"
        elif cap_skipped_count:
            context_anchor_status = "cap_skipped"
    return {
        "schema_version": SUMMARY_MERGE_FALLBACK_SCHEMA_VERSION,
        "status": "degraded_fallback",
        "fallback": "chunk_scaffold",
        "manager_label": "요약 품질 검토 필요",
        "raw_content_included": False,
        "timeline_item_count": len(parsed.get("timeline") or []),
        "timeline_item_cap": SUMMARY_MERGE_FALLBACK_TIMELINE_MAX_BLOCKS,
        "highlight_item_count": len(parsed.get("highlights") or []),
        "highlight_status": "withheld_due_to_merge_fallback",
        "context_anchor_status": context_anchor_status,
        "comment_timeline_candidate_count": candidate_count,
        "comment_timeline_inserted_count": inserted_count,
        "risk_flags": [
            "final_merge_coverage_or_structure_failed",
            "chunk_scaffold_recovery",
            "manager_review_required_before_publish",
        ],
    }

_REPORT_PROCESS_NOTE_SECTION_RE = re.compile(
    r"\n*^###\s*(?:🛠️\s*)?(?:작업\s*요약|처리\s*요약|포맷\s*정규화|검토\s*메모)[^\n]*\n.*?(?=^###\s*|\Z)",
    re.M | re.S,
)

_REPORT_FALLBACK_EDITOR_NOTE_RE = re.compile(
    r"\n*^###\s*(?:📝\s*)?에디터의?\s*방송\s*후기\s*\n+"
    r"자동 통합 응답이 별도 후기 섹션을 제공하지 않아 타임라인 중심으로 정리했습니다\.\s*",
    re.M,
)

def _normalize_manager_report_markdown(text: str) -> str:
    if not text:
        return text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"^(#{1,6})(?=[^\s#])", r"\1 ", normalized, flags=re.M)
    normalized = _REPORT_PROCESS_NOTE_SECTION_RE.sub("\n", normalized)
    normalized = _REPORT_FALLBACK_EDITOR_NOTE_RE.sub("\n", normalized)
    normalized = re.sub(r"(?<![\n#])(#{2,3}\s+)", r"\n\n\1", normalized)
    normalized = re.sub(r"(?<!\n)(-\s+\*\*(?:핵심 요약|\[\d{1,2}:\d{2}:\d{2}))", r"\n\1", normalized)

    normalized = re.sub(
        r"<!--[\s\S]*?-->|(?<!\n)(>\s*\"?)",
        lambda match: match.group(0) if match.group(1) is None else "\n\n" + match.group(1),
        normalized,
    )
    normalized = re.sub(r"([가-힣])\n\s*\1(?=[가-힣])", r"\1", normalized)
    normalized = re.sub(r"([가-힣])\s+\1(?=[가-힣])", r"\1", normalized)

    lines = normalized.splitlines()
    cleaned: list[str] = []
    report_heading_seen = False
    timeline_heading_seen = False
    highlight_heading_seen = False
    editor_heading_seen = False
    for line in lines:
        stripped = line.strip()
        if stripped == "#":
            continue
        if stripped == ".":
            continue
        if (
            cleaned
            and stripped
            and not re.match(r"^(#{1,6}\s|[-*]\s|\d+[.)]\s|>\s|```|---\s*$)", stripped)
            and not stripped.startswith("<!--")
            and not re.match(r"^\[(?:VOD\s+\d+(?:\]\s*\[|\s+))?\d{2}:\d{2}:\d{2}", stripped)
            and not re.match(r"^\s*$", cleaned[-1])
        ):
            previous = cleaned.pop()
            if previous and stripped and re.search(r"[가-힣A-Za-z0-9]$", previous):
                if previous[-1:] == stripped[:1]:
                    stripped = stripped[1:].lstrip()
                cleaned.append((previous.rstrip() + " " + stripped).rstrip())
                continue
            cleaned.append(previous)
        if re.match(r"^\s*##\s*.*방송\s*분석\s*리포트", line):
            if report_heading_seen:
                continue
            report_heading_seen = True
        if re.match(r"^\s*###\s*.*타임라인\s*상세\s*요약", line):
            if timeline_heading_seen:
                continue
            timeline_heading_seen = True
        if re.match(r"^\s*###\s*.*하이라이트\s*추천\s*구간", line):
            if highlight_heading_seen:
                continue
            highlight_heading_seen = True
        if re.match(r"^\s*###\s*.*에디터의?\s*방송\s*후기", line):
            if editor_heading_seen:
                continue
            editor_heading_seen = True
        cleaned.append(line.rstrip())
    normalized = "\n".join(cleaned)
    normalized = re.sub(r"([가-힣])\s+\1(?=[가-힣])", r"\1", normalized)
    normalized = re.sub(r"\b총평\s+을\b", "총평을", normalized)
    normalized = re.sub(r"\b돌아보\s+는\b", "돌아보는", normalized)
    normalized = re.sub(r"\b넣\s+어\b", "넣어", normalized)
    normalized = re.sub(r"\b시작\s+하\s+자마자\b", "시작하자마자", normalized)
    normalized = re.sub(r"\b파훼하기\s+위\s+해\b", "파훼하기 위해", normalized)
    normalized = re.sub(r"\b아\s+쉽게\b", "아쉽게", normalized)
    normalized = re.sub(r"\b일\s+갈\b", "일갈", normalized)
    normalized = re.sub(r"\b짚어내\s+며\b", "짚어내며", normalized)
    normalized = re.sub(r"일\s*갈을", "일갈을", normalized)
    normalized = re.sub(r"맛깔나\s*는", "맛깔나는", normalized)
    normalized = re.sub(r"총평\s*을\s*전하며", "총평을 전하며", normalized)
    normalized = re.sub(r"돌아보\s*는\s*오너", "돌아보는 오너", normalized)
    normalized = re.sub(r"넣\s*어\s*억울함", "넣어 억울함", normalized)
    normalized = re.sub(r"졸린\s*듯\s*차분한", "졸린 듯 차분한", normalized)
    normalized = re.sub(r"짚어내\s*며\s*전문적인", "짚어내며 전문적인", normalized)
    normalized = re.sub(r"끝난\s*후\s*다시보기를", "끝난 후 다시보기를", normalized)
    normalized = re.sub(r"큰\s*웃음", "큰 웃음", normalized)
    normalized = re.sub(r"밴\s*해제", "밴 해제", normalized)
    normalized = re.sub(r"수\s*싸움", "수 싸움", normalized)
    normalized = re.sub(r"건\s*순간적인", "건 순간적인", normalized)
    normalized = re.sub(r"속\s*억울한", "속 억울한", normalized)
    normalized = re.sub(r"밴\s*현장", "밴 현장", normalized)
    normalized = re.sub(r"대\s*퓨리아", "대 퓨리아", normalized)
    normalized = re.sub(r"숨\s*막히는", "숨 막히는", normalized)
    normalized = re.sub(r"픽\s*사수", "픽 사수", normalized)
    normalized = re.sub(r"비\s*상황", "비상 상황", normalized)
    normalized = re.sub(r"똑\s*부러지게", "똑부러지게", normalized)
    normalized = re.sub(r"\n\.\s*", "\n", normalized)
    normalized = re.sub(r"(###\s*📝\s*에디터의?\s*방송\s*후기)\s+(.+)", r"\1\n\n\2", normalized)

    scaffold_tail = re.compile(
        r"\n+###\s*🎬\s*하이라이트 추천 구간\s*\n+"
        r"-\s*위 타임라인에서 편집 후보를 우선 검토하세요\.\s*\n+"
        r"###\s*📝\s*에디터의 방송 후기\s*\n+"
        r"자동 통합 응답이 별도 후기 섹션을 제공하지 않아 타임라인 중심으로 정리했습니다\.\s*",
        re.S,
    )
    if len(re.findall(r"^###\s*🎬\s*하이라이트 추천 구간", normalized, flags=re.M)) > 1:
        normalized = scaffold_tail.sub("\n", normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip() + "\n"

_SUMMARY_EVIDENCE_CONTROLS_HTML = """
<style id="summaryEvidenceControlsStyle">
  .summary-evidence-quote-citations {
    display: flex; flex-wrap: wrap; gap: 6px 10px;
  }
  .summary-evidence-quote-citation {
    display: inline-flex; align-items: baseline; gap: 3px;
    color: var(--muted); line-height: 1.55;
  }
  .summary-evidence-quote-citation a {
    color: var(--tc); font-weight: 700; text-decoration: none;
  }
  .summary-evidence-quote-citation a:hover {
    color: var(--text-strong); text-decoration: underline; text-underline-offset: 3px;
  }
  .summary-evidence-quote-citation q {
    color: var(--text); quotes: none; overflow-wrap: anywhere; word-break: break-word;
  }
  .summary-evidence-not-verified {
    color: var(--muted); font-size: 12px; line-height: 1.5;
  }
</style>
<script id="summaryEvidenceControlsScript">
(function () {
  const quoteDataEl = document.getElementById('summaryEvidenceQuoteCitationsData');
  if (!quoteDataEl) return;
  let quotePayload = {};
  if (quoteDataEl) {
    try {
      quotePayload = JSON.parse(quoteDataEl.textContent || '{}');
    } catch (_err) {
      quotePayload = {};
    }
  }
  const formatTime = (sec) => {
    const value = Number(sec);
    if (!Number.isFinite(value) || value < 0) return '';
    const total = Math.floor(value);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };
  const chzzkUrlAt = (sec) => {
    const videoNo = String(quotePayload?.video_no || '').trim();
    const value = Number(sec);
    if (!videoNo || !Number.isFinite(value) || value < 0) return '';
    return `https://chzzk.naver.com/video/${encodeURIComponent(videoNo)}?currentTime=${Math.floor(value)}`;
  };
  const quoteBySegment = new Map();
  if (
    quotePayload &&
    quotePayload.raw_transcript_content_included !== true &&
    Array.isArray(quotePayload.items)
  ) {
    quotePayload.items.forEach((item) => {
      if (!item || !item.segment_id || !Array.isArray(item.citations)) return;
      quoteBySegment.set(String(item.segment_id), item);
    });
  }
  if (!quoteBySegment.size) return;

  const mountQuoteCitations = (card, item) => {
    if (!item || !Array.isArray(item.citations)) return false;
    let evidence = card.querySelector('.t-evidence');
    if (!evidence) {
      evidence = document.createElement('div');
      evidence.className = 't-evidence';
      const body = card.querySelector('.t-body');
      if (body) body.insertAdjacentElement('afterend', evidence);
      else card.append(evidence);
    }
    const wrap = document.createElement('div');
    wrap.className = 'summary-evidence-quote-citations';
    wrap.setAttribute('data-summary-evidence-quote-citations-mounted', '1');
    item.citations.filter((row) =>
      row && typeof row.quote === 'string' && row.quote.trim()
      && Number.isFinite(Number(row.start_sec))
      && /^\\d{2,}:\\d{2}:\\d{2}$/.test(String(row.timecode || ''))
      && String(row.match_status || '') !== 'unmatched'
    ).slice(0, 8).forEach((row) => {
      const quote = String(row.quote || '').trim();
      const chip = document.createElement('span');
      chip.className = 'summary-evidence-quote-citation';
      const time = row.timecode || formatTime(row.start_sec);
      const url = chzzkUrlAt(row.start_sec);
      if (time && url) {
        const link = document.createElement('a');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = `[${time}]`;
        link.title = '치지직 다시보기에서 이 근거 시점부터 재생';
        chip.append(link);
      } else if (time) {
        const span = document.createElement('span');
        span.textContent = `[${time}]`;
        chip.append(span);
      }
      const q = document.createElement('q');
      q.textContent = quote;
      chip.append(q);
      wrap.append(chip);
    });
    if (!wrap.childElementCount) {
      const status = document.createElement('span');
      status.className = 'summary-evidence-not-verified';
      status.setAttribute('role', 'note');
      status.textContent = '확인 가능한 자막·채팅 근거 없음';
      wrap.append(status);
    }
    evidence.replaceChildren(wrap);
    return true;
  };

  document.querySelectorAll('.t-item[data-segment-id]').forEach((card) => {
    const segmentId = card.getAttribute('data-segment-id') || '';
    mountQuoteCitations(card, quoteBySegment.get(segmentId));
  });
}());
</script>"""

def _inject_summary_evidence_payload_html(html_content: str, payload: dict) -> str:
    if not isinstance(payload, dict) or not payload:
        return html_content
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    fragments: list[str] = []
    if not re.search(r"<script\b[^>]*\bid=['\"]summaryEvidencePackData['\"]", html_content, re.IGNORECASE):
        fragments.append(
            '<script id="summaryEvidencePackData" type="application/json">'
            f"{payload_json}</script>"
        )
    if not re.search(r"<script\b[^>]*\bid=['\"]summaryEvidenceControlsScript['\"]", html_content, re.IGNORECASE):
        fragments.append(_SUMMARY_EVIDENCE_CONTROLS_HTML)
    if not fragments:
        return html_content
    injection = "\n".join(fragments)
    lower = html_content.lower()
    idx = lower.rfind("</body>")
    if idx >= 0:
        return html_content[:idx] + injection + "\n" + html_content[idx:]
    return html_content + "\n" + injection

def _inject_summary_quote_citations_payload_html(html_content: str, payload: dict) -> str:
    if not isinstance(payload, dict) or not payload:
        return html_content
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    payload_script = (
        '<script id="summaryEvidenceQuoteCitationsData" type="application/json">'
        f"{payload_json}</script>"
    )
    html = html_content
    if re.search(r"<script\b[^>]*\bid=['\"]summaryEvidenceQuoteCitationsData['\"]", html, re.IGNORECASE):
        html = re.sub(
            r'<script\s+id="summaryEvidenceQuoteCitationsData"[^>]*>.*?</script>',
            lambda _m: payload_script,
            html,
            count=1,
            flags=re.DOTALL,
        )
    else:
        lower = html.lower()
        control_idx = lower.find('<script id="summaryevidencecontrolsscript"')
        if control_idx >= 0:
            html = html[:control_idx] + payload_script + "\n" + html[control_idx:]
        else:
            idx = lower.rfind("</body>")
            if idx >= 0:
                html = html[:idx] + payload_script + "\n" + html[idx:]
            else:
                html = html + "\n" + payload_script
    if not re.search(r"<script\b[^>]*\bid=['\"]summaryEvidenceControlsScript['\"]", html, re.IGNORECASE):
        lower = html.lower()
        idx = lower.rfind("</body>")
        if idx >= 0:
            html = html[:idx] + _SUMMARY_EVIDENCE_CONTROLS_HTML + "\n" + html[idx:]
        else:
            html = html + "\n" + _SUMMARY_EVIDENCE_CONTROLS_HTML
    return html

def _inject_timeline_evidence_ux_payload_html(
    html_content: str,
    payload: dict,
) -> str:
    """Retire the time-only chip payload and remove it from legacy HTML."""

    return re.sub(
        r'\s*<script\s+id="timelineEvidenceUxData"[^>]*>.*?</script>',
        "",
        html_content,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
