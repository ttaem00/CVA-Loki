"""Normal report projection and rendering helpers.

This module owns normal-report parsing, segment metadata, and HTML rendering.
It intentionally depends only on lower pipeline modules, never the orchestration
facade in ``pipeline.summarizer``.
"""

import copy

import hashlib

import json

import re

from pathlib import Path

from typing import Any, Optional

from pipeline.summary.parse import (
    _SRT_XVAL_THRESHOLD_SEC,
    _parse_summary_sections,
    _strip_timestamp_confidence_markers,
)

from pipeline.summary.payload import (
    _bucketed_eligible_clip_objects,
    _clip_offset_int,
)

from pipeline.chapter_readiness import validate_youtube_chapter_readiness

from pipeline.editorial_highlight_cards import EDITORIAL_HIGHLIGHT_CARD_CSS, render_pending_editorial_review

from pipeline.models import UserClipsBundle, VODInfo

from pipeline.utils import sec_to_hms

SEGMENT_LEDGER_SCHEMA_VERSION = 1

from pipeline.summary.packet import (
    _segment_time_bounds,
)

from pipeline.summary.render import (
    _HIGHLIGHT_LEARNING_NOTICE,
    _VIEWER_EDITOR_TOOLS_CSS,
    _VIEWER_EDITOR_TOOLS_JS,
    _audio_peak_badge_html,
    _build_chzzk_vod_url,
    _build_og_meta,
    _build_user_clips_block,
    _build_viewer_editor_tools_html,
    _build_viewer_editor_tools_payload,
    _chartjs_script_tag,
    _extract_audio_peak_secs,
    _generate_youtube_html,
    _html_escape,
    _hms_to_seconds,
    _normalize_viewer_clip_title,
    _project_editorial_review_html,
    _render_chzzk_comment_text,
    _render_collaboration_chips_html,
    _render_community_reaction_md,
    _render_inline_md,
    _render_broadcast_summary_html,
    _render_naver_cafe_html,
    _render_tc_anchor_html,
    _render_top_vod_link_html,
    _scope_viewer_editor_evidence_source,
    _source_platform,
    _validate_editorial_review_projection,
    _viewer_clip_badge_html,
    _viewer_outline_projection_rows,
    render_normal_report_document,
)

_COMMUNITY_POST_STAT_RE = re.compile(
    r'\s*<div class="stat"><div class="stat-label">커뮤니티 글</div>'
    r'<div class="stat-value">[0-9,]+</div></div>'
)

def _remove_community_post_stat(html: str) -> str:
    """Remove the retired header metric without touching community evidence."""
    return _COMMUNITY_POST_STAT_RE.sub("", html)

_TOP_VOD_LINK_CSS = """
  .top-vod-link-row { display: inline; }
  .top-vod-link {
    color: var(--tc); text-decoration: none; font-size: inherit; font-weight: 600;
  }
  .top-vod-link:hover { color: var(--text-strong); text-decoration: underline; text-underline-offset: 3px; }
"""

VIEWER_CLIPS_CSS = """
  .user-clips-summary { color: var(--muted); margin: 0 0 14px; font-size: 13px; }
  .user-clips-empty { color: var(--muted); margin: 0; font-size: 14px; }
  .user-clips-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
  }
  .user-clip-card {
    display: flex; flex-direction: column; gap: 10px;
    background: var(--surface-2); border: 1px solid var(--border-soft);
    border-radius: 12px; padding: 12px; color: var(--text);
    transition: border-color 120ms ease, transform 120ms ease;
  }
  .user-clip-card:hover { border-color: var(--accent); transform: translateY(-1px); }
  .user-clip-title-link {
    display: block; color: inherit; text-decoration: none;
  }
  .user-clip-media {
    position: relative; width: 100%; aspect-ratio: 16/9; overflow: hidden;
    border-radius: 8px; background: #05070a;
  }
  .user-clip-play {
    position: relative; display: block; width: 100%; height: 100%; padding: 0;
    border: 0; border-radius: inherit; overflow: hidden; cursor: pointer;
    background: #05070a; color: #fff; font: inherit;
  }
  .user-clip-play[hidden] { display: none; }
  .user-clip-play::after {
    content: ''; position: absolute; inset: 0;
    background: linear-gradient(180deg, transparent 42%, rgba(0,0,0,.72));
  }
  .user-clip-play:hover .user-clip-play-label { transform: translate(-50%, -50%) scale(1.04); }
  .user-clip-play:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
  .user-clip-play-label {
    position: absolute; z-index: 1; left: 50%; top: 50%; transform: translate(-50%, -50%);
    display: inline-flex; align-items: center; gap: 7px; white-space: nowrap;
    padding: 9px 14px; border-radius: 999px; background: rgba(0,0,0,.78);
    box-shadow: 0 4px 18px rgba(0,0,0,.32); font-size: 13px; font-weight: 700;
    transition: transform 120ms ease;
  }
  .user-clip-thumb {
    width: 100%; height: 100%; object-fit: cover;
    background: var(--bg); display: block;
  }
  .user-clip-dialog {
    width: min(960px, calc(100vw - 32px)); margin: auto; padding: 0; border: 1px solid var(--border-soft);
    border-radius: 14px; background: var(--surface-1); color: var(--text); overflow: hidden;
  }
  .user-clip-dialog::backdrop { background: rgba(0, 0, 0, .78); }
  .user-clip-dialog-head {
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 10px 14px; background: var(--surface-2);
  }
  .user-clip-dialog-head strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .user-clip-dialog-close {
    flex: 0 0 auto; width: 40px; height: 40px; border: 0; border-radius: 8px;
    background: transparent; color: inherit; font-size: 28px; line-height: 1; cursor: pointer;
  }
  .user-clip-dialog-close:hover, .user-clip-dialog-close:focus-visible { background: rgba(255, 255, 255, .12); }
  .user-clip-dialog-body { aspect-ratio: 16/9; background: #000; }
  .user-clip-dialog-frame { display: block; width: 100%; height: 100%; border: 0; background: #000; }
  .user-clip-body { display: flex; flex-direction: column; gap: 8px; }
  .user-clip-title {
    color: var(--text-strong); font-size: 14px; font-weight: 600; line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .user-clip-source {
    align-self: flex-start;
    display: inline-flex; align-items: center; gap: 4px;
    background: rgba(122, 162, 247, 0.12);
    border: 1px solid rgba(122, 162, 247, 0.34);
    border-radius: 999px; padding: 2px 9px;
    color: var(--text-strong); font-size: 11px; line-height: 1.5; font-weight: 600;
  }
  .user-clip-title-link:hover .user-clip-title { color: var(--accent); }
  .user-clip-foot {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 12px; color: var(--muted);
  }
  .user-clip-tc { font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', Consolas, 'Courier New', monospace; }
  .user-clip-tc-missing { color: var(--muted); font-style: italic; }
  .user-clip-meta { font-family: 'JetBrains Mono', 'Cascadia Code', 'Fira Code', Consolas, 'Courier New', monospace; }
  .user-clips-source-health {
    display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 14px;
  }
  .user-clips-chip {
    display: inline-flex; align-items: center; gap: 4px;
    background: var(--surface-2); border: 1px solid var(--border-soft);
    border-radius: 999px; padding: 3px 10px;
    font-size: 12px; line-height: 1.5; color: var(--muted);
    cursor: help;
  }
  .user-clips-chip-warn {
    background: rgba(255, 165, 0, 0.10); border-color: rgba(255, 165, 0, 0.35);
    color: var(--text);
  }
  .user-clip-badge-promoted {
    align-self: flex-start;
    display: inline-flex; align-items: center;
    background: rgba(255, 99, 132, 0.14);
    border: 1px solid rgba(255, 99, 132, 0.45);
    color: var(--text-strong);
    border-radius: 999px; padding: 2px 9px;
    font-size: 11px; line-height: 1.5; font-weight: 600;
    cursor: help;
  }
""".strip()

_COMMUNITY_REACTION_HEADING_RE = re.compile(
    r"^(#{2,4})\s*(?:[^\n#]*커뮤니티[^\n#]*(?:반응|여론|리액션|화제)|[^\n#]*community\s+reaction[^\n#]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

def _extract_community_reaction_sections(md_text: str) -> list[tuple[int, int, str]]:
    if not md_text:
        return []
    matches = list(_COMMUNITY_REACTION_HEADING_RE.finditer(md_text))
    if not matches:
        return []
    spans: list[tuple[int, int]] = []
    for idx, match in enumerate(matches):
        level = len(match.group(1))
        next_start = len(md_text)
        for next_match in matches[idx + 1:]:
            if len(next_match.group(1)) <= level:
                next_start = next_match.start()
                break
        if next_start == len(md_text):
            boundary = re.search(rf"^#{{1,{level}}}\s+", md_text[match.end():], re.MULTILINE)
            if boundary:
                next_start = match.end() + boundary.start()
        spans.append((match.start(), next_start))
    return [
        (start, end, md_text[start:end].strip())
        for start, end in spans
        if md_text[start:end].strip()
    ]

def _strip_community_reaction_sections(md_text: str) -> str:
    """Remove standalone community reaction sections while keeping inline evidence."""
    sections = _extract_community_reaction_sections(md_text)
    if not sections:
        return md_text
    pieces: list[str] = []
    cursor = 0
    for start, end, _fragment in sections:
        pieces.append(md_text[cursor:start])
        cursor = end
    pieces.append(md_text[cursor:])
    return re.sub(r"\n{3,}", "\n\n", "".join(pieces)).strip() + "\n"

def _stable_segment_id(
    vod_info: VODInfo,
    kind: str,
    start_sec: int | None,
    end_sec: int | None,
    duplicate_index: int,
) -> str:
    """Stable segment id for one VOD/kind/time coordinate.

    Title/body text is intentionally excluded so manager title rewrites do not
    churn ids. duplicate_index is normally 1 and only disambiguates exact
    same-time entries inside the same kind.
    """
    vod_key = str(getattr(vod_info, "video_no", "") or "").strip()
    if not vod_key:
        vod_key = "|".join(
            str(getattr(vod_info, name, "") or "").strip()
            for name in ("channel_id", "title", "publish_date")
        )
    payload = "|".join(
        [
            "segment-ledger-v1",
            vod_key,
            kind,
            "" if start_sec is None else str(start_sec),
            "" if end_sec is None else str(end_sec),
            str(duplicate_index),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"seg_{kind}_{digest}"

def _segment_base_source(section: str, fields: list[str], tc_text: str = "") -> dict[str, Any]:
    source: dict[str, Any] = {
        "source_type": "summary_markdown",
        "section": section,
        "fields": fields,
    }
    if tc_text:
        source["timecode"] = tc_text
    return source

def _nearest_audio_peak_source(
    start_sec: int | None,
    end_sec: int | None,
    audio_peak_secs: list[float],
    tolerance_sec: int = 30,
) -> dict[str, Any] | None:
    if start_sec is None or not audio_peak_secs:
        return None
    seg_end = end_sec if end_sec is not None else start_sec
    best_peak: float | None = None
    best_delta: float | None = None
    for peak in audio_peak_secs:
        try:
            peak_f = float(peak)
        except (TypeError, ValueError):
            continue
        if start_sec <= peak_f <= seg_end:
            delta = 0.0
        else:
            delta = min(abs(peak_f - start_sec), abs(peak_f - seg_end))
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_peak = peak_f
    if best_peak is None or best_delta is None or best_delta > tolerance_sec:
        return None
    return {
        "source_type": "audio_reaction_peak",
        "center_sec": best_peak,
        "delta_sec": int(round(best_delta)),
        "role": "supporting_signal_only",
    }

def _promoted_anchor_sources(
    start_sec: int | None,
    end_sec: int | None,
    promoted_anchors: list | None,
    tolerance_sec: int = 60,
) -> list[dict[str, Any]]:
    if start_sec is None or not promoted_anchors:
        return []
    seg_end = end_sec if end_sec is not None else start_sec
    sources: list[dict[str, Any]] = []
    for anchor in promoted_anchors:
        try:
            offset = int(anchor["offset_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if start_sec - tolerance_sec <= offset <= seg_end + tolerance_sec:
            src: dict[str, Any] = {
                "source_type": "viewer_clip_anchor",
                "offset_sec": offset,
                "role": "proximity_signal",
            }
            for key in ("clip_uid", "title", "like_count", "play_count"):
                if isinstance(anchor, dict) and anchor.get(key) not in (None, ""):
                    src[key] = anchor.get(key)
            sources.append(src)
    return sources

def _highlight_signal_metadata(
    highlights: list[dict],
    vod_info: VODInfo,
) -> list[dict[str, Any]]:
    """Structured metadata for raw chat/audio highlight candidates."""
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[str, int | None, int | None], int] = {}
    for index, h in enumerate(highlights or [], start=1):
        try:
            sec = int(float(h.get("sec")))
        except (TypeError, ValueError):
            sec = None
        key = ("highlight_candidate", sec, None)
        seen[key] = seen.get(key, 0) + 1
        if h.get("source_alignment_candidate"):
            sources = [
                {
                    "source_type": "source_edited_alignment_public_signal",
                    "field": "highlights",
                    "sec": sec,
                    "source_start_sec": h.get("source_start_sec"),
                    "source_end_sec": h.get("source_end_sec"),
                    "record_id": h.get("source_alignment_record_id"),
                    "rank_score": h.get("source_alignment_rank_score"),
                    "role": "candidate_signal_only",
                }
            ]
        else:
            sources = [
                {
                    "source_type": "chat_density_peak",
                    "field": "highlights",
                    "sec": sec,
                }
            ]
        if h.get("audio_reaction_peak"):
            sources.append(
                {
                    "source_type": "audio_reaction_peak",
                    "sec": sec,
                    "role": "supporting_signal_only",
                }
            )
        risk_flags: list[str] = []
        if sec is None:
            risk_flags.append("missing_timecode")
        if h.get("audio_reaction_peak"):
            risk_flags.append("audio_peak_supporting_signal")
        if h.get("source_alignment_candidate"):
            risk_flags.append("source_alignment_candidate_not_final_truth")
        rows.append(
            {
                "segment_id": _stable_segment_id(
                    vod_info, "highlight_candidate", sec, None, seen[key]
                ),
                "kind": "highlight_candidate",
                "section": "metadata.highlights",
                "index": index,
                "timecode": sec_to_hms(sec) if sec is not None else "",
                "start_sec": sec,
                "end_sec": None,
                "sources": sources,
                "risk_flags": risk_flags,
                "sec": h.get("sec"),
                "composite": h.get("composite"),
                "count": h.get("count"),
            }
        )
    return rows

def _attach_segment_metadata(
    sec: dict,
    vod_info: VODInfo,
    *,
    highlights: Optional[list[dict]] = None,
    audio_peak_secs: Optional[list[float]] = None,
    audio_reaction_metadata: Optional[dict] = None,
    promoted_anchors: Optional[list] = None,
    comment_timeline_supplement_learning: Optional[dict] = None,
) -> dict:
    """Attach stable segment_id, sources[], risk_flags[] to parsed sections.

    The function mutates and returns ``sec`` so renderers and JSON export can
    share the exact same ids.
    """
    if audio_peak_secs is None:
        audio_peak_secs = _extract_audio_peak_secs(audio_reaction_metadata)
        if not audio_peak_secs:
            audio_peak_secs = _extract_audio_peak_secs(
                [h.get("sec") for h in (highlights or []) if h.get("audio_reaction_peak")]
            )

    ledger: list[dict[str, Any]] = []
    seen: dict[tuple[str, int | None, int | None], int] = {}
    comment_supplement_by_key: dict[tuple[int | None, str], dict] = {}
    if isinstance(comment_timeline_supplement_learning, dict):
        for row in comment_timeline_supplement_learning.get("inserted") or []:
            if not isinstance(row, dict):
                continue
            comment_supplement_by_key[
                (row.get("start_sec"), str(row.get("title") or "").strip())
            ] = row

    def _next_dup(kind: str, start_sec: int | None, end_sec: int | None) -> int:
        key = (kind, start_sec, end_sec)
        seen[key] = seen.get(key, 0) + 1
        return seen[key]

    def _add(item: dict, kind: str, section: str, index: int, tc_text: str, title: str) -> dict:
        start_sec, end_sec = _segment_time_bounds(tc_text)
        duplicate_index = _next_dup(kind, start_sec, end_sec)
        sources = [_segment_base_source(section, ["timecode", "title"], tc_text)]
        risk_flags: list[str] = []
        if start_sec is None and kind != "summary":
            risk_flags.append("missing_timecode")
        if not (title or "").strip():
            risk_flags.append("missing_title")

        if kind == "timeline":
            sources[0]["fields"] = ["timecode", "title", "summary", "evidence"]
            if item.get("evidence"):
                sources.append(
                    {
                        "source_type": "chat_or_text_evidence",
                        "section": section,
                        "field": "evidence",
                        "text": item.get("evidence"),
                    }
                )
            else:
                risk_flags.append("missing_evidence")
            if not item.get("summary"):
                risk_flags.append("missing_summary")
            comment_supplement = comment_supplement_by_key.get((start_sec, str(title or "").strip()))
            if comment_supplement:
                risk_flags.append("comment_timeline_supplement")
                sources.append(
                    {
                        "source_type": "comment_timeline_supplement",
                        "section": "chzzk_comment_timeline",
                        "role": "private_rewritten_anchor",
                        "tc": comment_supplement.get("tc") or tc_text or "",
                        "raw_comment_text_included": False,
                        "recommendation_counts_included": False,
                    }
                )
        elif kind == "highlight":
            sources[0]["fields"] = ["timecode", "title", "reason"]
            if item.get("reason"):
                sources.append(
                    {
                        "source_type": "llm_highlight_reason",
                        "section": section,
                        "field": "reason",
                        "text": item.get("reason"),
                    }
                )
            else:
                risk_flags.append("missing_reason")
            if "~" not in (tc_text or ""):
                risk_flags.append("point_highlight_without_end")
        elif kind == "chapter":
            sources[0]["fields"] = ["timecode", "title"]
            if end_sec is None:
                risk_flags.append("point_chapter")

        timestamp_confidence = item.get("timestamp_confidence")
        if isinstance(timestamp_confidence, dict):
            status = str(timestamp_confidence.get("status") or "")
            if status == "mismatch":
                risk_flags.append("timestamp_srt_quote_mismatch")
            elif status == "verified_with_correction":
                risk_flags.append("timestamp_srt_quote_corrected")
            if status in {"mismatch", "verified_with_correction"}:
                sources.append(
                    {
                        "source_type": "srt_quote_cross_validation",
                        "section": section,
                        "role": "timestamp_confidence",
                        "status": status,
                        "claimed_tc": timestamp_confidence.get("claimed_tc") or tc_text or "",
                        "actual_tc": timestamp_confidence.get("actual_tc") or "",
                        "gap_sec": timestamp_confidence.get("gap_sec"),
                        "threshold_sec": _SRT_XVAL_THRESHOLD_SEC,
                    }
                )

        if item.get("is_injected"):
            risk_flags.append("injected_viewer_clip_anchor")
            sources.append(
                {
                    "source_type": "viewer_clip_anchor",
                    "section": section,
                    "label": item.get("injected_label") or "",
                    "role": "deterministic_injection",
                }
            )

        sources.extend(_promoted_anchor_sources(start_sec, end_sec, promoted_anchors))
        audio_source = _nearest_audio_peak_source(start_sec, end_sec, audio_peak_secs or [])
        if audio_source:
            sources.append(audio_source)
            risk_flags.append("audio_peak_supporting_signal")

        segment_id = _stable_segment_id(vod_info, kind, start_sec, end_sec, duplicate_index)
        payload = {
            "segment_id": segment_id,
            "kind": kind,
            "section": section,
            "index": index,
            "timecode": tc_text or "",
            "start_sec": start_sec,
            "end_sec": end_sec,
            "title": title or "",
            "sources": sources,
            "risk_flags": risk_flags,
        }
        if item.get("summary"):
            payload["summary"] = item.get("summary")
        if item.get("reason"):
            payload["reason"] = item.get("reason")
        if isinstance(item.get("timestamp_confidence"), dict):
            payload["timestamp_confidence"] = item.get("timestamp_confidence")
        if kind == "timeline":
            comment_supplement = comment_supplement_by_key.get((start_sec, str(title or "").strip()))
            if comment_supplement is not None:
                payload["comment_timeline_supplement"] = True
                comment_supplement["segment_id"] = segment_id
        item.update(
            {
                "segment_id": segment_id,
                "sources": sources,
                "risk_flags": risk_flags,
            }
        )
        ledger.append(payload)
        return payload

    summary_sources = [
        _segment_base_source("summary", ["title", "hashtags", "pull_quote"])
    ]
    summary_risks: list[str] = []
    if not sec.get("title"):
        summary_risks.append("missing_report_title")
    if not sec.get("hashtags"):
        summary_risks.append("missing_hashtags")
    if not sec.get("pull_quote"):
        summary_risks.append("missing_pull_quote")
    summary_segment = {
        "segment_id": _stable_segment_id(vod_info, "summary", None, None, 1),
        "kind": "summary",
        "section": "summary",
        "index": 1,
        "timecode": "",
        "start_sec": None,
        "end_sec": None,
        "title": sec.get("title") or getattr(vod_info, "title", "") or "",
        "sources": summary_sources,
        "risk_flags": summary_risks,
        "hashtags": list(sec.get("hashtags") or []),
        "pull_quote": sec.get("pull_quote") or "",
    }
    sec["summary_segments"] = [summary_segment]
    ledger.append(summary_segment)

    for index, item in enumerate(sec.get("timeline") or [], start=1):
        _add(item, "timeline", "timeline", index, item.get("tc") or "", item.get("title") or "")

    for index, item in enumerate(sec.get("highlights") or [], start=1):
        _add(
            item,
            "highlight",
            "highlights",
            index,
            item.get("tc_range") or "",
            item.get("title") or "",
        )

    for index, item in enumerate(sec.get("chzzk_comment_timeline") or [], start=1):
        tc_text = item.get("tc") or ""
        if item.get("tc_end"):
            tc_text = f"{tc_text}~{item.get('tc_end')}"
        _add(item, "chapter", "chzzk_comment_timeline", index, tc_text, item.get("title") or "")

    highlight_candidates = _highlight_signal_metadata(highlights or [], vod_info)
    ledger.extend(highlight_candidates)
    sec["highlight_candidates"] = highlight_candidates
    sec["segment_ledger_schema_version"] = SEGMENT_LEDGER_SCHEMA_VERSION
    sec["segment_ledger"] = ledger
    return sec

def _dedupe_visible_timeline_items(items: list[dict]) -> list[dict]:
    """Keep manager-visible timeline cards unique by timestamp.

    Auxiliary comment/chapter rows may remain in the editor tool ledger, but the
    main summary timeline should not show two cards at the same start second.
    """
    result: list[dict] = []
    seen_keys: set[tuple[str, str | int]] = set()
    for item in items or []:
        start, _end = _segment_time_bounds(str(item.get("tc") or ""))
        outline_id = str(item.get("outline_id") or "").strip()
        key: tuple[str, str | int] | None = (
            ("outline", outline_id)
            if outline_id
            else (("start", start) if start is not None else None)
        )
        if key is not None and key in seen_keys:
            continue
        if key is not None:
            seen_keys.add(key)
        result.append(item)
    return result

def _outline_to_summary_timeline_rows(outline: dict[str, Any]) -> list[dict[str, Any]]:
    """Show accepted Points, or the accepted navigation when there are none.

    A Point-free VOD still has useful D1/D2 navigation. Keep ranges out of the
    Point timeline when Points exist so the public report does not duplicate
    the detailed scene cards.
    """
    rows: list[dict[str, Any]] = []
    points = outline.get("points") or []
    for row in points:
        rows.append({
            "tc": row["timestamp"],
            "title": row["title"],
            "summary": row.get("content") or "",
            "evidence": "",
            "mood": "hot",
            "mood_raw": "Point",
            "segment_id": row["id"],
            "outline_id": row["id"],
            "outline_level": "Point",
        })
    if not points:
        for row in outline.get("ranges") or []:
            level = str(row.get("level") or "")
            if level not in {"D1", "D2"}:
                continue
            rows.append({
                "tc": row["start"],
                "title": row["title"],
                "summary": f"{row['start']}–{row['end']}",
                "evidence": "",
                "mood": "chill",
                "mood_raw": level,
                "segment_id": row["id"],
                "outline_id": row["id"],
                "outline_level": level,
            })
    return rows

def _report_projection_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get("report_projection")
    return nested if isinstance(nested, dict) else value

def _report_projection_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"report projection invalid {field}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"report projection invalid {field}") from exc
    if number < 0:
        raise ValueError(f"report projection invalid {field}")
    return number

def _report_projection_chat_buckets(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list):
        return []
    buckets: list[dict[str, int]] = []
    previous_start = -1
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("report projection invalid chat bucket")
        start = _report_projection_nonnegative_int(row.get("start_sec"), field="chat bucket start")
        count = _report_projection_nonnegative_int(row.get("count"), field="chat bucket count")
        if start is None or count is None or start < previous_start:
            raise ValueError("report projection invalid chat bucket")
        end = _report_projection_nonnegative_int(row.get("end_sec"), field="chat bucket end")
        if end is None:
            end = start
        if end < start:
            raise ValueError("report projection invalid chat bucket")
        buckets.append({"start_sec": start, "end_sec": end, "count": count})
        previous_start = start
    return buckets

def _build_report_projection_snapshot(
    *,
    chats: list[dict] | None,
    highlights: list[dict] | None,
    viewer_editor_tools_payload: dict[str, Any] | None = None,
    report_projection_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one normal-report stat/chart snapshot and reject split authority.

    ``None`` means unavailable at this projection boundary. An explicit empty
    list means the producer knows that the source is empty. Report Admin uses
    the saved snapshot when raw work inputs are unavailable instead of silently
    turning the saved values into zero.
    """
    payload = viewer_editor_tools_payload if isinstance(viewer_editor_tools_payload, dict) else {}
    payload_projection = _report_projection_source(payload)
    source_projection = _report_projection_source(report_projection_source)

    payload_buckets = _report_projection_chat_buckets(payload.get("chat_buckets"))
    snapshot_buckets = _report_projection_chat_buckets(
        payload_projection.get("chat_buckets") or source_projection.get("chat_buckets")
    )
    buckets = payload_buckets or snapshot_buckets
    if payload_buckets and snapshot_buckets and payload_buckets != snapshot_buckets:
        raise ValueError("report projection chat buckets mismatch")
    if not buckets and chats:
        from pipeline.chat_analyzer import WINDOW_SEC, build_time_series

        time_series = build_time_series(chats, WINDOW_SEC)
        buckets = [
            {
                "start_sec": int(start),
                "end_sec": int(start) + int(WINDOW_SEC),
                "count": int(row.get("count") or 0),
            }
            for start, row in sorted((time_series.get("buckets") or {}).items())
        ]

    chat_count_candidates: list[int] = []
    if chats is not None:
        chat_count_candidates.append(len(chats))
    if buckets:
        chat_count_candidates.append(sum(row["count"] for row in buckets))
    for container in (
        payload_projection,
        source_projection,
        payload.get("stats"),
        payload.get("summary"),
        report_projection_source,
    ):
        if not isinstance(container, dict):
            continue
        count = _report_projection_nonnegative_int(
            container.get("total_chats"), field="total chats"
        )
        if count is not None:
            chat_count_candidates.append(count)
    if len(set(chat_count_candidates)) > 1:
        raise ValueError("report projection chat count mismatch")
    total_chats = chat_count_candidates[0] if chat_count_candidates else None
    chat_status = (
        "available"
        if total_chats is not None and total_chats > 0
        else "known_empty"
        if total_chats == 0
        else "unavailable"
    )
    if chat_status == "available" and not buckets:
        raise ValueError("report projection chat buckets unavailable")

    signal_count_candidates: list[int] = []
    if highlights is not None:
        signal_count_candidates.append(len(highlights))
    for container in (payload_projection, source_projection, report_projection_source):
        if not isinstance(container, dict):
            continue
        count = _report_projection_nonnegative_int(
            container.get("signal_scene_count", container.get("highlight_count")),
            field="signal scene count",
        )
        if count is not None:
            signal_count_candidates.append(count)
    metadata_highlights = (
        report_projection_source.get("highlights")
        if isinstance(report_projection_source, dict)
        else None
    )
    snapshot_has_signal_count = any(
        _report_projection_nonnegative_int(
            container.get("signal_scene_count", container.get("highlight_count")),
            field="signal scene count",
        )
        is not None
        for container in (payload_projection, source_projection)
        if isinstance(container, dict)
    )
    if isinstance(metadata_highlights, list) and not snapshot_has_signal_count:
        signal_count_candidates.append(len(metadata_highlights))
    if len(set(signal_count_candidates)) > 1:
        raise ValueError("report projection signal scene count mismatch")
    signal_scene_count = signal_count_candidates[0] if signal_count_candidates else None
    signal_scene_status = (
        "available"
        if signal_scene_count is not None and signal_scene_count > 0
        else "known_empty"
        if signal_scene_count == 0
        else "unavailable"
    )

    signal_scene_secs: list[int] = []
    source_scene_secs = payload_projection.get("signal_scene_secs") or source_projection.get(
        "signal_scene_secs"
    )
    if isinstance(source_scene_secs, list):
        for value in source_scene_secs:
            sec = _report_projection_nonnegative_int(value, field="signal scene time")
            if sec is not None:
                signal_scene_secs.append(sec)
    elif highlights is not None:
        for row in highlights:
            if not isinstance(row, dict):
                continue
            sec = _report_projection_nonnegative_int(row.get("sec"), field="signal scene time")
            if sec is not None:
                signal_scene_secs.append(sec)
    elif isinstance(metadata_highlights, list):
        for row in metadata_highlights:
            if not isinstance(row, dict):
                continue
            sec = _report_projection_nonnegative_int(row.get("sec"), field="signal scene time")
            if sec is not None:
                signal_scene_secs.append(sec)

    return {
        "chat_status": chat_status,
        "total_chats": total_chats,
        "chat_buckets": buckets,
        "signal_scene_status": signal_scene_status,
        "signal_scene_count": signal_scene_count,
        "signal_scene_secs": signal_scene_secs,
    }

def _generate_html(
    summary_md: str,
    vod_info: VODInfo,
    highlights: list[dict] | None,
    chats: list[dict] | None,
    community_posts: list = None,
    public_url_base: str = "",
    user_clips: Optional[UserClipsBundle] = None,
    promoted_anchors: Optional[list] = None,
    show_community_reaction_summary: bool = False,
    show_community_reaction_cafe: bool = False,
    audio_reaction_metadata: Optional[dict] = None,
    youtube_chapter_readiness: Optional[dict] = None,
    srt_path: str = "",
    work_dir: str | Path | None = None,
    evidence_video_no: str = "",
    viewer_editor_tools_source: Optional[dict[str, Any]] = None,
    outline_source_path: str | Path | None = None,
    structured_outline: Optional[dict] = None,
    editorial_review_packet: Optional[dict] = None,
    report_projection_source: Optional[dict[str, Any]] = None,
    review_progress: Optional[dict[str, Any]] = None,
) -> str:
    """구조화된 카드 레이아웃 HTML 렌더링 (Tokyo Night 팔레트, 중앙 정렬)"""
    if _source_platform(vod_info) == "youtube":
        return _generate_youtube_html(summary_md, vod_info)
    from pipeline.chat_analyzer import WINDOW_SEC

    scoped_viewer_editor_tools_source = (
        _scope_viewer_editor_evidence_source(
            copy.deepcopy(viewer_editor_tools_source),
            report_video_no=str(vod_info.video_no or ""),



            evidence_video_no=str(vod_info.video_no or ""),
        )
        if isinstance(viewer_editor_tools_source, dict)
        else None
    )
    report_projection = _build_report_projection_snapshot(
        chats=chats,
        highlights=highlights,
        viewer_editor_tools_payload=scoped_viewer_editor_tools_source,
        report_projection_source=report_projection_source,
    )
    bucket_rows = report_projection["chat_buckets"]
    bucket_keys = [row["start_sec"] for row in bucket_rows]
    chart_labels = json.dumps([sec_to_hms(k) for k in bucket_keys], ensure_ascii=False)
    chart_counts = json.dumps([row["count"] for row in bucket_rows])
    chart_bucket_secs = json.dumps(bucket_keys)
    peak_secs = json.dumps(report_projection["signal_scene_secs"])
    render_chats = chats or []
    render_highlights = highlights or []
    audio_peak_secs_list = _extract_audio_peak_secs(audio_reaction_metadata)
    if not audio_peak_secs_list:
        audio_peak_secs_list = _extract_audio_peak_secs([h["sec"] for h in render_highlights if h.get("audio_reaction_peak")])
    audio_peak_secs = json.dumps(audio_peak_secs_list)
    viewer_clip_secs = {
        int(offset)
        for clip in _bucketed_eligible_clip_objects(user_clips, vod_info)
        if (offset := _clip_offset_int(clip)) is not None
    }

    summary_md_for_page = (
        summary_md if show_community_reaction_summary
        else _strip_community_reaction_sections(summary_md)
    )
    public_summary_md_for_page = _strip_timestamp_confidence_markers(summary_md_for_page)
    summary_md_for_cafe = (
        summary_md if show_community_reaction_cafe
        else _strip_community_reaction_sections(summary_md)
    )


    sec = _parse_summary_sections(summary_md_for_page)
    if scoped_viewer_editor_tools_source:
        from ..collaboration_candidates import public_collaboration_candidates
        sec["collaboration_candidates"] = public_collaboration_candidates(
            scoped_viewer_editor_tools_source.get("collaboration_candidates", []))

    outline_projection: dict[str, Any] | None = None
    outline_owns_timeline = False
    if isinstance(structured_outline, dict):
        outline_projection = json.loads(json.dumps(structured_outline))
        outline_owns_timeline = True
    elif outline_source_path:
        outline_path = Path(outline_source_path)
        if not outline_path.is_file():
            raise FileNotFoundError(f"manager outline source not found: {outline_path}")
        from pipeline.manager_outline import parse_manager_outline

        outline_projection = parse_manager_outline(
            outline_path.read_text(encoding="utf-8"),
            duration_sec=vod_info.duration or None,
        )


    if (
        not isinstance(structured_outline, dict)
        and "[실제 목차]" in summary_md_for_page
        and "[Point]" in summary_md_for_page
    ):
        from pipeline.manager_outline import parse_manager_outline

        outline_projection = parse_manager_outline(
            summary_md_for_page,
            duration_sec=vod_info.duration or None,
        )
        outline_owns_timeline = True
    if outline_projection is not None:



        sec["manager_outline_projection"] = outline_projection
        if outline_owns_timeline or not sec.get("timeline"):
            sec["timeline"] = _outline_to_summary_timeline_rows(outline_projection)
    sec = _attach_segment_metadata(
        sec,
        vod_info,
        highlights=render_highlights,
        audio_peak_secs=audio_peak_secs_list,
        promoted_anchors=promoted_anchors,
    )
    if isinstance(audio_reaction_metadata, dict) and isinstance(audio_reaction_metadata.get("audio_waveform"), dict):
        sec["audio_waveform"] = audio_reaction_metadata["audio_waveform"]
    if isinstance(audio_reaction_metadata, dict) and isinstance(audio_reaction_metadata.get("audio_reaction_peaks"), list):
        sec["audio_reaction_peaks"] = audio_reaction_metadata["audio_reaction_peaks"]
    sec_for_cafe = _parse_summary_sections(summary_md_for_cafe)
    if youtube_chapter_readiness is None:
        youtube_chapter_readiness = validate_youtube_chapter_readiness(
            sec,
            duration_sec=vod_info.duration,
        )
    community_reaction_fragments = [
        fragment for _start, _end, fragment in _extract_community_reaction_sections(summary_md)
    ]
    title_display = sec["title"] or vod_info.title


    tag_chips = ""
    if sec["hashtags"]:
        tag_chips = "\n".join(
            f'<span class="tag">#{_html_escape(t)}</span>' for t in sec["hashtags"][:6]
        )

    pull_quote_html = ""
    if sec["pull_quote"]:
        pull_quote_html = f'<div class="quote">"{_html_escape(sec["pull_quote"])}"</div>'

    hero_html = ""
    if tag_chips or pull_quote_html:
        hero_html = f'''
<div class="hero"><div class="bleed-inner">
  {f'<div class="tags">{tag_chips}</div>' if tag_chips else ''}
  {pull_quote_html}
</div></div>'''

    broadcast_summary_html = _render_broadcast_summary_html(summary_md_for_page)


    timeline_html = ""
    if sec["timeline"]:
        items = []
        for t in _dedupe_visible_timeline_items(sec["timeline"]):
            mood_class = f"mood-{t['mood']}"
            mood_emoji = "" if t["mood_raw"] in {"D1", "D2", "Point"} else (t["mood_raw"] or {"hot": "🔥", "funny": "😂", "game": "🎮", "chat": "💬", "warm": "💖", "tense": "😬", "spicy": "🌶️", "chill": "💤", "veryhot": "🔥"}.get(t["mood"], ""))
            segment_id_attr = _html_escape(t.get("segment_id", ""))
            outline_id_attr = _html_escape(t.get("outline_id", ""))
            evidence_html = ""
            public_title, title_viewer_clip = _normalize_viewer_clip_title(
                _strip_timestamp_confidence_markers(t["title"])
            )
            timeline_sec = _hms_to_seconds(str(t.get("tc") or ""))
            viewer_clip = (
                bool(t.get("is_injected"))
                or title_viewer_clip
                or (timeline_sec is not None and timeline_sec in viewer_clip_secs)
            )
            public_summary = _strip_timestamp_confidence_markers(t["summary"])
            public_evidence = _strip_timestamp_confidence_markers(t["evidence"])
            if public_evidence:
                evidence_html = f'<div class="t-evidence">{_render_inline_md(public_evidence)}</div>'
            tc_html = _render_tc_anchor_html(vod_info.video_no, t["tc"])
            injected_badge = _viewer_clip_badge_html(
                is_injected=viewer_clip,
            )
            injected_badge_slot = (
                f'<span class="viewer-editor-point-badge-slot">{injected_badge}</span>'
                if injected_badge
                else ""
            )
            audio_badge = _audio_peak_badge_html(t["tc"], audio_peak_secs_list)
            items.append(f'''
<div class="t-item {mood_class}{' is-injected' if viewer_clip else ''}" data-segment-id="{segment_id_attr}" data-outline-id="{outline_id_attr}">
  <div class="t-head">
    {tc_html}
    <span class="t-title">{_render_inline_md(public_title)}</span>
    <span class="mood">{_html_escape(mood_emoji)}</span>
    {injected_badge_slot}
    {audio_badge}
  </div>
  <div class="t-body">{_render_inline_md(public_summary)}</div>
  {evidence_html}
</div>''')
        outline_ranges = outline_projection.get("ranges") if isinstance(outline_projection, dict) else []
        timeline_hierarchy_attr = (
            ' data-manager-outline-hierarchy="pending"'
            if items
            and any(
                isinstance(row, dict) and str(row.get("level") or "") == "D1"
                for row in (outline_ranges or [])
            )
            else ""
        )
        timeline_html = f'''
<div class="card">
  <div class="card-head">
    <h2>📍 타임라인 상세 요약</h2>
    <button class="t-toggle" onclick="toggleAll(this)">근거 모두 펼치기</button>
  </div>
  <div class="card-body">
    <div class="timeline"{timeline_hierarchy_attr}>{''.join(items)}</div>
  </div>
</div>'''


    highlights_html = ""
    if sec["highlights"]:
        items = []
        for h in sec["highlights"]:
            segment_id_attr = _html_escape(h.get("segment_id", ""))
            public_title = _strip_timestamp_confidence_markers(h["title"])
            public_reason = _strip_timestamp_confidence_markers(h["reason"])
            reason_html = f'<div class="hl-reason">{_render_inline_md(public_reason)}</div>' if public_reason else ""
            tc_html = _render_tc_anchor_html(vod_info.video_no, h["tc_range"])
            injected_badge = _viewer_clip_badge_html(
                is_injected=bool(h.get("is_injected")),
            )
            items.append(f'''
<div class="hl{' is-injected' if h.get('is_injected') else ''}" data-segment-id="{segment_id_attr}">
  <div class="hl-body">
    <div class="hl-title">{tc_html}&nbsp;&nbsp;{_render_inline_md(public_title)}{injected_badge}</div>
    {reason_html}
  </div>
</div>''')
        highlights_html = f'''
<div class="card">
  <div class="card-head"><h2>🎬 하이라이트 추천</h2></div>
  <div class="card-body">
    <p class="hl-note">{_html_escape(_HIGHLIGHT_LEARNING_NOTICE)}</p>
    <div class="hl-list">{''.join(items)}</div>
  </div>
</div>'''

    editorial_review_html = ""
    if editorial_review_packet is not None:
        editorial_count = len(_validate_editorial_review_projection(editorial_review_packet)["highlights"])
        editorial_review_html = f'''
<div class="card editorial-review-packet" id="editorial-highlights">
  <div class="card-head"><h2>🎬 편집 Highlight {editorial_count}</h2></div>
  <div class="card-body">{_project_editorial_review_html(editorial_review_packet, video_no=str(vod_info.video_no or ""))}</div>
</div>'''

    elif (pending_review := render_pending_editorial_review(
        review_progress, video_no=str(vod_info.video_no or "")
    )):
        editorial_review_html = f'''
<div class="card editorial-review-packet" id="editorial-highlights">
  <div class="card-head"><h2>🎬 편집 Highlight · 작성 대기</h2></div>
  <div class="card-body">{pending_review}</div>
</div>'''


    injection_warning_html = ""
    if sec.get("injection_warnings"):
        warning_items = "".join(
            f"<li>{_html_escape(w)}</li>" for w in sec["injection_warnings"]
        )
        injection_warning_html = f'''
<div class="card" style="border-left:3px solid #e0af68">
  <div class="card-head"><h2>⚠️ 시청자 클립 누락 알림</h2></div>
  <div class="card-body">
    <p style="color:var(--muted);font-size:13px">아래 시청자 클립 anchor 는 같은 시각의 자막/채팅 evidence 가 부족해 자동 주입을 건너뛰었습니다 (silence 표기 또는 채팅 누락 구간).</p>
    <ul style="color:var(--muted);font-size:12px;line-height:1.7">{warning_items}</ul>
  </div>
</div>'''


    comment_timeline_html = ""


    user_clips_html = _build_user_clips_block(user_clips, vod_info)


    notes_html = ""

    community_reaction_html = ""
    if show_community_reaction_summary and community_reaction_fragments:
        body = "\n".join(
            _render_community_reaction_md(fragment)
            for fragment in community_reaction_fragments
        )
        community_reaction_html = f'''
<div class="card">
  <div class="card-head"><h2>커뮤니티 반응</h2></div>
  <div class="card-body"><div class="notes">{body}</div></div>
</div>'''




    naver_cafe_html = _render_naver_cafe_html(
        vod_info,
        public_url_base,
        sec_for_cafe,
        user_clips=user_clips,
        audio_peak_secs=audio_peak_secs_list,
    )
    if show_community_reaction_cafe and community_reaction_fragments:
        naver_cafe_html += "\n" + "\n".join(
            _render_community_reaction_md(fragment)
            for fragment in community_reaction_fragments
        )
    naver_export_html = f'''
<div class="card">
  <div class="card-head"><h2>네이버 카페 붙여넣기</h2></div>
  <div class="card-body">
    <p class="export-help">버튼을 누르면 네이버 카페 글쓰기 창에 바로 붙여넣기 쉬운 HTML이 복사됩니다.</p>
    <div class="export-actions">
      <button type="button" class="export-btn" onclick="copyNaverCafeHtml(this)">HTML 복사</button>
      <span class="export-status" id="naverCafeCopyStatus" aria-live="polite"></span>
    </div>
    <details class="export-preview">
      <summary>붙여넣기용 미리보기</summary>
      <div class="naver-preview">{naver_cafe_html}</div>
    </details>
    <template id="naverCafeTemplate">{naver_cafe_html}</template>
    <div id="naverCafeClipboard" class="clipboard-sandbox" aria-hidden="true"></div>
  </div>
</div>'''

    chzzk_comment_text = _render_chzzk_comment_text(
        sec,
        public_url_base,
        vod_info.video_no,
    )
    chzzk_comment_escaped = _html_escape(chzzk_comment_text)
    chzzk_export_html = f'''
<div class="card">
  <div class="card-head"><h2>치지직 다시보기 댓글 붙여넣기용</h2></div>
  <div class="card-body">
    <p class="export-help">버튼을 누르면 아래 타임라인 텍스트가 그대로 복사됩니다. 치지직 다시보기 댓글창에 바로 붙여넣으세요.</p>
    <div class="export-actions">
      <button type="button" class="export-btn" onclick="copyChzzkCommentText(this)">텍스트 복사</button>
      <span class="export-status" id="chzzkCommentCopyStatus" aria-live="polite"></span>
    </div>
    <pre class="chzzk-comment-preview" id="chzzkCommentPreview">{chzzk_comment_escaped}</pre>
  </div>
</div>''' if chzzk_comment_text else ""

    body = _html_escape(public_summary_md_for_page)
    body = re.sub(r"^####\s+(.+)$", r"<h4>\1</h4>", body, flags=re.M)
    body = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", body, flags=re.M)
    body = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", body, flags=re.M)
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)

    body = re.sub(
        r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
        r'<a href="\2" target="_blank" rel="noopener" style="color:var(--accent);word-break:break-all">\1</a>',
        body,
    )
    body = body.replace("\n\n", "</p><p>")

    if not any([sec["timeline"], sec["highlights"]]):
        fallback_html = (
            f'<div class="card"><div class="card-head"><h2>📄 원본 요약 (구조화 파싱 실패)</h2></div>'
            f'<div class="card-body"><div class="notes"><p>{body}</p></div></div></div>'
        )
    else:
        fallback_html = (
            f'<div class="card"><div class="card-body">'
            f'<details><summary style="cursor:pointer;color:var(--muted);font-family:monospace;font-size:13px">'
            f'📄 원본 요약 마크다운 보기</summary>'
            f'<div class="notes" style="margin-top:16px"><p>{body}</p></div></details></div></div>'
        )
    if isinstance(outline_projection, dict):



        fallback_html = ""

    if isinstance(scoped_viewer_editor_tools_source, dict):




        viewer_editor_tools_payload = scoped_viewer_editor_tools_source
    else:
        viewer_editor_tools_payload = _build_viewer_editor_tools_payload(
            dict(sec, highlights=render_highlights),
            vod_info,
            chats,
            srt_path=srt_path,
            work_dir=work_dir,
            evidence_video_no=evidence_video_no,
            user_clips=user_clips,
        )
    outline_projection = sec.get("manager_outline_projection")
    if isinstance(outline_projection, dict):
        viewer_editor_tools_payload["outline_projection"] = _viewer_outline_projection_rows(
            outline_projection,
            sec,
            editorial_review_packet,
        )
    viewer_editor_tools_payload["report_projection"] = copy.deepcopy(report_projection)

    from ..collaboration_replays import load_replays, attach_member_vods, render_replay_links, watch_root, _read
    collaboration_replays = load_replays(str(vod_info.video_no), duration_sec=vod_info.duration,
                                        channel_id=vod_info.channel_id)
    if collaboration_replays:
        viewer_editor_tools_payload["collaboration_replays"] = collaboration_replays
        sec["collaboration_replays"] = collaboration_replays
        replay_request = _read(watch_root() / f"{vod_info.video_no}.json")
        if viewer_editor_tools_payload.get("collaboration_periods") and replay_request:
            viewer_editor_tools_payload["collaboration_periods"] = attach_member_vods(
                viewer_editor_tools_payload["collaboration_periods"], collaboration_replays,
                main_start_ms=replay_request["main"]["broadcast_start_ms"])
    viewer_editor_tools_html = _build_viewer_editor_tools_html(viewer_editor_tools_payload)


    pub_display = vod_info.publish_date
    try:
        from datetime import datetime as _dt
        pub_display = _dt.fromisoformat(vod_info.publish_date.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    og_meta = _build_og_meta(vod_info, public_summary_md_for_page, public_url_base, sec)
    top_vod_link_html = _render_top_vod_link_html(vod_info.video_no)
    chzzk_plain_url = _html_escape(_build_chzzk_vod_url(vod_info.video_no))
    editor_jump_html = f'''
  <div class="editor-jump report-editor-entry" data-editor-entry data-editor-entry-state="fallback" data-editor-mode="companion" data-editor-video-no="{_html_escape(vod_info.video_no)}" data-editor-chzzk-url="{chzzk_plain_url}">
    <a class="report-editor-inline-button editor-companion-entry-link" data-editor-entry-primary href="{chzzk_plain_url}" target="ttaem-chzzk-companion" rel="opener" aria-label="영상 연결. 브라우저별 분할 보기 안내를 확인하세요">영상 연결</a>
    <span class="editor-entry-status" data-editor-entry-status hidden></span>
    <a class="editor-jump-link report-editor-tool-link" href="#youtube-editor-tools" aria-label="유튜브 편집자용 장면 도구로 이동" title="장면 도구로 이동">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M7 4v16"></path>
        <path d="M11 7h9"></path>
        <path d="M11 12h9"></path>
        <path d="M11 17h9"></path>
      </svg>
      <span class="report-editor-visually-hidden">장면 도구</span>
    </a>
  </div>'''

    return _remove_community_post_stat(render_normal_report_document({
        "REPORT_TITLE": _html_escape(vod_info.title),
        "OG_META": og_meta,
        "CHART_SCRIPT": _chartjs_script_tag(),
        "TOP_VOD_CSS": _TOP_VOD_LINK_CSS,
        "VIEWER_CLIPS_CSS": VIEWER_CLIPS_CSS,
        "EDITORIAL_HIGHLIGHT_CARD_CSS": EDITORIAL_HIGHLIGHT_CARD_CSS,
        "VIEWER_TOOLS_CSS": _VIEWER_EDITOR_TOOLS_CSS,
        "CHANNEL_NAME": _html_escape(vod_info.channel_name),
        "VOD_ID": _html_escape(vod_info.video_no),
        "DISPLAY_TITLE": _html_escape(title_display),
        "PUBLISHED_AT": _html_escape(pub_display),
        "DURATION": sec_to_hms(vod_info.duration),
        "CATEGORY_META": f'<span>{_html_escape(vod_info.category)}</span>' if vod_info.category else "",
        "COLLABORATION_CHIPS": _render_collaboration_chips_html(sec.get("collaboration_candidates", [])) + render_replay_links(collaboration_replays),
        "TOP_VOD_LINK": top_vod_link_html,
        "EDITOR_JUMP": editor_jump_html,
        "HERO_HTML": hero_html,
        "BROADCAST_SUMMARY_HTML": broadcast_summary_html,
        "CHAT_COUNT": (
            f"{report_projection['total_chats']:,}"
            if report_projection["total_chats"] is not None
            else "자료 없음"
        ),
        "DURATION_STAT": sec_to_hms(vod_info.duration),
        "SIGNAL_SCENE_COUNT": (
            str(report_projection["signal_scene_count"])
            if report_projection["signal_scene_count"] is not None
            else "자료 없음"
        ),
        "EDITORIAL_HIGHLIGHT_STAT": f'<div class="stat"><div class="stat-label">편집 Highlight</div><div class="stat-value accent">{len((editorial_review_packet or {}).get("editorial_highlights") or [])}</div></div>' if editorial_review_packet is not None else "",
        "TIMELINE_HTML": timeline_html,
        "EDITORIAL_REVIEW_HTML": editorial_review_html,
        "HIGHLIGHTS_HTML": highlights_html,
        "INJECTION_WARNING_HTML": injection_warning_html,
        "COMMENT_TIMELINE_HTML": comment_timeline_html,
        "USER_CLIPS_HTML": user_clips_html,
        "COMMUNITY_REACTION_HTML": community_reaction_html,
        "NOTES_HTML": notes_html,
        "VIEWER_TOOLS_HTML": viewer_editor_tools_html,
        "FALLBACK_HTML": fallback_html,
        "CHART_LABELS": chart_labels,
        "CHART_COUNTS": chart_counts,
        "CHART_BUCKET_SECS": chart_bucket_secs,
        "CHAT_CHART_STATE": (
            ""
            if report_projection["chat_status"] == "available"
            else '<p class="chart-state">채팅 없음</p>'
            if report_projection["chat_status"] == "known_empty"
            else '<p class="chart-state">자료 없음 · 저장된 리포트나 채팅 원본을 확인하세요.</p>'
        ),
        "PEAK_SECS": peak_secs,
        "AUDIO_PEAK_SECS": audio_peak_secs,
        "VOD_VIDEO_NO": json.dumps(vod_info.video_no),
        "WINDOW_SEC": str(WINDOW_SEC),
        "VIEWER_JS": _VIEWER_EDITOR_TOOLS_JS,
    }))
