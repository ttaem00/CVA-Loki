"""Build compact edit-point clusters for viewer/editor scene finding."""

from __future__ import annotations

import hashlib

from typing import Any

SCHEMA_VERSION = "edit_point_cluster.v1"

DEFAULT_CLUSTER_WINDOW_SEC = 8.0

_OVERVIEW_KINDS = {"audio_waveform", "chat_volume"}

def build_edit_point_clusters(
    events: list[dict[str, Any]],
    *,
    duration_sec: int | float | None = None,
    window_sec: int | float = DEFAULT_CLUSTER_WINDOW_SEC,
    max_events_per_cluster: int = 3,
    max_clusters: int | None = None,
) -> dict[str, Any]:
    """Group renderable scene events into editor-first time clusters."""

    window = max(0.0, _safe_float(window_sec, DEFAULT_CLUSTER_WINDOW_SEC) or DEFAULT_CLUSTER_WINDOW_SEC)
    candidates = [_cluster_event(row) for row in events if isinstance(row, dict)]
    candidates = [row for row in candidates if row is not None]
    candidates.sort(key=lambda row: (row["start_sec"], row["end_sec"], row["event_id"]))

    clusters: list[list[dict[str, Any]]] = []
    for event in candidates:
        if not clusters:
            clusters.append([event])
            continue
        current = clusters[-1]
        if event["start_sec"] - current[-1]["start_sec"] <= window:
            current.append(event)
        else:
            clusters.append([event])

    original_count = len(clusters)
    rows = [
        _cluster_summary(cluster, index=index, max_events=max_events_per_cluster)
        for index, cluster in enumerate(clusters, start=1)
    ]
    if max_clusters is not None and max_clusters > 0 and len(rows) > max_clusters:
        rows = _select_overview_clusters(rows, max_clusters)
    return {
        "schema_version": SCHEMA_VERSION,
        "window_sec": window,
        "duration_sec": max(0.0, _safe_float(duration_sec, 0.0) or 0.0),
        "original_cluster_count": original_count,
        "cluster_count": len(rows),
        "clusters": rows,
    }

def _cluster_event(row: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(row.get("kind") or row.get("lane") or "").strip()
    if not kind or kind in _OVERVIEW_KINDS:
        return None
    start = _safe_float(row.get("start_sec", row.get("sec")))
    if start is None:
        return None
    end = _safe_float(row.get("end_sec"), start)
    if end is None:
        end = start
    if end < start:
        start, end = end, start
    event_id = str(row.get("id") or row.get("event_id") or f"{kind}_{start:g}").strip()
    return {
        "event_id": event_id,
        "kind": kind,
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "timecode": str(row.get("timecode") or _sec_to_tc(start)),
        "title": str(row.get("title") or row.get("label") or row.get("kind_label") or kind).strip(),
        "role": str(row.get("role") or ""),
        "source_identity_key": str(row.get("source_identity_key") or ""),
    }

def _cluster_summary(cluster: list[dict[str, Any]], *, index: int, max_events: int) -> dict[str, Any]:
    start = min(row["start_sec"] for row in cluster)
    end = max(row["end_sec"] for row in cluster)
    kinds = sorted({row["kind"] for row in cluster if row["kind"]})
    viewer_clip_events = [row for row in cluster if row["kind"] == "viewer_clip"]
    event_ids = [row["event_id"] for row in cluster]
    cluster_id = _cluster_id(start, event_ids)
    return {
        "cluster_id": cluster_id,
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "timecode": _sec_to_tc(start),
        "signal_count": len(cluster),
        "family_count": len(kinds),
        "signal_families": kinds,
        "viewer_clip_count": len(viewer_clip_events),
        "confidence": _cluster_confidence(kinds, len(cluster), len(viewer_clip_events)),
        "representative_events": cluster[:max(0, max_events)],
        "overflow_event_count": max(0, len(cluster) - max(0, max_events)),
        "event_ids": event_ids,
    }

def _cluster_confidence(kinds: list[str], signal_count: int, viewer_clip_count: int) -> str:
    if len(kinds) >= 3 or (len(kinds) >= 2 and signal_count >= 3) or viewer_clip_count >= 3:
        return "high"
    if len(kinds) >= 2 or signal_count >= 2:
        return "medium"
    return "low"

def _select_overview_clusters(rows: list[dict[str, Any]], max_clusters: int) -> list[dict[str, Any]]:
    """Keep the strongest overview markers without crowding bundle timelines."""
    ranked = sorted(
        enumerate(rows),
        key=lambda pair: (
            int(pair[1].get("viewer_clip_count") or 0),
            int(pair[1].get("family_count") or 0),
            int(pair[1].get("signal_count") or 0),
            -pair[0],
        ),
        reverse=True,
    )[:max_clusters]
    keep_ids = {id(row) for _idx, row in ranked}
    return [row for row in rows if id(row) in keep_ids]

def _cluster_id(start_sec: float, event_ids: list[str]) -> str:
    seed = f"{start_sec:.3f}|" + "|".join(event_ids)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"epc_{digest}"

def _sec_to_tc(sec: float) -> str:
    value = max(0, int(sec))
    h = value // 3600
    m = (value % 3600) // 60
    s = value % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
