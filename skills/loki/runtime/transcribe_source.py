"""Run the existing local Whisper and anonymous F2 pipeline on one owned video."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

try:
    from . import loki, speech_setup
except ImportError:
    import loki
    import speech_setup

SCHEMA = "loki.local-transcription.v1"
PROTOCOL = ["-protocol_whitelist", "file,pipe"]
FORMAT = ["-format_whitelist", "mov,matroska,webm,wav"]


def _owned(root, path):
    result = speech_setup.plain_path(path)
    if result == root or not result.is_relative_to(root):
        raise ValueError("speech_source_path_outside_workspace")
    return result


def _media_paths(root, work, video_path, output_path):
    video = _owned(root, video_path)
    output = _owned(root, output_path)
    if (not video.is_relative_to(work) or not video.relative_to(work).parts[0].isdigit()
            or video.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"} or not video.is_file()):
        raise ValueError("speech_source_owned_video_required")
    if output.parent != video.parent or output.suffix.lower() != ".srt":
        raise ValueError("speech_source_sibling_srt_required")
    if output.exists():
        raise FileExistsError("speech_source_srt_will_not_be_overwritten")
    return video, output


def _tools(root):
    receipt = _owned(root, root / ".loki-tools.json")
    if receipt.stat().st_size > 16_000:
        raise ValueError("speech_tool_receipt_too_large")
    value = json.loads(receipt.read_text(encoding="utf-8"))
    if value.get("schema_version") != "loki.local-tools.v1":
        raise ValueError("speech_tool_receipt_schema")
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        row = value["tools"][name]
        binary = Path(row["path"]).resolve(strict=True)
        if not binary.is_file() or speech_setup.digest(binary) != row["sha256"]:
            raise ValueError("speech_tool_changed: " + name)
        result[name] = binary
    return result


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0 <= number <= 1_000_000


def _validate_command(argv, root, work, tools, speaker_models):
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(p, (str, os.PathLike)) for p in argv):
        raise PermissionError("speech_process_argv_required")
    args = [os.fspath(p) for p in argv]
    executable = Path(args[0]).resolve()

    def media(path):
        candidate = _owned(root, path)
        if not candidate.is_relative_to(work):
            raise PermissionError("speech_process_path_outside_work")
        return candidate

    if executable == Path(sys.executable).resolve():
        if (len(args) != 11 or args[1:4] != ["-m", "pipeline.sherpa_turn_worker", "--wav"]
                or args[5] != "--segmentation-model" or args[7] != "--embedding-model" or args[9] != "--output"
                or Path(args[6]).resolve() != speaker_models[0] or Path(args[8]).resolve() != speaker_models[1]
                or media(args[4]).suffix.lower() != ".wav" or media(args[10]).suffix.lower() != ".json"):
            raise PermissionError("speech_only_pinned_sherpa_worker_is_admitted")
        return args
    if executable not in tools.values() or args[1:5] != [*PROTOCOL, *FORMAT]:
        raise PermissionError("speech_only_local_media_tools_are_admitted")
    tail = args[5:]
    if executable == tools["ffprobe"]:
        if len(tail) < 3:
            raise PermissionError("speech_probe_arguments")
        media(tail[-1])
        prefix = tail[:-1]
        patterns = [
            ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"],
            ["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1"],
            ["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0"],
        ]
        packet_probe = (len(prefix) == 10 and prefix[:3] == ["-v", "error", "-read_intervals"]
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%\+[0-9]+(?:\.[0-9]+)?", prefix[3])
            and prefix[4:] == ["-select_streams", "a:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0"])
        if prefix not in patterns and not packet_probe:
            raise PermissionError("speech_probe_arguments")
        return args
    valid = False
    if len(tail) in {10, 14} and tail[:2] == ["-y", "-i"]:
        media(tail[2]); media(tail[-1])
        valid = (tail[3:4] == ["-ss"] and _number(tail[4]) and tail[5:6] == ["-t"] and _number(tail[6])
            and (tail[7:-1] == ["-c", "copy"] or tail[7:-1] == ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k"]))
    if len(tail) == 11 and tail[:2] == ["-y", "-i"]:
        media(tail[2]); media(tail[-1])
        valid = tail[3:-1] == ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"]
    if len(tail) == 20 and tail[:3] == ["-v", "error", "-ss"]:
        media(tail[5])
        valid = (_number(tail[3]) and tail[4] == "-i" and tail[6] == "-t" and _number(tail[7])
            and tail[8:] == ["-vn", "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "-"])
    if not valid:
        raise PermissionError("speech_ffmpeg_arguments")
    return args


@contextmanager
def _offline_processes(root, work, tools, speaker_models):
    """Limit this preparation process to the existing decoder and F2 worker commands."""
    original_run, original_popen = subprocess.run, subprocess.Popen
    original_init = original_popen.__init__
    admitted = ContextVar("loki_speech_process", default=None)
    active = [True]

    def worker_cwd(argv, cwd):
        if Path(argv[0]).resolve() == Path(sys.executable).resolve():
            if cwd is None or Path(cwd).resolve() != Path(__file__).resolve().parent:
                raise PermissionError("speech_worker_runtime_cwd_required")

    def audit(event, args):
        if not active[0]:
            return
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "os.system"}:
            raise PermissionError("speech_offline_policy: " + event)
        if event == "subprocess.Popen":
            executable, argv = args[0], args[1]
            if isinstance(argv, str):
                bound = admitted.get()
                if os.name != "nt" or bound is None or argv != subprocess.list2cmdline(bound):
                    raise PermissionError("speech_process_argv_required")
                argv = bound
            _validate_command(argv, root, work, tools, speaker_models)
            worker_cwd(argv, args[2])
            if args[3] is not None or executable is not None and Path(executable).resolve() != Path(argv[0]).resolve():
                raise PermissionError("speech_process_override_forbidden")

    def popen_init(process, argv, *args, **kwargs):
        if kwargs.get("shell") or kwargs.get("env") is not None or kwargs.get("executable"):
            raise PermissionError("speech_process_override_forbidden")
        bound = _validate_command(argv, root, work, tools, speaker_models)
        worker_cwd(bound, kwargs.get("cwd"))
        token = admitted.set(tuple(bound))
        try:
            original_init(process, bound, *args, **kwargs)
        finally:
            admitted.reset(token)

    def run(argv, *args, **kwargs):
        if not isinstance(argv, (list, tuple)) or not argv:
            raise PermissionError("speech_process_argv_required")
        if Path(argv[0]).resolve() in tools.values():
            argv = [str(Path(argv[0]).resolve()), *PROTOCOL, *FORMAT, *argv[1:]]
        return original_run(argv, *args, **kwargs)

    sys.addaudithook(audit)


    original_popen.__init__, subprocess.run = popen_init, run
    try:
        yield
    finally:
        active[0] = False
        original_popen.__init__, subprocess.run = original_init, original_run


def run(workspace, video_path, srt_output_path, *, initial_prompt_text=None):
    root = speech_setup.plain_path(workspace)
    _, work, config = loki.configuration(root / "loki.json")
    video, output = _media_paths(root, work, video_path, srt_output_path)
    if video.stat().st_size > config["max_media_bytes"]:
        raise ValueError("speech_source_exceeds_media_policy")
    receipt = _owned(root, output.with_suffix(".loki-transcription.json"))
    if receipt.exists():
        raise FileExistsError("speech_source_receipt_will_not_be_overwritten")
    runtime, model = speech_setup.load_runtime(root)
    tools = _tools(root)
    original_hash = speech_setup.digest(video)
    environment = dict(LOKI_WORKSPACE=str(root), LOKI_WORK_DIR=str(work), HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1",
        HF_HOME=str(root / ".models/huggingface-speech"), HF_TOKEN_PATH=str(root / ".models/huggingface-speech/token"),
        TORCH_HOME=str(root / ".models/torch"), DO_NOT_TRACK="1")
    prior = {name: os.environ.get(name) for name in (*environment, "LOKI_SOURCE_PREPARATION", "WHISPER_VAD_PRESCAN_WORKERS")}
    os.environ.update(environment)
    os.environ.pop("LOKI_SOURCE_PREPARATION", None)
    os.environ.pop("WHISPER_VAD_PRESCAN_WORKERS", None)
    try:
        from pipeline import speaker_turn_runtime as turns
        if Path(turns.__file__).resolve() != Path(__file__).resolve().parent / "pipeline/speaker_turn_runtime.py":
            raise ValueError("speech_installed_public_runtime_required")
        speaker_models = tuple(path.resolve() for path in turns.ensure_sherpa_models(allow_download=False))
        with _offline_processes(root, work, tools, speaker_models), redirect_stdout(sys.stderr):
            from pipeline.transcriber import transcribe_video
            actual = transcribe_video(str(video), initial_prompt_text=initial_prompt_text,
                vad_prescan_workers=1, whisper_model="large-v3-turbo", speaker_turn_split_enabled=True,
                nemotron_turns_enabled=False, subtitle_profile=None, srt_output_path=str(output))
        if _owned(root, actual) != output or not output.is_file():
            raise ValueError("speech_source_did_not_return_requested_srt")
        from pipeline.asr_segments import parse_srt_segments
        segments = parse_srt_segments(output.read_text(encoding="utf-8-sig"))
        if speech_setup.digest(video) != original_hash:
            raise ValueError("speech_source_video_changed")
        turn_metadata = _owned(root, output.with_suffix(".speaker_turns.json"))
        speaker_turns = dict(enabled=True, status="not_reported", metadata_ref=None)
        if turn_metadata.is_file():
            if turn_metadata.stat().st_size > 5_000_000:
                raise ValueError("speech_speaker_metadata_too_large")
            payload = json.loads(turn_metadata.read_text(encoding="utf-8"))
            speaker_turns.update(status=payload.get("status", "unknown"),
                metadata_ref=dict(path=str(turn_metadata), sha256=speech_setup.digest(turn_metadata)))
        result = dict(status="ready", schema_version=SCHEMA, srt_path=str(output), receipt_path=str(receipt),
            source_path=str(video), source_sha256=original_hash, srt_sha256=speech_setup.digest(output),
            model=dict(id=model["model"], revision=model["revision"]), device=runtime["device"],
            runtime_ref=json.loads((root / ".loki-speech.json").read_text(encoding="utf-8")),
            segments=len(segments), vad_prescan_workers=1, speaker_turn_split_enabled=True, speaker_turns=speaker_turns,
            nemotron_turns_enabled=False, clock="source_video_seconds", author_model_calls=0)
        speech_setup.write_json(receipt, result)
        return result
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--initial-prompt")
    args = parser.parse_args(argv)
    try:
        result = run(args.workspace, args.video, args.output, initial_prompt_text=args.initial_prompt)
    except (ValueError, OSError, RuntimeError, ImportError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status="failed", error=str(error)), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
