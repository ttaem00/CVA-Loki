"""Decode validated frame intent from a trusted, preserved local media window.

No URL resolution or acquisition fallback. The caller owns source identity and
the window's clock binding; this module verifies bytes and requested coverage.
"""

from pathlib import Path

from copy import deepcopy

from .contracts import MediaToolError, ToolRequest

from ..evidence_world.contracts import digest, native_path

from .executor import (_extract_frames, _tool_paths, _probe_duration,
                       _sha256, _artifact_rows, FRAME_PROFILES)

def _collected_decode_input(path, request, root):
    from ..collected_vod_media import collected_media_window
    try:
        return collected_media_window(path,
            dict(start=request.start_sec, duration=request.duration_sec), root)
    except ValueError as exc:
        if str(exc) in {'collected_video_window_gap','collected_video_window_incomplete',
                'collected_video_window_overlap','full_author_navigation_local_window_clock_anchor'}:
            raise MediaToolError('preserved_media_clock_unavailable',str(exc)) from exc
        raise

def _decode_input(media, request, root):
    """Keep the original binding; narrow a verified captured playlist for decoding."""
    path = native_path(media['path']).resolve(strict=True)
    if _sha256(path) != media['sha']:
        raise MediaToolError('source_revision_changed', 'preserved media bytes changed')
    offset = request.start_sec - float(media['start'])
    from ..collected_vod_media import assert_collected_media_fresh
    intervals = [[request.start_sec, request.start_sec + request.duration_sec]]
    collected = assert_collected_media_fresh(path, intervals=intervals)
    if collected:
        original = media.get('original_media')
        if original and _sha256(native_path(original['path'])) != original['sha256']:
            raise MediaToolError('source_revision_changed', 'original media bytes changed')
        selected, offset = _collected_decode_input(path, request, root)
        return selected, offset, dict(path=str(selected), sha256=_sha256(selected),
            source_playlist=dict(path=str(path), sha256=media['sha']))
    return path, offset, None

def _source_identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

def extract_preserved_frames(request: ToolRequest, *, media: dict, output_root: Path):
    if request.action != "frames" or request.duration_sec is None:
        raise MediaToolError("invalid_request", "preserved inspection accepts frames only")
    start, end = float(media["start"]), float(media["end"])
    if not start <= request.start_sec < request.start_sec + request.duration_sec <= end:
        raise MediaToolError("window_out_of_range", "preserved media does not cover the request")
    ffmpeg, ffprobe = _tool_paths()
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = native_path(media['path'])
    original_identity = _source_identity(source)
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
    try:
        if (_source_identity(source) != original_identity or _sha256(source) != media['sha']
                or _source_identity(source) != original_identity):
            raise MediaToolError('source_revision_changed', 'preserved source changed during decode')
        original = media.get('original_media') if effective else None
        if original and _sha256(native_path(original['path'])) != original['sha256']:
            raise MediaToolError('source_revision_changed', 'preserved source changed during decode')
    except OSError as exc:
        raise MediaToolError('source_revision_changed', 'preserved source changed during decode') from exc
    if effective:
        from ..collected_vod_media import assert_collected_media_fresh
        assert_collected_media_fresh(path)
    return dict(schema_version="media.preserved-frames.v1", request=request.canonical_payload(),
                media_sha256=media["sha"], frame_timestamps_sec=times,
                artifacts=_artifact_rows(paths, root, ["frame"] * len(paths)),
                source_height=media["saved_height"], output_frame_height=height,
                time_binding="requested decode positions in preserved window; not precision matching",
                **(dict(effective_media=effective) if effective else {}),
                audio_listened=False)

def extract_preserved_frames_batch(requests, *, media: dict, output_root: Path,
                                   output_height: int | None = None):
    """Decode one owned local source batch, verifying unique bytes before/after."""
    if output_height is not None and (type(output_height) is not int or output_height < 2):
        raise MediaToolError("invalid_request", "output height must be an integer of at least two pixels")
    requests = tuple(requests)
    if not requests:
        return []
    media = deepcopy(media)
    start, end = float(media["start"]), float(media["end"])
    for request in requests:
        if request.action != "frames" or request.duration_sec is None:
            raise MediaToolError("invalid_request", "preserved inspection accepts frames only")
        if not start <= request.start_sec < request.start_sec + request.duration_sec <= end:
            raise MediaToolError("window_out_of_range", "preserved media does not cover the request")



    sources = {}

    def bind_source(locator, expected=None):
        original = native_path(locator)
        path = original.resolve(strict=True)
        if path not in sources:
            sources[path] = (original, _sha256(path))
        actual = sources[path][1]
        if expected is not None and actual != expected:
            raise MediaToolError("source_revision_changed", "preserved media bytes changed")
        return path, actual

    source_path, _ = bind_source(media["path"], media["sha"])
    if media.get("original_media"):
        bind_source(media["original_media"]["path"], media["original_media"]["sha256"])
    ffmpeg, ffprobe = _tool_paths()
    root = output_root.resolve()
    from ..collected_vod_media import assert_collected_media_fresh
    plans = {}
    for request in requests:
        key = digest(request.canonical_payload() if output_height is None else
                     [request.canonical_payload(), output_height])
        if key in plans:
            continue
        folder = root / key
        folder.mkdir(parents=True, exist_ok=True)
        path, offset, effective = source_path, request.start_sec - start, None
        intervals = [[request.start_sec, request.start_sec + request.duration_sec]]
        if assert_collected_media_fresh(source_path, intervals=intervals):
            selected, offset = _collected_decode_input(source_path, request, folder)
            path, selected_sha = bind_source(selected)
            effective = dict(path=str(path), sha256=selected_sha,
                source_playlist=dict(path=str(source_path), sha256=media["sha"]))
        plans[key] = (request, folder, path, offset, effective)

    receipts = {}
    try:
        for key, (request, folder, path, offset, effective) in plans.items():
            observed = _probe_duration(path, ffprobe)
            if offset + request.duration_sec > observed + 0.05:
                raise MediaToolError("media_truncated", "requested window exceeds the saved media")
            height = min(FRAME_PROFILES[request.inspection_mode]["height"] if output_height is None
                         else output_height, int(media["saved_height"]))
            if height < 2:
                raise MediaToolError("invalid_request", "source height is missing")
            paths, times = _extract_frames(path, folder, ffmpeg, request.duration_sec,
                request.start_sec, height, local_start_sec=offset, decode_from_start=effective is not None)
            if effective:
                assert_collected_media_fresh(path)
            receipts[key] = dict(schema_version="media.preserved-frames.v1",
                request=request.canonical_payload(), media_sha256=media["sha"],
                frame_timestamps_sec=times, artifacts=_artifact_rows(paths, folder, ["frame"] * len(paths)),
                source_height=media["saved_height"], output_frame_height=height,
                time_binding="requested decode positions in preserved window; not precision matching",
                **(dict(effective_media=effective) if effective else {}), audio_listened=False)
    finally:
        for locator, expected in sources.values():
            if _sha256(locator) != expected:
                raise MediaToolError("source_revision_changed", "preserved source changed during batch decode")
    return [deepcopy(receipts[digest(request.canonical_payload() if output_height is None else
                                    [request.canonical_payload(), output_height])]) for request in requests]
