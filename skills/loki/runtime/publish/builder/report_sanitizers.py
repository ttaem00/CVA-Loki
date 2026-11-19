"""Public report metadata and viewer-editor payload sanitizers.

Keep file-copy orchestration in ``build_site.py`` and the public/private
boundary rules here so privacy and payload-preservation tests can target the
boundary directly.
"""

from __future__ import annotations

import re

from typing import Any

def _normalize_public_scene_signal_aliases(value: Any, *, field_name: str = "") -> Any:
    """Translate obsolete internal scene-signal aliases at the public boundary.

    Local metadata keeps its original learning/evaluation taxonomy. Public
    metadata and viewer payloads instead use the versioned scene-signal
    contract, so an older generated artifact cannot leak a retired label when
    it is republished by a newer checkout.
    """

    from pipeline.scene_signal_contract import lane_viewer_labels, obsolete_lane_aliases

    aliases = obsolete_lane_aliases()
    viewer_labels = lane_viewer_labels()

    def normalize(item: Any, current_field: str) -> Any:
        if isinstance(item, dict):
            return {
                str(key): normalize(child, str(key))
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [normalize(child, current_field) for child in item]
        if isinstance(item, str):
            canonical = aliases.get(item.strip())
            if canonical:
                if current_field.lower() in {"label", "viewer_label", "title"}:
                    return viewer_labels.get(canonical, canonical)
                return canonical
        return item

    return normalize(value, field_name)

def _public_scene_image_rows(rows: Any) -> Any:
    """Project only the selected scene-image field, retaining source identity."""
    if not isinstance(rows, list):
        return rows
    from pipeline.editorial_highlight_cards import editorial_scene_image_data_url

    projected = []
    for row in rows:
        image = row.get("image") if isinstance(row, dict) else None
        if isinstance(image, dict) and image.get("data_url"):
            row = {**row, "image": {**image, "data_url": editorial_scene_image_data_url(image["data_url"])}}
        projected.append(row)
    return projected

def _project_public_editorial_scene_images_payload(payload: Any) -> Any:
    """Project selected public editor data, never unrelated cached reports."""
    if not isinstance(payload, dict) or "outline_projection" not in payload:
        return payload
    return {**payload, "outline_projection": _public_scene_image_rows(payload["outline_projection"])}

def _normalize_public_viewer_editor_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    from pipeline.edit_point_clusters import build_edit_point_clusters
    from pipeline.summarizer import _dedupe_viewer_editor_viewer_clip_events

    events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
    deduped_events = _dedupe_viewer_editor_viewer_clip_events(events)
    event_counts: dict[str, int] = {}
    for event in deduped_events:
        kind = str(event.get("kind") or "").strip()
        if kind:
            event_counts[kind] = event_counts.get(kind, 0) + 1
    normalized = dict(payload)
    normalized["events"] = deduped_events
    if not isinstance(payload.get("edit_point_clusters"), dict):
        normalized["edit_point_clusters"] = build_edit_point_clusters(
            deduped_events,
            duration_sec=payload.get("duration_sec"),
        )
    chat_buckets = [row for row in (payload.get("chat_buckets") or []) if isinstance(row, dict)]
    total_chats = 0
    for bucket in chat_buckets:
        try:
            total_chats += int(bucket.get("count") or 0)
        except (TypeError, ValueError):
            continue
    stats = dict(payload.get("stats") or {}) if isinstance(payload.get("stats"), dict) else {}
    if total_chats:
        stats["total_chats"] = total_chats
    if stats:
        normalized["stats"] = stats
    summary = dict(payload.get("summary") or {}) if isinstance(payload.get("summary"), dict) else {}
    summary.update(
        {
            "event_count": len(deduped_events),
            "chat_bucket_count": len(chat_buckets),
            "subtitle_context_count": len(payload.get("subtitles") or []),
        }
    )
    if total_chats:
        summary["total_chats"] = total_chats
    normalized["summary"] = summary
    lanes = []
    for row in payload.get("lanes") or []:
        if not isinstance(row, dict):
            continue
        lane = dict(row)
        key = str(lane.get("key") or "").strip()
        if key and str(lane.get("display_role") or "").lower() != "overview" and key not in {"audio_waveform", "chat_volume"}:
            lane["event_count"] = event_counts.get(key, 0)
        lanes.append(lane)
    if lanes:
        normalized["lanes"] = lanes
    return normalized

def _strip_admin_only_timeline_comment_events(payload: Any) -> Any:
    """Remove private rewritten comment anchors from public viewer payloads.

    Pipeline 2.5 may use these anchors privately and Report Admin may display
    them, but the public site receives only the resulting D1/D2/Point report
    plus aggregate provenance.  Legacy public chapter events are preserved.
    """

    if not isinstance(payload, dict):
        return payload
    events = []
    for row in payload.get("events") or []:
        if not isinstance(row, dict):
            continue
        risk_flags = {
            str(value)
            for value in (row.get("risk_flags") or [])
        }
        if "private_rewritten_timetable_anchor" in risk_flags:
            continue
        events.append(row)
    cleaned = dict(payload)
    cleaned["events"] = events
    return _normalize_public_viewer_editor_payload(cleaned)

_PUBLIC_VIEWER_EDITOR_FORBIDDEN_KEY_RE = re.compile(
    r"(^|_)(cookie|cookies|token|secret|password|nid_aut|nid_ses)($|_)"
    r"|(^|_)(raw_transcript|raw_subtitle|raw_srt|raw_chat|chat_dump|chat_log)($|_)"
    r"|(^|_)(raw_ocr_text|raw_frame)($|_)"
    r"|(^|_)(internal_learning|label_tier|manager_edit_diff|selected_ranges_draft)($|_)",
    re.IGNORECASE,
)

def _is_false_public_visual_raw_flag(key: Any, value: Any) -> bool:
    key_text = str(key).lower()
    return key_text in {"raw_frame_included", "raw_ocr_text_included"} and value is False

def _strip_public_viewer_editor_unsafe_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_public_viewer_editor_unsafe_fields(item)
            for key, item in value.items()
            if _is_false_public_visual_raw_flag(key, item)
            or not _PUBLIC_VIEWER_EDITOR_FORBIDDEN_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_strip_public_viewer_editor_unsafe_fields(item) for item in value]
    return value

def _sanitize_public_bundle_viewer_editor_payload(payload: dict) -> dict:
    """Keep public-safe context while removing manager-only scene signals."""

    if not isinstance(payload, dict):
        return payload

    public_payload = _strip_public_viewer_editor_unsafe_fields(payload)
    if not isinstance(public_payload, dict):
        return payload
    public_payload = _normalize_public_scene_signal_aliases(public_payload)
    from pipeline.summary.viewer_editor import _project_public_viewer_editor_tools_payload

    public_payload = _project_public_viewer_editor_tools_payload(public_payload)
    public_payload["edit_export_preview"] = {
        "schema_version": "edit_export_preview.v1",
        "status": "draft",
        "source": "public_bundle_public_safe",
        "clip_count": 0,
        "clips": [],
        "edl": "",
    }
    return public_payload
