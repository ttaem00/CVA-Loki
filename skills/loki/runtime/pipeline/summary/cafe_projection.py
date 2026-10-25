"""Primary owner for Naver Cafe and chapter-readiness projections."""

from __future__ import annotations

import re

from typing import Optional

from ..models import VODInfo

from ..utils import sec_to_hms

from .parse import _strip_timestamp_confidence_markers

from .payload import _bucketed_eligible_clip_objects

from .render_primitives import _audio_peak_delta_text, _build_public_vod_url, _chzzk_video_url_at, _html_escape

_CAFE_SECTION_TITLE_STYLE = "font-size:24px;line-height:1.55;margin:0;font-weight:900;color:#111827"

_CAFE_PARAGRAPH_STYLE = "font-size:16px;line-height:2.0;margin:0 0 14px;color:#202124"

_CAFE_RULE_HTML = (
    '<hr style="border:0;border-top:1px solid #e5e7eb;margin:34px 0 22px">'
)

_HIGHLIGHT_LEARNING_NOTICE = (
    "이 추천은 자동으로 고른 후보예요. 유튜브 편집 영상 데이터를 참고해 "
    "더 좋은 장면을 찾도록 개선 중입니다."
)

def _audio_peak_cafe_badge_html(tc_text: str, audio_peak_secs: list[float], tolerance_sec: int = 30) -> str:
    delta_text = _audio_peak_delta_text(tc_text, audio_peak_secs, tolerance_sec=tolerance_sec)
    if not delta_text:
        return ""
    return f' <strong>[🔊 오디오 피크 {delta_text}]</strong>'

def _render_inline_md(s: str) -> str:
    """간단한 인라인 마크다운(**strong**, `code`, [text](url)) → HTML"""
    import re
    s = _html_escape(s)

    def _link_repl(m: "re.Match") -> str:
        text, url = m.group(1), m.group(2)
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", text):
            return f'<a class="inline-tc" href="{url}" target="_blank" rel="noopener">{text}</a>'
        return f'<a href="{url}" target="_blank" rel="noopener">{text}</a>'

    s = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', _link_repl, s)
    s = re.sub(
        r"\[실제 시점:\s*(\d{2}:\d{2}:\d{2})\]",
        r'<span class="inline-tc timestamp-inline">실제 시점 \1</span>',
        s,
    )
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s

def _top_user_clips_for_cafe(
    user_clips: "Optional[UserClipsBundle]", vod_info: VODInfo, limit: int = 3,
) -> list:
    """본 VOD 시점 매칭 시청자 클립 중 ♥ 좋아요 → ▶ 조회수 순 상위 `limit` 개."""
    if user_clips is None:
        return []
    matched = _bucketed_eligible_clip_objects(user_clips, vod_info)
    matched.sort(key=lambda c: (
        -(c.like_count or 0), -(c.play_count or 0), c.offset_sec or 0, c.clip_uid or "",
    ))
    return matched[:limit]

def _cafe_heading(text: str) -> str:
    return (
        '<div style="margin:0 0 18px">'
        f'<h2 style="{_CAFE_SECTION_TITLE_STYLE}">{_html_escape(text)}</h2>'
        '</div>'
    )

def _append_cafe_section(parts: list[str], title: str) -> None:
    if parts:
        parts.append(_CAFE_RULE_HTML)
    parts.append(_cafe_heading(title))

def _cafe_tag_chips(tags: list[str]) -> str:
    chips: list[str] = []
    for tag in tags[:6]:
        chips.append(
            '<span style="color:#03c75a;font-size:16px;line-height:1.8">'
            f'#{_html_escape(tag)}</span>'
        )
    return " ".join(chips)

def _cafe_report_title(vod_info: VODInfo, sec: dict) -> str:
    title = (sec.get("title") or vod_info.title or "방송 분석 리포트").strip()
    title = re.sub(r"^\s*(?:방송\s*분석\s*리포트|분석\s*리포트)\s*[:：]\s*", "", title)
    return title or "방송 분석 리포트"

def _cafe_report_date(vod_info: VODInfo) -> str:
    raw = (vod_info.publish_date or "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return ""
    _year, month, day = match.groups()
    return f"{int(month)}/{int(day)}"

def _cafe_split_quote_lines(text: str, max_len: int = 42) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []
    if len(clean) <= max_len:
        return [clean]

    lines: list[str] = []
    remaining = clean
    while len(remaining) > max_len and len(lines) < 2:
        window = remaining[: max_len + 1]
        split_at = max(
            window.rfind(" "),
            window.rfind(","),
            window.rfind("·"),
        )
        if split_at < max_len // 2:
            split_at = max_len
        lines.append(remaining[:split_at].strip(" ,·"))
        remaining = remaining[split_at:].strip(" ,·")
    if remaining:
        lines.append(remaining)
    return [line for line in lines if line]

def _cafe_native_card_preview(card: dict, *, fallback_description: str = "") -> str:
    url = str(card.get("url") or "").strip()
    title = str(card.get("label") or card.get("title") or "링크 카드").strip()
    description = str(card.get("description") or fallback_description or "").strip()
    if not url:
        return ""
    return (
        '<p style="font-size:17px;line-height:1.7;margin:0 0 4px;color:#111827">'
        '<strong>{title}</strong></p>'
        '<p style="font-size:14px;line-height:1.7;margin:0 0 4px;color:#4b5563">{description}</p>'
        '<p style="font-size:15px;line-height:1.8;margin:0 0 18px;word-break:break-all">'
        '{url}</p>'
    ).format(
        url=_html_escape(url),
        title=_html_escape(title),
        description=_html_escape(description),
    )

def _append_cafe_publish_source_section(parts: list[str], model: dict) -> None:
    source_cards = list(model.get("source_cards") or [])
    vod_cards = [card for card in source_cards if str(card.get("kind") or "") == "vod" and card.get("url")]
    native_source_cards = [card for card in source_cards if card.get("native_card") and card.get("url")]
    if not vod_cards and not native_source_cards:
        return

    rows = [
        '<p style="font-size:24px;line-height:1.55;margin:0 0 16px;'
        'color:#111827;font-weight:900">🔗 바로 열기</p>',
    ]
    for idx, card in enumerate(vod_cards, start=1):
        safe_url = _html_escape(str(card.get("url") or ""))
        label = "원본 VOD" if len(vod_cards) == 1 else f"원본 VOD {idx}"
        rows.append(
            '<p style="font-size:15px;line-height:1.9;margin:0 0 14px;'
            'word-break:break-all">'
            f'<strong style="color:#4b5563">{label}</strong> '
            f'<a href="{safe_url}" style="color:#2563eb;text-decoration:underline">{safe_url}</a></p>'
        )
    for card in native_source_cards:
        rows.append(_cafe_native_card_preview(
            card,
            fallback_description="타임라인 전체와 자세한 장면 기록을 웹페이지로 봅니다.",
        ))
    parts.append("\n".join(row for row in rows if row))

def _append_cafe_shorts_link_card(parts: list[str], shorts_url: str) -> None:
    safe_url = _html_escape(shorts_url.strip())
    if not safe_url:
        return
    parts.append(
        '<p style="font-size:16px;line-height:1.55;margin:0 0 6px;'
        'color:#111827;font-weight:900">쇼츠 먼저 보기</p>'
        '<p style="font-size:15px;line-height:1.8;margin:0 0 24px;word-break:break-all;'
        'color:#2563eb">'
        f'{safe_url}</p>'
    )

def _cafe_timeline_row(idx: int, tc_segment: str, title: str) -> str:
    return (
        '<p style="font-size:16px;line-height:1.9;margin:0 0 8px;color:#202124">'
        f'<strong>{idx}. {tc_segment}</strong> {title}</p>'
    )

def _group_cafe_chapter_items(items: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    index_by_key: dict[tuple[str, str, str, str, str], int] = {}
    for item in items:
        tc = (item.get("tc") or "").strip()
        tc_raw = (item.get("tc_raw") or tc).strip()
        title = (item.get("title") or "").strip()
        if not tc or not title:
            continue
        tc_end = (item.get("tc_end") or "").strip()
        source_values = {
            source_key: str(item.get(source_key) or "").strip()
            for source_key in ("video_no", "source_video_no", "vod_label")
        }
        key = (
            tc_raw,
            tc_end,
            source_values["video_no"],
            source_values["source_video_no"],
            source_values["vod_label"],
        )
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            index_by_key[key] = len(grouped)
            grouped_item = {"tc": tc, "tc_raw": tc_raw, "tc_end": tc_end, "titles": [title]}
            for source_key, source_value in source_values.items():
                if source_value:
                    grouped_item[source_key] = source_value
            grouped.append(grouped_item)
            continue
        if title not in grouped[existing_idx]["titles"]:
            grouped[existing_idx]["titles"].append(title)
    return grouped

def _should_render_cafe_highlights() -> bool:

    return False

def _cafe_video_url(video_no: str, tc: str = "") -> str:
    base = f"https://chzzk.naver.com/video/{video_no}"
    clean_tc = (tc or "").split("~", 1)[0].strip().strip("[]")
    if not clean_tc:
        return base
    chzzk_url = _chzzk_video_url_at(video_no, clean_tc)
    return chzzk_url or base

def _cafe_engagement_text(clip) -> str:
    parts: list[str] = []
    if clip.engagement_status == "ok":
        if clip.like_count is not None:
            parts.append(f"♥ {clip.like_count:,}")
        if clip.play_count is not None:
            parts.append(f"▶ {clip.play_count:,}")
    elif clip.read_count:
        parts.append(f"▶ {clip.read_count:,}")
    return f"({' · '.join(parts)})" if parts else ""

def build_naver_cafe_content_model(
    vod_info: VODInfo,
    public_url_base: str,
    sec: dict,
    user_clips: "Optional[UserClipsBundle]" = None,
    audio_peak_secs: Optional[list[float]] = None,
    shorts_url: str = "",
) -> dict:
    """Canonical Cafe post model used by both HTML copy and SmartEditor publish."""

    report_date = _cafe_report_date(vod_info)
    title_body = _cafe_report_title(vod_info, sec)
    video_no = vod_info.video_no
    summary_url = _build_public_vod_url(public_url_base, video_no)
    vod_url = _cafe_video_url(video_no) if video_no else ""

    source_cards: list[dict] = []
    if vod_url:
        source_cards.append(
            {
                "kind": "vod",
                "title": "치지직 다시보기 열기",
                "label": "원본 VOD",
                "description": "클릭하면 원본 VOD로 이동합니다.",
                "url": vod_url,
                "native_card": False,
            }
        )
    if summary_url:
        source_cards.append(
            {
                "kind": "summary",
                "title": "전체 요약 웹페이지",
                "label": "전체 요약 웹페이지",
                "description": "타임라인 전체와 자세한 장면 기록을 웹페이지로 봅니다.",
                "url": summary_url,
                "native_card": True,
            }
        )

    flow_items: list[dict] = []
    for item in _group_cafe_chapter_items(sec.get("chzzk_comment_timeline") or []):
        tc = item["tc"]
        tc_end = item["tc_end"]
        title = " / ".join(item.get("titles") or [])
        tc_label = f"[{tc}~{tc_end}]" if tc_end else f"[{tc}]"
        flow_items.append(
            {
                "tc": tc,
                "tc_end": tc_end,
                "tc_label": tc_label,
                "title": title,
                "url": _cafe_video_url(video_no, tc) if video_no else "",
            }
        )

    timeline_items: list[dict] = []
    for item in sec.get("timeline") or []:
        tc = item.get("tc") or ""
        mood = (item.get("mood_raw") or "").strip()
        mood = mood.replace("분위기:", "").strip()
        timeline_items.append(
            {
                "tc": tc,
                "title": _strip_timestamp_confidence_markers(item.get("title") or ""),
                "mood": mood,
                "mood_raw": item.get("mood_raw") or "",
                "summary": _strip_timestamp_confidence_markers(item.get("summary") or ""),
                "url": _cafe_video_url(video_no, tc) if video_no else "",
                "is_injected": bool(item.get("is_injected")),
                "audio_peak": bool(_audio_peak_cafe_badge_html(tc, audio_peak_secs or [])),
            }
        )
    if not flow_items:
        for item in timeline_items:
            tc = item.get("tc") or ""
            title = item.get("title") or ""
            if not tc and not title:
                continue
            flow_items.append(
                {
                    "tc": tc,
                    "tc_end": "",
                    "tc_label": f"[{tc}]" if tc else "",
                    "title": title,
                    "url": item.get("url") or "",
                }
            )

    highlight_items: list[dict] = []
    if _should_render_cafe_highlights():
        for item in sec.get("highlights") or []:
            tc_range = item.get("tc_range") or ""
            highlight_items.append(
                {
                    "tc_range": tc_range,
                    "title": _strip_timestamp_confidence_markers(item.get("title") or ""),
                    "reason": _strip_timestamp_confidence_markers(item.get("reason") or ""),
                    "url": _cafe_video_url(video_no, tc_range) if video_no else "",
                    "is_injected": bool(item.get("is_injected")),
                }
            )

    clip_cards: list[dict] = []
    for clip in _top_user_clips_for_cafe(user_clips, vod_info, limit=3):
        clip_url = f"https://chzzk.naver.com/clips/{clip.clip_uid}" if clip.clip_uid else ""
        tc_text = ""
        tc_url = ""
        if (
            clip.offset_status == "ok"
            and clip.video_no == vod_info.video_no
            and clip.offset_sec is not None
        ):
            tc_text = sec_to_hms(int(clip.offset_sec))
            tc_url = _cafe_video_url(video_no, tc_text) if video_no else ""
        clip_cards.append(
            {
                "url": clip_url,
                "title": clip.title or "(제목 없음)",
                "tc": tc_text,
                "tc_url": tc_url,
                "engagement": _cafe_engagement_text(clip),
                "native_card": True,
            }
        )

    return {
        "schema_version": "naver_cafe_content_model.v1",
        "article_title": (
            f"📋 {report_date} 방송 분석 리포트: {title_body}"
            if report_date else f"📋 방송 분석 리포트: {title_body}"
        ),
        "report_heading": f"📋 {report_date} 방송 요약" if report_date else "📋 방송 요약",
        "quote_lines": _cafe_split_quote_lines(sec.get("pull_quote") or ""),
        "tags": [f"#{tag}" for tag in (sec.get("hashtags") or [])[:6]],
        "source_cards": source_cards,
        "summary_url": summary_url,
        "vod_url": vod_url,
        "shorts_url": shorts_url.strip(),
        "highlight_items": highlight_items,
        "clip_cards": clip_cards,
        "flow_items": flow_items,
        "timeline_items": timeline_items,
    }

def _render_naver_cafe_content_model_html(model: dict, audio_peak_secs: Optional[list[float]] = None) -> str:
    def _p(inner: str) -> str:
        return f'<p style="{_CAFE_PARAGRAPH_STYLE}">{inner}</p>'

    parts: list[str] = []
    rows: list[str] = [
        '<p style="font-size:30px;line-height:1.45;margin:0 0 18px;'
        'color:#111827;font-weight:900;text-align:center;letter-spacing:0">'
        f'{_html_escape(model.get("report_heading") or "📋 방송 요약")}</p>',
    ]
    quote_lines = list(model.get("quote_lines") or [])
    if quote_lines:
        rows.append(
            '<blockquote style="margin:0 0 18px;padding:0 18px;text-align:center;'
            'border-left:3px solid #d9dee5;color:#1f2937;font-size:20px;'
            'line-height:1.85;font-weight:800">'
            f'{"<br>".join(_html_escape(line) for line in quote_lines)}</blockquote>'
        )
    tags = list(model.get("tags") or [])
    if tags:
        rows.append(
            '<p style="margin:0 0 24px;line-height:1.9;text-align:center">'
            f'{_cafe_tag_chips([tag.lstrip("#") for tag in tags])}</p>'
        )
    rows.append('<hr style="border:0;border-top:1px solid #e5e7eb;margin:30px 0 26px">')
    parts.append("\n".join(rows))

    if model.get("shorts_url"):
        _append_cafe_shorts_link_card(parts, str(model.get("shorts_url") or ""))

    _append_cafe_publish_source_section(parts, model)

    if model.get("highlight_items"):
        _append_cafe_section(parts, "🎬 먼저 볼만한 구간")
        for idx, item in enumerate(model.get("highlight_items") or [], start=1):
            tc_text = _html_escape(item.get("tc_range") or "")
            url = item.get("url") or ""
            tc_segment = (
                f'<a href="{_html_escape(url)}">[{tc_text}]</a>'
                if url else f"[{tc_text}]"
            )
            injected_badge = (
                ' <strong>[📎 시청자 클립]</strong>'
                if item.get("is_injected") else ""
            )
            title = _strip_timestamp_confidence_markers(item.get("title") or "")
            reason = _strip_timestamp_confidence_markers(item.get("reason") or "")
            parts.append(_p(f"{idx}. <strong>{tc_segment}</strong> {_render_inline_md(title)}{injected_badge}"))
            if reason:
                parts.append(_p(f"추천 이유: {_render_inline_md(reason)}"))

    clip_cards = list(model.get("clip_cards") or [])
    if clip_cards:
        _append_cafe_section(parts, "🎯 시청자 인기 클립")
        parts.append(_p("이미 클립으로 잘린 반응 좋은 장면입니다."))
        for clip in clip_cards:
            parts.append(_cafe_native_card_preview(clip, fallback_description="시청자 인기 클립"))

    flow_items = list(model.get("flow_items") or [])
    _append_cafe_section(parts, "📌 방송 흐름")
    if flow_items:
        for idx, item in enumerate(flow_items, start=1):
            tc_label = item.get("tc_label") or f'[{item.get("tc") or ""}]'
            url = item.get("url") or ""
            tc_segment = (
                f'<a href="{_html_escape(url)}">{_html_escape(tc_label)}</a>'
                if url else _html_escape(tc_label)
            )
            title = _strip_timestamp_confidence_markers(item.get("title") or "")
            parts.append(_cafe_timeline_row(idx, tc_segment, _html_escape(title)))
    else:
        parts.append(_p("&nbsp;"))

    timeline_items = list(model.get("timeline_items") or [])
    _append_cafe_section(parts, "📍 자세한 장면 기록")
    if timeline_items:
        for item in timeline_items:
            mood_suffix = (
                f' <span style="font-size:13px;color:#777">({_html_escape(item.get("mood_raw") or item.get("mood") or "")})</span>'
                if item.get("mood_raw") or item.get("mood") else ""
            )
            tc_text = _html_escape(item.get("tc") or "")
            url = item.get("url") or ""
            tc_segment = (
                f'<a href="{_html_escape(url)}">[{tc_text}]</a>'
                if url else f"[{tc_text}]"
            )
            injected_badge = (
                ' <strong>[📎 시청자 클립]</strong>'
                if item.get("is_injected") else ""
            )
            audio_badge = _audio_peak_cafe_badge_html(item.get("tc") or "", audio_peak_secs or [])
            title = _strip_timestamp_confidence_markers(item.get("title") or "")
            summary = _strip_timestamp_confidence_markers(item.get("summary") or "")
            parts.append(
                '<p style="font-size:16px;line-height:1.9;margin:0 0 6px;color:#202124">'
                    f"<strong>{tc_segment} "
                    f"{_render_inline_md(title)}</strong>{injected_badge}{audio_badge}{mood_suffix}"
                '</p>'
            )
            if summary:
                parts.append(
                    '<p style="font-size:15px;line-height:2.0;margin:0 0 22px;color:#555">'
                        f'{_render_inline_md(summary)}'
                    '</p>'
                )
    else:
        parts.append(_p("&nbsp;"))

    return "\n".join(parts)

def _render_naver_cafe_html(
    vod_info: VODInfo,
    public_url_base: str,
    sec: dict,
    user_clips: "Optional[UserClipsBundle]" = None,
    audio_peak_secs: Optional[list[float]] = None,
    shorts_url: str = "",
) -> str:
    """Generate a clean HTML fragment that pastes well into Naver Cafe."""
    model = build_naver_cafe_content_model(
        vod_info,
        public_url_base,
        sec,
        user_clips=user_clips,
        audio_peak_secs=audio_peak_secs or [],
        shorts_url=shorts_url,
    )
    return _render_naver_cafe_content_model_html(model, audio_peak_secs=audio_peak_secs or [])
