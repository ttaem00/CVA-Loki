"""Primary owner for viewer-editor payloads, cards, and source-health rendering."""

from __future__ import annotations

import copy

import hashlib

import json

import logging

import math

import re

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Optional

from collections import Counter

from ..scene_signal_contract import (
    lane_colors as _scene_signal_lane_colors,
    lane_labels as _scene_signal_lane_labels,
    lane_purposes as _scene_signal_lane_purposes,
    lane_viewer_labels as _scene_signal_lane_viewer_labels,
    overview_lane_keys as _scene_signal_overview_lane_keys,
)

from ..editorial_highlight_cards import editorial_scene_image_data_url

from ..models import VODInfo

from ..models import UserClipsBundle

from ..user_clips_filter import is_eligible_in_vod_clip

from ..utils import sec_to_hms

from .parse import _first_timecode_sec, _strip_timestamp_confidence_markers

logger = logging.getLogger("pipeline")

_CHARTJS_ASSET_PATH = (
    Path(__file__).resolve().parents[2]
    / "publish" / "web" / "assets" / "vendor" / "chart.umd.min.js"
)

_CHARTJS_INLINE_CACHE: Optional[str] = None

_VIEWER_CLIP_TITLE_MARKER_RE = re.compile(r"\s*\[\s*📎\s*시청자\s*클립\s*\]\s*")

_EDITORIAL_ROLE_LABELS = {
    "setup": "준비", "performance": "특이한 행동·퍼포먼스", "event": "사건",
    "reaction": "반응", "result": "결과",
}

VIEWER_CLIPS_MARKER_START = "<!-- viewer-clips:start -->"

VIEWER_CLIPS_MARKER_END = "<!-- viewer-clips:end -->"

_SAFE_THUMB_SCHEMES = ("http://", "https://")

_BADGE_LOWER_PLAY_MIN_V1 = 300

VIEWER_EDITOR_TOOLS_RENDER_VERSION = "persisted-scene-images-first-look-v13"

from .render_primitives import (
    _audio_peak_delta_text,
    _build_public_vod_url,
    _chzzk_video_url_at,
    _hms_to_seconds,
    _html_escape,
)

def _chat_sec_for_viewer_editor(item: Any) -> float | None:
    if not isinstance(item, dict):
        return None
    for key in ("sec", "time_sec", "offset_sec"):
        try:
            if item.get(key) not in (None, ""):
                return float(item.get(key))
        except (TypeError, ValueError):
            pass
    try:
        if item.get("ms") not in (None, ""):
            return float(item.get("ms")) / 1000.0
    except (TypeError, ValueError):
        pass
    return None

def _chat_text_for_viewer_editor(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("msg", "message", "text", "content"):
        text = str(item.get(key) or "").strip()
        if text:
            return text[:90]
    return ""

def _viewer_editor_chat_buckets(chats: list[dict], *, bucket_sec: int = 30) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    for item in chats or []:
        sec = _chat_sec_for_viewer_editor(item)
        if sec is None:
            continue
        start = int(sec // bucket_sec) * bucket_sec
        bucket = buckets.setdefault(
            start,
            {
                "start_sec": start,
                "end_sec": start + bucket_sec,
                "count": 0,
                "reaction_count": 0,
                "samples": [],
            },
        )
        bucket["count"] += 1
        text = _chat_text_for_viewer_editor(item)
        if text:
            if "ㅋ" in text or "웃" in text or any(token in text for token in ("헐", "대박", "뭐야", "?")):
                bucket["reaction_count"] += 1
            if len(bucket["samples"]) < 3:
                bucket["samples"].append({"sec": round(sec, 3), "text": text})
    return sorted(buckets.values(), key=lambda row: row["start_sec"])

_VIEWER_EDITOR_LANE_LABELS = {
    **_scene_signal_lane_labels(),
    "timeline": "기존 요약",
    "chapter": "댓글 시간표 후보",
    "highlight_candidate": "기존 요약 추천",
}

_VIEWER_EDITOR_LANE_VIEWER_LABELS = {
    **_scene_signal_lane_viewer_labels(),
    "timeline": "기존 요약 목차",
    "chapter": "댓글 시간표 후보",
    "highlight_candidate": "기존 요약 추천 보기",
    "chat": "전체 채팅량",
}

_VIEWER_EDITOR_LANE_COLORS = {
    **_scene_signal_lane_colors(),
    "timeline": "rgba(122,162,247,0.86)",
    "chapter": "rgba(187,154,247,0.78)",
    "highlight_candidate": "rgba(247,118,142,0.82)",
}

_VIEWER_EDITOR_LANE_PURPOSES = {
    **_scene_signal_lane_purposes(),
    "timeline": "요약 문단에서 뽑은 목차 후보",
    "chapter": "댓글에 붙일 시각 메모",
    "highlight_candidate": "기존 요약 단계가 추천했던 장면 비교",
}

_VIEWER_EDITOR_OVERVIEW_LANE_KEYS = _scene_signal_overview_lane_keys()

def _viewer_editor_event_counts_by_kind(events: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in events:
        if not isinstance(row, dict):
            continue
        key = str(row.get("kind") or row.get("lane") or "")
        if key:
            counts[key] += 1
    return counts

def _viewer_editor_should_hide_scene_lane(key: str, event_counts: Counter[str]) -> bool:
    """Hide redundant or empty scene lanes from the editor scene list.

    Final comment chapters are the editor-facing comment timeline, so the raw
    comment replay lane remains in events for traceability but is not rendered
    as a duplicate lane when chapters exist. Overview rows have their own
    coverage UI; a scene row is useful only when its producer emitted an event.
    """
    return (
        (key == "comment_replay" and event_counts.get("chapter", 0) > 0)
        or (
            key not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
            and event_counts.get(key, 0) == 0
        )
    )

def _viewer_editor_lane_summaries_for_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_counts = _viewer_editor_event_counts_by_kind(events)
    lanes: list[dict[str, Any]] = []
    for key, label in _VIEWER_EDITOR_LANE_LABELS.items():
        if _viewer_editor_should_hide_scene_lane(key, event_counts):
            continue
        lane = _viewer_editor_lane_summary(key, label, events)
        if lane is not None:
            lanes.append(lane)
    return lanes

def _viewer_editor_limit_events_preserving_viewer_clips(
    events: list[dict[str, Any]],
    *,
    max_events: int = 220,
) -> list[dict[str, Any]]:
    private_timetable_events = [
        row
        for row in events
        if "private_rewritten_timetable_anchor" in {
            str(value) for value in (row.get("risk_flags") or [])
        }
    ]
    private_timetable_row_ids = {id(row) for row in private_timetable_events}
    regular_events = [
        row for row in events if id(row) not in private_timetable_row_ids
    ]
    if len(regular_events) <= max_events:
        selected_regular = list(regular_events)
    else:
        viewer_clip_events = [
            row for row in regular_events if row.get("kind") == "viewer_clip"
        ]
        if not viewer_clip_events:
            selected_regular = list(regular_events[:max_events])
        elif len(viewer_clip_events) >= max_events:
            selected_regular = list(viewer_clip_events[:max_events])
        else:
            selected_regular = [
                row for row in regular_events
                if row.get("kind") != "viewer_clip"
            ][: max_events - len(viewer_clip_events)] + viewer_clip_events
    selected = [*selected_regular, *private_timetable_events]
    selected.sort(
        key=lambda item: (
            item["start_sec"],
            item.get("end_sec") or item["start_sec"],
            item["id"],
        )
    )
    return selected

def _viewer_editor_guidance_from_flags(flags: Any, kind: str = "") -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(title: str, text: str) -> None:
        key = (title, text)
        if key not in seen:
            seen.add(key)
            messages.append({"title": title, "text": text})

    for flag in [str(v) for v in (flags or []) if v]:
        if flag == "subtitle_context_not_final_claim":
            add("자막 참고 구간", "긴 자막이 잡힌 장면입니다. 최종 하이라이트라는 뜻은 아니고, 앞뒤 맥락을 확인하기 좋은 위치입니다.")
        elif flag in {"chat_bucket_supporting_signal", "chat_peak_supporting_signal"}:
            if kind == "chat_laughter":
                add("웃음/놀람 채팅", "웃기거나 놀란 반응이 채팅에 많이 잡힌 구간입니다. 리액션 컷 후보로 보고, 정확한 장면 내용은 자막과 요약으로 확인하세요.")
            elif kind == "chat_surprise":
                add("채팅 급증 구간", "채팅량이 갑자기 늘어난 구간입니다. 무슨 일이 났는지 찾는 출발점으로 보고, 웃긴 장면인지 이슈 장면인지는 자막과 화면 흐름으로 확인하세요.")
            else:
                add("채팅 반응 신호", "시청자 반응이 몰린 구간입니다. 방송 내용의 사실 판단은 자막이나 기존 요약과 함께 보세요.")
        elif flag in {"viewer_interest_signal_not_ground_truth", "injected_viewer_clip_anchor"}:
            add("시청자 관심 신호", "시청자 클립이나 반응으로 잡힌 위치입니다. 편집 후보로 보고 장면 내용은 다시 확인하세요.")
        elif flag == "live_context_supporting_signal_not_truth":
            add("라이브 맥락 신호", "방송 제목이나 시청자 수가 바뀐 위치입니다. 전환점 후보로만 보고, 실제 장면 내용은 자막/채팅/요약과 함께 확인하세요.")
        elif flag == "visual_scene_supporting_signal_not_truth":
            add("화면 신호 후보", "화면 변화, OCR 유형, 게임 상태로 잡은 보조 후보입니다. 확정 장면 근거가 아니므로 자막/채팅/요약과 함께 확인하세요.")
        elif flag == "live_context_missing_elapsed":
            add("시각 매핑 없음", "라이브 시작 시각이나 경과 시간이 없어 VOD 시각으로 연결하지 못했습니다. 수집 안 됨으로 보고 기본 요약 판단에는 쓰지 마세요.")
        elif flag == "emoticon_aggregate_supporting_signal":
            add("감정표현 반응 신호", "채팅 감정표현이 집계로 몰린 구간입니다. 감정표현 뜻을 자동 해석한 것이 아니므로 자막과 요약 흐름을 함께 확인하세요.")
        elif flag == "audio_peak_supporting_signal":
            add("소리 변화 참고", "소리 변화가 큰 구간입니다. 의미 있는 장면인지는 자막과 화면 맥락으로 확인하세요.")
        elif flag in {"comment_chapter_is_navigation_not_fact", "point_chapter"}:
            add("댓글 시간표 후보", "방송 댓글이나 편집 메모에 적을 시각입니다. 편집 컷 확정이 아니므로 장면 내용은 주변 자막/요약과 함께 확인하세요.")
        elif flag == "source_alignment_candidate_not_final_truth":
            add("편집 후보 신호", "편집본 학습 후보에서 온 참고 신호입니다. 최종 판단은 원본 장면 맥락과 함께 보세요.")
        elif flag == "timestamp_srt_quote_mismatch":
            add("시각 확인 필요", "요약 카드의 시각과 SRT에서 찾은 인용 근거 시각이 크게 다릅니다. 그대로 쓰지 말고 실제 시각 주변 자막과 장면을 확인하세요.")
        elif flag == "timestamp_srt_quote_corrected":
            add("시각 보정됨", "인용 근거가 SRT에서 다른 시각에 잡혀 실제 시각을 함께 표시했습니다.")
        elif flag in {"missing_evidence", "missing_summary", "missing_reason"}:
            add("근거 부족", "이 항목은 설명이나 근거가 부족합니다. 그대로 쓰기 전에 주변 맥락을 확인하세요.")
        elif flag == "point_highlight_without_end":
            add("끝 시각 없음", "시작 시각만 있는 후보입니다. 편집 컷으로 쓰려면 끝나는 시각을 따로 정해야 합니다.")
        elif flag == "missing_timecode":
            add("시각 확인 필요", "원본 시각 연결이 부족한 항목입니다.")

    if not messages and kind in {"chat", "chat_laughter", "chat_surprise"}:
        if kind == "chat_laughter":
            add("웃음/놀람 채팅", "웃기거나 놀란 반응이 채팅에 많이 잡힌 구간입니다. 리액션 컷 후보로 보고, 정확한 장면 내용은 자막과 요약으로 확인하세요.")
        elif kind == "chat_surprise":
            add("채팅 급증 구간", "채팅량이 갑자기 늘어난 구간입니다. 무슨 일이 났는지 찾는 출발점으로 보고, 웃긴 장면인지 이슈 장면인지는 자막과 화면 흐름으로 확인하세요.")
        else:
            add("채팅 반응 신호", "채팅이 몰린 위치입니다. 장면 내용은 자막이나 기존 요약과 함께 확인하세요.")
    return messages

def _thin_viewer_editor_chat_buckets(
    buckets: list[dict[str, Any]],
    *,
    max_buckets: int = 360,
) -> list[dict[str, Any]]:
    """Bound density DOM work while keeping the full VOD time shape."""
    rows = [row for row in buckets if isinstance(row, dict)]
    if len(rows) <= max_buckets:
        return rows
    step = max(1, (len(rows) + max_buckets - 1) // max_buckets)
    out: list[dict[str, Any]] = []
    for idx in range(0, len(rows), step):
        group = rows[idx : idx + step]
        if not group:
            continue
        start = group[0].get("start_sec")
        end = group[-1].get("end_sec", group[-1].get("start_sec"))
        samples: list[dict[str, Any]] = []
        for item in group:
            for sample in item.get("samples") or []:
                if isinstance(sample, dict) and len(samples) < 3:
                    samples.append(sample)
        thinned = {
            "start_sec": start,
            "end_sec": end,
            "count": sum(int(row.get("count") or 0) for row in group),
            "reaction_count": sum(
                int(row.get("reaction_count") or row.get("laughter_count") or 0)
                + int(row.get("surprise_count") or 0)
                for row in group
            ),
            "samples": samples,
        }
        first_context = next(
            (
                row
                for row in group
                if isinstance(row, dict)
                and (row.get("video_no") or row.get("vod_label") or row.get("seek_sec") is not None)
            ),
            None,
        )
        if first_context:
            for key in ("video_no", "vod_label"):
                if first_context.get(key):
                    thinned[key] = first_context.get(key)
            if first_context.get("seek_sec") is not None:
                thinned["seek_sec"] = first_context.get("seek_sec")
        out.append(thinned)
    return out

def _thin_viewer_editor_subtitles(
    subtitles: list[dict[str, Any]],
    *,
    max_rows: int = 900,
) -> list[dict[str, Any]]:
    rows = [row for row in subtitles if isinstance(row, dict)]
    if len(rows) <= max_rows:
        return rows
    step = max(1, (len(rows) + max_rows - 1) // max_rows)
    out: list[dict[str, Any]] = []
    for row in rows[::step]:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        item = {
            "start_sec": row.get("start_sec"),
            "end_sec": row.get("end_sec", row.get("start_sec")),
            "text": text[:220],
        }
        for key in ("video_no", "vod_label", "seek_sec", "timecode"):
            if row.get(key) not in (None, ""):
                item[key] = row.get(key)
        if row.get("index") is not None:
            item["index"] = row.get("index")
        out.append(item)
    return out[:max_rows]

def _thin_viewer_editor_audio_waveform(
    source: dict[str, Any] | None,
    *,
    max_samples: int = 720,
) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    if str(source.get("schema_version") or "") != "audio_waveform.v1":
        return None
    samples: list[dict[str, Any]] = []
    for row in source.get("samples") or []:
        if not isinstance(row, dict):
            continue
        try:
            start = round(max(0.0, float(row.get("start_sec"))), 3)
            end = round(max(start, float(row.get("end_sec"))), 3)
            value = round(max(0.0, min(1.0, float(row.get("value")))), 4)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        samples.append({"start_sec": start, "end_sec": end, "value": value})
    if not samples:
        return None
    samples.sort(key=lambda row: (row["start_sec"], row["end_sec"]))
    if len(samples) > max_samples:
        samples = _thin_ordered_rows_evenly(samples, max_samples)
    try:
        duration = round(max(float(source.get("duration_sec") or 0), samples[-1]["end_sec"]), 3)
    except (TypeError, ValueError):
        duration = samples[-1]["end_sec"]
    return {
        "schema_version": "audio_waveform.v1",
        "source": "audio_loudness_windows",
        "sample_count": len(samples),
        "duration_sec": duration,
        "unit": "relative_loudness_normalized_0_1",
        "privacy_class": "public_safe",
        "decision_rule": "waveform is timeline context only; it does not prove highlight importance",
        "samples": samples,
    }

def _thin_ordered_rows_evenly(rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    if max_rows <= 0:
        return []
    if len(rows) <= max_rows:
        return rows
    if max_rows == 1:
        return [rows[0]]
    last_index = len(rows) - 1
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for out_index in range(max_rows):
        source_index = round((out_index * last_index) / (max_rows - 1))
        if source_index in seen:
            continue
        selected.append(rows[source_index])
        seen.add(source_index)
    return selected

def _viewer_editor_source_label(src: dict[str, Any]) -> str:
    raw = str(src.get("source_type") or src.get("section") or "source").strip()
    lowered = raw.lower()
    if "summary_markdown" in lowered:
        return "요약에 적힌 장면"
    if "chat_or_text_evidence" in lowered:
        return "채팅/자막 근거"
    if "subtitle_chunk" in lowered or "timed_evidence" in lowered:
        return "자막 근거"
    if "viewer_clip" in lowered:
        return "시청자 클립"
    if "audio" in lowered:
        return "오디오 반응"
    if "chat_density_peak" in lowered:
        return "채팅 반응"
    if "comment_timeline" in lowered or "chapter" in lowered:
        return "댓글 시간표 후보"
    if "semantic_vector" in lowered or "timestamp_overlap" in lowered:
        return "가까운 방송 근거"
    if raw in {"source", "supporting", "candidate"}:
        return "참고 자료"
    return raw or "참고 자료"

def _viewer_editor_evidence_item_from_source(src: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(src, dict):
        return None
    label = _viewer_editor_source_label(src)
    text = _strip_timestamp_confidence_markers(
        str(src.get("text") or src.get("title") or src.get("timecode") or "")
    )
    item: dict[str, Any] = {"label": label or "source", "text": text[:180]}
    for key in ("start_sec", "sec", "seek_sec", "end_sec", "end_seek_sec", "video_no", "source_video_no", "vod_label", "timecode"):
        if src.get(key) not in (None, ""):
            item[key] = src.get(key)

    actual_tc = str(src.get("actual_tc") or "").strip()
    actual_sec = _first_timecode_sec(actual_tc)
    if actual_tc and actual_sec is not None:
        claimed_tc = str(src.get("claimed_tc") or src.get("timecode") or "").strip()
        item["label"] = "실제 인용 시각"
        item["text"] = (
            f"SRT 인용 근거는 {actual_tc} 주변입니다."
            + (f" 요약 카드 시각 {claimed_tc}와 따로 확인하세요." if claimed_tc else "")
        )
        item["start_sec"] = actual_sec
        item["sec"] = actual_sec
        item["timecode"] = actual_tc
    if not str(item.get("text") or "").strip() and item.get("start_sec") in (None, ""):
        return None
    return item

def _viewer_editor_stable_source_identity_key(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"

def _viewer_editor_clip_source_identity(
    src: dict[str, Any],
    row: dict[str, Any],
    start: Any,
) -> dict[str, Any]:
    clip_uid = str(src.get("clip_uid") or src.get("uid") or "").strip()
    video_no = str(src.get("video_no") or src.get("source_video_no") or row.get("video_no") or "").strip()
    offset = src.get("offset_sec", start)
    title = src.get("title") or row.get("title") or ""
    if clip_uid:
        return {
            "source_identity_key": _viewer_editor_stable_source_identity_key("viewer_clip", video_no, clip_uid),
            "source_identity_kind": "public_viewer_clip_uid",
            "source_identity_confidence": "high",
            "engagement_weighted": True,
        }
    return {
        "source_identity_key": _viewer_editor_stable_source_identity_key("viewer_clip_anchor", video_no, offset, title),
        "source_identity_kind": "deterministic_viewer_clip_anchor",
        "source_identity_confidence": "low",
        "engagement_weighted": False,
    }

def _viewer_editor_event_from_viewer_clip_source(
    row: dict[str, Any],
    src: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not isinstance(src, dict):
        return None
    if str(src.get("source_type") or "") != "viewer_clip_anchor":
        return None
    try:
        start = int(float(src.get("offset_sec", row.get("start_sec"))))
    except (TypeError, ValueError):
        return None
    title = _strip_timestamp_confidence_markers(str(src.get("title") or row.get("title") or "시청자 클립"))
    evidence_item = _viewer_editor_evidence_item_from_source(src) or {"label": "시청자 클립", "text": title[:180]}
    identity = _viewer_editor_clip_source_identity(src, row, start)
    return {
        "id": str(row.get("segment_id") or "segment") + f"_viewer_clip_{index}",
        "kind": "viewer_clip",
        "kind_label": _VIEWER_EDITOR_LANE_LABELS.get("viewer_clip", "시청자 클립"),
        "start_sec": start,
        "end_sec": None,
        "timecode": str(src.get("timecode") or sec_to_hms(start)),
        "title": title or "시청자 클립",
        "evidence": [evidence_item],
        "guidance": _viewer_editor_guidance_from_flags(
            [] if identity["engagement_weighted"] else ["viewer_clip_anchor_not_engagement_weighted"],
            "viewer_clip",
        ),
        **identity,
    }

def _viewer_editor_viewer_clip_events_from_ledger(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in ledger:
        if not isinstance(row, dict):
            continue
        for index, src in enumerate(row.get("sources") or []):
            event = _viewer_editor_event_from_viewer_clip_source(row, src, index)
            if event is not None:
                events.append(event)
    return events

def _viewer_editor_lane_summary(key: str, label: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    lane_events = [event for event in events if event.get("kind") == key]
    if not lane_events:
        return None
    lane = {"key": key, "label": label, "event_count": len(lane_events)}
    if key == "viewer_clip":
        distinct_keys = {
            str(event.get("source_identity_key") or "")
            for event in lane_events
            if event.get("source_identity_key")
        }
        engagement_keys = {
            str(event.get("source_identity_key") or "")
            for event in lane_events
            if event.get("source_identity_key") and event.get("engagement_weighted") is not False
        }
        if distinct_keys:
            lane["distinct_source_count"] = len(distinct_keys)
            lane["distinct_engagement_source_count"] = len(engagement_keys)
    return lane

def _viewer_editor_event_from_segment(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    kind = str(row.get("kind") or "")
    if kind not in {"timeline", "highlight", "chapter", "highlight_candidate"}:
        return None
    try:
        start = int(float(row.get("start_sec")))
    except (TypeError, ValueError):
        return None
    end: int | None
    try:
        end = int(float(row.get("end_sec"))) if row.get("end_sec") not in (None, "") else None
    except (TypeError, ValueError):
        end = None
    evidence: list[dict[str, Any]] = []
    if row.get("summary"):
        summary_text = _strip_timestamp_confidence_markers(str(row.get("summary") or ""))
        if summary_text:
            evidence.append({"label": "요약 본문", "text": summary_text})
    if row.get("reason"):
        reason_text = _strip_timestamp_confidence_markers(str(row.get("reason") or ""))
        if reason_text:
            evidence.append({"label": "추천 이유", "text": reason_text})
    for src in (row.get("sources") or [])[:4]:
        source_item = _viewer_editor_evidence_item_from_source(src)
        if source_item:
            evidence.append(source_item)
    kind_label = {
        "timeline": "기존 요약",
        "highlight": "기존 요약 추천",
        "chapter": "댓글 시간표 후보",
        "highlight_candidate": "기존 요약 추천",
    }.get(kind, kind)
    return {
        "id": str(row.get("segment_id") or f"{kind}_{start}_{end or ''}"),
        "kind": kind,
        "kind_label": kind_label,
        "start_sec": start,
        "end_sec": end,
        "timecode": str(row.get("timecode") or sec_to_hms(start)),
        "title": _strip_timestamp_confidence_markers(str(row.get("title") or row.get("timecode") or kind_label)),
        "evidence": evidence[:6],
        "guidance": _viewer_editor_guidance_from_flags(row.get("risk_flags"), kind),
    }

def _viewer_editor_event_from_audio_peak(peak: dict[str, Any], index: int) -> dict[str, Any] | None:
    if not isinstance(peak, dict):
        return None
    try:
        center = int(round(float(peak.get("center_sec", peak.get("sec", peak.get("start_sec"))))))
    except (TypeError, ValueError):
        return None
    labels = [str(label) for label in (peak.get("labels") or []) if label]
    if "chat_audio_overlap" in labels:
        title = "소리와 채팅이 함께 튄 구간"
    elif "voice_reaction_candidate" in labels:
        title = "목소리 리액션이 커진 구간"
    else:
        title = "소리가 크게 튄 구간"
    evidence_text = ", ".join(labels) if labels else "supporting_signal_only"
    try:
        score = float(peak.get("score"))
    except (TypeError, ValueError):
        score = 0.0
    if score:
        evidence_text = f"{evidence_text} · score={score:.2f}"
    return {
        "id": f"audio_peak_{center}_{index}",
        "kind": "audio_peak",
        "kind_label": _VIEWER_EDITOR_LANE_LABELS.get("audio_peak", "오디오 피크"),
        "start_sec": center,
        "end_sec": None,
        "timecode": sec_to_hms(center),
        "title": title,
        "evidence": [{"label": "오디오 피크", "text": evidence_text[:220]}],
        "guidance": _viewer_editor_guidance_from_flags(["audio_peak_supporting_signal"], "audio_peak"),
    }

def _viewer_editor_event_from_workbench(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    try:
        start = int(float(row.get("start_sec")))
    except (TypeError, ValueError):
        return None
    try:
        end = int(float(row.get("end_sec"))) if row.get("end_sec") not in (None, "") else None
    except (TypeError, ValueError):
        end = None
    lane = str(row.get("lane") or "")
    lane_label = str(_VIEWER_EDITOR_LANE_VIEWER_LABELS.get(lane) or row.get("lane_label") or _VIEWER_EDITOR_LANE_LABELS.get(lane) or lane or "장면")
    evidence: list[dict[str, Any]] = []
    for item in row.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text or item.get("start_sec") not in (None, "") or item.get("sec") not in (None, ""):
            out = {
                "label": str(item.get("label") or item.get("type") or "근거"),
                "text": text[:220],
            }
            for key in ("start_sec", "sec", "seek_sec", "end_sec", "end_seek_sec", "video_no", "source_video_no", "vod_label", "timecode"):
                if item.get(key) not in (None, ""):
                    out[key] = item.get(key)
            evidence.append(out)
    return {
        "id": str(row.get("event_id") or row.get("segment_id") or f"{lane}_{start}_{end or ''}"),
        "kind": lane,
        "kind_label": lane_label,
        "start_sec": start,
        "end_sec": end,
        "timecode": str(row.get("timecode") or sec_to_hms(start)),
        "title": str(row.get("label") or lane_label),
        "evidence": evidence[:6],
        "guidance": _viewer_editor_guidance_from_flags(row.get("risk_flags"), lane),
        **({
            "source_identity_key": str(row.get("source_identity_key")),
            "source_identity_kind": str(row.get("source_identity_kind") or ""),
            "source_identity_confidence": str(row.get("source_identity_confidence") or ""),
            "engagement_weighted": bool(row.get("engagement_weighted")),
        } if lane == "viewer_clip" and row.get("source_identity_key") else {}),
    }

def _scope_viewer_editor_evidence_source(
    payload: dict[str, Any],
    *,
    report_video_no: str,
    evidence_video_no: str,
) -> dict[str, Any]:
    report_video_no = str(report_video_no or "")
    payload_video_no = str(payload.get("video_no") or "")
    evidence_video_no = str(evidence_video_no or payload_video_no or report_video_no)
    canonical_video_no = evidence_video_no or report_video_no


    def has_foreign_video_identity(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"video_no", "videoNo", "video_id", "videoId"}:
                    child_id = str(child or "")
                    if child_id and child_id != canonical_video_no:
                        return True
                if has_foreign_video_identity(child):
                    return True
            return False
        if isinstance(value, list):
            return any(has_foreign_video_identity(child) for child in value)
        if isinstance(value, str):
            for match in re.finditer(r"(?:/video/|/vods?/)(\d+)", value):
                if match.group(1) != canonical_video_no:
                    return True
        return False

    if (
        (payload_video_no and payload_video_no != canonical_video_no)
        or has_foreign_video_identity(payload)
    ):



        return {
            "schema_version": str(payload.get("schema_version") or "viewer_editor_tools.v1"),
            "video_no": canonical_video_no,
            "duration_sec": payload.get("duration_sec", 0),
            "events": [],
            "lanes": [],
            "chat_buckets": [],
            "subtitles": [],
        }
    payload["video_no"] = canonical_video_no
    payload.pop("evidence_video_no", None)


    for event in payload.get("events") or []:
        if not isinstance(event, dict) or str(event.get("kind") or "") == "existing_segments":
            continue
        if event.get("seek_sec") in (None, ""):
            event["seek_sec"] = event.get("start_sec", 0)
    return _reconcile_viewer_editor_overview_lanes(payload)

def _build_viewer_editor_tools_payload(
    sec: dict,
    vod_info: VODInfo,
    chats: list[dict] | None,
    srt_path: str = "",
    work_dir: str | Path | None = None,
    evidence_video_no: str = "",
    user_clips: UserClipsBundle | None = None,
) -> dict[str, Any]:
    from ..edit_point_clusters import build_edit_point_clusters
    from .packet import _merge_public_user_clip_events_into_viewer_payload

    ledger = [row for row in (sec.get("segment_ledger") or []) if isinstance(row, dict)]
    try:
        from ..edit_export_preview import build_export_preview

        edit_preview = build_export_preview(ledger, source_name=str(vod_info.video_no or "vod"))
    except Exception as exc:
        edit_preview = {
            "schema_version": "edit_export_preview.v1",
            "status": "fail",
            "source": "segment_ledger",
            "clip_count": 0,
            "clips": [],
            "edl": "",
            "error": str(exc),
        }

    work_path = Path(work_dir) if work_dir else None
    if work_path is None and srt_path:


        source_parent = Path(srt_path).parent
        if source_parent.name == str(evidence_video_no or vod_info.video_no or ""):
            work_path = source_parent.parent
    if work_path is not None:
        try:
            from ..signal_timeline_workbench import build_signal_timeline_workbench

            signal_video_no = str(evidence_video_no or vod_info.video_no or "")
            workbench_meta = {
                **sec,



                "video_no": signal_video_no,
                "title": str(vod_info.title or ""),
                "duration": int(vod_info.duration or 0),
                **({"total_chats": len(chats)} if chats is not None else {}),
            }
            workbench = build_signal_timeline_workbench(
                workbench_meta,
                base=signal_video_no,
                work_dir=work_path,
                draft_dir=None,
                max_events=220,
            )
            if isinstance(workbench, dict) and workbench.get("events"):
                payload = _build_viewer_editor_tools_payload_from_workbench(
                    workbench,
                    edit_export_preview=edit_preview,
                )


                if chats is not None:
                    chat_buckets = _viewer_editor_chat_buckets(chats)
                    payload["chat_buckets"] = _thin_viewer_editor_chat_buckets(chat_buckets)
                    stats = payload.get("stats")
                    payload["stats"] = dict(stats) if isinstance(stats, dict) else {}
                    payload["stats"]["total_chats"] = len(chats)
                    summary = payload.get("summary")
                    payload["summary"] = dict(summary) if isinstance(summary, dict) else {}
                    payload["summary"]["total_chats"] = len(chats)
                    payload["summary"]["chat_bucket_count"] = len(chat_buckets)
                audio_waveform = _thin_viewer_editor_audio_waveform(sec.get("audio_waveform"))
                if audio_waveform:
                    payload["audio_waveform"] = audio_waveform
                existing_ids = {
                    str(row.get("id") or "")
                    for row in (payload.get("events") or [])
                    if isinstance(row, dict)
                }
                sec_audio_events = []
                workbench_has_audio_peaks = any(
                    isinstance(row, dict) and str(row.get("kind") or "") == "audio_peak"
                    for row in (payload.get("events") or [])
                )
                if not workbench_has_audio_peaks:
                    for index, peak in enumerate(sec.get("audio_reaction_peaks") or []):
                        event = _viewer_editor_event_from_audio_peak(peak, index)
                        if event is None or str(event.get("id") or "") in existing_ids:
                            continue
                        existing_ids.add(str(event.get("id") or ""))
                        sec_audio_events.append(event)
                payload["events"] = sorted(
                    [*(payload.get("events") or []), *sec_audio_events],
                    key=lambda item: (item["start_sec"], item.get("end_sec") or item["start_sec"], item["id"]),
                )
                payload = _merge_public_user_clip_events_into_viewer_payload(payload, user_clips, vod_info)
                payload["lanes"] = _ensure_viewer_editor_required_lanes(
                    _viewer_editor_lane_summaries_for_events(payload["events"]),
                    payload["events"],
                )
                payload.setdefault("summary", {})["event_count"] = len(payload["events"])
                payload["summary"]["lane_count"] = len(payload["lanes"])
                payload["edit_point_clusters"] = build_edit_point_clusters(
                    payload["events"],
                    duration_sec=int(float(payload.get("duration_sec") or 0)),
                )
                return _scope_viewer_editor_evidence_source(
                    payload,
                    report_video_no=str(vod_info.video_no or ""),
                    evidence_video_no=signal_video_no,
                )
        except Exception as exc:
            logger.warning(f"편집자 workbench payload 생성 실패, legacy payload로 fallback: {exc}")

    events = [
        event for event in (_viewer_editor_event_from_segment(row) for row in ledger)
        if event is not None
    ]
    events.extend(_viewer_editor_viewer_clip_events_from_ledger(ledger))
    for index, peak in enumerate(sec.get("audio_reaction_peaks") or []):
        event = _viewer_editor_event_from_audio_peak(peak, index)
        if event is not None:
            events.append(event)
    events = _dedupe_viewer_editor_viewer_clip_events(events)
    events.sort(key=lambda item: (item["start_sec"], item.get("end_sec") or item["start_sec"], item["id"]))
    clip_payload = _merge_public_user_clip_events_into_viewer_payload(
        dict(video_no=str(vod_info.video_no or ""), duration_sec=int(vod_info.duration or 0), events=events),
        user_clips, vod_info)
    events = clip_payload["events"]
    chat_buckets = _viewer_editor_chat_buckets(chats or [])
    total_chats = len(chats) if chats is not None else None
    audio_waveform = _thin_viewer_editor_audio_waveform(sec.get("audio_waveform"))
    subtitles = _viewer_editor_subtitles_from_srt(srt_path)
    lanes = _ensure_viewer_editor_required_lanes(
        _viewer_editor_lane_summaries_for_events(events),
        events,
    )
    return _scope_viewer_editor_evidence_source({
        "schema_version": "viewer_editor_tools.v1",
        "video_no": str(vod_info.video_no or ""),
        "duration_sec": int(vod_info.duration or 0),
        "events": _viewer_editor_limit_events_preserving_viewer_clips(events),
        "viewer_clip_source": clip_payload["viewer_clip_source"],
        "edit_point_clusters": build_edit_point_clusters(
            _viewer_editor_limit_events_preserving_viewer_clips(events),
            duration_sec=int(vod_info.duration or 0),
        ),
        "chat_buckets": _thin_viewer_editor_chat_buckets(chat_buckets),
        "subtitles": _thin_viewer_editor_subtitles(subtitles),
        "stats": {
            "total_chats": total_chats,
        },
        "summary": {
            "event_count": len(events),
            "lane_count": len(lanes),
            "chat_bucket_count": len(chat_buckets),
            "subtitle_context_count": len(subtitles),
            "total_chats": total_chats,
        },
        **({"audio_waveform": audio_waveform} if audio_waveform else {}),
        "edit_export_preview": edit_preview,
        "lanes": lanes,
        "labels": dict(_VIEWER_EDITOR_LANE_LABELS),
        "viewer_labels": dict(_VIEWER_EDITOR_LANE_VIEWER_LABELS),
        "colors": dict(_VIEWER_EDITOR_LANE_COLORS),
        "purposes": dict(_VIEWER_EDITOR_LANE_PURPOSES),
    }, report_video_no=str(vod_info.video_no or ""), evidence_video_no=evidence_video_no)

def _viewer_editor_subtitles_from_srt(srt_path: str, *, max_rows: int = 900) -> list[dict[str, Any]]:
    if not srt_path:
        return []
    try:
        from .chunker import parse_srt

        cues = parse_srt(srt_path)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for idx, cue in enumerate(cues[:max_rows], start=1):
        start = getattr(cue, "start", None)
        end = getattr(cue, "end", None)
        text = getattr(cue, "text", None)
        if start is None and getattr(cue, "start_ms", None) is not None:
            start = float(getattr(cue, "start_ms")) / 1000.0
        if end is None and getattr(cue, "end_ms", None) is not None:
            end = float(getattr(cue, "end_ms")) / 1000.0
        if text is None and getattr(cue, "text_lines", None) is not None:
            text = " ".join(str(line).strip() for line in getattr(cue, "text_lines") if str(line).strip())
        if isinstance(cue, dict):
            start = cue.get("start_sec", cue.get("start", start))
            end = cue.get("end_sec", cue.get("end", end))
            text = cue.get("text", text)
        try:
            start_sec = float(start)
        except (TypeError, ValueError):
            continue
        try:
            end_sec = float(end)
        except (TypeError, ValueError):
            end_sec = start_sec
        text_value = _viewer_editor_clip_subtitle_text(text, 220)
        if not text_value:
            continue
        rows.append(
            {
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "text": text_value,
                "index": idx,
            }
        )
    return rows

def _viewer_editor_clip_subtitle_text(value: Any, max_chars: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"

def _ensure_viewer_editor_required_lanes(
    lanes: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_counts = _viewer_editor_event_counts_by_kind(events)
    result = [
        dict(row)
        for row in lanes
        if isinstance(row, dict)
        and row.get("key")
        and not _viewer_editor_should_hide_scene_lane(str(row.get("key") or ""), event_counts)
    ]
    seen = {str(row.get("key") or "") for row in result}
    for key in (
        "existing_segments",
        "audio_waveform",
        "chat_volume",
        "chat_laughter",
        "chat_surprise",
        "comment_replay",
        "viewer_clip",
        "live_context",
        "emoticon_reaction",
        "audio_peak",
        "subtitle_density",
        "highlight",
    ):
        if _viewer_editor_should_hide_scene_lane(key, event_counts):
            continue
        if key in seen:
            continue
        event_count = len([event for event in events if event.get("kind") == key or event.get("lane") == key])
        lane = {
            "key": key,
            "label": _VIEWER_EDITOR_LANE_LABELS.get(key, key),
            "event_count": event_count,
            "display_role": "overview" if key in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS else "scene",
        }
        if event_count <= 0:
            lane["status"] = "empty"
            lane["empty_reason"] = "No signal data is available for this lane."
        result.append(lane)
        seen.add(key)
    return result

def _reconcile_viewer_editor_overview_lanes(payload: dict[str, Any]) -> dict[str, Any]:
    """Overview data can exist without discrete scene events."""
    waveform = payload.get("audio_waveform")
    samples = waveform.get("samples") if isinstance(waveform, dict) else None
    has_waveform = isinstance(samples, list) and bool(samples)
    buckets = payload.get("chat_buckets")
    has_chat_volume = isinstance(buckets, list) and any(isinstance(row, dict) for row in buckets)
    events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
    lanes = []
    for row in payload.get("lanes") or []:
        if not isinstance(row, dict):
            continue
        lane = dict(row)
        key = str(lane.get("key") or "")
        if key in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS:
            data_present = has_waveform if key == "audio_waveform" else has_chat_volume
            event_count = sum(
                str(event.get("kind") or "") == key or str(event.get("lane") or "") == key
                for event in events
            )
            status = str(lane.get("status") or "").lower()
            if data_present or event_count:
                lane["event_count"] = max(event_count, int(data_present))
                if status in {"", "empty", "present"}:
                    lane["status"] = "present"
                    lane.pop("empty_reason", None)
            elif status in {"", "present"}:
                lane["event_count"] = 0
                lane["status"] = "empty"
                lane.setdefault("empty_reason", "No signal data is available for this lane.")
        lanes.append(lane)
    return {**payload, "lanes": lanes}

def _project_public_viewer_editor_tools_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the full editor workbench into the public report contract.

    Report generation and Report Admin keep the complete signal workbench.
    Public reports retain the already-computed cluster times and totals, while
    private event rows, labels and member references stay in Report Admin.
    """

    from ..edit_point_clusters import build_edit_point_clusters
    from ..scene_signal_contract import lane_definitions, manager_only_lane_keys

    if not isinstance(payload, dict):
        return payload
    clusters = payload.get("edit_point_clusters")
    if not isinstance(clusters, dict):
        clusters = build_edit_point_clusters(
            [row for row in (payload.get("events") or []) if isinstance(row, dict)],
            duration_sec=payload.get("duration_sec"),
        )
    hidden = manager_only_lane_keys()
    hidden_labels = {
        str(row.get(field) or "").strip()
        for row in lane_definitions()
        if str(row.get("key") or "") in hidden
        for field in ("label", "viewer_label")
        if str(row.get(field) or "").strip()
    }
    signal_label_fields = {"kind_label", "label", "lane_label", "title", "viewer_label"}
    projected = copy.deepcopy(payload)
    if "collaboration_replays" in projected:
        from ..collaboration_replays import normalize_public_replays
        projected["collaboration_replays"] = normalize_public_replays(
            projected["collaboration_replays"], video_no=projected.get("video_no"))
    if "collaboration_candidates" in projected:
        from ..collaboration_candidates import public_collaboration_candidates
        projected["collaboration_candidates"] = public_collaboration_candidates(
            projected["collaboration_candidates"])
    if "collaboration_periods" in projected:
        from ..chatgpt_export.contract.collaboration_periods import public_collaboration_periods
        projected["collaboration_periods"] = public_collaboration_periods(
            projected["collaboration_periods"], projected.get("duration_sec")
        )
    events = [
        row
        for row in (projected.get("events") or [])
        if isinstance(row, dict)
        and str(row.get("kind") or "").strip() not in hidden
        and str(row.get("lane") or "").strip() not in hidden
    ]
    lanes = [
        row
        for row in (projected.get("lanes") or [])
        if isinstance(row, dict)
        and str(row.get("key") or "").strip() not in hidden
    ]
    projected["events"] = events
    projected["lanes"] = lanes
    for map_key in ("labels", "viewer_labels", "colors", "purposes"):
        values = projected.get(map_key)
        if isinstance(values, dict):
            projected[map_key] = {
                key: value
                for key, value in values.items()
                if str(key).strip() not in hidden
            }

    def strip_hidden_refs(item: Any) -> Any:
        if isinstance(item, dict):
            cleaned: dict[str, Any] = {}
            for key, child in item.items():
                key_text = str(key).strip()
                if key_text in hidden:
                    continue
                if isinstance(child, str) and child.strip() in hidden:
                    continue
                if key_text in signal_label_fields and isinstance(child, str) and child.strip() in hidden_labels:
                    continue
                cleaned[str(key)] = strip_hidden_refs(child)
            return cleaned
        if isinstance(item, list):
            return [
                strip_hidden_refs(child)
                for child in item
                if not (isinstance(child, str) and child.strip() in hidden)
            ]
        return item

    projected = strip_hidden_refs(projected)

    projected["chat_buckets"] = [
        {key: value for key, value in row.items()
         if key not in {"reaction_count", "laughter_count", "surprise_count", "hot"}}
        for row in (projected.get("chat_buckets") or [])
        if isinstance(row, dict)
    ]
    events = [row for row in (projected.get("events") or []) if isinstance(row, dict)]
    lanes = [row for row in (projected.get("lanes") or []) if isinstance(row, dict)]
    public_ids = {str(row.get("id") or row.get("event_id")) for row in events if row.get("id") or row.get("event_id")}
    source_events = {
        str(row.get("id") or row.get("event_id") or ""): row
        for row in (payload.get("events") or []) if isinstance(row, dict)
    }
    aggregate_keys = (
        "cluster_id", "start_sec", "end_sec", "timecode", "signal_count",
        "family_count", "viewer_clip_count", "confidence", "overflow_event_count",
        "video_no", "source_video_no", "seek_sec", "end_seek_sec", "vod_label",
    )
    public_clusters = []
    for row in (clusters.get("clusters") or []):
        if not isinstance(row, dict):
            continue
        aggregate = {key: copy.deepcopy(row[key]) for key in aggregate_keys if key in row}


        for event_id in row.get("event_ids") or []:
            source = source_events.get(str(event_id), {})
            for key in ("video_no", "source_video_no", "seek_sec", "end_seek_sec", "vod_label"):
                if key not in aggregate and source.get(key) is not None:
                    aggregate[key] = copy.deepcopy(source[key])
            if source:
                break
        aggregate["signal_families"] = [key for key in (row.get("signal_families") or []) if key not in hidden]
        aggregate["event_ids"] = [key for key in (row.get("event_ids") or []) if str(key) in public_ids]
        aggregate["representative_events"] = [
            copy.deepcopy(member) for member in (row.get("representative_events") or [])
            if isinstance(member, dict) and str(member.get("event_id") or member.get("id") or "") in public_ids
        ]
        public_clusters.append(aggregate)
    projected["edit_point_clusters"] = {
        **{key: copy.deepcopy(clusters[key]) for key in (
            "schema_version", "window_sec", "duration_sec", "original_cluster_count", "cluster_count",
        ) if key in clusters},
        "clusters": public_clusters,
    }
    summary = dict(projected.get("summary") or {}) if isinstance(projected.get("summary"), dict) else {}
    summary["event_count"] = len(events)
    summary["lane_count"] = len(lanes)
    projected["summary"] = summary
    return _reconcile_viewer_editor_overview_lanes(projected)

def _dedupe_viewer_editor_viewer_clip_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate clip anchors while preserving distinct same-time clips."""

    def title_key(event: dict[str, Any]) -> str:
        return " ".join(str(event.get("title") or "").strip().casefold().split())

    def is_anchor_event(event: dict[str, Any]) -> bool:
        if str(event.get("source_identity_kind") or "") == "deterministic_viewer_clip_anchor":
            return True
        return any(
            isinstance(item, dict) and str(item.get("label") or "") == "viewer_clip_anchor"
            for item in (event.get("evidence") or [])
        )

    def is_public_clip_event(event: dict[str, Any]) -> bool:
        if str(event.get("source_identity_kind") or "") == "public_viewer_clip_uid":
            return True
        return event.get("kind") == "viewer_clip" and not is_anchor_event(event)

    public_clip_keys: dict[tuple[str, str], set[str]] = {}
    for event in events:
        if event.get("kind") != "viewer_clip" or not is_public_clip_event(event):
            continue
        event_offset = str(event.get("seek_sec") or event.get("start_sec") or "").strip()
        key = title_key(event)
        identity_key = str(event.get("source_identity_key") or "").strip()
        if event_offset and key:
            public_clip_keys.setdefault((event_offset, key), set()).add(identity_key)

    deduped: list[dict[str, Any]] = []
    seen_identity: set[tuple[str, str]] = set()
    for event in events:
        if event.get("kind") != "viewer_clip":
            deduped.append(event)
            continue
        identity_key = str(event.get("source_identity_key") or "").strip()
        event_offset = str(event.get("seek_sec") or event.get("start_sec") or "").strip()
        if identity_key:
            dedupe_key = (identity_key, event_offset)
            if dedupe_key in seen_identity:
                continue
            seen_identity.add(dedupe_key)
        if is_anchor_event(event) and event_offset:
            public_identities = public_clip_keys.get((event_offset, title_key(event)), set())
            anchor_identity = str(event.get("source_identity_key") or "").strip()
            if anchor_identity and any(identity and (identity in anchor_identity or anchor_identity in identity) for identity in public_identities):
                continue
        deduped.append(event)
    return deduped

def _build_viewer_editor_tools_payload_from_workbench(
    workbench: dict[str, Any],
    *,
    edit_export_preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..edit_point_clusters import build_edit_point_clusters

    events = [
        event for event in (
            _viewer_editor_event_from_workbench(row)
            for row in (workbench.get("events") or [])
        )
        if event is not None
    ]
    events.sort(key=lambda item: (item["start_sec"], item.get("end_sec") or item["start_sec"], item["id"]))
    context = workbench.get("context_index") if isinstance(workbench.get("context_index"), dict) else {}
    raw_buckets = [row for row in (context.get("chat_buckets") or []) if isinstance(row, dict)]
    raw_subtitles = [row for row in (context.get("subtitles") or []) if isinstance(row, dict)]
    total_chats = 0
    for bucket in raw_buckets:
        try:
            total_chats += int(bucket.get("count") or 0)
        except (TypeError, ValueError):
            continue
    audio_waveform = _thin_viewer_editor_audio_waveform(workbench.get("audio_waveform"))
    event_counts = _viewer_editor_event_counts_by_kind(events)
    lanes = []
    for row in (workbench.get("lanes") or []):
        if not isinstance(row, dict) or not row.get("key"):
            continue
        key = str(row.get("key") or "")
        if _viewer_editor_should_hide_scene_lane(key, event_counts):
            continue
        lane = {
            "key": key,
            "label": str(_VIEWER_EDITOR_LANE_VIEWER_LABELS.get(key) or row.get("label") or _VIEWER_EDITOR_LANE_LABELS.get(key) or row.get("key") or ""),
            "event_count": len([event for event in events if event["kind"] == key])
            if key not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
            else int(row.get("event_count") or 0),
            "display_role": "overview" if key in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS else "scene",
        }
        for optional_count_key in ("distinct_source_count", "distinct_engagement_source_count"):
            if row.get(optional_count_key) not in (None, ""):
                lane[optional_count_key] = int(row.get(optional_count_key) or 0)
        for optional_key in ("status", "empty_reason", "reason", "message"):
            if row.get(optional_key) not in (None, ""):
                lane[optional_key] = str(row.get(optional_key))
        lanes.append(lane)
    seen_lane_keys = {row["key"] for row in lanes}
    for key in (
        "existing_segments",
        "audio_waveform",
        "chat_volume",
        "chat_laughter",
        "chat_surprise",
        "comment_replay",
        "viewer_clip",
        "live_context",
        "emoticon_reaction",
        "audio_peak",
        "subtitle_density",
        "highlight",
    ):
        if key not in seen_lane_keys and not _viewer_editor_should_hide_scene_lane(key, event_counts):
            lanes.append(
                {
                    "key": key,
                    "label": _VIEWER_EDITOR_LANE_VIEWER_LABELS.get(key, _VIEWER_EDITOR_LANE_LABELS.get(key, key)),
                    "event_count": len([event for event in events if event["kind"] == key]),
                    "display_role": "overview" if key in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS else "scene",
                    "status": "empty",
                    "empty_reason": "No signal data is available for this lane.",
                }
            )
    active_lane_keys = {str(lane.get("key") or "") for lane in lanes if isinstance(lane, dict) and lane.get("key")}
    active_event_keys = {str(event.get("kind") or "") for event in events if isinstance(event, dict) and event.get("kind")}
    active_keys = active_lane_keys | active_event_keys | set(_VIEWER_EDITOR_OVERVIEW_LANE_KEYS)

    def active_map(values: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in values.items() if key in active_keys}

    return _reconcile_viewer_editor_overview_lanes({
        "schema_version": "viewer_editor_tools.v1",
        "video_no": str(workbench.get("video_no") or ""),
        "duration_sec": int(float(workbench.get("duration_sec") or 0)),
        "events": _viewer_editor_limit_events_preserving_viewer_clips(events),
        "edit_point_clusters": build_edit_point_clusters(
            _viewer_editor_limit_events_preserving_viewer_clips(events),
            duration_sec=int(float(workbench.get("duration_sec") or 0)),
        ),
        "chat_buckets": _thin_viewer_editor_chat_buckets(raw_buckets),
        "subtitles": _thin_viewer_editor_subtitles(raw_subtitles),
        "stats": {
            "total_chats": total_chats,
        },
        "summary": {
            **(workbench.get("summary") if isinstance(workbench.get("summary"), dict) else {}),
            "event_count": len(events),
            "lane_count": len(lanes),
            "chat_bucket_count": len(raw_buckets),
            "subtitle_context_count": len(raw_subtitles),
            "total_chats": total_chats,
        },
        **({"audio_waveform": audio_waveform} if audio_waveform else {}),
        "outline_projection": copy.deepcopy(
            workbench.get("outline_projection")
            if isinstance(workbench.get("outline_projection"), list)
            else []
        ),
        "edit_export_preview": edit_export_preview or {},
        "lanes": lanes,
        "labels": active_map(_VIEWER_EDITOR_LANE_LABELS),
        "viewer_labels": active_map(_VIEWER_EDITOR_LANE_VIEWER_LABELS),
        "colors": active_map(_VIEWER_EDITOR_LANE_COLORS),
        "purposes": active_map(_VIEWER_EDITOR_LANE_PURPOSES),
        "lazy_render": True,
    })

def _audio_peak_badge_html(tc_text: str, audio_peak_secs: list[float], tolerance_sec: int = 30) -> str:
    delta_text = _audio_peak_delta_text(tc_text, audio_peak_secs, tolerance_sec=tolerance_sec)
    if not delta_text:
        return ""
    return (
        '<span class="audio-peak-badge" '
        f'title="오디오 반응 피크와 가까운 타임라인 항목 ({delta_text})">'
        f'🔊 오디오 피크 {delta_text}</span>'
    )

def _build_chzzk_vod_url(video_no: str = "") -> str:
    video_no = str(video_no or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", video_no):
        return ""
    return f"https://chzzk.naver.com/video/{video_no}"

def _build_source_vod_url(vod_info: VODInfo) -> str:
    source_id = _source_id(vod_info)
    if not source_id:
        return ""
    if _source_platform(vod_info) == "youtube":
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", source_id):
            return ""
        return f"https://www.youtube.com/watch?v={source_id}"
    return _build_chzzk_vod_url(source_id)

def _build_user_clips_block(
    bundle: Optional[UserClipsBundle],
    vod_info: VODInfo,
) -> str:
    """viewer-clips report 섹션 (옵션 B shared helper).

    `_generate_html()`, `scripts/report_admin_server.py:_render_blocks`,
    `scripts/refresh_reports.py:patch_html` 셋이 모두 본 helper 를 호출 — drift 차단.

    검증된 VOD anchor와 같은 채널·시간창의 미확인 클립을 표시한다.
    미확인 클립은 원래 클립만 열고 VOD 시각 링크를 만들지 않는다.
    열람 가능한 클립이 없으면 빈 섹션은 만들지 않는다.

    bundle 출처 (3 진입점 동일 약속):
    - pipeline.main: result.user_clips (PipelineResult.user_clips)
    - admin/refresh: pipeline.chzzk_user_clips.load_chzzk_user_clips(
        video_no, work_dir, cache_ttl_hours=0)
    """

    if bundle is None:
        return ""
    title = "🎯 시청자 클립"
    clips = list(bundle.clips or [])
    matched = [clip for clip in clips if is_eligible_in_vod_clip(clip, vod_info.video_no)]
    unanchored = [clip for clip in clips
        if not is_eligible_in_vod_clip(clip, vod_info.video_no)
        and str(bundle.video_no) == str(vod_info.video_no)
        and bool(vod_info.channel_id) and bundle.channel_id == vod_info.channel_id
        and clip.owner_channel_id == vod_info.channel_id
        and clip.creation_window_status == "in_window"
        and clip.offset_status not in {"ok", "wrong_vod"}
        and str(clip.video_no or "") in {"", str(vod_info.video_no)}
        and clip.manual_override != "ignore"]
    displayed = matched + unanchored
    displayed.sort(key=lambda c: (
        -(c.like_count or 0), -(c.play_count or 0), c.offset_sec or 0, c.clip_uid or "",
    ))
    if not displayed:
        return ""

    cards = "\n".join(_render_user_clip_card(c, vod_info) for c in displayed)
    summary_line = (
        f'<p class="user-clips-summary">'
        + (f'시청자 클립 {len(displayed)}개 · VOD 시점 확인 {len(matched)}개 · VOD 시각 미확인 {len(unanchored)}개'
           if unanchored else f'이 VOD 시점 매칭 시청자 클립 {len(matched)}개') + '</p>'
    )



    health_badge = _render_user_clips_source_health_badge(
        _compute_user_clips_source_health(clips)
    )
    body = summary_line + health_badge + f'<div class="user-clips-grid">{cards}</div>'
    return _wrap_user_clips_card(title, body)

def _build_viewer_editor_tools_html(
    payload: dict[str, Any],
    *,
    public_projection: bool = True,
) -> str:
    if public_projection:
        payload = _project_public_viewer_editor_tools_payload(payload)
    event_count = len([
        row for row in (payload.get("events") or [])
        if isinstance(row, dict)
        and str(row.get("kind") or "") not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
    ])
    clip_count = int((payload.get("edit_export_preview") or {}).get("clip_count") or 0)
    scene_event_kinds = {
        str(row.get("kind") or "")
        for row in (payload.get("events") or [])
        if isinstance(row, dict) and str(row.get("kind") or "")
    }
    lane_count = len(
        [
            row for row in (payload.get("lanes") or [])
            if isinstance(row, dict)
            and str(row.get("key") or "") not in _VIEWER_EDITOR_OVERVIEW_LANE_KEYS
            and (
                str(row.get("key") or "") in scene_event_kinds
                or int(row.get("event_count") or 0) > 0
            )
        ]
    )
    display_payload = payload
    if payload.get("outline_projection"):
        display_rows = []
        for row in payload["outline_projection"]:
            image = row.get("image") if isinstance(row, dict) else None
            if isinstance(image, dict) and image.get("data_url"):
                row = {**row, "image": {**image, "data_url": editorial_scene_image_data_url(image["data_url"])}}
            display_rows.append(row)

        display_payload = {**payload, "outline_projection": display_rows}
    data_json = _viewer_editor_json(display_payload)
    return f'''
<div class="card viewer-editor-tools" id="youtube-editor-tools" data-report-block="viewer-editor-tools" data-viewer-editor-render-version="{VIEWER_EDITOR_TOOLS_RENDER_VERSION}">
  <div class="viewer-editor-head">
    <div>
      <h2 class="viewer-editor-title">편집자 워크스페이스</h2>
      <p class="viewer-editor-lead">유튜브 편집자가 훑어볼 만한 시각을 한곳에 모았습니다. 시간축에서 후보를 고르면 바로 아래에 자막, 요약, 채팅, 클립 근거가 이어서 표시됩니다.</p>
    </div>
  </div>
  <div class="viewer-editor-grid">
    <section class="viewer-editor-panel" aria-labelledby="viewerEditorSceneTitle">
      <h3 id="viewerEditorSceneTitle">1. 장면 찾기</h3>
      <p>요약에 이미 들어간 장면과 시청자 반응, 클립, 소리, 자막 신호를 같은 시간축에서 비교합니다.</p>
      <div class="viewer-editor-filter">
        <label for="viewerEditorFilter">자료 필터</label>
        <select id="viewerEditorFilter"></select>
        <span id="viewerEditorCount">{lane_count}개 자료 · {event_count}개 장면 · {clip_count}개 컷 후보</span>
      </div>
      <div class="viewer-editor-density-control">
        <label for="viewerEditorDensityRange">알갱이 표시 밀도</label>
        <input id="viewerEditorDensityRange" type="range" min="0" max="10" step="1" value="6" aria-label="장면 찾기 알갱이 표시 밀도">
        <span id="viewerEditorDensityValue" class="viewer-editor-density-value">핵심</span>
      </div>
      <div class="viewer-editor-zoom" aria-label="장면 찾기 확대 축소">
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomOutBtn" title="시간축 축소" aria-label="시간축 축소">-</button>
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomInBtn" title="시간축 확대" aria-label="시간축 확대">+</button>
        <button type="button" class="viewer-editor-zoom-btn" id="viewerEditorZoomResetBtn" title="시간축 기본 배율">기본</button>
        <input id="viewerEditorZoomRange" type="range" min="10" max="260" step="5" value="100" aria-label="시간축 확대 배율">
        <span id="viewerEditorZoomValue" class="viewer-editor-zoom-value">1.00x</span>
        <span>전체 보기 0.10x · Ctrl+휠 확대/축소</span>
      </div>
      <div class="viewer-editor-axis" id="viewerEditorAxis" aria-label="유튜브 편집자용 전체 영상 장면 찾기"></div>
    </section>
    <section class="viewer-editor-panel evidence-panel" aria-labelledby="viewerEditorEvidenceTitle">
      <h3 id="viewerEditorEvidenceTitle">2. 선택한 시각의 근거</h3>
      <p id="viewerEditorEvidenceIntro">시간축에서 항목을 선택하면 근거와 방송 맥락이 표시됩니다.</p>
      <div id="viewerEditorEvidence"></div>
    </section>
  </div>
  <script id="viewerEditorToolsData" type="application/json">{data_json}</script>
</div>'''

def _chartjs_script_tag() -> str:
    """Chart.js 를 HTML 에 주입하는 <script> 태그를 생성.

    publish/web/assets/vendor/chart.umd.min.js 를 인라인으로 임베드해
    output/*.html 직접 열기, site/vods/<id>/report.html iframe 로딩, pages.dev
    퍼블리시 3개 환경 모두에서 외부 네트워크 없이 차트가 동작하도록 한다.
    에셋 로드 실패 시에만 기존 relative path 로 폴백."""
    content = _load_chartjs_inline()
    if content:
        return f"<script>{content}</script>"
    return '<script src="../../assets/vendor/chart.umd.min.js"></script>'

def _compute_user_clips_source_health(
    clips: list,
    *,
    ref_now: Optional[datetime] = None,
) -> dict:
    'T4 P3.4(b) — 시청자 클립 번들의 source health 3 aggregate.'
    if ref_now is None:
        ref_now = datetime.now(timezone.utc)

    def _is_wrong_vod(c) -> bool:
        return (getattr(c, "offset_status", "") or "") == "wrong_vod"

    wrong_vod_count = sum(1 for c in clips if _is_wrong_vod(c))
    eng_ok = sum(
        1 for c in clips
        if not _is_wrong_vod(c)
        and (getattr(c, "engagement_status", "") or "") == "ok"
    )
    eng_parse_fail = sum(
        1 for c in clips
        if not _is_wrong_vod(c)
        and (getattr(c, "engagement_status", "") or "") == "parse_failure"
    )
    eng_attempted = eng_ok + eng_parse_fail
    parse_rate: Optional[float] = (eng_ok / eng_attempted) if eng_attempted else None

    age_hours: Optional[float] = None
    for c in clips:
        if _is_wrong_vod(c):
            continue
        if (getattr(c, "engagement_status", "") or "") != "ok":
            continue
        fetched_at_raw = getattr(c, "engagement_fetched_at", None)
        if not fetched_at_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(fetched_at_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        delta_hours = max(0.0, (ref_now - ts).total_seconds() / 3600.0)
        age_hours = delta_hours if age_hours is None else max(age_hours, delta_hours)

    return {
        "wrong_vod_count": wrong_vod_count,
        "parse_rate": parse_rate,
        "age_hours": age_hours,
    }

def _format_age_hours_label(age_hours: float) -> str:
    """비개발자용 한국어 라벨 — "방금 수집" / "N시간 전" / "N일 전"."""
    if age_hours < 1:
        return "방금 수집"
    if age_hours < 24:
        return f"{int(age_hours)}시간 전"
    return f"{int(age_hours // 24)}일 전"

def _generate_youtube_html(summary_md: str, vod_info: VODInfo) -> str:
    """Render a source-safe YouTube report without CHZZ publish/editor controls."""
    source_url = _build_source_vod_url(vod_info)
    escaped = _html_escape(summary_md)
    escaped = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", escaped, flags=re.M)
    escaped = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", escaped, flags=re.M)
    escaped = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", escaped, flags=re.M)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(
        r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        escaped,
    )
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>\n")
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_escape(vod_info.title)} — YouTube 방송 분석 리포트</title>
<style>
body{{margin:0;background:#111827;color:#e5e7eb;font-family:system-ui,sans-serif;line-height:1.7}}
main{{max-width:900px;margin:0 auto;padding:32px 20px 64px}}
.source{{padding:16px;border:1px solid #374151;border-radius:12px;background:#1f2937}}
a{{color:#60a5fa;word-break:break-all}} article{{margin-top:24px;padding:24px;background:#1f2937;border-radius:12px}}
h1,h2,h3{{line-height:1.3;color:#f9fafb}} .muted{{color:#9ca3af}}
</style>
</head>
<body><main>
<header><h1>{_html_escape(vod_info.title)}</h1><p class="muted">{_html_escape(vod_info.channel_name)}</p>
<p class="source"><strong>원본:</strong> <a href="{_html_escape(source_url)}" target="_blank" rel="noopener">{_html_escape(source_url)}</a></p></header>
<article><p>{escaped}</p></article>
</main></body></html>"""

def _is_promoted_for_badge_v1(clip) -> bool:
    """anchored in-VOD clip 이 promoted 임계를 통과했는지 (badge 전용).

    `_render_user_clip_card` 만 호출. T2 가 머지되면 T2 의 selection helper 로
    교체. 본 함수는 T1/T2 와 병행 작성을 위해 v1 spec 의 floor 만 단독 구현.

    Promotion 조건 (anchored 만 — unanchored 는 별도 channel-popularity signal):
      - engagement_status == "ok"
      - play_count >= lower_play_min (300)  ← `_BADGE_UPPER_ESCAPE_PLAY_V1=1000`
        은 lower_play_min 의 superset 이므로 OR 가 collapse 됨. T2 가 top-K cap
        을 적용해 strict-set 을 좁히면 본 predicate 도 cap 반영하도록 refactor.
    """
    if (getattr(clip, "engagement_status", "") or "") != "ok":
        return False
    play = getattr(clip, "play_count", None)
    if not isinstance(play, (int, float)) or play < 0:
        return False
    return int(play) >= _BADGE_LOWER_PLAY_MIN_V1

def _load_chartjs_inline() -> str:
    """Chart.js UMD 파일 내용을 읽어 inline <script> 에 넣을 문자열로 반환.
    파일이 없으면 빈 문자열 — 이 경우 _chartjs_script_tag() 가 relative path
    fallback 을 쓴다."""
    global _CHARTJS_INLINE_CACHE
    if _CHARTJS_INLINE_CACHE is not None:
        return _CHARTJS_INLINE_CACHE
    try:
        _CHARTJS_INLINE_CACHE = _CHARTJS_ASSET_PATH.read_text(encoding="utf-8")
    except OSError:
        _CHARTJS_INLINE_CACHE = ""
    return _CHARTJS_INLINE_CACHE

def _normalize_viewer_clip_title(title: object) -> tuple[str, bool]:
    """Promote a legacy literal title marker into a typed viewer-clip claim."""
    raw_title = str(title or "")
    viewer_clip = bool(_VIEWER_CLIP_TITLE_MARKER_RE.search(raw_title))
    clean_title = _VIEWER_CLIP_TITLE_MARKER_RE.sub(" ", raw_title).strip()
    return clean_title, viewer_clip

def _prepend_summary_webpage_line(text: str, summary_url: str = "") -> str:
    if not summary_url:
        return text
    prefix = "🔗 요약 웹페이지:"
    lines = [
        line for line in (text or "").splitlines()
        if not line.strip().startswith(prefix)
    ]
    return "\n".join([f"{prefix} {summary_url}", *lines])

def _render_chzzk_comment_text(
    sec: dict,
    public_url_base: str = "",
    video_no: str = "",
    *,
    structured_outline: dict | None = None,
    editorial_review_packet: dict | None = None,
) -> str:
    '치지직 다시보기 댓글용 plain text — 챕터 우선 + 상세 시간순 병합.\n\n    챕터 인덱스를 우선 배치하고, 상세 타임라인은 챕터와\n    2분(120초) 이내로 겹치지 않는 항목만 포함.\n    '
    from ..timeline_alignment import hms_to_sec

    if isinstance(structured_outline, dict):
        lines: list[str] = []
        if isinstance(editorial_review_packet, dict):
            view = _validate_editorial_review_projection(editorial_review_packet)

            for highlight in view["highlights"]:
                title = " ".join(_strip_inline_md(highlight["title"]).split())
                for span in highlight["source_spans"]:
                    lines.append(f'- {sec_to_hms(span["start_sec"])} ~ '
                                 f'{sec_to_hms(span["end_sec"])} [H] {title}')
        rows = []
        for row in structured_outline.get("ranges") or []:
            if row.get("level") not in {"D1", "D2"}:
                continue
            depth = 1 if row["level"] == "D1" else 2
            title = " ".join(_strip_inline_md(row["title"]).split())
            rows.append((hms_to_sec(row["start"]), depth,
                         f'{"#" * depth} {row["start"]} ~ {row["end"]} {title}'))
        for row in structured_outline.get("points") or []:
            title = " ".join(_strip_inline_md(row["title"]).split())
            rows.append((hms_to_sec(row["timestamp"]), 3,
                         f'- {row["timestamp"]} {title}'))
        if lines and rows:
            lines.append("")
        lines.extend(text for _, _, text in sorted(rows, key=lambda row: row[:2]))
        return _prepend_summary_webpage_line(
            "\n".join(lines), _build_public_vod_url(public_url_base, video_no))

    merged: dict[str, str] = {}
    chapter_secs: list[int] = []

    for item in sec.get("chzzk_comment_timeline") or []:
        tc = (item.get("tc") or "").strip()
        title = _strip_timestamp_confidence_markers(item.get("title") or "")
        if not tc or not title:
            continue
        tc_end = (item.get("tc_end") or "").strip()
        label = f"[{tc} ~ {tc_end}] {title}" if tc_end else f"[{tc}] {title}"
        merged[tc] = label
        try:
            chapter_secs.append(hms_to_sec(tc))
        except (ValueError, TypeError):
            pass

    for item in sec.get("timeline") or []:
        tc = (item.get("tc") or "").strip()
        title = _strip_timestamp_confidence_markers(_strip_inline_md(item.get("title") or ""))
        if not tc or not title:
            continue
        tc_start = tc.split("~")[0].strip() if "~" in tc else tc
        try:
            tl_sec = hms_to_sec(tc_start)
        except (ValueError, TypeError):
            continue
        if any(abs(tl_sec - cs) <= 120 for cs in chapter_secs):
            continue
        mood_raw = (item.get("mood_raw") or "").strip()
        line = f"[{tc}] {title} (분위기: {mood_raw})" if mood_raw else f"[{tc}] {title}"
        merged[tc] = line

    body = "\n".join(line for _, line in sorted(merged.items())) if merged else ""
    return _prepend_summary_webpage_line(
        body,
        _build_public_vod_url(public_url_base, video_no),
    )

def _render_community_reaction_md(md_fragment: str) -> str:
    escaped = _html_escape(md_fragment)
    escaped = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", escaped, flags=re.M)
    escaped = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", escaped, flags=re.M)
    escaped = re.sub(r"^- (.+)$", r"<p>• \1</p>", escaped, flags=re.M)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("\n\n", "</p><p>")
    return f"<div>{escaped}</div>"

def _render_tc_anchor_html(video_no: str, tc_text: str, css_class: str = "tc") -> str:
    """`<a class="tc" href="chzzk.../currentTime=...">tc</a>` 또는 link 없을 때 `<span>`.

    css_class 는 시각적 스타일을 그대로 유지하기 위해 동일하게 적용.
    inline style 로 underline 제거 — 백필되는 구 리포트(.tc CSS rule 만 있고
    a.tc rule 없는 HTML)에서도 모양 깨지지 않게.
    """
    safe_text = _html_escape(tc_text or "")
    url = _chzzk_video_url_at(video_no, tc_text or "")
    if not url:
        return f'<span class="{css_class}">{safe_text}</span>'
    return (
        f'<a class="{css_class}" href="{_html_escape(url)}" '
        f'target="_blank" rel="noopener" '
        f'style="text-decoration:none" '
        f'title="치지직 다시보기에서 이 시점부터 재생">{safe_text}</a>'
    )

def _render_top_vod_link_html(video_no: str = "") -> str:
    url = _build_chzzk_vod_url(video_no)
    if not url:
        return ""
    safe_url = _html_escape(url)
    return (
        '<span class="top-vod-link-row">'
        f'<a class="top-vod-link" href="{safe_url}" target="_blank" rel="noopener" '
        'title="치지직에서 원본 방송을 새 탭으로 엽니다">원본 방송 보기</a>'
        '</span>'
    )

def _render_user_clip_card(clip, vod_info: VODInfo, source_label: str = "") -> str:
    title_html = _html_escape(clip.title or "(제목 없음)")
    source_label_html = ""
    if source_label:
        source_label_html = f'<span class="user-clip-source">{_html_escape(source_label)}</span>'
    thumb_html = ""
    safe_thumb = _safe_thumb_url(clip.thumbnail_url or "")
    if safe_thumb:
        thumb_html = (
            f'<img class="user-clip-thumb" loading="lazy" '
            f'alt="" src="{_html_escape(safe_thumb)}">'
        )
    tc_html = ""
    if clip.offset_status == "ok" and clip.video_no == vod_info.video_no and clip.offset_sec is not None:
        tc_text = sec_to_hms(int(clip.offset_sec))
        tc_html = _render_tc_anchor_html(vod_info.video_no, tc_text, css_class="tc user-clip-tc")
    elif clip.offset_status != "ok":
        tc_html = '<span class="user-clip-tc-missing">시점 매칭 실패</span>'

    engagement_parts: list[str] = []
    if clip.engagement_status == "ok":
        if clip.like_count is not None:
            engagement_parts.append(f"♥ {clip.like_count:,}")
        if clip.play_count is not None:
            engagement_parts.append(f"▶ {clip.play_count:,}")
    elif clip.read_count:
        engagement_parts.append(f"▶ {clip.read_count:,}")
    engagement_html = (
        f'<div class="user-clip-meta">{" · ".join(engagement_parts)}</div>'
        if engagement_parts else ""
    )

    badge_html = ""
    if _is_promoted_for_badge_v1(clip):
        badge_html = (
            '<span class="user-clip-badge-promoted" '
            'title="조회수가 충분해 시청자 반응이 강한 클립">🔥 핫 클립</span>'
        )

    clip_id_html = _html_escape(clip.clip_uid)
    clip_url = f"https://chzzk.naver.com/clips/{clip_id_html}"
    return (
        f'<div class="user-clip-card">\n'
        f'  <div class="user-clip-media">\n'
        f'    <button class="user-clip-play" type="button" data-clip-id="{clip_id_html}" '
        f'data-clip-title="{title_html}" aria-label="{title_html} 바로 재생">\n'
        f'      {thumb_html}\n'
        f'      <span class="user-clip-play-label" aria-hidden="true">▶ 바로 재생</span>\n'
        f'    </button>\n'
        f'  </div>\n'
        f'  <div class="user-clip-body">\n'
        f'    {source_label_html}\n'
        f'    <a class="user-clip-title-link" href="{clip_url}" target="_blank" rel="noopener">\n'
        f'      <div class="user-clip-title">{title_html}</div>\n'
        f'    </a>\n'
        f'    {badge_html}\n'
        f'    <div class="user-clip-foot">{tc_html}{engagement_html}</div>\n'
        f'  </div>\n'
        f'</div>'
    )

def _render_user_clips_source_health_badge(stats: dict) -> str:
    """3 chip — wrong_vod count / 데이터 신선도 / 회수 성공률.

    chip 은 의미 있는 신호가 있을 때만 표시 (None / 0 시 hide). 모든 chip
    빈 경우 빈 string 반환 (markup 0). 비개발자 한국어 라벨 + tooltip 으로
    숫자의 의미를 설명.
    """
    chips: list[str] = []

    wrong_vod = int(stats.get("wrong_vod_count") or 0)
    if wrong_vod > 0:
        chips.append(
            '<span class="user-clips-chip user-clips-chip-warn" '
            'title="이 VOD 가 아닌 다른 다시보기를 가리키는 클립 수 (시점 연결 불일치)">'
            f'⚠️ 다른 다시보기 클립 {wrong_vod}개</span>'
        )

    age = stats.get("age_hours")
    if age is not None:
        chips.append(
            '<span class="user-clips-chip" '
            'title="좋아요/재생수가 마지막으로 갱신된 시점 (수집에 성공한 클립 중 가장 오래된 값)">'
            f'⏱️ 인기도 데이터 · {_format_age_hours_label(float(age))}</span>'
        )

    parse_rate = stats.get("parse_rate")
    if parse_rate is not None:
        pct = int(round(float(parse_rate) * 100))
        chips.append(
            '<span class="user-clips-chip" '
            'title="좋아요/재생수를 정상 회수한 클립 비율 (시도한 클립 중)">'
            f'✓ 인기도 수집 성공률 {pct}%</span>'
        )

    if not chips:
        return ""
    return f'<div class="user-clips-source-health">{"".join(chips)}</div>'

def _safe_thumb_url(url: str) -> str:
    """thumbnail_url scheme allowlist — javascript:/data: 등 XSS 차단.

    Codex adversarial review: sidecar JSON 이 disk 파일이라 손상/수동 편집/비정상
    응답이 가능. _html_escape 만으로는 scheme injection 못 막음.
    """
    if not url:
        return ""
    lowered = url.lower().lstrip()
    return url if lowered.startswith(_SAFE_THUMB_SCHEMES) else ""

def _source_id(vod_info: VODInfo) -> str:
    return str(getattr(vod_info, "source_id", "") or vod_info.video_no or "").strip()

def _source_platform(vod_info: VODInfo) -> str:
    platform = str(getattr(vod_info, "source_platform", "") or "chzzk").strip().lower()
    return platform if platform in {"chzzk", "youtube"} else "chzzk"

def _strip_inline_md(s: str) -> str:
    """Plain text 용: **bold** / `code` / [text](url) 마커를 걷어내고 본문만 남긴다."""
    import re
    s = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'\1', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s

def _validate_editorial_review_projection(packet: dict) -> dict[str, Any]:
    """Fail closed before an accepted packet reaches a user-facing projection."""

    if not isinstance(packet, dict):
        raise ValueError("editorial_review_packet_required")
    review_mode = packet.get("projection_mode")
    if review_mode == "composed_review_draft":
        from ..editorial_review_import import validate_composed_review
        validate_composed_review(packet)
    elif review_mode == "external_review_draft":
        from ..chatgpt_export.import_result import validate_imported_editorial_result
        validate_imported_editorial_result(packet)
    elif review_mode != "qg1_review_candidate":
        raise ValueError("editorial_review_packet_not_accepted")

    broadcast_map = packet.get("broadcast_map")
    if not isinstance(broadcast_map, dict):
        raise ValueError("editorial_review_broadcast_map_required")
    points = list(broadcast_map.get("points") or [])
    stories = list(packet.get("stories") or [])
    highlights = list(packet.get("editorial_highlights") or [])
    if any(not isinstance(row, dict) for row in points + stories + highlights):
        raise ValueError("editorial_review_rows_must_be_objects")

    points_by_id: dict[str, dict] = {}
    point_story_refs: list[str] = []
    point_forbidden = {
        "context_start", "event_timestamp", "end", "review_start", "range",
        "window", "start", "start_sec", "end_sec", "currentTime",
    }
    for point in points:
        point_id = str(point.get("id") or "")
        story_ref = str(point.get("story_ref") or "")
        if not point_id or point_id in points_by_id:
            raise ValueError("editorial_review_point_id_not_unique")
        if not story_ref:
            raise ValueError("editorial_review_point_story_ref_required")
        if point_forbidden & set(point):
            raise ValueError("editorial_review_point_auxiliary_time_forbidden")
        if _hms_to_seconds(str(point.get("timestamp") or "")) is None:
            raise ValueError("editorial_review_point_timestamp_invalid")
        points_by_id[point_id] = point
        point_story_refs.append(story_ref)

    stories_by_ref: dict[str, dict] = {}
    for story in stories:
        story_ref = str(story.get("story_ref") or "")
        if not story_ref or story_ref in stories_by_ref:
            raise ValueError("editorial_review_story_ref_not_unique")
        roles = story.get("roles") or {}
        if not isinstance(roles, dict) or not set(roles).issubset(_EDITORIAL_ROLE_LABELS):
            raise ValueError("editorial_review_story_roles_invalid")
        if any(not isinstance(refs, list) for refs in roles.values()):
            raise ValueError("editorial_review_story_role_refs_invalid")
        stories_by_ref[story_ref] = story

    if len(point_story_refs) != len(set(point_story_refs)):
        raise ValueError("editorial_review_story_reused_by_multiple_points")
    if set(point_story_refs) != set(stories_by_ref):
        raise ValueError("editorial_review_point_story_bijection_invalid")
    rejected_refs = {
        str(row.get("story_ref") or "")
        for row in packet.get("rejected_stories") or []
        if isinstance(row, dict)
    }
    if rejected_refs & set(stories_by_ref):
        raise ValueError("editorial_review_rejected_story_promoted")

    highlight_forbidden = {
        "start", "end", "start_sec", "end_sec", "range", "window",
        "context_start", "event_timestamp", "review_start",
    }
    highlight_ids: set[str] = set()
    for highlight in highlights:
        highlight_id = str(highlight.get("highlight_id") or "")
        if not highlight_id or highlight_id in highlight_ids:
            raise ValueError("editorial_review_highlight_id_not_unique")
        highlight_ids.add(highlight_id)
        if highlight_forbidden & set(highlight):
            raise ValueError("editorial_review_highlight_independent_time_forbidden")
        point_refs = [str(value or "") for value in highlight.get("point_refs") or []]
        independent = (highlight.get("story_link_mode") == "source_independent"
            and highlight.get("composition_contract") == "editorial.context-author.v22"
            and bool(highlight.get("native_timing")))
        if independent and (point_refs or highlight.get("story_refs") or highlight.get("story_ref")):
            raise ValueError("editorial_review_independent_highlight_has_link")
        if (not point_refs and not independent) or any(not value for value in point_refs):
            raise ValueError("editorial_review_highlight_point_refs_required")
        if len(point_refs) != len(set(point_refs)):
            raise ValueError("editorial_review_highlight_point_refs_not_unique")
        if not set(point_refs).issubset(points_by_id):
            raise ValueError("editorial_review_highlight_point_ref_unknown")
        lineage_ref = str(highlight.get("story_ref") or "")
        referenced_story_refs = {
            str(points_by_id[point_ref].get("story_ref") or "")
            for point_ref in point_refs
        }
        if lineage_ref and lineage_ref not in referenced_story_refs:
            raise ValueError("editorial_review_highlight_story_lineage_mismatch")

        spans = list(highlight.get("source_spans") or [])
        if not spans or any(not isinstance(span, dict) for span in spans):
            raise ValueError("editorial_review_highlight_source_spans_required")
        previous_end = -1.0
        for span in spans:
            try:
                start = float(span["start_sec"])
                end = float(span["end_sec"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("editorial_review_source_span_invalid") from exc
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError("editorial_review_source_span_invalid")
            if start < previous_end:
                raise ValueError("editorial_review_source_spans_not_ordered")
            previous_end = end

    collaboration = {}
    if "collaboration_periods" in packet:
        from ..chatgpt_export.contract.collaboration_periods import recover_collaboration_periods
        collaboration["collaboration_periods"] = recover_collaboration_periods(
            packet["collaboration_periods"], packet.get("duration_sec"))["periods"]
    return {
        **collaboration,
        "points": points,
        "points_by_id": points_by_id,
        "stories": stories,
        "stories_by_ref": stories_by_ref,
        "highlights": highlights,
        "completion_state": packet.get("completion_state"),
        "source_exploration": packet.get("source_exploration"),
        "review_progress": packet.get("review_progress"),
        "external_review_draft": review_mode in {"external_review_draft", "composed_review_draft"},
        "review_mode": review_mode,
        "unresolved_group_count": len(packet.get("unresolved_group_outcomes") or []),
        "unresolved_questions": packet.get("unresolved_questions") or [],
        "unresolved_group_outcomes": packet.get("unresolved_group_outcomes") or [],
        "unresolved_proposal_count": len(packet.get("unresolved_proposals") or []),
        "unresolved_exploration_count": len((packet.get("source_exploration") or {}).get("unresolved_questions") or []),
    }

def _viewer_clip_badge_html(
    *,
    is_injected: bool = False,
) -> str:
    """Badge only for deterministic injected viewer-clip anchors.

    Ordinary cards use the verified timeline evidence chip adapter. Mere time
    proximity must never create a public viewer-clip claim.
    """

    if is_injected:
        return (
            '<span class="injected-badge" '
            'title="LLM 추천이 아니라 시청자 클립 anchor 에서 결정론으로 주입된 항목">'
            '📎 시청자 클립</span>'
        )
    return ""

def _viewer_editor_json(data: dict[str, Any]) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

def _wrap_user_clips_card(title: str, body: str) -> str:
    return (
        f"{VIEWER_CLIPS_MARKER_START}\n"
        f'<div class="card" data-report-block="viewer-clips">\n'
        f'  <div class="card-head"><h2>{_html_escape(title)}</h2></div>\n'
        f'  <div class="card-body">{body}</div>\n'
        f'</div>\n'
        f"{VIEWER_CLIPS_MARKER_END}"
    )
