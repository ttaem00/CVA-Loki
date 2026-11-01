"""Deterministic boundary diagnostics for report segments.

The output is an inspection aid only. Nearby scene/chat/audio/context/ASR
markers are not ground truth and must not be promoted into automatic cuts.
"""

from __future__ import annotations

from typing import Any, Iterable

from .timeline_alignment import hms_to_sec

SCHEMA_VERSION = "boundary_diagnostics.v1"

DEFAULT_WINDOW_SEC = 5

def build_boundary_diagnostics(
    segments: Iterable[dict[str, Any]],
    *,
    scene_cuts: Iterable[Any] | None = None,
    chat_spikes: Iterable[Any] | None = None,
    audio_events: Iterable[Any] | None = None,
    context_lines: Iterable[Any] | None = None,
    asr_words: Iterable[Any] | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
) -> dict[str, Any]:
    """Attach nearby diagnostic markers to segment start/end boundaries."""

    window = max(0, int(window_sec))
    markers = _normalize_all_markers(
        scene_cuts=scene_cuts,
        chat_spikes=chat_spikes,
        audio_events=audio_events,
        context_lines=context_lines,
        asr_words=asr_words,
    )
    items: list[dict[str, Any]] = []
    segment_count = 0
    marker_count = 0

    for raw in segments or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("kind") not in {"timeline", "highlight", "chapter"}:
            continue
        segment_count += 1
        for boundary_name, sec in _segment_boundaries(raw):
            nearby = _nearby_markers(sec, markers, window)
            marker_count += len(nearby)
            items.append(
                {
                    "segment_id": str(raw.get("segment_id") or ""),
                    "kind": str(raw.get("kind") or ""),
                    "title": str(raw.get("title") or ""),
                    "boundary": boundary_name,
                    "boundary_sec": sec,
                    "window_sec": window,
                    "markers": nearby,
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if items else "no_segments",
        "diagnostic_only": True,
        "segment_count": segment_count,
        "boundary_count": len(items),
        "marker_match_count": marker_count,
        "items": items,
        "decision_rule": (
            "Boundary markers are diagnostic context only; they are not "
            "automatic cut decisions or probabilities."
        ),
    }

def _normalize_all_markers(**groups: Iterable[Any] | None) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    type_map = {
        "scene_cuts": "scene_cut",
        "chat_spikes": "chat_spike",
        "audio_events": "audio_event",
        "context_lines": "context_line",
        "asr_words": "asr_word_anchor",
    }
    for group_name, values in groups.items():
        marker_type = type_map[group_name]
        for index, raw in enumerate(values or [], start=1):
            marker = _normalize_marker(raw, marker_type, index)
            if marker is not None:
                markers.append(marker)
    markers.sort(key=lambda item: (item["sec"], item["marker_type"], item["index"]))
    return markers

def _normalize_marker(raw: Any, marker_type: str, index: int) -> dict[str, Any] | None:
    if isinstance(raw, (int, float)):
        sec = int(round(float(raw)))
        label = ""
        source_id = ""
    elif isinstance(raw, dict):
        sec = _first_sec(raw, ("sec", "time_sec", "center_sec", "start_sec", "t", "tc", "timestamp"))
        if sec is None:
            return None
        label = str(raw.get("label") or raw.get("title") or raw.get("text") or raw.get("word") or "")
        source_id = str(raw.get("source_id") or raw.get("id") or "")
    else:
        return None
    if sec < 0:
        return None
    return {
        "marker_type": marker_type,
        "sec": sec,
        "label": label[:160],
        "source_id": source_id,
        "index": index,
    }

def _nearby_markers(boundary_sec: int, markers: list[dict[str, Any]], window_sec: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for marker in markers:
        delta = int(marker["sec"]) - boundary_sec
        if abs(delta) <= window_sec:
            row = dict(marker)
            row["delta_sec"] = delta
            out.append(row)
    out.sort(key=lambda item: (abs(item["delta_sec"]), item["marker_type"], item["sec"]))
    return out

def _segment_boundaries(segment: dict[str, Any]) -> list[tuple[str, int]]:
    start = _sec_value(segment, "start_sec")
    end = _sec_value(segment, "end_sec")
    out: list[tuple[str, int]] = []
    if start is not None:
        out.append(("start", start))
    if end is not None and end != start:
        out.append(("end", end))
    return out

def _sec_value(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None and key == "start_sec":
        value = row.get("sec")
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return hms_to_sec(value)
        except ValueError:
            pass
    try:
        sec = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return sec if sec >= 0 else None

def _first_sec(row: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, str):
            try:
                return hms_to_sec(value)
            except ValueError:
                pass
        try:
            sec = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if sec >= 0:
            return sec
    return None
