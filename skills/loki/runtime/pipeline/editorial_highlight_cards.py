"""Canonical user-facing renderer for accepted editorial Highlight cards.

The renderer owns presentation only.  Point, Story, and Highlight authority is
validated by the caller before it reaches this module.  The same HTML and CSS
are reused by fresh report generation and Report Admin's saved-page repair so
the iframe and final report cannot silently drift into two visual systems.
"""

from __future__ import annotations

import base64

import html

from io import BytesIO

import math

import re

from collections.abc import Mapping

from decimal import Decimal

from typing import Any

from PIL import Image

from .utils import sec_to_hms

EDITORIAL_HIGHLIGHT_CARD_DESIGN = "chzz.editorial-highlight-card.v2"

EDITORIAL_HIGHLIGHT_CARD_CSS_START = "/* editorial-highlight-cards:start */"

EDITORIAL_HIGHLIGHT_CARD_CSS_END = "/* editorial-highlight-cards:end */"

EDITORIAL_HIGHLIGHT_CARD_CSS = f"""
{EDITORIAL_HIGHLIGHT_CARD_CSS_START}
.editorial-review-packet .card-body {{ padding: clamp(16px, 3vw, 28px); }}
.editorial-highlights {{ display: grid; gap: 18px; }}
.editorial-scene-image {{ margin: 16px 24px; min-width: 0; }}
.editorial-scene-image img {{ display: block; width: 100%; height: auto; border-radius: 8px; }}
.editorial-scene-image figcaption {{ margin-top: 8px; color: var(--muted); font-size: 12px; line-height: 1.7; }}
.editorial-scene-clock {{ display: block; }}
.collaboration-periods {{ min-width: 0; }}
.collaboration-periods > h3 {{ margin: 0 0 12px; color: var(--text-strong); font-size: 16px; }}
.collaboration-periods > ul {{ display: grid; gap: 14px; margin: 0; padding: 0; list-style: none; }}
.collaboration-roster-group {{ min-width: 0; }}
.collaboration-roster-heading {{ display: grid; grid-template-columns: auto minmax(0,1fr); gap: 5px 14px; align-items: start; min-width: 0; }}
.collaboration-roster-clock {{ color: var(--muted); font: 700 12px/1.7 'JetBrains Mono','Cascadia Code',Consolas,monospace; overflow-wrap: anywhere; }}
.collaboration-roster-members {{ min-width: 0; color: var(--text); font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }}
.collaboration-roster-status {{ color: var(--muted); font-size: 12px; }}
.collaboration-roster-group > details {{ min-width: 0; margin-top: 6px; }}
.collaboration-roster-group > details > summary {{ min-height: 36px; padding: 6px 0; color: var(--muted); font-size: 12px; line-height: 1.6; cursor: pointer; overflow-wrap: anywhere; }}
.collaboration-roster-group > details > ul {{ display: grid; gap: 8px; margin: 8px 0 0; padding-left: 16px; color: var(--muted); font-size: 12px; line-height: 1.7; }}
.collaboration-source-period {{ min-width: 0; overflow-wrap: anywhere; }}
.collaboration-source-period a {{ color: var(--highlight-cyan, #7dcfff); text-underline-offset: 3px; }}
.collaboration-periods > p {{ margin-top: 12px; color: var(--muted); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }}
.editorial-highlight-list {{ display: grid; gap: 18px; list-style: none; margin: 0; padding: 0; }}
.highlight-card {{
  --highlight-cyan: #7dcfff; --highlight-violet: #bb9af7; --highlight-magenta: #f7768e;
  position: relative; min-width: 0; overflow: hidden;
  border: 1px solid transparent; border-radius: 16px;
  background:
    linear-gradient(145deg, rgba(38,43,63,.98), rgba(25,29,45,.98)) padding-box,
    linear-gradient(145deg,
      rgba(125,207,255,.9) 0%, rgba(187,154,247,.72) 14%,
      rgba(247,118,142,.56) 28%, rgba(125,207,255,.3) 48%,
      rgba(125,207,255,.2) 100%) border-box;
  box-shadow: 0 16px 38px rgba(7,10,20,.24), inset 0 1px 0 rgba(255,255,255,.035);
  transition: box-shadow .18s ease, transform .18s ease;
}}
.highlight-card:hover, .highlight-card:focus-within {{
  box-shadow: 0 18px 42px rgba(7,10,20,.32), 0 0 24px rgba(125,207,255,.11), inset 0 1px 0 rgba(255,255,255,.045);
}}
.highlight-card-header {{ padding: 22px 24px 14px 27px; }}
.highlight-card h3 {{
  margin: 0; color: var(--text-strong); font-size: clamp(18px, 2.4vw, 22px);
  line-height: 1.4; letter-spacing: -.02em; overflow-wrap: anywhere;
}}
.highlight-card-reason {{
  margin: 12px 0 0; color: var(--text); font-size: 14px; line-height: 1.7; overflow-wrap: anywhere;
}}
.highlight-card-reason-label {{
  display: inline-flex; margin-right: 7px; color: #d9c9ff; font-size: 12px; font-weight: 800;
}}
.highlight-card-actions {{
  display: grid; align-items: center; gap: 10px;
  margin: 14px 24px 22px 27px;
}}
.highlight-card-time-ranges {{
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; min-width: 0;
  color: var(--muted); font: 700 12px/1.55 'JetBrains Mono','Cascadia Code',Consolas,monospace;
}}
.highlight-card-time-range {{
  display: inline-flex; min-height: 32px; align-items: center; border-radius: 7px;
  color: var(--highlight-cyan); text-decoration: none; white-space: nowrap;
}}
.highlight-card-time-range + .highlight-card-time-range::before {{
  content: '·'; margin-right: 8px; color: var(--muted); text-decoration: none;
}}
.highlight-card-time-range:hover {{ color: var(--text-strong); text-decoration: underline; text-underline-offset: 3px; }}
.highlight-link {{
  display: inline-flex; align-items: center; justify-content: center; min-height: 44px;
  padding: 9px 16px; border: 1px solid rgba(125,207,255,.62); border-radius: 10px;
  background: rgba(125,207,255,.13); color: var(--text-strong); font-weight: 800;
  text-decoration: none; box-shadow: 0 0 16px rgba(125,207,255,.06);
  transition: background .16s ease, border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}}
.highlight-link:hover {{
  background: rgba(125,207,255,.22); border-color: var(--highlight-cyan);
  box-shadow: 0 0 18px rgba(125,207,255,.13); transform: translateY(-1px);
}}
.highlight-link.is-disabled {{
  cursor: not-allowed; border-color: var(--border); background: rgba(18,21,32,.45);
  color: var(--muted); box-shadow: none;
}}
.highlight-time-link, .source-time-link {{
  color: var(--highlight-cyan); text-decoration: underline;
  text-decoration-color: rgba(125,207,255,.42); text-underline-offset: 3px;
  text-decoration-thickness: 1px;
}}
.highlight-time-link:hover, .source-time-link:hover {{
  color: var(--text-strong); text-decoration-color: currentColor;
}}
.highlight-card-details {{ border-top: 1px solid var(--border-soft); }}
.highlight-card-details > summary {{
  min-height: 48px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 0 24px 0 27px; color: var(--muted); font-size: 13px; font-weight: 750;
  cursor: pointer; user-select: none; list-style-position: inside;
}}
.highlight-card-details > summary:hover {{ color: var(--text-strong); background: rgba(125,207,255,.035); }}
.highlight-card-details-body {{
  display: grid; gap: 18px; padding: 3px 24px 22px 27px;
  border-top: 1px solid rgba(255,255,255,.035); background: rgba(12,15,25,.2);
}}
.highlight-story-flow {{ padding-top: 16px; }}
.highlight-detail-heading {{ margin: 0 0 7px; color: #d9c9ff; font-size: 12px; font-weight: 800; }}
.highlight-story-flow p {{ margin: 0; color: var(--text); font-size: 13px; line-height: 1.68; }}
.highlight-point-jumps {{ display: grid; gap: 9px; margin: 0; }}
.highlight-point-jumps-title {{ margin: 0; color: #d9c9ff; font-size: 12px; font-weight: 800; }}
.highlight-point-jump {{
  display: grid; gap: 5px; min-width: 0; padding: 11px 12px;
  border: 1px solid var(--border); border-radius: 10px; background: rgba(18,21,32,.42);
}}
.point-link {{
  display: grid; grid-template-columns: auto minmax(0,1fr); gap: 9px; align-items: center;
  min-height: 44px; color: var(--text-strong); text-decoration: none;
}}
.point-link-time {{
  padding: 4px 8px; border-radius: 6px; background: rgba(125,207,255,.12);
  color: var(--highlight-cyan); font: 700 12px/1.4 'JetBrains Mono','Cascadia Code',Consolas,monospace;
}}
.point-link-title {{ min-width: 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }}
.point-link:hover .point-link-title {{ color: var(--highlight-cyan); }}
.point-story-reason {{ margin: -1px 0 0; color: var(--muted); font-size: 12px; line-height: 1.6; }}
.source-spans-wrap {{ margin: 0; }}
.source-spans-summary {{ margin: 0 0 8px; color: var(--muted); font-size: 12px; }}
.source-spans {{ margin: 0; padding-left: 20px; display: grid; gap: 8px; }}
.source-spans {{ display: grid; gap: 8px; }}
.source-span-scene {{
  padding: 9px 11px; border-left: 3px solid rgba(125,207,255,.7);
  border-radius: 6px; background: rgba(18,21,32,.32); color: var(--text);
}}
.source-span-sequence-label {{
  display: block; margin-bottom: 3px; color: #d9c9ff; font-size: 11px; font-weight: 800;
}}
.source-span-clock {{ display: block; color: var(--highlight-cyan); font: 700 12px/1.5 'JetBrains Mono','Cascadia Code',Consolas,monospace; }}
.source-span-reason {{ display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }}
.source-span-skip {{
  display: block; margin: 0; padding: 7px 9px; border-radius: 6px; list-style: none;
  background: rgba(187,154,247,.08); color: #d9c9ff; font-size: 12px; line-height: 1.55;
}}
.source-span-skip-label {{ font-weight: 800; }}
.source-span-evidence-refs {{ display: block; margin-top: 2px; color: var(--muted); overflow-wrap: anywhere; }}
.source-span-gap-occupants {{ display: block; margin-top: 5px; color: var(--text); }}
.source-span-gap-occupant {{
  display: inline-flex; align-items: center; min-height: 30px; margin: 3px 6px 0 0; padding: 3px 8px;
  border: 1px solid rgba(247,118,142,.34); border-radius: 999px; color: #ff9eae;
  background: rgba(247,118,142,.08); font-weight: 750; text-decoration: none;
}}
.source-span-gap-occupant:hover {{ border-color: #ff9eae; color: var(--text-strong); }}
.source-spans-empty {{ margin: 0; color: var(--muted); font-size: 13px; }}
.standalone-points {{ display: grid; gap: 8px; margin-top: 8px; }}
.standalone-points h3 {{ margin: 0; color: var(--text-strong); font-size: 16px; }}
.standalone-point-list {{
  display: grid; gap: 0; margin: 0; padding: 0; list-style: none;
  border: 1px solid var(--border); border-radius: 12px; background: rgba(30,34,50,.46); overflow: hidden;
}}
.standalone-point-card {{
  display: grid; grid-template-columns: auto minmax(0,1fr); align-items: start; gap: 8px 10px;
  min-width: 0; padding: 7px 10px; border-bottom: 1px solid var(--border-soft);
}}
.standalone-point-card:last-child {{ border-bottom: 0; }}
.standalone-point-time-link {{
  display: inline-flex; min-height: 36px; align-items: center; color: var(--text-strong); text-decoration: none;
}}
.standalone-point-time-link:hover .standalone-point-time {{ color: var(--text-strong); }}
.standalone-point-time {{
  padding: 3px 7px; border-radius: 6px; background: rgba(125,207,255,.11); color: var(--highlight-cyan);
  font: 700 11px/1.45 'JetBrains Mono','Cascadia Code',Consolas,monospace;
}}
.point-evidence-panel {{ min-width: 0; }}
.point-evidence-panel > summary {{
  display: grid; grid-template-columns: minmax(0,1fr) auto; min-height: 36px; align-items: center; gap: 8px;
  padding: 3px 6px; border-radius: 7px; color: var(--text-strong); font-size: 13px; font-weight: 750;
  cursor: pointer; list-style: none;
}}
.point-evidence-panel > summary::-webkit-details-marker {{ display: none; }}
.point-evidence-panel > summary:hover {{ color: var(--text-strong); background: rgba(125,207,255,.06); }}
.standalone-point-disclosure-title {{ min-width: 0; overflow-wrap: anywhere; }}
.standalone-point-chevron {{ color: var(--muted); transition: transform .16s ease; }}
.point-evidence-panel[open] .standalone-point-chevron {{ transform: rotate(90deg); }}
.point-evidence-body {{ grid-column: 1 / -1; padding: 2px 4px 7px; color: var(--muted); font-size: 12px; line-height: 1.6; }}
.point-evidence-body p {{ margin: 4px 0 0; }}
.highlight-card a:focus-visible, .highlight-card summary:focus-visible,
.collaboration-periods a:focus-visible, .collaboration-periods summary:focus-visible,
.standalone-point-card a:focus-visible, .standalone-point-card summary:focus-visible {{
  outline: 3px solid #fff; outline-offset: 3px; border-radius: 6px;
}}
@media (max-width: 720px) {{
  .collaboration-roster-heading {{ grid-template-columns: minmax(0,1fr); }}
  .editorial-review-packet .card-body {{ padding: 14px; }}
  .editorial-highlight-list {{ gap: 14px; }}
  .highlight-card-header {{ padding: 19px 17px 13px 20px; }}
  .highlight-card-actions {{ margin: 12px 17px 20px 20px; }}
  .highlight-card-actions {{ align-items: stretch; }}
  .highlight-link {{ width: 100%; }}
  .highlight-card-range, .highlight-card-footage {{ width: 100%; text-align: left; }}
  .highlight-card-details > summary {{ align-items: flex-start; padding: 14px 17px 14px 20px; }}
  .highlight-card-details-body {{ padding: 2px 17px 19px 20px; }}
  .point-link {{ grid-template-columns: 1fr; gap: 5px; }}
  .point-link-time {{ width: fit-content; }}
  .standalone-point-card {{ grid-template-columns: auto minmax(0,1fr); padding: 8px 10px; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .highlight-card, .highlight-link {{ transition: none; }}
  .highlight-card:hover, .highlight-link:hover {{ transform: none; }}
}}
{EDITORIAL_HIGHLIGHT_CARD_CSS_END}
""".strip()

def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)

def editorial_scene_image_data_url(data_url: str) -> str:
    """Encode display pixels without changing the selected source evidence."""


    if data_url.startswith("data:image/webp;base64,"):
        return data_url
    encoded = BytesIO()
    with Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as pixels:
        pixels.save(encoded, format="WEBP", quality=85)
    return "data:image/webp;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")

def render_editorial_scene_image(image: object, *, video_no: str) -> str:
    """Display the author's persisted selection; never choose another image."""
    if not isinstance(image, Mapping) or not image.get("data_url"):
        return ""
    sec = float(image["source_sec"])
    interval = image.get("source_interval_sec")
    clock = (f'{sec_to_hms(interval[0])}–{sec_to_hms(interval[1])} 미리보기'
             if interval else f'{sec_to_hms(sec)} 원본 장면')
    caption = str(image.get("caption") or clock)
    return (
        '<figure class="cva-highlight-visual editorial-scene-image" '
        f'data-frame-ref="{_esc(image["frame_ref"])}" data-image-sha256="{_esc(image["sha256"])}">'
        f'<img src="{_esc(editorial_scene_image_data_url(image["data_url"]))}" alt="{_esc(image["alt"])}" '
        'loading="lazy" decoding="async">'
        '<figcaption><a '
        f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(sec)}" '
        f'data-start="{int(sec)}">{_esc(caption)}</a>'
        f'<span class="editorial-scene-clock">{_esc(clock)}</span></figcaption></figure>'
    )

def _hms_to_seconds(value: object) -> int | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = (int(part) for part in parts)
            if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                return None
            return hours * 3600 + minutes * 60 + seconds
        if len(parts) == 2:
            minutes, seconds = (int(part) for part in parts)
            if minutes < 0 or not 0 <= seconds < 60:
                return None
            return minutes * 60 + seconds
    except ValueError:
        return None
    return None

def _validated_spans(raw_spans: object) -> list[dict[str, Any]] | None:
    if not isinstance(raw_spans, list) or not raw_spans:
        return None
    validated: list[dict[str, Any]] = []
    previous_end: float | None = None
    for raw in raw_spans:
        if not isinstance(raw, Mapping):
            return None
        try:
            start = float(raw.get("start_sec"))
            end = float(raw.get("end_sec"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            return None
        if previous_end is not None and start < previous_end:
            return None
        row = dict(raw)
        row["start_sec"] = start
        row["end_sec"] = end
        validated.append(row)
        previous_end = end
    return validated

def _duration_label(start: int, end: int) -> str:
    duration = max(0, end - start)
    if duration < 60:
        return f"{duration}초"
    minutes, seconds = divmod(duration, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분"

def _seconds_label(duration: float) -> str:
    return _duration_label(0, int(round(max(0, duration))))

def _editorial_progress_notice(view: Mapping[str, Any]) -> str:
    """Describe recorded progress without treating missing work as rejection."""
    progress = view.get("review_progress")
    if (isinstance(progress, Mapping) and progress.get("pipeline_complete") is True
            and progress.get("completion_disposition") == "context_unverified_preserved"):
        questions = view.get("unresolved_questions") or []
        descriptions = [row.get("question") if isinstance(row, Mapping) else row for row in questions]
        if not descriptions:
            descriptions = [row.get("reason") for row in view.get("unresolved_group_outcomes") or []]
        items = "".join(f"<li>미확인: {_esc(text)}</li>" for text in descriptions if isinstance(text, str) and text.strip())
        return ('<div class="editorial-partial-status" role="status">'
                '<strong>요약 처리가 완료됐습니다.</strong> 확인하지 못한 내용은 미확인으로 남겼습니다.'
                f'<ul>{items}</ul></div>')
    pending = isinstance(progress, Mapping) and progress.get("pipeline_complete") is False
    partial = str(view.get("completion_state") or "").startswith("partial_")
    if not pending and not partial:
        return ""
    parts = []
    if not view.get("highlights"):
        parts.append("<strong>편집 구간이 아직 생성되지 않았습니다.</strong> ")
    else:
        parts.append("<strong>일부 결과입니다.</strong> ")
    if pending:
        completed = progress.get("completed_stages") or []
        parts.append("Highlight 작성 대기입니다. " if "editorial_review" not in completed else "장면 분석에 남은 작업이 있습니다. ")
    if partial:
        parts.append(
            f'장면 근거 보완 {_esc(view.get("unresolved_group_count", 0))}건, '
            f'구간 작성 보완 {_esc(view.get("unresolved_proposal_count", 0))}건이 남아 있습니다. '
        )
    exploration = view.get("source_exploration")
    if isinstance(exploration, Mapping):
        incomplete = exploration.get("incomplete_hypothesis_refs")
        unreached = exploration.get("not_reached_hypothesis_refs")
        if isinstance(incomplete, list):
            parts.append(f"추가 탐색 미완 후보 {len(incomplete)}개. ")
        if isinstance(unreached, list):
            parts.append(f"탐색 미도달 후보 {len(unreached)}개. ")
    if view.get("unresolved_exploration_count"):
        parts.append(f'미해결 탐색 질문 {_esc(view["unresolved_exploration_count"])}건. ')
    parts.append("미완·미도달 후보는 선정 전이며, 편집 가치가 없다는 판단이 아닙니다.")
    return '<p class="editorial-partial-status" role="status">' + "".join(parts) + "</p>"

def render_pending_editorial_review(review_progress: object, *, video_no: str) -> str:
    """Keep the normal section visible when no authored packet exists yet."""
    if not isinstance(review_progress, Mapping) or review_progress.get("pipeline_complete") is not False:
        return ""
    return render_editorial_highlight_cards({"review_progress": review_progress}, video_no=video_no)

def _collaboration_clock(seconds: int | float) -> str:
    fraction = Decimal(str(seconds)) - int(seconds)
    return sec_to_hms(seconds) + (format(fraction, 'f')[1:] if fraction else '')

def _render_collaboration_periods(periods: list[Mapping[str, Any]]) -> str:
    """Group adjacent rosters for display while retaining every original row."""
    groups: list[list[Mapping[str, Any]]] = []
    for row in periods:
        previous = groups[-1][-1] if groups else None
        if (
            previous is not None
            and row["members"]
            and previous["members"]
            and previous["end_sec"] == row["start_sec"]
            and frozenset(previous["members"]) == frozenset(row["members"])
            and previous["source_kind"] == row["source_kind"]
            and previous["support_status"] == row["support_status"]
        ):
            groups[-1].append(row)
        else:
            groups.append([row])

    rendered_groups = []
    for group in groups:
        first, last = group[0], group[-1]
        original_rows = []
        for row in group:
            links = ' · '.join(
                '<a href="https://chzzk.naver.com/video/' + _esc(link["vod_no"]) + '">'
                + _esc(link["member_name"]) + ' VOD ' + _esc(link["vod_no"]) + '</a>'
                for link in row.get("member_vods", [])
            )
            original_rows.append(
                '<li class="collaboration-source-period">'
                + _esc(_collaboration_clock(row["start_sec"])) + '–' + _esc(_collaboration_clock(row["end_sec"]))
                + ': ' + (links or '등록된 다시보기 없음') + '</li>'
            )
        rendered_groups.append(
            '<li class="collaboration-roster-group">'
            + '<div class="collaboration-roster-heading"><span class="collaboration-roster-clock">'
            + _esc(_collaboration_clock(first["start_sec"])) + '–' + _esc(_collaboration_clock(last["end_sec"]))
            + '</span><span class="collaboration-roster-members">'
            + _esc(', '.join(first["members"]) or '합방 멤버 없음')
            + ('<span class="collaboration-roster-status"> · 미확인</span>' if first["support_status"] == 'provisional' else '')
            + '</span></div>'
            + '<details><summary>등록된 다시보기와 원래 시간 범위</summary><ul>'
            + ''.join(original_rows) + '</ul></details></li>'
        )
    return (
        '<section class="collaboration-periods"><h3>시점별 합방 멤버</h3><ul>'
        + ''.join(rendered_groups)
        + '</ul><p>끝 시각은 포함하지 않습니다. 구간 사이의 공백은 미확인입니다.</p></section>'
    )

def render_editorial_highlight_cards(view: Mapping[str, Any], *, video_no: str) -> str:
    """Render one validated editorial view as editor-ready cards."""

    points = list(view.get("points") or [])
    points_by_id = dict(view.get("points_by_id") or {})
    stories_by_ref = dict(view.get("stories_by_ref") or {})
    highlights = list(view.get("highlights") or [])
    highlight_projections: list[dict[str, Any]] = []
    for highlight_index, highlight in enumerate(highlights, start=1):
        highlight_id = str(highlight.get("highlight_id") or f"highlight-{highlight_index}")
        fragment_id = "editorial-highlight-" + re.sub(
            r"[^a-zA-Z0-9_-]+", "-", highlight_id
        ).strip("-")
        highlight_projections.append({
            "highlight_id": highlight_id,
            "fragment_id": fragment_id,
            "label": f"H{highlight_index}",
            "title": str(highlight.get("title") or "편집 Highlight"),
            "spans": _validated_spans(highlight.get("source_spans")),
        })
    referenced_points: set[str] = set()
    cards: list[str] = []

    for card_index, highlight in enumerate(highlights, start=1):
        projection = highlight_projections[card_index - 1]
        spans = projection["spans"]
        has_valid_spans = spans is not None
        spans = spans or []
        start = int(spans[0]["start_sec"]) if spans else None
        end = int(spans[-1]["end_sec"]) if spans else None
        used_duration = sum(span["end_sec"] - span["start_sec"] for span in spans)
        excluded_duration = sum(
            max(0, spans[index]["start_sec"] - spans[index - 1]["end_sec"])
            for index in range(1, len(spans))
        )
        point_refs = [str(value) for value in highlight.get("point_refs") or []]
        referenced_points.update(point_refs)
        point_stories = [
            stories_by_ref[str(points_by_id[point_ref].get("story_ref") or "")]
            for point_ref in point_refs
        ]
        why_notable = " / ".join(dict.fromkeys(
            str(story.get("why_notable") or "").strip()
            for story in point_stories
            if str(story.get("why_notable") or "").strip()
        ))
        story_summary = str(highlight.get("story_summary") or "").strip()
        selection_reason = str(highlight.get("selection_reason") or "").strip()
        if "selection_reason" in highlight:
            card_reason = selection_reason
        elif why_notable and story_summary == str(highlight.get("edit_rationale") or "").strip():
            card_reason = why_notable
        elif len(point_refs) == 1:
            card_reason = why_notable or story_summary or "승인된 편집 구간입니다."
        else:
            card_reason = story_summary or why_notable or "관련 Point를 잇는 승인된 편집 구간입니다."

        point_links: list[str] = []
        for point_ref in point_refs:
            point = points_by_id[point_ref]
            point_start = _hms_to_seconds(point.get("timestamp"))
            story = stories_by_ref[str(point.get("story_ref") or "")]
            point_title = str(point.get("title") or "Point")
            point_reason = str(story.get("why_notable") or "").strip()
            point_title_html = (
                f'<span class="point-link-title">{_esc(point_title)}</span>'
                if len(point_refs) > 1 else
                '<span class="point-link-title">이 시작 지점으로 이동</span>'
            )
            point_reason_html = (
                f'<p class="point-story-reason">{_esc(point_reason)}</p>'
                if len(point_refs) > 1 and point_reason else ""
            )
            point_seek = (
                '<a class="point-link" '
                f'data-start="{point_start}" '
                f'aria-label="{_esc(point.get("timestamp") or "")} {_esc(point_title)}로 이동" '
                f'href="https://chzzk.naver.com/video/'
                f'{_esc(video_no)}?currentTime={point_start}">'
                f'<span class="point-link-time">{_esc(point.get("timestamp") or "")}</span>'
                f'{point_title_html}</a>'
                if point_start is not None else
                '<span class="point-link is-disabled" aria-disabled="true">'
                '<span class="point-link-time">구간 정보 없음</span>'
                f'{point_title_html}</span>'
            )
            point_links.append(
                '<div class="highlight-point-jump">'
                f'{point_seek}{point_reason_html}</div>'
            )

        point_heading = "시작 지점" if len(point_refs) == 1 else f"주요 시작 지점 {len(point_refs)}개"
        point_nav = (
            '<nav class="highlight-point-jumps" aria-label="Highlight 안의 Point 이동">'
            f'<p class="highlight-point-jumps-title">{_esc(point_heading)}</p>'
            + "".join(point_links)
            + "</nav>"
        )
        source_span_rows: list[str] = []
        for span_index, span in enumerate(spans):
            exclusion = span.get("exclusion_before") if isinstance(span.get("exclusion_before"), Mapping) else {}
            gap = (
                max(0, span["start_sec"] - spans[span_index - 1]["end_sec"])
                if span_index else 0
            )
            exclusion_reason = str(exclusion.get("reason") or "").strip()
            exclusion_refs = [str(value) for value in exclusion.get("evidence_refs") or [] if str(value).strip()]
            gap_occupants = []
            if span_index and gap > 0:
                gap_start = float(spans[span_index - 1]["end_sec"])
                gap_end = float(span["start_sec"])
                for other_index, other in enumerate(highlight_projections):
                    if other_index == card_index - 1 or not other["spans"]:
                        continue
                    if any(
                        max(gap_start, float(other_span["start_sec"]))
                        < min(gap_end, float(other_span["end_sec"]))
                        for other_span in other["spans"]
                    ):
                        gap_occupants.append(other)
            gap_occupants_html = (
                '<span class="source-span-gap-occupants">이 구간의 다른 Highlight: '
                + "".join(
                    '<a class="source-span-gap-occupant" '
                    f'data-gap-occupant-highlight-id="{_esc(other["highlight_id"])}" '
                    f'href="#{_esc(other["fragment_id"])}">'
                    f'{_esc(other["label"])} · {_esc(other["title"])}</a>'
                    for other in gap_occupants
                )
                + '</span>'
                if gap_occupants else ''
            )
            exclusion_html = (
                '<li class="source-span-skip">'
                f'<span class="source-span-skip-label">건너뜀 · {_esc(_seconds_label(gap))}</span>'
                + (f' · {_esc(exclusion_reason)}' if exclusion_reason else '')
                + (
                    '<span class="source-span-evidence-refs">근거: '
                    + _esc(", ".join(exclusion_refs)) + '</span>'
                    if exclusion_refs else ''
                )
                + gap_occupants_html
                + '</li>'
                if span_index and gap > 0 else ''
            )
            if exclusion_html:
                source_span_rows.append(exclusion_html)
            source_span_rows.append(
                '<li class="source-span-scene" '
                f'data-start="{_esc(span.get("start_sec"))}" data-end="{_esc(span.get("end_sec"))}">'
                f'<span class="source-span-sequence-label">장면 {span_index + 1}</span>'
                '<span class="source-span-clock">'
            f'<a class="source-time-link" data-start="{int(float(span["start_sec"]))}" '
            f'aria-label="{_esc(sec_to_hms(float(span["start_sec"])))} 구간 시작으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["start_sec"]))}">'
            f'{_esc(sec_to_hms(float(span["start_sec"])))}</a> → '
            f'<a class="source-time-link" data-start="{int(float(span["end_sec"]))}" '
            f'aria-label="{_esc(sec_to_hms(float(span["end_sec"])))} 구간 끝으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["end_sec"]))}">'
            f'{_esc(sec_to_hms(float(span["end_sec"])))}</a></span>'
                f'<span class="source-span-reason">{_esc(span.get("reason") or "편집 구간")}</span></li>'
            )
        source_spans = "".join(source_span_rows)
        story_flow = (
            '<section class="highlight-story-flow"><h4 class="highlight-detail-heading">이야기 흐름</h4>'
            f'<p>{_esc(story_summary)}</p></section>'
            if story_summary and story_summary != card_reason else ""
        )
        source_panel = (
            '<section class="source-spans-wrap"><h4 class="highlight-detail-heading">편집 순서</h4>'
            f'<p class="source-spans-summary">장면 {len(spans)}개 · 실제 사용 {_esc(_seconds_label(used_duration))}</p>'
            f'<ol class="source-spans" aria-label="원본 편집 구간">{source_spans}</ol></section>'
            if has_valid_spans else
            '<section class="source-spans-wrap"><h4 class="highlight-detail-heading">원본 편집 구간</h4>'
            '<p class="source-spans-empty">구간 정보 없음</p></section>'
        )
        details_panel = (
            '<details class="highlight-card-details"><summary>'
            '<span>편집 정보 보기</span>'
            '</summary><div class="highlight-card-details-body">'
            f'{story_flow}{point_nav}{source_panel}</div></details>'
        )
        if not has_valid_spans:
            action_html = (
                '<div class="highlight-card-actions"><span class="highlight-link is-disabled" '
                'aria-disabled="true">구간 정보 없음</span></div>'
            )
        else:
            action_label = (
                f'{_esc(sec_to_hms(start))}부터 보기'
                if len(spans) == 1
                else f'{_esc(sec_to_hms(start))} 첫 장면부터 보기'
            )
            action_html = (
                '<div class="highlight-card-actions">'
                '<nav class="highlight-card-time-ranges" aria-label="Highlight 원본 장면">'
                + "".join(
                    '<a class="highlight-card-time-range" '
                    f'data-highlight-span-index="{span_index}" data-start="{int(float(span["start_sec"]))}" '
                    f'aria-label="장면 {span_index + 1}/{len(spans)}, '
                    f'{_esc(sec_to_hms(float(span["start_sec"])))}부터 '
                    f'{_esc(sec_to_hms(float(span["end_sec"])))}까지, 시작으로 이동" '
                    f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={int(float(span["start_sec"]))}">'
                    f'[{_esc(sec_to_hms(float(span["start_sec"])))}–{_esc(sec_to_hms(float(span["end_sec"])))}]</a>'
                    for span_index, span in enumerate(spans)
                )
                + '</nav><a class="highlight-link" '
                f'data-start="{start}" '
                f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={start}">'
                f'{action_label}</a></div>'
            )
        cards.append(
            f'<article id="{_esc(projection["fragment_id"])}" class="highlight-card" '
            f'data-editorial-card-design="{EDITORIAL_HIGHLIGHT_CARD_DESIGN}" '
            f'data-point-count="{len(point_refs)}" data-span-count="{len(spans)}">'
            '<header class="highlight-card-header">'
            f'<h3>{_esc(highlight.get("title") or "편집 Highlight")}</h3>'
            '<p class="highlight-card-reason"><span class="highlight-card-reason-label">볼 이유</span>'
            f'{_esc(card_reason)}</p></header>'
            + render_editorial_scene_image(highlight.get("image"), video_no=video_no)
            + action_html + details_panel + "</article>"
        )

    standalone_links: list[str] = []
    for point in points:
        if str(point.get("id") or "") in referenced_points:
            continue
        point_start = _hms_to_seconds(point.get("timestamp"))
        story = stories_by_ref[str(point.get("story_ref") or "")]
        point_title = str(point.get("title") or "주요 장면")
        point_seek = (
            '<a class="standalone-point standalone-point-time-link" '
            f'data-start="{point_start}" aria-label="{_esc(point.get("timestamp") or "")} 원본으로 이동" '
            f'href="https://chzzk.naver.com/video/{_esc(video_no)}?currentTime={point_start}">'
            f'<span class="standalone-point-time">{_esc(point.get("timestamp") or "")}</span></a>'
            if point_start is not None else
            '<span class="standalone-point standalone-point-time-link is-disabled" aria-disabled="true">'
            '<span class="standalone-point-time">구간 정보 없음</span></span>'
        )
        standalone_links.append(
            '<li class="standalone-point-card">'
            f'{point_seek}'
            '<details class="point-evidence-panel"><summary '
            f'aria-label="{_esc(point_title)} Story 펼치기">'
            f'<span class="standalone-point-disclosure-title">{_esc(point_title)}</span>'
            '<span class="standalone-point-chevron" aria-hidden="true">›</span></summary>'
            '<div class="point-evidence-body">'
            f'<p>{_esc(story.get("why_notable") or "-")}</p></div></details></li>'
        )
    standalone_html = (
        f'<section class="standalone-points"><h3>나머지 주요 장면 {len(standalone_links)}개</h3>'
        '<ol class="standalone-point-list">' + "".join(standalone_links) + "</ol></section>"
        if standalone_links else ""
    )
    partial_notice = _editorial_progress_notice(view)
    external_notice = (
        '<p class="editorial-external-draft-status" role="status">'
        '<strong>편집 검토본입니다.</strong> 외부 편집 초안과 선택된 분석 결과의 출처를 보존합니다. '
        '원본 파일 대조와 영상·음성 확인 범위는 각 출처의 기록을 따릅니다.</p>'
        if view.get("external_review_draft") else ""
    )
    collaboration_html = ""
    if "collaboration_periods" in view:
        collaboration_html = _render_collaboration_periods(view["collaboration_periods"])
    section_label = "편집 검토본" if external_notice else "승인된 편집 Highlight"
    return (
        f'<section class="editorial-highlights" aria-label="{section_label}">'
        + external_notice + partial_notice + collaboration_html
        + '<div class="editorial-highlight-list">' + "".join(cards) + "</div>"
        + standalone_html + "</section>"
    )
