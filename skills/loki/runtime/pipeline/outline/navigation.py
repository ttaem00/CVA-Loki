"""Private source navigation with source-bound occurrence handoff.

The map contains clocks, aggregates and occurrence pointers, not editorial answers.
Source windows are paged by serialized size, never by an editorial time limit.
Only cited, verified source reads enter the existing observation owner; notes do not.
"""

from __future__ import annotations

import math

FIRST_LOOK_SCHEMA = "source_first_look.v1"

SOURCE_FAMILIES = ("stt", "chat", "density", "waveform", "audio_peaks", "clips", "heatmap")

FIRST_LOOK_BUCKET_SEC = 600

FIRST_LOOK_CONTEXT_SEC = 90

def _readiness_complete(row):
    return row["status"] == "absent_in_source" or (row["status"] == "available" and row["count"] > 0)

def _merge_intervals(rows, *, duration_sec, padding_sec=FIRST_LOOK_CONTEXT_SEC):
    intervals = sorted((max(0.0, float(row["start_sec"]) - padding_sec),
                        min(float(duration_sec), float(row["end_sec"]) + padding_sec), row)
                       for row in rows)
    merged = []
    for start, end, row in intervals:
        if merged and start <= merged[-1]["end_sec"]:
            saved = merged[-1]
            saved["end_sec"] = max(saved["end_sec"], end)
            saved["zone_ids"].append(row["zone_id"])
            saved["signal_families"] = sorted(set(saved["signal_families"]) | set(row["signal_families"]))
            saved["signal_count"] += int(row["signal_count"])
            saved["source_pointers"].update(row.get("source_pointers", {}))
            continue
        merged.append(dict(start_sec=start, end_sec=end, zone_ids=[row["zone_id"]],
                           signal_families=list(row["signal_families"]),
                           signal_count=int(row["signal_count"]),
                           source_pointers=dict(row.get("source_pointers", {}))))
    for index, row in enumerate(merged, 1):
        row["interval_id"] = f"first_look_{index:03d}"
        row["duration_sec"] = round(row["end_sec"] - row["start_sec"], 3)
        row["attention"] = "strongest" if len(row["signal_families"]) >= 2 else "uncertain_representative"
    return merged

def _build_first_look(*, video_no, duration_sec, readiness, zones, source_fingerprint):
    allowed_pointers = {"waveform", "audio_peaks", "density", "clips"}
    zones = [dict(row, source_pointers={name:value for name,value in row.get("source_pointers", {}).items()
                  if name in allowed_pointers}) for row in zones]
    ranked = sorted(zones, key=lambda row: (-int(row["family_count"]), -int(row["signal_count"]),
        -float(row.get("waveform_max", 0)), float(row["start_sec"])))
    selected = {row["zone_id"]: row for row in ranked if int(row["family_count"]) >= 2}
    for bucket_start in range(0, math.ceil(duration_sec), FIRST_LOOK_BUCKET_SEC):
        local = [row for row in ranked if bucket_start <= float(row["start_sec"]) < bucket_start + FIRST_LOOK_BUCKET_SEC]
        if local:
            selected[local[0]["zone_id"]] = local[0]
    intervals = _merge_intervals(selected.values(), duration_sec=duration_sec)
    input_ready = all(_readiness_complete(readiness[name]) for name in SOURCE_FAMILIES[:-1])
    status = "complete" if input_ready and intervals else "partial"
    recommended_duration = sum(row["duration_sec"] for row in intervals)
    projection = {name: dict(status=readiness[name]["status"], count=readiness[name]["count"],
        reason=readiness[name]["reason"], producer=readiness[name]["producer"],
        source_ref=readiness[name]["source_ref"], source_fingerprint=readiness[name]["source_fingerprint"])
        for name in SOURCE_FAMILIES[:-1]}
    return dict(schema_version=FIRST_LOOK_SCHEMA, status=status, video_no=str(video_no),
        duration_sec=float(duration_sec), source_fingerprint=source_fingerprint,
        producer="pipeline.outline.navigation._build_first_look", readiness=projection,
        buckets=intervals,
        strongest_zone_count=sum(int(row["family_count"]) >= 2 for row in selected.values()),
        temporal_representative_bucket_sec=FIRST_LOOK_BUCKET_SEC,
        context_padding_sec=FIRST_LOOK_CONTEXT_SEC,
        recommended_duration_sec=round(recommended_duration, 3),
        exploration_ratio=round(recommended_duration / duration_sec, 6),
        exploration_reduction_ratio=round(1 - recommended_duration / duration_sec, 6),
        raw_source_text_included=False,
        remaining_source_clock="available through browse_map(order=low_signal) and read_window",
        boundary="Navigation evidence only; intervals are not Points, Stories, or Highlights.")
