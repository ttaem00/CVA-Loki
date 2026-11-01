"""Pure bundle viewer-editor packet assembly helpers.

This module owns deterministic bundle packet construction. It may depend on
summary render and payload projections, but never imports the summarizer facade.
"""

from __future__ import annotations

import re

from typing import Any, Optional

from ..models import UserClipsBundle, VODInfo

from ..user_clips_filter import is_eligible_in_vod_clip

from ..utils import sec_to_hms

from .payload import _clip_offset_int

from .render import _VIEWER_EDITOR_LANE_LABELS, _dedupe_viewer_editor_viewer_clip_events, _hms_to_seconds, _viewer_editor_guidance_from_flags, _viewer_editor_lane_summaries_for_events, _viewer_editor_limit_events_preserving_viewer_clips, _viewer_editor_stable_source_identity_key

def _segment_time_bounds(tc_text: str) -> tuple[int | None, int | None]:
    """Return start/end seconds from a timecode or range.

    End is None unless the source explicitly provides a range. This keeps
    point-like chapters/highlights distinguishable from bounded segments.
    """
    text = (tc_text or "").strip().strip("[] ")
    if not text:
        return None, None
    parts = re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text)
    start = _hms_to_seconds(parts[0]) if parts else None
    end = _hms_to_seconds(parts[1]) if len(parts) > 1 else None
    if start is not None and end is not None and end < start:
        start, end = end, start
    return start, end

def _merge_public_user_clip_events_into_viewer_payload(
    payload: dict[str, Any],
    user_clips: Optional[UserClipsBundle],
    vod_info: VODInfo,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload)
    from .report_generation import _viewer_clip_window_provenance

    viewer_clip_source = _viewer_clip_window_provenance(user_clips, vod_info)


    viewer_clip_source.update({
        "window_status": str(getattr(user_clips, "window_status", "") or "unknown"),
        "raw_candidate_count": int(viewer_clip_source.get("raw_clip_count") or 0),
        "selected_count": int(viewer_clip_source.get("selected_clip_count") or 0),
        "verified_offset_count": int(viewer_clip_source.get("verified_same_vod_count") or 0),
        "engagement_available": bool(
            (viewer_clip_source.get("popularity") or {}).get("engagement_available")
        ),
    })
    merged["viewer_clip_source"] = viewer_clip_source
    if user_clips is None:
        return merged
    clips = [clip for clip in (user_clips.clips or [])
             if is_eligible_in_vod_clip(clip, vod_info.video_no)]
    if not clips:
        return merged

    events = [dict(row) for row in (merged.get("events") or []) if isinstance(row, dict)]
    current_popular_window = (
        str(getattr(user_clips, "order_type", "") or "").upper() == "POPULAR"
        and str(getattr(user_clips, "window_status", "") or "").lower() == "ok"
    )
    if current_popular_window:
        events = [row for row in events if row.get("kind") != "viewer_clip"]
    existing_clip_uids = {
        str(item.get("clip_uid") or "").strip()
        for row in events
        if isinstance(row, dict) and row.get("kind") == "viewer_clip"
        for item in (row.get("evidence") or [])
        if isinstance(item, dict) and str(item.get("clip_uid") or "").strip()
    }
    video_no = str(getattr(vod_info, "video_no", "") or "")
    for index, clip in enumerate(clips, start=1):
        start = _clip_offset_int(clip)
        if start is None:
            continue
        clip_uid = str(getattr(clip, "clip_uid", "") or "").strip()
        if clip_uid and clip_uid in existing_clip_uids:
            continue
        title = str(getattr(clip, "title", "") or "시청자 클립").strip()
        duration = int(getattr(clip, "duration", 0) or 0)
        play_count = int(getattr(clip, "play_count", 0) or 0)
        like_count = int(getattr(clip, "like_count", 0) or 0)
        identity_key = (
            _viewer_editor_stable_source_identity_key("viewer_clip", video_no, clip_uid)
            if clip_uid
            else _viewer_editor_stable_source_identity_key("viewer_clip_anchor", video_no, start, title)
        )
        events.append({
            "id": f"user_clip_{clip_uid or identity_key.rsplit(':', 1)[-1]}_{index}",
            "kind": "viewer_clip",
            "kind_label": _VIEWER_EDITOR_LANE_LABELS.get("viewer_clip", "시청자 클립"),
            "start_sec": start,
            "seek_sec": start,
            "end_sec": start + duration if duration > 0 else None,
            "timecode": sec_to_hms(start),
            "title": title,
            "evidence": [{
                "label": "public_viewer_clip",
                "text": f"시청자 클립: {title}",
                "clip_uid": clip_uid,
                "offset_sec": start,
                "play_count": play_count,
                "like_count": like_count,
            }],
            "guidance": _viewer_editor_guidance_from_flags([], "viewer_clip"),
            "source_identity_key": identity_key,
            "source_identity_kind": "public_viewer_clip_uid" if clip_uid else "deterministic_viewer_clip_anchor",
            "source_identity_confidence": "high" if clip_uid else "low",
            "engagement_weighted": str(getattr(clip, "engagement_status", "") or "") == "ok",
        })

    events = _dedupe_viewer_editor_viewer_clip_events(events)
    events.sort(key=lambda item: (item["start_sec"], item.get("end_sec") or item["start_sec"], item["id"]))
    visible_events = _viewer_editor_limit_events_preserving_viewer_clips(events)
    merged["events"] = visible_events
    merged["lanes"] = _viewer_editor_lane_summaries_for_events(visible_events)
    return merged
