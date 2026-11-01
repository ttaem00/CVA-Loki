"""Decode validated frame intent from a trusted, preserved local media window.

No URL resolution or acquisition fallback. The caller owns source identity and
the window's clock binding; this module verifies bytes and requested coverage.
"""

from pathlib import Path

from .contracts import MediaToolError, ToolRequest

from .executor import (_extract_frames, _tool_paths, _probe_duration,
                       _sha256, _artifact_rows, FRAME_PROFILES)

def extract_preserved_frames(request: ToolRequest, *, media: dict, output_root: Path):
    if request.action != "frames" or request.duration_sec is None:
        raise MediaToolError("invalid_request", "preserved inspection accepts frames only")
    path = Path(media["path"]).resolve(strict=True)
    if _sha256(path) != media["sha"]:
        raise MediaToolError("source_revision_changed", "preserved media bytes changed")
    start, end = float(media["start"]), float(media["end"])
    if not start <= request.start_sec < request.start_sec + request.duration_sec <= end:
        raise MediaToolError("window_out_of_range", "preserved media does not cover the request")
    ffmpeg, ffprobe = _tool_paths()
    observed = _probe_duration(path, ffprobe)
    offset = request.start_sec - start
    if offset + request.duration_sec > observed + 0.05:
        raise MediaToolError("media_truncated", "requested window exceeds the saved media")
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    height = min(FRAME_PROFILES[request.inspection_mode]["height"], int(media["saved_height"]))
    if height < 2:
        raise MediaToolError("invalid_request", "source height is missing")
    paths, times = _extract_frames(path, root, ffmpeg, request.duration_sec,
                                  request.start_sec, height, local_start_sec=offset)
    return dict(schema_version="media.preserved-frames.v1", request=request.canonical_payload(),
                media_sha256=media["sha"], frame_timestamps_sec=times,
                artifacts=_artifact_rows(paths, root, ["frame"] * len(paths)),
                source_height=media["saved_height"], output_frame_height=height,
                time_binding="requested decode positions in preserved window; not precision matching",
                audio_listened=False)
