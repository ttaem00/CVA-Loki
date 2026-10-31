"""Prepare one anonymous public CHZZK VOD for the selected caller; never author a report."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

SCHEMA = "loki.public-source.v1"
HEIGHTS = (144, 360, 480, 720, 1080)


def video_number(url):
    if re.fullmatch(r"[0-9]{1,16}", str(url)):
        return str(url)
    match = re.fullmatch(r"https://chzzk\.naver\.com/video/([0-9]{1,16})/?", str(url))
    if not match:
        raise ValueError("loki_single_public_vod_url_required")
    return match[1]


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def owned(root, value):
    value = str(value)
    if os.name == "nt" and value.startswith("\\\\?\\") and re.match(r"^[A-Za-z]:[\\/]", value[4:]):
        value = value[4:]
    path = Path(value)
    path = path if path.is_absolute() else root / path
    result = path.resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError("loki_source_path_outside_workspace")
    current = root
    for part in path.absolute().relative_to(root).parts:
        current /= part
        if current.is_symlink() or current.exists() and getattr(current.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("loki_source_link_forbidden")
    return result


def save(path, value):
    """Atomically update only this acquisition's own checkpoint/receipt."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def verify_media(ffprobe, media, expected_duration):
    result = subprocess.run([str(ffprobe), "-v", "error", "-show_entries",
        "format=duration,start_time:stream=codec_type,duration,start_time", "-of", "json", str(media)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    if result.returncode:
        raise ValueError("loki_source_media_probe_failed")
    probe = json.loads(result.stdout)
    streams = probe.get("streams", [])
    kinds = {row.get("codec_type") for row in streams}
    if not {"audio", "video"} <= kinds:
        raise ValueError("loki_source_audio_video_required")
    duration = float(probe.get("format", {}).get("duration", 0))
    tolerance = max(5, expected_duration * .005)
    if not math.isfinite(duration) or abs(duration - expected_duration) > tolerance:
        raise ValueError("loki_source_media_duration_mismatch")
    for stream in streams:
        value = stream.get("duration")
        if stream.get("codec_type") in {"audio", "video"} and value not in (None, "N/A"):
            if not math.isfinite(float(value)) or abs(float(value) - expected_duration) > tolerance:
                raise ValueError("loki_source_stream_duration_mismatch")
    return dict(duration_sec=duration, format_start_time=probe.get("format", {}).get("start_time"),
                streams=streams, time_basis="complete_vod_elapsed_seconds", source_start_sec=0)


@contextmanager
def download_processes(root, tools):
    """The existing downloader may probe local files; remote FFmpeg fallback is refused."""
    original = subprocess.run
    admitted = ContextVar("loki_source_probe", default=None)
    active = [True]
    allowed = {Path(row["path"]).resolve() for row in tools.values()}

    def validate(argv):
        if not isinstance(argv, (list, tuple)) or not argv or Path(str(argv[0])).resolve() not in allowed:
            raise PermissionError("loki_source_unadmitted_process")
        for argument in argv[1:]:
            text = str(argument)
            if "://" in text or re.match(r"^(?:https?|tcp|tls|crypto|concat|subfile|file):", text):
                raise PermissionError("loki_source_remote_ffmpeg_unavailable")

        for argument in argv[1:]:
            text = str(argument)
            if Path(text).is_absolute():
                owned(root, text)

    def audit(event, args):
        if not active[0]:
            return
        if event == "os.system":
            raise PermissionError("loki_source_shell_forbidden")
        if event == "subprocess.Popen":
            executable, argv = args[0], args[1]
            if args[3] is not None:
                raise PermissionError("loki_source_process_override")
            if isinstance(argv, str):
                bound = admitted.get()
                if os.name != "nt" or bound is None or argv != subprocess.list2cmdline(bound):
                    raise PermissionError("loki_source_process_argv")
                argv = bound
            validate(argv)
            if executable is not None and Path(str(executable)).resolve() != Path(str(argv[0])).resolve():
                raise PermissionError("loki_source_process_override")

    def run(argv, *args, **kwargs):
        if kwargs.get("shell") or kwargs.get("env") is not None or kwargs.get("executable"):
            raise PermissionError("loki_source_process_override")
        validate(argv)
        argv = [argv[0], "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,mpegts", *argv[1:]]
        token = admitted.set(tuple(argv))
        try:
            return original(argv, *args, **kwargs)
        finally:
            admitted.reset(token)

    sys.addaudithook(audit)
    subprocess.run = run
    try:
        yield
    finally:
        active[0] = False
        subprocess.run = original


def collect_chat(network, video, duration, target):
    """Optional anonymous chat never determines whether speech preparation succeeds."""
    meta_path = target.with_name(target.stem + ".meta.json")
    if any(path.exists() or path.is_symlink() for path in (target, meta_path)):
        return dict(status="preserved_existing", path=str(target), meta_path=str(meta_path))
    rows, cursor, seen, skipped = [], 0, set(), 0
    started = time.monotonic()
    status = "unavailable"
    try:
        for _ in range(300):
            if cursor in seen or time.monotonic() - started > 60:
                break
            seen.add(cursor)
            data = json.loads(network.public_document("https://api.chzzk.naver.com/service/v1/videos/" + video
                                                     + "/chats?playerMessageTime=" + str(cursor)))
            content = data.get("content") or {}
            if data.get("code") != 200:
                break
            page = content.get("videoChats") or []
            for item in page:
                ms = item.get("playerMessageTime")
                if type(ms) in (int, float) and math.isfinite(ms) and 0 <= ms <= duration * 1000:
                    rows.append(dict(ms=int(ms), msg=str(item.get("content") or "")[:2000]))
                else:
                    skipped += 1
            next_cursor = content.get("nextPlayerMessageTime")
            if not page or next_cursor is None:
                status = "partial" if skipped else "complete"
                break
            if type(next_cursor) is not int or next_cursor < 0 or len(rows) >= 200_000:
                break
            cursor = next_cursor
    except (ValueError, OSError, RuntimeError, PermissionError):
        pass
    if status != "complete":
        status = "partial" if rows else "unavailable"
    save(target, rows)
    save(meta_path, dict(status=status, video_no=video, message_count=len(rows), clock_basis="vod_player_time",
                         unbound_clock_count=skipped))
    return dict(status=status, path=str(target), meta_path=str(meta_path))


def run(workspace, vod, *, height=480, max_download_seconds=21600):
    video = video_number(vod)
    vod = "https://chzzk.naver.com/video/" + video
    if height not in HEIGHTS or type(max_download_seconds) is not int or not 60 <= max_download_seconds <= 21600:
        raise ValueError("loki_source_limits_invalid")
    root = Path(workspace).resolve(strict=True)
    from loki import configuration, validate_author_mode
    config = owned(root, root / "loki.json")
    root, work, policy = configuration(config)
    limit = min(policy["max_media_bytes"], 20_000_000_000)
    source = owned(root, work / video)
    source.mkdir(parents=True, exist_ok=True)
    checkpoint = owned(root, source / ".loki-source-preparation.json")
    lock = source / ".loki-source.lock"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    phase, state = "preflight", None
    old_env = {name: os.environ.get(name) for name in ("LOKI_WORKSPACE", "LOKI_SOURCE_PREPARATION", "PATH")}
    try:
        if checkpoint.exists():
            candidate = json.loads(checkpoint.read_text(encoding="utf-8"))
            if candidate.get("schema_version") != SCHEMA or candidate.get("vod") != vod.rstrip("/") or candidate.get("height") != height:
                raise ValueError("loki_source_resume_identity_changed")
            attempt = owned(root, candidate["attempt_directory"])
            if attempt.parent != source or not re.fullmatch(r"source-[a-f0-9]{12}", attempt.name):
                raise ValueError("loki_source_resume_attempt_changed")
            state = candidate
        else:
            attempt = owned(root, source / ("source-" + uuid.uuid4().hex[:12]))
            attempt.mkdir()
            state = dict(schema_version=SCHEMA, vod=vod.rstrip("/"), video_no=video, height=height,
                         attempt_directory=str(attempt), status="preparing")
            save(checkpoint, state)
        tools_path = owned(root, root / ".loki-tools.json")
        if tools_path.stat().st_size > 64_000:
            raise ValueError("loki_source_tools_receipt_invalid")
        tools = json.loads(tools_path.read_text(encoding="utf-8"))["tools"]
        binaries = {name: tools[name] for name in ("ffmpeg", "ffprobe")}
        for name, row in binaries.items():
            path = Path(row["path"]).resolve(strict=True)
            if (path.name.lower() not in {name, name + ".exe"} or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                    or digest(path) != row["sha256"]):
                raise ValueError("loki_source_tool_changed")
        os.environ["LOKI_WORKSPACE"] = str(root)
        os.environ["PATH"] = os.pathsep.join([*(str(Path(row["path"]).parent) for row in binaries.values()), os.environ.get("PATH", "")])
        from content import network
        if not hasattr(network, "public_requests"):
            raise ValueError("loki_source_installed_public_adapter_required")
        from pipeline import downloader
        if "media" not in state:
            phase = "public_download"
            os.environ["LOKI_SOURCE_PREPARATION"] = str(root)
            with network.public_requests(limit, max_download_seconds) as check, download_processes(root, binaries):
                _, _, _, _, _, metadata = network.NetworkManager.get_video_info(video, {}, public_only=True)
                duration = metadata["duration"]
                if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 86400:
                    raise ValueError("loki_source_duration_unavailable_or_exceeds_limit")
                def progress(*_args):
                    check()
                    if sum(p.stat().st_size for p in attempt.iterdir() if p.is_file()) > limit:
                        raise ValueError("loki_source_media_exceeds_limit")
                media = owned(root, downloader.download_vod_media(video, {}, str(attempt), progress_func=progress,
                    expected_duration_sec=int(duration), target_height=height))
                if not media.is_relative_to(attempt) or media.stat().st_size > limit:
                    raise ValueError("loki_source_media_outside_attempt_or_limit")
                clock = verify_media(binaries["ffprobe"]["path"], media, duration)
                if not downloader._is_valid_media_file(str(media), expected_duration_sec=int(duration), duration_tolerance=.995):
                    raise ValueError("loki_source_media_audio_tail_invalid")
                state.update(media=dict(path=str(media), sha256=digest(media), bytes=media.stat().st_size),
                             metadata=metadata, clock=clock)
                save(checkpoint, state)
                state["chat"] = collect_chat(network, video, duration, source / (video + "_chat.log.json"))
                save(checkpoint, state)
            os.environ.pop("LOKI_SOURCE_PREPARATION", None)
        media = owned(root, state["media"]["path"])
        if not media.is_relative_to(attempt) or digest(media) != state["media"]["sha256"]:
            raise ValueError("loki_source_cached_media_changed")
        phase = "local_stt"
        if "speech" not in state:
            from transcribe_source import run as transcribe
            state["speech"] = transcribe(root, media, attempt / "source.srt")
            save(checkpoint, state)
        srt = owned(root, state["speech"]["srt_path"])
        if not srt.is_relative_to(attempt) or ("srt_sha256" in state and digest(srt) != state["srt_sha256"]):
            raise ValueError("loki_source_cached_srt_changed")
        state["srt_sha256"] = digest(srt)
        metadata = state["metadata"]
        vod_info = dict(video_no=video, title=metadata["title"], channel_id=metadata["channelId"],
            channel_name=metadata["channelName"], duration=math.ceil(state["clock"]["duration_sec"]),
            publish_date=metadata["liveOpenDate"], broadcast_start_at=metadata["liveOpenDate"],
            replay_publish_at=metadata["publishDate"], source_url=vod.rstrip("/"))
        base = attempt / "base.md"
        if not base.exists():
            with base.open("x", encoding="utf-8") as stream:
                stream.write("")
        template = dict(vod_info=vod_info, source_work_dir=str(source), srt_path=str(srt),
                        media_path=str(media), base_path=str(base), job_id="vod-" + video + "-" + attempt.name)
        template_path = attempt / "prepare-spec.template.json"
        author_mode = policy["author_mode"]
        existing = None
        if template_path.exists():
            existing = json.loads(template_path.read_text(encoding="utf-8-sig"))
            if any(existing.get(key) != value for key, value in template.items()):
                raise ValueError("loki_source_prepare_template_changed")
        if state.get("chatgpt_session_id") and author_mode != "current_chatgpt":
            raise ValueError("loki_author_mode_mismatch")
        if existing is not None and "author" in existing:
            validate_author_mode(existing["author"], author_mode)
        if author_mode == "current_chatgpt":
            author = existing.get("author") if existing is not None else None
            pinned = state.get("chatgpt_session_id")
            if author is None:
                if pinned:
                    raise ValueError("loki_source_author_changed")
                author = dict(host="chatgpt", session_id=uuid.uuid4().hex,
                    actual_model="unknown", identity_basis="workspace_local")
            validate_author_mode(author, author_mode)
            if pinned is not None and pinned != author["session_id"]:
                raise ValueError("loki_source_author_changed")
            if existing is None or "author" not in existing:
                save(template_path, dict(existing or template, author=author))
            state["chatgpt_session_id"] = author["session_id"]
            save(checkpoint, state)
        elif existing is None:
            save(template_path, template)
        result = dict(status="ready", schema_version=SCHEMA, video_no=video, workspace=str(root),
            prepare_spec_path=str(template_path), author_required=author_mode == "current_codex", source_work_dir=str(source),
            media=state["media"], srt_path=str(srt), srt_sha256=state["srt_sha256"], clock=state["clock"],
            speech_receipt=state["speech"].get("receipt_path"), cookies_used=False, model_author_calls=0,
            optional_chat=state.get("chat"),
            next_action=("Current ChatGPT must read the prepared originals, author and submit; the session ID is workspace-local, not a platform identity."
                if author_mode == "current_chatgpt" else
                "Current Codex must add its actual author identity to the spec, then prepare/read/author/submit."))
        save(attempt / "source-result.json", result)
        state["status"] = "ready"
        save(checkpoint, state)
        return result
    except Exception as error:
        if state is not None:
            save(owned(root, state["attempt_directory"]) / ("failure-" + uuid.uuid4().hex[:12] + ".json"),
                 dict(status="FAIL", stage=phase, error=type(error).__name__, message=str(error),
                      local_prepared_source_fallback=True))
        raise
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        lock.unlink()


def main(argv=None):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--vod", required=True)
    parser.add_argument("--height", type=int, choices=HEIGHTS, default=480)
    parser.add_argument("--max-download-seconds", type=int, default=21600)
    args = parser.parse_args(argv)
    try:
        result = run(args.workspace, args.vod, height=args.height, max_download_seconds=args.max_download_seconds)
    except (OSError, ValueError, RuntimeError, KeyError, PermissionError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status="FAIL", error=str(error), local_prepared_source_fallback=True), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
