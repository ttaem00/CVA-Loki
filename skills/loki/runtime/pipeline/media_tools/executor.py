"""Cached ffmpeg execution for bounded CHZZK media requests."""

from __future__ import annotations

import hashlib

import json

import os

import shutil

import subprocess

import threading

import time

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Mapping

from urllib.parse import urlsplit

from pipeline.replay_continuity.live_capture import _hls_demuxer_args

from .chzzk import ResolvedChzzkSource, resolve_chzzk_source

from .contracts import MediaToolError, ToolRequest

from .frames import build_contact_sheet

SCHEMA_VERSION = 1

CACHE_TTL_SEC = 24 * 60 * 60

FRAME_PROFILES = {
    "scene": {"height": 360, "tile_width": 640, "tile_height": 360},
    "ocr": {"height": 480, "tile_width": 854, "tile_height": 480},
}

RETRYABLE_CODES = {
    "source_unavailable",
    "media_timeout",
    "media_processing_failed",
    "media_probe_failed",
    "media_truncated",
    "frame_count_mismatch",
}

class SharedSourcePool:
    """Resolve one short-lived source per batch source shape."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: dict[tuple[str, int, bool], ResolvedChzzkSource] = {}
        self._key_locks: dict[tuple[str, int, bool], threading.Lock] = {}

    def resolve(
        self,
        video_no: str,
        *,
        target_height: int,
        prefer_audio_only: bool,
        cookies: Mapping[str, str] | None,
        refresh: bool = False,
    ) -> ResolvedChzzkSource:
        key = (video_no, target_height, prefer_audio_only)
        with self._lock:
            if not refresh and key in self._sources:
                return self._sources[key]
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                if not refresh and key in self._sources:
                    return self._sources[key]
            source = resolve_chzzk_source(
                video_no,
                target_height=target_height,
                prefer_audio_only=prefer_audio_only,
                cookies=cookies,
            )
            with self._lock:
                self._sources[key] = source
            return source

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)

def _tool_paths() -> tuple[str, str]:
    repo_root = Path(__file__).resolve().parents[2]
    runtime_bin = repo_root / ".runtime" / "ffmpeg-provider" / "bin"
    ffmpeg = shutil.which("ffmpeg") or str(runtime_bin / "ffmpeg.exe")
    ffprobe = shutil.which("ffprobe") or str(runtime_bin / "ffprobe.exe")
    if not Path(ffmpeg).is_file() and shutil.which("ffmpeg") is None:
        raise MediaToolError("ffmpeg_unavailable", "ffmpeg is not available in PATH or the repo runtime")
    if not Path(ffprobe).is_file() and shutil.which("ffprobe") is None:
        raise MediaToolError("ffprobe_unavailable", "ffprobe is not available in PATH or the repo runtime")
    return ffmpeg, ffprobe

def _run(command: list[str], *, timeout_sec: int) -> None:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("media_timeout", "media processing exceeded its bounded timeout") from exc
    except OSError as exc:
        raise MediaToolError("media_tool_unavailable", "the local media processor could not start") from exc
    if completed.returncode != 0:
        raise MediaToolError("media_processing_failed", "the selected CHZZK media window could not be processed")

def _window(source: ResolvedChzzkSource, request: ToolRequest) -> tuple[float, float]:
    if request.start_sec >= source.duration_sec:
        raise MediaToolError("window_out_of_range", "start_sec is beyond the end of the VOD")
    remaining = source.duration_sec - request.start_sec
    duration = remaining if request.duration_sec is None else min(request.duration_sec, remaining)
    if duration <= 0:
        raise MediaToolError("window_out_of_range", "the requested VOD window is empty")
    return request.start_sec, duration

def _timeout(duration_sec: float, *, minimum: int = 180) -> int:
    return max(minimum, min(6 * 60 * 60, int(duration_sec * 3 + 180)))

def _partial_for(path: Path) -> Path:
    return path.with_name(f"{path.stem}.partial{path.suffix}")

def _remote_input_args(ffmpeg: str, url: str) -> list[str]:
    options = ["-rw_timeout", "15000000", "-protocol_whitelist", "https,http,tcp,tls,crypto"]
    if urlsplit(url).path.lower().endswith(".m3u8"):
        options.extend(_hls_demuxer_args(ffmpeg))
    return options

def _download_audio(
    request: ToolRequest,
    source: ResolvedChzzkSource,
    cache_dir: Path,
    ffmpeg: str,
    start_sec: float,
    duration_sec: float,
) -> list[Path]:
    profile = request.audio_profile
    extension = {"stt": ".mka", "listen": ".mp3", "archive": ".ogg"}[profile]
    output = cache_dir / f"audio-{profile}{extension}"
    partial = _partial_for(output)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        *_remote_input_args(ffmpeg, source.media_url),
        "-i",
        source.media_url,
        "-t",
        f"{duration_sec:.3f}",
        "-map",
        "0:a:0",
        "-vn",
    ]
    if profile == "stt":


        command += ["-c:a", "copy"]
    elif profile == "listen":
        command += ["-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k"]
    else:
        command += ["-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k"]
    command.append(str(partial))
    _run(command, timeout_sec=_timeout(duration_sec))
    os.replace(partial, output)
    return [output]

def _download_video(
    source: ResolvedChzzkSource,
    cache_dir: Path,
    ffmpeg: str,
    start_sec: float,
    duration_sec: float,
    *,
    include_audio: bool = True,
) -> Path:
    output = cache_dir / "video-window.mp4"
    partial = _partial_for(output)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        *_remote_input_args(ffmpeg, source.media_url),
        "-i",
        source.media_url,
        "-t",
        f"{duration_sec:.3f}",
        "-map",
        "0:v:0",
    ]
    if include_audio:
        command += ["-map", "0:a:0?"]
    command += [
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(partial),
    ]
    _run(command, timeout_sec=_timeout(duration_sec))
    os.replace(partial, output)
    return output

def _probe_duration(path: Path, ffprobe: str) -> float:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        duration = float((completed.stdout or "").strip())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise MediaToolError("media_probe_failed", "the downloaded media duration could not be verified") from exc
    if completed.returncode != 0 or duration <= 0:
        raise MediaToolError("media_probe_failed", "the downloaded media duration could not be verified")
    return duration

def _validate_duration(expected_sec: float, observed_sec: float) -> None:
    tolerance = max(2.0, min(10.0, expected_sec * 0.02))
    if observed_sec + tolerance < expected_sec:
        raise MediaToolError("media_truncated", "the downloaded media ended before the requested window")

def _extract_frames(
    clip: Path,
    cache_dir: Path,
    ffmpeg: str,
    duration_sec: float,
    source_start_sec: float,
    output_height: int,
    *,
    local_start_sec: float = 0.0,
) -> tuple[list[Path], list[float]]:
    frames_dir = cache_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale_frame in frames_dir.glob("frame-*.jpg"):
        stale_frame.unlink(missing_ok=True)
    interval = duration_sec / 9.0
    first = interval / 2.0
    pattern = frames_dir / "frame-%02d.jpg"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{local_start_sec + first:.6f}",
        "-i",
        str(clip),
        "-vf",
        f"fps=1/{interval:.9f},scale=-2:{output_height}",
        "-frames:v",
        "9",
        "-q:v",
        "3",

        "-threads:v",
        "1",
        str(pattern),
    ]
    _run(command, timeout_sec=_timeout(duration_sec, minimum=120))
    frame_paths = sorted(frames_dir.glob("frame-*.jpg"))
    if len(frame_paths) != 9:
        raise MediaToolError("frame_count_mismatch", "the requested window did not yield exactly 9 frames")
    timestamps = [source_start_sec + first + interval * index for index in range(9)]
    return frame_paths, timestamps

def _artifact_rows(paths: list[Path], output_root: Path, kinds: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, kind in zip(paths, kinds):
        rows.append(
            {
                "name": path.name,
                "kind": kind,
                "relative_path": path.relative_to(output_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return rows

def _cached_receipt(
    receipt_path: Path,
    output_root: Path,
    request: ToolRequest,
) -> dict[str, Any] | None:
    if not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if int(receipt["schema_version"]) != SCHEMA_VERSION:
            return None
        created_epoch = float(receipt["created_epoch"])
        if time.time() - created_epoch > CACHE_TTL_SEC:
            return None
        artifacts: list[dict[str, Any]] = []
        for artifact in receipt["artifacts"]:
            relative_path = Path(str(artifact["relative_path"]))
            if relative_path.is_absolute():
                return None
            path = (output_root / relative_path).resolve()
            try:
                path.relative_to(output_root)
                path.relative_to(receipt_path.parent.resolve())
            except ValueError:
                return None
            if not path.is_file() or _sha256(path) != artifact["sha256"]:
                return None
            kind = str(artifact["kind"])
            if kind not in {"audio", "video", "frame", "contact_sheet"}:
                return None
            artifacts.append(
                {
                    "name": path.name,
                    "kind": kind,
                    "relative_path": path.relative_to(output_root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": str(artifact["sha256"]),
                }
            )
        actual_window = receipt["actual_window"]
        start_sec = float(actual_window["start_sec"])
        duration_sec = float(actual_window["duration_sec"])
        raw_height = receipt["selected_height"]
        selected_height = None if raw_height is None else int(raw_height)
        source_kind = str(receipt["source_kind"])
        if source_kind not in {"audio_only", "muxed_video"}:
            return None
        raw_bitrate = receipt["selected_audio_bitrate"]
        selected_audio_bitrate = None if raw_bitrate is None else int(raw_bitrate)
        observed_duration_sec = float(receipt["observed_duration_sec"])
        inspection_mode = str(receipt.get("inspection_mode") or "")
        output_frame_height = receipt.get("output_frame_height")
        if request.action == "frames":
            if inspection_mode != request.inspection_mode:
                return None
            output_frame_height = int(output_frame_height)
        else:
            inspection_mode = ""
            output_frame_height = None
        source_revision = str(receipt["source_revision_sha256"])
        if len(source_revision) != 64 or any(character not in "0123456789abcdef" for character in source_revision):
            return None
        frame_timestamps = [float(value) for value in receipt.get("frame_timestamps_sec", [])]
        kinds = [artifact["kind"] for artifact in artifacts]
        if request.action == "audio" and kinds != ["audio"]:
            return None
        if request.action == "video" and kinds != ["video"]:
            return None
        if request.action == "frames":
            if kinds.count("frame") != 9 or kinds.count("contact_sheet") != 1 or len(kinds) != 10:
                return None
            if len(frame_timestamps) != 9:
                return None
        elif frame_timestamps:
            return None
        tolerance = max(2.0, min(10.0, duration_sec * 0.02))
        if duration_sec <= 0 or observed_duration_sec + tolerance < duration_sec:
            return None
        if source_kind == "audio_only":
            if selected_height is not None or not selected_audio_bitrate or selected_audio_bitrate <= 0:
                return None
        elif selected_height is None or selected_height <= 0 or selected_audio_bitrate is not None:
            return None
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "request_id": "",
        "action": request.action,
        "video_no": request.video_no,
        "request_fingerprint": request.fingerprint,
        "requested_window": {
            "start_sec": request.start_sec,
            "duration_sec": request.duration_sec,
        },
        "actual_window": {"start_sec": start_sec, "duration_sec": duration_sec},
        "selected_height": selected_height,
        "source_kind": source_kind,
        "selected_audio_bitrate": selected_audio_bitrate,
        "observed_duration_sec": observed_duration_sec,
        "inspection_mode": inspection_mode,
        "output_frame_height": output_frame_height,
        "source_revision_sha256": source_revision,
        "frame_timestamps_sec": frame_timestamps,
        "artifacts": artifacts,
        "cache": "hit",
        "created_at": datetime.fromtimestamp(created_epoch, timezone.utc).isoformat(),
        "created_epoch": created_epoch,
    }

def acquire_request(
    request: ToolRequest,
    output_root: Path,
    *,
    cookies: Mapping[str, str] | None = None,
    max_attempts: int = 2,
    source_pool: SharedSourcePool | None = None,
) -> dict[str, Any]:
    """Acquire one request and return a URL-free, relative-path-only receipt."""

    output_root = output_root.resolve()
    cache_dir = output_root / request.video_no / request.fingerprint
    cache_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = cache_dir / "receipt.json"
    cached = _cached_receipt(receipt_path, output_root, request)
    if cached is not None:
        return cached

    ffmpeg, ffprobe = _tool_paths()
    last_error: MediaToolError | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            target_height = 144 if request.action == "audio" else 720
            prefer_audio_only = request.action == "audio"
            if source_pool is None:
                source = resolve_chzzk_source(
                    request.video_no,
                    target_height=target_height,
                    prefer_audio_only=prefer_audio_only,
                    cookies=cookies,
                )
            else:
                source = source_pool.resolve(
                    request.video_no,
                    target_height=target_height,
                    prefer_audio_only=prefer_audio_only,
                    cookies=cookies,
                    refresh=attempt > 1,
                )
            start_sec, duration_sec = _window(source, request)
            paths: list[Path]
            kinds: list[str]
            timestamps: list[float] = []
            inspection_mode = ""
            output_frame_height: int | None = None
            if request.action == "audio":
                paths = _download_audio(request, source, cache_dir, ffmpeg, start_sec, duration_sec)
                kinds = ["audio"]
                observed = _probe_duration(paths[0], ffprobe)
                _validate_duration(duration_sec, observed)
            elif request.action == "video":
                paths = [_download_video(source, cache_dir, ffmpeg, start_sec, duration_sec, include_audio=True)]
                kinds = ["video"]
                observed = _probe_duration(paths[0], ffprobe)
                _validate_duration(duration_sec, observed)
            else:
                clip = _download_video(source, cache_dir, ffmpeg, start_sec, duration_sec, include_audio=False)
                observed = _probe_duration(clip, ffprobe)
                _validate_duration(duration_sec, observed)
                extraction_duration = min(duration_sec, observed)
                profile = FRAME_PROFILES[request.inspection_mode]
                inspection_mode = request.inspection_mode
                output_frame_height = profile["height"]
                frame_paths, timestamps = _extract_frames(
                    clip,
                    cache_dir,
                    ffmpeg,
                    extraction_duration,
                    start_sec,
                    output_frame_height,
                )
                sheet = build_contact_sheet(
                    frame_paths,
                    timestamps,
                    cache_dir / "contact-sheet-3x3.jpg",
                    tile_width=profile["tile_width"],
                    tile_height=profile["tile_height"],
                )
                clip.unlink(missing_ok=True)
                paths = [*frame_paths, sheet]
                kinds = [*(["frame"] * 9), "contact_sheet"]

            created_epoch = time.time()
            receipt: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",


                "request_id": "",
                "action": request.action,
                "video_no": request.video_no,
                "request_fingerprint": request.fingerprint,
                "requested_window": {
                    "start_sec": request.start_sec,
                    "duration_sec": request.duration_sec,
                },
                "actual_window": {"start_sec": start_sec, "duration_sec": duration_sec},
                "selected_height": source.selected_height,
                "source_kind": source.source_kind,
                "selected_audio_bitrate": source.selected_audio_bitrate,
                "observed_duration_sec": round(observed, 3),
                "inspection_mode": inspection_mode,
                "output_frame_height": output_frame_height,
                "source_revision_sha256": source.source_revision,
                "frame_timestamps_sec": [round(value, 3) for value in timestamps],
                "artifacts": _artifact_rows(paths, output_root, kinds),
                "cache": "miss",
                "created_at": datetime.fromtimestamp(created_epoch, timezone.utc).isoformat(),
                "created_epoch": created_epoch,
            }
            _atomic_json(receipt_path, receipt)
            return receipt
        except MediaToolError as exc:
            last_error = exc
            for partial in cache_dir.rglob("*.partial.*"):
                partial.unlink(missing_ok=True)
            if exc.code not in RETRYABLE_CODES:
                break
    assert last_error is not None
    raise last_error
