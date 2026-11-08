"""Decode validated frame intent from a trusted, preserved local media window.

No URL resolution or acquisition fallback. The caller owns source identity and
the window's clock binding; this module verifies bytes and requested coverage.
"""

from pathlib import Path

from .contracts import MediaToolError, ToolRequest

from .executor import (_extract_frames, _tool_paths, _probe_duration,
                       _sha256, _artifact_rows, FRAME_PROFILES)

def _decode_input(media, request, root):
    """Keep the original binding; narrow a verified captured playlist for decoding."""
    path = Path(media['path']).resolve(strict=True)
    if _sha256(path) != media['sha']:
        raise MediaToolError('source_revision_changed', 'preserved media bytes changed')
    offset = request.start_sec - float(media['start'])
    from ..collected_vod_media import assert_collected_media_fresh, collected_media_window
    intervals = [[request.start_sec, request.start_sec + request.duration_sec]]
    collected = assert_collected_media_fresh(path, intervals=intervals)
    if collected:
        original = media.get('original_media')
        if original and _sha256(Path(original['path'])) != original['sha256']:
            raise MediaToolError('source_revision_changed', 'original media bytes changed')
        try:
            selected, offset = collected_media_window(path,
                dict(start=request.start_sec, duration=request.duration_sec), root)
        except ValueError as exc:
            if str(exc) in {'collected_video_window_gap','collected_video_window_incomplete',
                    'collected_video_window_overlap','full_author_navigation_local_window_clock_anchor'}:
                raise MediaToolError('preserved_media_clock_unavailable',str(exc)) from exc
            raise
        return selected, offset, dict(path=str(selected), sha256=_sha256(selected),
            source_playlist=dict(path=str(path), sha256=media['sha']))
    return path, offset, None

def extract_preserved_frames(request: ToolRequest, *, media: dict, output_root: Path):
    if request.action != "frames" or request.duration_sec is None:
        raise MediaToolError("invalid_request", "preserved inspection accepts frames only")
    start, end = float(media["start"]), float(media["end"])
    if not start <= request.start_sec < request.start_sec + request.duration_sec <= end:
        raise MediaToolError("window_out_of_range", "preserved media does not cover the request")
    ffmpeg, ffprobe = _tool_paths()
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path, offset, effective = _decode_input(media, request, root)
    observed = _probe_duration(path, ffprobe)
    if offset + request.duration_sec > observed + 0.05:
        raise MediaToolError("media_truncated", "requested window exceeds the saved media")
    height = min(FRAME_PROFILES[request.inspection_mode]["height"], int(media["saved_height"]))
    if height < 2:
        raise MediaToolError("invalid_request", "source height is missing")
    paths, times = _extract_frames(path, root, ffmpeg, request.duration_sec,
                                  request.start_sec, height, local_start_sec=offset,
                                  decode_from_start=effective is not None)
    if effective:
        from ..collected_vod_media import assert_collected_media_fresh
        if _sha256(Path(media['path'])) != media['sha'] or (
                media.get('original_media') and
                _sha256(Path(media['original_media']['path'])) != media['original_media']['sha256']):
            raise MediaToolError('source_revision_changed', 'preserved source changed during decode')
        assert_collected_media_fresh(path)
    return dict(schema_version="media.preserved-frames.v1", request=request.canonical_payload(),
                media_sha256=media["sha"], frame_timestamps_sec=times,
                artifacts=_artifact_rows(paths, root, ["frame"] * len(paths)),
                source_height=media["saved_height"], output_frame_height=height,
                time_binding="requested decode positions in preserved window; not precision matching",
                **(dict(effective_media=effective) if effective else {}),
                audio_listened=False)
