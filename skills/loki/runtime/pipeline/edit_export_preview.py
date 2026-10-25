"""Preview-only edit timeline export from segment ledger data."""

from __future__ import annotations

from typing import Any, Iterable

from .editor_cut_candidates import build_editor_cut_candidates

from .utils import sec_to_hms

SCHEMA_VERSION = "edit_export_preview.v1"

def build_export_preview(
    segment_ledger: Iterable[dict[str, Any]],
    *,
    boundary_signals: Iterable[dict[str, Any]] = (),
    source_name: str = "vod",
    fps: int = 30,
) -> dict[str, Any]:
    """Build EDL text and an OTIO-like JSON preview without extra deps."""

    clips = _clips_from_segments(segment_ledger, boundary_signals=boundary_signals)
    edl_text = _render_edl(clips, source_name=source_name, fps=fps)
    otio_preview = _render_otio_preview(clips, source_name=source_name)
    validation = validate_export_preview({"clips": clips, "edl": edl_text, "otio": otio_preview})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if clips and validation["status"] == "pass" else "UNVERIFIED",
        "source": "segment_ledger",
        "clip_count": len(clips),
        "fps": fps,
        "candidate_schema_version": "editor_cut_candidate.v1",
        "clips": clips,
        "edl": edl_text,
        "otio": otio_preview,
        "validation": validation,
    }

def validate_export_preview(preview: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    clips = preview.get("clips") or []
    if not clips:
        failures.append({"code": "NO_EXPORT_CLIPS", "message": "no timeline/highlight spans available"})
    previous_start = -1
    for index, clip in enumerate(clips, start=1):
        start = clip.get("start_sec")
        end = clip.get("end_sec")
        if not isinstance(start, int) or not isinstance(end, int):
            failures.append({"code": "INVALID_TIMECODE", "clip_index": index})
            continue
        if end <= start:
            failures.append({"code": "NON_POSITIVE_DURATION", "clip_index": index})
        if start < previous_start:
            failures.append({"code": "NOT_SORTED", "clip_index": index})
        previous_start = start
    return {
        "schema_version": f"{SCHEMA_VERSION}.validation",
        "status": "fail" if failures else "pass",
        "failures": failures,
    }

def _clips_from_segments(
    segment_ledger: Iterable[dict[str, Any]],
    *,
    boundary_signals: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    candidates = build_editor_cut_candidates(segment_ledger, boundary_signals=boundary_signals)
    return [
        {
            "segment_id": row["segment_id"],
            "kind": row["kind"],
            "title": row["title"],
            "start_sec": row["start_sec"],
            "end_sec": row["end_sec"],
            "duration_sec": row["duration_sec"],
            "cut_candidate": row,
            "original_start_sec": row["original_start_sec"],
            "original_end_sec": row["original_end_sec"],
            "snap_reason": row["snap_reason"],
            "confidence": row["confidence"],
            "needs_review": row["needs_review"],
            "source_signal_refs": row["source_signal_refs"],
        }
        for row in candidates
    ]

def _render_edl(clips: list[dict[str, Any]], *, source_name: str, fps: int) -> str:
    lines = ["TITLE: CHZZK SEGMENT PREVIEW", "FCM: NON-DROP FRAME", ""]
    record_cursor = 0
    for index, clip in enumerate(clips, start=1):
        src_in = _sec_to_edl_tc(clip["start_sec"], fps)
        src_out = _sec_to_edl_tc(clip["end_sec"], fps)
        rec_in = _sec_to_edl_tc(record_cursor, fps)
        record_cursor += clip["duration_sec"]
        rec_out = _sec_to_edl_tc(record_cursor, fps)
        lines.append(
            f"{index:03d}  AX       V     C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {source_name}")
        lines.append(f"* SEGMENT_ID: {clip['segment_id']}")
        if clip["title"]:
            lines.append(f"* TITLE: {clip['title']}")
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if clips else "")

def _render_otio_preview(clips: list[dict[str, Any]], *, source_name: str) -> dict[str, Any]:
    return {
        "schema_version": "otio-preview/1",
        "name": f"{source_name} segment preview",
        "tracks": [
            {
                "kind": "Video",
                "clips": [
                    {
                        "name": clip["title"] or clip["segment_id"],
                        "segment_id": clip["segment_id"],
                        "source_range": {
                            "start_time": sec_to_hms(clip["start_sec"]),
                            "duration_sec": clip["duration_sec"],
                        },
                        "metadata": {"kind": clip["kind"]},
                    }
                    for clip in clips
                ],
            }
        ],
    }

def _sec_to_edl_tc(sec: int, fps: int) -> str:
    total_frames = max(0, int(sec)) * max(1, int(fps))
    frames = total_frames % fps
    total_seconds = total_frames // fps
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
