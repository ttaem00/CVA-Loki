"""Shared yt-dlp runtime with a public-only default acquisition entry point.

This is the shared consumer for Report Admin, scripts, and agent sessions. Its
public entry never imports browser state or user yt-dlp configuration. Downloads are
contained below one explicit output directory and missing public evidence is
recorded as MISS instead of being synthesized.
"""

from __future__ import annotations

import json

import math

import os

import re

import shutil

import subprocess

import sys

import time

from email import policy

from email.parser import BytesParser

from pathlib import Path

from typing import Any, Callable, Iterable

from tools.elio_preview.components.public_urls import validate_public_media_url

from .runtime_root import runtime_data_path

from .yt_dlp_runtime import windows_safe_filename_args

ACQUISITION_SCHEMA = "public_video_assets.v1"

SUPPORTED_ASSETS = frozenset(
    {"metadata", "heatmap", "thumbnail", "storyboard", "subtitles", "video", "audio", "frames", "chat"}
)

_SAFE_EXTENSIONS = frozenset({"json", "jpg", "jpeg", "png", "webp", "vtt", "srt", "mhtml", "mp4", "mkv", "webm", "m4a", "wav", "opus"})

_VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d{6})?$")

Runner = Callable[..., subprocess.CompletedProcess[str]]

def managed_yt_dlp_root() -> Path:
    """Return the one runtime-data location shared by all repository worktrees."""

    return runtime_data_path(".runtime", "youtube-pipeline", "yt-dlp")

def yt_dlp_js_runtime_args() -> tuple[str, ...]:
    """Select a supported local JS runtime; never inspect browser profiles."""

    deno = shutil.which("deno")
    if deno:
        return ("--js-runtimes", f"deno:{Path(deno).resolve()}")
    node = shutil.which("node")
    if node:
        try:
            proc = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=5, check=False
            )
            major = int((proc.stdout or "").strip().lstrip("v").split(".", 1)[0])
        except (OSError, ValueError, subprocess.SubprocessError):
            major = 0
        if major >= 22:
            return (
                "--js-runtimes",
                f"node:{Path(node).resolve()}",
                "--remote-components",
                "ejs:github",
            )
    return ()

def safe_yt_dlp_args() -> tuple[str, ...]:
    return (
        *windows_safe_filename_args(),
        "--no-playlist",
        "--no-cookies",
        "--no-cookies-from-browser",
        *yt_dlp_js_runtime_args(),
    )

def resolve_yt_dlp_command(*, managed_root: Path | None = None) -> tuple[str, ...]:
    """Prefer the verified shared executable, then the current Python runtime."""

    root = (managed_root or managed_yt_dlp_root()).resolve()
    managed = root / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if managed.is_file():
        return (str(managed),)
    interpreter = Path(sys.executable).resolve()
    try:
        import yt_dlp
    except ImportError:
        pass
    else:
        return (str(interpreter), "-m", "yt_dlp")
    executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if executable:
        return (str(Path(executable).resolve()),)
    raise RuntimeError("yt-dlp is unavailable; run scripts/first_run.py once")

def _run(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)

def _safe_output_dir(value: Path) -> Path:
    output = value.resolve()
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ValueError("output directory must be a real directory")
    output.mkdir(parents=True, exist_ok=True)
    return output

def _artifact_files(output: Path, before: set[Path]) -> list[Path]:
    files: list[Path] = []
    for candidate in output.rglob("*"):
        if not candidate.is_file() or candidate.resolve() in before:
            continue
        candidate.resolve().relative_to(output)
        if candidate.suffix.lower().lstrip(".") not in _SAFE_EXTENSIONS:
            continue
        files.append(candidate.resolve())
    return sorted(files)

def _public_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    scalar_fields = (
        "id", "webpage_url", "title", "description", "duration", "timestamp", "upload_date", "release_timestamp", "_type",
        "uploader", "uploader_id", "channel", "channel_id", "availability", "live_status",
        "view_count", "like_count", "comment_count", "age_limit",
    )
    cleaned = {key: payload.get(key) for key in scalar_fields if payload.get(key) is not None}
    if payload.get("_type") == "multi_video":
        cleaned["entries"] = [_public_metadata(row) for row in payload.get("entries", []) if isinstance(row, dict)]
    cleaned["chapters"] = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
    cleaned["subtitles"] = sorted((payload.get("subtitles") or {}).keys())
    cleaned["automatic_caption_languages"] = sorted((payload.get("automatic_captions") or {}).keys())
    cleaned["thumbnails"] = [
        {key: row.get(key) for key in ("id", "width", "height", "preference") if row.get(key) is not None}
        for row in (payload.get("thumbnails") or []) if isinstance(row, dict)
    ]
    cleaned["formats"] = [
        {key: row.get(key) for key in ("format_id", "ext", "protocol", "format_note", "width", "height", "fps", "vcodec", "acodec") if row.get(key) is not None}
        for row in (payload.get("formats") or []) if isinstance(row, dict)
    ]
    return cleaned

def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def extract_storyboard_sprites(mhtml_path: Path, output_dir: Path) -> list[Path]:
    """Extract composite storyboard images embedded in one yt-dlp MHTML file."""

    output = _safe_output_dir(output_dir)
    message = BytesParser(policy=policy.default).parsebytes(mhtml_path.read_bytes())
    written: list[Path] = []
    for part in message.walk():
        content_type = part.get_content_type().casefold()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        body = part.get_payload(decode=True)
        if not body:
            continue
        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
        target = output / f"sprite-{len(written) + 1:04d}{suffix}"
        target.write_bytes(body)
        written.append(target.resolve())
    return written

def _result(status: str, code: str, files: Iterable[Path] = (), **details: Any) -> dict[str, Any]:
    return {"status": status, "code": code, "files": [str(path) for path in files], **details}

def _usable_subtitle_files(files: Iterable[Path]) -> list[Path]:
    """Keep subtitle artifacts only when at least one real cue can be parsed.

    yt-dlp may return a non-zero code after successfully writing one requested
    language when another requested language is unavailable.  The per-asset
    result therefore has to describe the artifact we actually received, not
    only the aggregate process exit code.
    """

    from .youtube_subtitle_alignment import load_caption_file

    usable: list[Path] = []
    for path in files:
        if path.suffix.casefold() not in {".vtt", ".srt", ".json"}:
            continue
        try:
            if load_caption_file(path):
                usable.append(path)
        except (OSError, UnicodeError, ValueError):
            continue
    return usable

def _acquire_video_assets(
    url: str,
    output_dir: Path,
    *,
    assets: Iterable[str] = ("metadata",),
    sections: Iterable[tuple[float, float]] = (),
    frames: Iterable[float] = (),
    languages: Iterable[str] = ("ko", "en"),
    yt_dlp_command: tuple[str, ...] | None = None,
    runner: Runner = _run,
    frame_max_height: int = 480,
    time_budget_seconds: float | None = None,
    access_policy: dict[str, Any] | None = None,
    whole_video_bounds: dict[str, Any] | None = None,
    ffmpeg_location: str | None = None,
) -> dict[str, Any]:
    """Internal shared engine. Only the explicit membership module passes policy."""

    source_url = validate_public_media_url(url)
    requested = {str(item).strip().casefold() for item in assets}
    unknown = requested - SUPPORTED_ASSETS
    if unknown:
        raise ValueError(f"unsupported assets: {', '.join(sorted(unknown))}")
    if "chat" in requested:
        from .source_platforms import resolve_source
        if access_policy is not None or resolve_source(source_url).platform != "youtube":
            raise ValueError("replay chat requires an anonymous public YouTube source")
    section_list = [(float(start), float(end)) for start, end in sections]
    if any(start < 0 or end <= start or end - start > 3600 for start, end in section_list):
        raise ValueError("each section must be positive, ordered, and at most 3600 seconds")
    frame_list = [float(value) for value in frames]
    if any(value < 0 for value in frame_list):
        raise ValueError("frame timestamps must be non-negative")
    if "video" in requested and not section_list and whole_video_bounds is None:
        raise ValueError("video acquisition requires at least one bounded section")
    if whole_video_bounds is not None:
        if section_list or "video" not in requested:
            raise ValueError("whole-video acquisition cannot be combined with sections")
        if (whole_video_bounds.get("max_duration_sec") != 86400
                or type(whole_video_bounds.get("max_bytes")) is not int
                or not 0 < whole_video_bounds["max_bytes"] <= 16 * 1024**3
                or whole_video_bounds.get("max_height") not in {144, 360, 480, 720, 1080}):
            raise ValueError("unsupported whole-video bounds")
    if "audio" in requested and not section_list:
        raise ValueError("audio acquisition requires at least one bounded section")
    if "frames" in requested and not frame_list:
        raise ValueError("frame acquisition requires at least one timestamp")
    if frame_max_height not in {360, 480, 720}:
        raise ValueError("frame max height must be 360, 480, or 720")
    maximum_budget = 21600 if whole_video_bounds is not None else 300
    if time_budget_seconds is not None and not 5 <= float(time_budget_seconds) <= maximum_budget:
        raise ValueError(f"acquisition time budget must stay between 5 and {maximum_budget} seconds")

    deadline = (
        time.monotonic() + float(time_budget_seconds)
        if time_budget_seconds is not None else None
    )

    def command_timeout(default: int) -> int:
        if deadline is None:
            return default
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("public-media-acquisition", 0)
        return max(1, min(default, int(remaining + 0.999)))

    output = _safe_output_dir(output_dir)
    command_prefix = yt_dlp_command or resolve_yt_dlp_command()
    member = access_policy is not None
    base = [*command_prefix, *(access_policy["args"] if member else safe_yt_dlp_args())]
    if ffmpeg_location:
        base.extend(("--ffmpeg-location", ffmpeg_location))

    def asset_result(status, code, files=(), **details):
        return _result(status, code.replace("PUBLIC_", "MEMBER_", 1) if member else code, files, **details)

    budget_failures: list[dict[str, str]] = []

    def run_asset(command: list[str], default_timeout: int, asset: str):
        """Apply the caller's shared deadline without retrying timed-out work."""
        try:
            timeout = command_timeout(default_timeout)
        except subprocess.TimeoutExpired:
            budget_failures.append({"asset": asset, "reason": "TIME_BUDGET_EXHAUSTED"})
            return subprocess.CompletedProcess(command, 124, "", "")
        try:
            return runner(command, output, timeout)
        except subprocess.TimeoutExpired:
            if deadline is None:
                raise
            budget_failures.append({"asset": asset, "reason": "COMMAND_TIMEOUT"})
            return subprocess.CompletedProcess(command, 124, "", "")

    def asset_budget_details(asset: str) -> dict[str, Any]:
        failures = [row for row in budget_failures if row["asset"] == asset]
        return {"budget_exhausted": True, "first_failure": failures[0]} if failures else {}
    try:
        metadata_proc = runner(
            [*base, "--skip-download", "--dump-single-json", "--clean-info-json", "--", source_url],
            output,
            command_timeout(120),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("public yt-dlp metadata lookup exceeded time budget") from exc
    if metadata_proc.returncode != 0:
        raise RuntimeError("public yt-dlp metadata lookup failed")
    try:
        raw_metadata = json.loads(metadata_proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp metadata was not valid JSON") from exc
    if whole_video_bounds is not None:
        duration = float(raw_metadata.get("duration") or 0)
        if (not math.isfinite(duration) or not 0 < duration <= 86400 or raw_metadata.get("is_live")
                or raw_metadata.get("live_status") in {"is_live", "is_upcoming", "post_live"}):
            raise ValueError("whole-video preparation requires a completed video at most 24 hours long")
        if raw_metadata.get("_type") == "multi_video":
            entries = raw_metadata.get("entries") or []
            durations = [float(row.get("duration") or 0) for row in entries if isinstance(row, dict)]
            if (not entries or len(durations) != len(entries)
                    or any(not math.isfinite(value) or value <= 0 for value in durations)
                    or abs(sum(durations) - duration) > max(1, duration * .005)):
                raise ValueError("whole-video multi-part duration is incomplete")

    results: dict[str, Any] = {}
    if "metadata" in requested:
        metadata_path = output / "metadata.json"
        _write_json(metadata_path, {"schema_version": "youtube_member_metadata.v1" if member else "public_video_metadata.v1", **_public_metadata(raw_metadata)})
        results["metadata"] = asset_result("PASS", "PUBLIC_METADATA_READY", [metadata_path.resolve()])
    if "heatmap" in requested:
        heatmap = raw_metadata.get("heatmap")
        if isinstance(heatmap, list) and heatmap:
            heatmap_path = output / "heatmap.json"
            _write_json(heatmap_path, {"schema_version": "youtube_heatmap.v1", "source": "yt_dlp_authorized_watch_page" if member else "yt_dlp_public_watch_page", "points": heatmap})
            results["heatmap"] = asset_result("PASS", "PUBLIC_HEATMAP_READY", [heatmap_path.resolve()], point_count=len(heatmap))
        else:
            results["heatmap"] = asset_result("MISS", "PUBLIC_HEATMAP_NOT_EXPOSED")

    if "chat" in requested:
        if raw_metadata.get("live_status") in {"is_live", "is_upcoming", "post_live"} or raw_metadata.get("is_live"):
            raise ValueError("replay chat requires a completed video")
        chat_formats = (raw_metadata.get("subtitles") or {}).get("live_chat") or []
        if not any(isinstance(row, dict) and row.get("protocol") == "youtube_live_chat_replay" for row in chat_formats):
            results["chat"] = asset_result("MISS", "PUBLIC_CHAT_NOT_EXPOSED")
        else:
            before = {path.resolve() for path in output.rglob("*") if path.is_file()}
            proc = run_asset([*base, "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                "--sub-format", "json", "--socket-timeout", "10", "--retries", "0", "--fragment-retries", "0",
                "-o", "replay.%(ext)s", "--", source_url], 60, "chat")


            files = sorted(path.resolve() for path in output.glob("replay.live_chat.json*")
                           if path.is_file() and not path.is_symlink() and path.resolve() not in before
                           and path.name in {"replay.live_chat.json", "replay.live_chat.json.part"})
            complete = proc.returncode == 0 and any(path.name.endswith(".json") for path in files)
            status = "PASS" if complete else "PARTIAL" if files else "FAIL"
            results["chat"] = asset_result(status, "PUBLIC_CHAT_" + ("READY" if complete else "PARTIAL" if files else "FAILED"),
                files, returncode=proc.returncode, **asset_budget_details("chat"))

    simple_commands = {
        "thumbnail": ["--skip-download", "--write-thumbnail", "-o", "thumbnail.%(ext)s"],
        "subtitles": ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", ",".join(languages), "--sub-format", "vtt/srt/json3", "-o", "subtitles.%(ext)s"],
    }
    for asset, args in simple_commands.items():
        if asset not in requested:
            continue
        before = {path.resolve() for path in output.rglob("*") if path.is_file()}
        proc = run_asset([*base, *args, "--", source_url], 300, asset)
        files = _artifact_files(output, before)
        usable_files = _usable_subtitle_files(files) if asset == "subtitles" else files
        ready = bool(usable_files) and (proc.returncode == 0 or asset == "subtitles")
        code = f"PUBLIC_{asset.upper()}_{'READY' if ready else 'NOT_AVAILABLE'}"
        if asset == "subtitles" and ready and proc.returncode != 0:
            code = "PUBLIC_SUBTITLES_READY_PARTIAL_LANGUAGES"
        results[asset] = asset_result(
            "PASS" if ready else "FAIL" if asset_budget_details(asset) else "MISS",
            code,
            usable_files if asset == "subtitles" else files,
            returncode=proc.returncode,
            **asset_budget_details(asset),
        )

    if "storyboard" in requested:
        storyboard_formats = [
            row for row in (raw_metadata.get("formats") or [])
            if isinstance(row, dict) and (row.get("protocol") == "mhtml" or "storyboard" in str(row.get("format_note") or "").casefold())
        ]
        if not storyboard_formats:
            results["storyboard"] = asset_result("MISS", "PUBLIC_STORYBOARD_NOT_EXPOSED")
        else:
            chosen = max(storyboard_formats, key=lambda row: (int(row.get("height") or 0), int(row.get("width") or 0)))
            storyboard_dir = output / "storyboards"
            storyboard_dir.mkdir(exist_ok=True)
            before = {path.resolve() for path in output.rglob("*") if path.is_file()}
            proc = run_asset([*base, "-f", str(chosen["format_id"]), "-o", "storyboards/storyboard.%(ext)s", "--", source_url], 600, "storyboard")
            files = _artifact_files(output, before)
            mhtml = next((path for path in files if path.suffix.casefold() == ".mhtml"), None)
            sprites = extract_storyboard_sprites(mhtml, storyboard_dir / "sprites") if mhtml else []
            all_files = [*files, *sprites]
            ready = proc.returncode == 0 and bool(mhtml and sprites)
            results["storyboard"] = asset_result(
                "PASS" if ready else "FAIL" if asset_budget_details("storyboard") else "MISS",
                "PUBLIC_STORYBOARD_READY" if ready else "PUBLIC_STORYBOARD_INCOMPLETE",
                all_files,
                returncode=proc.returncode,
                format_id=chosen.get("format_id"),
                sprite_count=len(sprites),
                tile_width=int(chosen.get("width") or 0),
                tile_height=int(chosen.get("height") or 0),
                storyboard_fps=float(chosen.get("fps") or 0),
                **asset_budget_details("storyboard"),
            )

    if "video" in requested:
        clip_dir = output / "clips"
        clip_dir.mkdir(exist_ok=True)
        all_files: list[Path] = []
        failures = 0
        if whole_video_bounds is not None:
            before = {path.resolve() for path in output.rglob("*") if path.is_file()}
            height = whole_video_bounds["max_height"]
            multi = raw_metadata.get("_type") == "multi_video"
            output_args = (["--concat-playlist", "always", "-o", "clips/whole-%(playlist_index)03d.%(ext)s",
                            "-o", "pl_video:clips/whole.%(ext)s"] if multi else ["-o", "clips/whole.%(ext)s"])
            proc = run_asset([*base, "-f", f"bv*[height<={height}]+ba/b[height<={height}]",
                "--max-filesize", str(whole_video_bounds["max_bytes"]), "--socket-timeout", "30",
                "--retries", "3", "--fragment-retries", "3", "--merge-output-format", "mp4",
                *output_args, "--", source_url], maximum_budget if time_budget_seconds is not None else 1800, "video")
            actual = _artifact_files(output, before)
            all_files.extend(path for path in actual if path.parent == clip_dir.resolve()
                             and path.stem == "whole" and path.suffix in {".mp4", ".mkv", ".webm"})
            failures += int(proc.returncode != 0 or not all_files
                            or sum(path.stat().st_size for path in actual) > whole_video_bounds["max_bytes"])
        for index, (start, end) in enumerate(section_list, 1):
            before = {path.resolve() for path in output.rglob("*") if path.is_file()}
            proc = run_asset([*base, "--download-sections", f"*{start:g}-{end:g}", "--force-keyframes-at-cuts", "--downloader", "ffmpeg", "-f", "bestvideo[protocol^=m3u8][height<=360]+bestaudio[protocol^=m3u8]/bv*[height<=360]+ba/b[height<=360]", "--merge-output-format", "mp4", "-o", f"clips/clip-{index:03d}.%(ext)s", "--", source_url], 1800, "video")
            files = _artifact_files(output, before)
            all_files.extend(files)
            failures += int(proc.returncode != 0 or not files)
        results["video"] = asset_result("PASS" if all_files and failures == 0 else "FAIL", "PUBLIC_VIDEO_SECTIONS_READY" if all_files and failures == 0 else "PUBLIC_VIDEO_SECTION_FAILED", all_files, requested_count=len(section_list), failed_count=failures, **asset_budget_details("video"))
        if whole_video_bounds is not None:
            results["video"].update(code=("MEMBER_" if member else "PUBLIC_") + ("WHOLE_VIDEO_READY" if failures == 0 else "WHOLE_VIDEO_FAILED"),
                                    whole_video=True, bounds=dict(whole_video_bounds), requested_count=1)

    if "audio" in requested:
        audio_dir = output / "audio"
        audio_dir.mkdir(exist_ok=True)
        all_files = []
        failures = 0
        for index, (start, end) in enumerate(section_list, 1):
            before = {path.resolve() for path in output.rglob("*") if path.is_file()}
            proc = run_asset([*base, "--download-sections", f"*{start:g}-{end:g}", "--downloader", "ffmpeg", "-f", "bestaudio[protocol^=m3u8]/bestaudio/b", "--extract-audio", "--audio-format", "wav", "-o", f"audio/audio-{index:03d}.%(ext)s", "--", source_url], 1800, "audio")
            files = _artifact_files(output, before)
            all_files.extend(files)
            failures += int(proc.returncode != 0 or not files)
        results["audio"] = asset_result("PASS" if all_files and failures == 0 else "FAIL", "PUBLIC_AUDIO_SECTIONS_READY" if all_files and failures == 0 else "PUBLIC_AUDIO_SECTION_FAILED", all_files, requested_count=len(section_list), failed_count=failures, **asset_budget_details("audio"))

    if "frames" in requested:
        frame_dir = output / "frames"
        frame_dir.mkdir(exist_ok=True)
        source_dir = output / "frame-sources"
        source_dir.mkdir(exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        written: list[Path] = []
        budget_exhausted = False
        if ffmpeg:
            for index, timestamp in enumerate(frame_list, 1):
                try:
                    remaining_timeout = command_timeout(600)
                except subprocess.TimeoutExpired:
                    budget_exhausted = True
                    break
                before = {path.resolve() for path in output.rglob("*") if path.is_file()}
                start = max(0.0, timestamp - 1.0)
                end = timestamp + 2.0
                try:
                    media = runner(
                        [*base, "--download-sections", f"*{start:g}-{end:g}", "--force-keyframes-at-cuts", "--downloader", "ffmpeg", "-f", f"bestvideo[protocol^=m3u8][height<={frame_max_height}]/bv*[height<={frame_max_height}]/b[height<={frame_max_height}]", "--merge-output-format", "mp4", "-o", f"frame-sources/source-{index:03d}.%(ext)s", "--", source_url],
                        output,
                        remaining_timeout,
                    )
                except subprocess.TimeoutExpired:
                    budget_exhausted = True
                    break
                candidates = [path for path in _artifact_files(output, before) if path.parent == source_dir.resolve()]
                source = next((path for path in candidates if path.suffix.casefold() in {".mp4", ".mkv", ".webm"}), None)
                if media.returncode != 0 or source is None:
                    continue
                target = frame_dir / f"frame-{index:03d}-{int(timestamp):010d}.jpg"
                try:
                    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp - start:g}", "-i", str(source), "-vf", f"scale=-2:min(ih\\,{frame_max_height})", "-frames:v", "1", "-y", str(target)], cwd=output, capture_output=True, text=True, timeout=command_timeout(300), check=False)
                except subprocess.TimeoutExpired:
                    budget_exhausted = True
                    break
                if proc.returncode == 0 and target.is_file():
                    written.append(target.resolve())
        results["frames"] = asset_result("PASS" if len(written) == len(frame_list) else "FAIL", "PUBLIC_FRAMES_READY" if len(written) == len(frame_list) else "PUBLIC_FRAME_FAILED", written, requested_count=len(frame_list), frame_max_height=frame_max_height, time_budget_seconds=time_budget_seconds, budget_exhausted=budget_exhausted)

    try:
        yt_dlp_version = _yt_dlp_version(
            command_prefix, output, runner, timeout=command_timeout(15),
        )
    except subprocess.TimeoutExpired:
        yt_dlp_version = None
    manifest = {
        "schema_version": "youtube_member_assets.v1" if member else ACQUISITION_SCHEMA,
        "source_url": source_url,
        "video_id": raw_metadata.get("id"),
        "yt_dlp_version": yt_dlp_version,
        "credential_route_used": member,
        "user_config_used": False,
        "netrc_used": False,
        "results": results,
    }
    if budget_failures:
        manifest["first_failure"] = dict(budget_failures[0])
        manifest["budget_failures"] = budget_failures
    if member:
        manifest.update(access="explicit_member", visibility="private",
                        authorization=access_policy["provenance"])
    manifest_path = output / "acquisition-manifest.json"
    _write_json(manifest_path, manifest)
    manifest["manifest_path"] = str(manifest_path.resolve())
    return manifest

def acquire_public_video_assets(
    url: str, output_dir: Path, *, assets: Iterable[str] = ("metadata",),
    sections: Iterable[tuple[float, float]] = (), frames: Iterable[float] = (),
    languages: Iterable[str] = ("ko", "en"), yt_dlp_command: tuple[str, ...] | None = None,
    runner: Runner = _run, frame_max_height: int = 480,
    time_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Acquire public-only assets; this API never accepts an auth policy."""
    return _acquire_video_assets(url, output_dir, assets=assets, sections=sections,
        frames=frames, languages=languages, yt_dlp_command=yt_dlp_command, runner=runner,
        frame_max_height=frame_max_height, time_budget_seconds=time_budget_seconds)

def acquire_public_whole_video(
    url: str, output_dir: Path, *, max_height: int = 360,
    yt_dlp_command: tuple[str, ...] | None = None, runner: Runner = _run,
    platform: str = "youtube", max_bytes: int = 16 * 1024**3,
    time_budget_seconds: float | None = None, ffmpeg_location: str | None = None,
) -> dict[str, Any]:
    """Explicit whole public VOD preparation, bounded to 24h/16GiB and a height cap.

    The ordinary public asset API still requires explicit bounded sections.
    This path preserves original media timing without segment re-encoding.
    """
    from .source_platforms import resolve_source
    reference = resolve_source(url)
    if platform not in {"youtube", "soop"} or reference.platform != platform:
        raise ValueError("whole-video source platform mismatch")
    source = reference.url
    return _acquire_video_assets(source, output_dir, assets=("metadata", "video"),
        yt_dlp_command=yt_dlp_command, runner=runner,
        time_budget_seconds=time_budget_seconds, ffmpeg_location=ffmpeg_location,
        whole_video_bounds={"max_duration_sec": 86400, "max_bytes": max_bytes, "max_height": max_height})

def _yt_dlp_version(
    command: tuple[str, ...], cwd: Path, runner: Runner, *, timeout: int = 15,
) -> str | None:
    proc = runner([*command, "--ignore-config", "--version"], cwd, timeout)
    value = (proc.stdout or "").strip()
    return value if proc.returncode == 0 and _VERSION_RE.fullmatch(value) else None
