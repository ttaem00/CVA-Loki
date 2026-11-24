"""Opt-in, non-blocking short-clip audiovisual verification via gemini-api-cli.

Never replaces EvidenceWorld original reads, never gates submit(), and degrades
cleanly to status='unavailable' when gemini-api is absent or unauthenticated.
"""

from __future__ import annotations

import argparse

import json

from pathlib import Path

import shutil

import subprocess

import time

from .evidence_world.contracts import canonical_json, digest, file_hash, native_path

from .evidence_world.index import atomic_json

SCHEMA = "chzz.editorial_short_video_check.v1"

MAX_CLIP_SEC = 60.0

CLOCK_TOLERANCE_SEC = 1.0

DEFAULT_MODEL = "gemini-2.5-flash"

MODE_MAX_CALLS = {"lite": 2, "normal": 5, "full": 10}

DEFAULT_MAX_CALLS = 5

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "observed_action": {"type": "string"},
        "audible_cues": {"type": "string"},
        "uncertainty": {"type": "string"},
        "supports_claim": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "start_offset_sec": {"type": "number"},
        "end_offset_sec": {"type": "number"},
    },
    "required": [
        "observed_action",
        "audible_cues",
        "uncertainty",
        "supports_claim",
        "start_offset_sec",
        "end_offset_sec",
    ],
    "additionalProperties": False,
}

def resolve_max_calls(execution_mode: str | None = None) -> int:
    if isinstance(execution_mode, str) and execution_mode in MODE_MAX_CALLS:
        return MODE_MAX_CALLS[execution_mode]
    return DEFAULT_MAX_CALLS

def _ledger_path(job_root: Path) -> Path:
    return native_path(job_root).resolve() / "short-video-checks" / "ledger.json"

def load_ledger(job_root: Path) -> dict:
    path = _ledger_path(job_root)
    if not path.is_file():
        return {"schema_version": "chzz.short_video_check_ledger.v1", "calls": []}
    return json.loads(path.read_text(encoding="utf-8"))

def check_gemini_api_available(*, binary: str = "gemini-api", timeout_sec: int = 10) -> dict:
    """Check whether gemini-api binary exists and has active credentials."""
    resolved = shutil.which(binary)
    if not resolved:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "gemini_api_cli_not_installed",
            "binary": None,
        }
    try:
        proc = subprocess.run(
            [resolved, "whoami", "-o", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "status": "unavailable",
            "reason": f"gemini_api_whoami_error:{type(exc).__name__}",
            "binary": resolved,
        }
    if proc.returncode != 0:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "gemini_api_auth_required",
            "binary": resolved,
            "stderr": (proc.stderr or "").strip()[:300],
        }
    return {
        "available": True,
        "status": "ready",
        "reason": None,
        "binary": resolved,
    }

def validate_clip_window(start_sec: float, end_sec: float, *, vod_duration_sec: float | None = None) -> tuple[float, float, float]:
    start = float(start_sec)
    end = float(end_sec)
    if start < 0 or end <= start:
        raise ValueError("short_video_check_window_invalid")
    duration = end - start
    if duration > MAX_CLIP_SEC:
        raise ValueError(f"short_video_check_exceeds_max_clip_sec:{MAX_CLIP_SEC}")
    if vod_duration_sec is not None and end > float(vod_duration_sec) + CLOCK_TOLERANCE_SEC:
        raise ValueError("short_video_check_window_outside_vod")
    return round(start, 3), round(end, 3), round(duration, 3)

def slice_short_clip(
    *,
    media_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    ffmpeg_path: str | None = None,
) -> Path:
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg_not_available")
    media = native_path(media_path).resolve()
    if not media.is_file():
        raise FileNotFoundError(str(media))
    out = native_path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(media),
        "-t",
        f"{duration_sec:.3f}",
        "-vf",
        "scale=-2:480,fps=5",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"short_clip_slice_failed:{(proc.stderr or '').strip()[:300]}")
    return out

def build_gemini_api_command(
    *,
    binary: str,
    model: str,
    clip_path: Path,
    question: str,
    start_sec: float,
    end_sec: float,
) -> tuple[list[str], dict]:
    prompt = (
        f"Inspect this short clip ({start_sec:.1f}s to {end_sec:.1f}s) to answer one specific "
        f"audiovisual verification question without inventing unseen events.\n"
        f"Question: {question}\n"
        f"Return ONLY valid JSON matching the required schema."
    )
    payload_meta = {
        "model": model,
        "store": False,
        "response_format": RESPONSE_SCHEMA,
        "prompt": prompt,
        "clip_path": str(clip_path),
    }
    command = [
        binary,
        "generate",
        "-m",
        model,
        "--stream=false",
        "--no-interactive",
        "-o",
        "json",
        "--file",
        str(clip_path),
        "--response-schema",
        json.dumps(RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":")),
        "--no-store",
        prompt,
    ]
    return command, payload_meta

def _unavailable_response(*, reason: str, start_sec: float, end_sec: float, question: str, model: str, mode: str | None) -> dict:
    return {
        "schema_version": SCHEMA,
        "status": "unavailable",
        "reason": reason,
        "non_blocking": True,
        "original_read": False,
        "authority": "advisory_short_video_perception; never_blocks_submit_or_replaces_ew_reads",
        "window_sec": [start_sec, end_sec],
        "question": question,
        "model": model,
        "execution_mode": mode,
        "store": False,
        "observation": None,
    }

def check_short_video(
    *,
    job_root: Path,
    media_path: Path | None,
    start_sec: float,
    end_sec: float,
    question: str,
    execution_mode: str | None = None,
    vod_duration_sec: float | None = None,
    model: str = DEFAULT_MODEL,
    binary: str = "gemini-api",
    ffmpeg_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Run one bounded short-clip perception check via gemini-api-cli.

    Always non-blocking: missing binary, missing auth, missing media, or budget
    exhaustion return structured status='unavailable' without raising or blocking.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("short_video_check_question_required")
    start, end, duration = validate_clip_window(start_sec, end_sec, vod_duration_sec=vod_duration_sec)
    root = native_path(job_root).resolve()
    checks_dir = root / "short-video-checks"
    ledger = load_ledger(root)
    max_calls = resolve_max_calls(execution_mode)
    if len(ledger.get("calls", [])) >= max_calls and not dry_run:
        return _unavailable_response(
            reason=f"short_video_check_budget_exhausted:{len(ledger['calls'])}/{max_calls}",
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )
    clip_key = digest({"start": start, "end": end, "question": question.strip(), "model": model})[:16]
    clip_path = checks_dir / "clips" / f"clip-{int(start)}-{int(end)}-{clip_key}.mp4"

    if dry_run:
        command, payload_meta = build_gemini_api_command(
            binary=binary,
            model=model,
            clip_path=clip_path,
            question=question.strip(),
            start_sec=start,
            end_sec=end,
        )
        return {
            "schema_version": SCHEMA,
            "status": "dry_run",
            "non_blocking": True,
            "original_read": False,
            "authority": "advisory_short_video_perception; never_blocks_submit_or_replaces_ew_reads",
            "window_sec": [start, end],
            "duration_sec": duration,
            "question": question.strip(),
            "model": model,
            "execution_mode": execution_mode,
            "budget": {"used": len(ledger.get("calls", [])), "max_calls": max_calls},
            "store": False,
            "planned_clip_path": str(clip_path),
            "command": command,
            "request_meta": payload_meta,
            "observation": None,
        }

    if media_path is None or not native_path(media_path).is_file():
        return _unavailable_response(
            reason="source_media_not_available",
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )

    avail = check_gemini_api_available(binary=binary)
    if not avail["available"]:
        return _unavailable_response(
            reason=avail["reason"],
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )

    try:
        sliced = slice_short_clip(
            media_path=native_path(media_path),
            output_path=clip_path,
            start_sec=start,
            duration_sec=duration,
            ffmpeg_path=ffmpeg_path,
        )
    except Exception as exc:
        return _unavailable_response(
            reason=f"clip_slice_unavailable:{exc}",
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )

    command, payload_meta = build_gemini_api_command(
        binary=avail["binary"],
        model=model,
        clip_path=sliced,
        question=question.strip(),
        start_sec=start,
        end_sec=end,
    )
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unavailable_response(
            reason=f"gemini_api_exec_error:{type(exc).__name__}",
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )
    if proc.returncode != 0:
        return _unavailable_response(
            reason=f"gemini_api_nonzero_exit:{proc.returncode}",
            start_sec=start,
            end_sec=end,
            question=question.strip(),
            model=model,
            mode=execution_mode,
        )
    raw_text = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            observation = json.loads(parsed["text"])
        else:
            observation = parsed
    except Exception:
        observation = {
            "observed_action": raw_text[:600],
            "audible_cues": "",
            "uncertainty": "unstructured_cli_output",
            "supports_claim": "unclear",
            "start_offset_sec": 0.0,
            "end_offset_sec": duration,
        }

    receipt = {
        "schema_version": SCHEMA,
        "status": "complete",
        "non_blocking": True,
        "original_read": False,
        "authority": "advisory_short_video_perception; never_blocks_submit_or_replaces_ew_reads",
        "window_sec": [start, end],
        "duration_sec": duration,
        "question": question.strip(),
        "model": model,
        "execution_mode": execution_mode,
        "store": False,
        "clip_path": str(sliced),
        "clip_sha256": file_hash(sliced),
        "elapsed_sec": round(max(0.0, time.time() - started), 3),
        "observation": observation,
    }
    receipt_id = digest(receipt)[:16]
    receipt_path = checks_dir / f"check-{receipt_id}.json"
    atomic_json(receipt_path, receipt)
    ledger.setdefault("calls", []).append({
        "receipt_id": receipt_id,
        "receipt_path": str(receipt_path),
        "window_sec": [start, end],
        "model": model,
        "created_at": time.time(),
    })
    atomic_json(_ledger_path(root), ledger)
    receipt["receipt_path"] = str(receipt_path)
    receipt["budget"] = {"used": len(ledger["calls"]), "max_calls": max_calls}
    return receipt

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", required=True)
    parser.add_argument("--media")
    parser.add_argument("--start-sec", type=float, required=True)
    parser.add_argument("--end-sec", type=float, required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--mode", choices=tuple(MODE_MAX_CALLS))
    parser.add_argument("--vod-duration-sec", type=float)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--binary", default="gemini-api")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = check_short_video(
        job_root=Path(args.job_root),
        media_path=Path(args.media) if args.media else None,
        start_sec=args.start_sec,
        end_sec=args.end_sec,
        question=args.question,
        execution_mode=args.mode,
        vod_duration_sec=args.vod_duration_sec,
        model=args.model,
        binary=args.binary,
        ffmpeg_path=args.ffmpeg,
        dry_run=args.dry_run,
    )
    print(canonical_json(result))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
