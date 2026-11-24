"""Standalone opt-in gemini-api short-clip verification wrapper outside loki.isolate()."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def _setup_workspace_env(config_path: Path) -> tuple[Path, Path, dict]:
    runtime_dir = Path(__file__).resolve().parent
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    import loki

    root, work, policy = loki.configuration(config_path)
    loki.verify_release()
    os.environ["LOKI_WORKSPACE"] = str(root)
    os.environ["LOKI_CONFIG_PATH"] = str(Path(config_path).resolve())
    os.environ["LOKI_WORK_DIR"] = str(work)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return root, work, policy


def run_short_video_check(
    *,
    config_path: Path,
    job_root: Path,
    media_path: Path | None,
    start_sec: float,
    end_sec: float,
    question: str,
    mode: str | None = None,
    vod_duration_sec: float | None = None,
    model: str | None = None,
    binary: str = "gemini-api",
    ffmpeg_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    import loki

    root, work, policy = _setup_workspace_env(config_path)
    job = loki.contained(root, job_root)
    if not job.is_relative_to(work):
        raise ValueError("loki_job_outside_work")
    prepared_file = job / "prepared.json"
    if prepared_file.is_file():
        saved = json.loads(loki.contained(root, prepared_file).read_text(encoding="utf-8-sig"))
        loki.validate_author_mode(saved["identity"]["author"], policy["author_mode"])
    owned_media = loki.contained(root, media_path) if media_path is not None else None
    resolved_ffmpeg = ffmpeg_path
    if not resolved_ffmpeg and (root / ".loki-tools.json").is_file():
        try:
            tools = json.loads((root / ".loki-tools.json").read_text(encoding="utf-8")).get("tools") or {}
            resolved_ffmpeg = (tools.get("ffmpeg") or {}).get("path")
        except (OSError, ValueError):
            resolved_ffmpeg = ffmpeg_path
    from pipeline.editorial_short_video_check import MODE_MAX_CALLS, check_short_video

    _ = MODE_MAX_CALLS
    return check_short_video(
        job_root=job,
        media_path=owned_media,
        start_sec=start_sec,
        end_sec=end_sec,
        question=question,
        execution_mode=mode,
        vod_duration_sec=vod_duration_sec,
        model=model,
        binary=binary,
        ffmpeg_path=resolved_ffmpeg,
        dry_run=dry_run,
    )


def main(argv=None) -> int:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Workspace-local loki.json")
    parser.add_argument("--job-root", required=True)
    parser.add_argument("--media")
    parser.add_argument("--start-sec", type=float, required=True)
    parser.add_argument("--end-sec", type=float, required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--mode", choices=("lite", "normal", "full"))
    parser.add_argument("--vod-duration-sec", type=float)
    parser.add_argument("--model", default=None)
    parser.add_argument("--binary", default="gemini-api")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_short_video_check(
        config_path=Path(args.config),
        job_root=Path(args.job_root),
        media_path=Path(args.media) if args.media else None,
        start_sec=args.start_sec,
        end_sec=args.end_sec,
        question=args.question,
        mode=args.mode,
        vod_duration_sec=args.vod_duration_sec,
        model=args.model,
        binary=args.binary,
        ffmpeg_path=args.ffmpeg,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
