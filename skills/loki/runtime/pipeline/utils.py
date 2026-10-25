"""공통 유틸리티: 로깅, 리트라이, 포맷팅"""

import functools

import hashlib

import logging

import os

import re

import time

from pathlib import Path

def retry(max_retries: int = 3, backoff_base: float = 2.0, exceptions=(Exception,)):
    """지수 백오프 리트라이 데코레이터"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = backoff_base ** attempt
                        logging.getLogger("pipeline").warning(
                            f"{func.__name__} 실패 (시도 {attempt + 1}/{max_retries + 1}): {e}. "
                            f"{delay:.1f}초 후 재시도..."
                        )
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator

def sec_to_hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"

def format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

WINDOWS_PATH_BUDGET = 240

WINDOWS_COMPONENT_BUDGET = 254

WINDOWS_RESERVED_BASENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

def stable_artifact_key(
    video_id: str,
    date_text: str,
    *,
    discriminator: str = "",
) -> str:
    """Title-independent identity for report/work/cache filenames."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", str(video_id or "")) or "video"
    safe_date = re.sub(r"[^0-9]", "", str(date_text or ""))[:8] or "nodate"
    digest = hashlib.sha256(
        (
            f"video_id={video_id}\ndate={date_text}"
            f"\ndiscriminator={discriminator}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    key = f"{safe_id}_{safe_date}_{digest}".rstrip(" .")
    if key.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES:
        key = f"artifact_{key}"
    return key

def validate_artifact_path_budget(
    root: str | os.PathLike[str],
    base: str,
    *,
    suffixes: tuple[str, ...],
    full_path_limit: int = WINDOWS_PATH_BUDGET,
) -> None:
    """Fail closed before temp/final artifact paths exceed shell-safe limits."""
    if not base or base != base.rstrip(" ."):
        raise ValueError("ARTIFACT_BASENAME_TRAILING_DOT_OR_SPACE")
    if base.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES:
        raise ValueError("ARTIFACT_BASENAME_WINDOWS_RESERVED")
    lowered = [f"{base}{suffix}".casefold() for suffix in suffixes]
    if len(lowered) != len(set(lowered)):
        raise ValueError("ARTIFACT_CASE_INSENSITIVE_COLLISION")
    resolved_root = Path(root).resolve()
    for suffix in suffixes:
        component = f"{base}{suffix}"
        if len(component) > WINDOWS_COMPONENT_BUDGET:
            raise ValueError("ARTIFACT_COMPONENT_PATH_BUDGET_EXCEEDED")
        if len(str(resolved_root / component)) > full_path_limit:
            raise ValueError("ARTIFACT_FULL_PATH_BUDGET_EXCEEDED")

def clip_video(src_path: str, dst_path: str, duration_sec: int, start_sec: int = 0) -> str:
    """ffmpeg으로 영상 앞부분만 잘라 새 파일 생성 (테스트용).

    Args:
        src_path: 원본 영상
        dst_path: 잘라낸 결과 파일 (덮어씀)
        duration_sec: 앞에서부터 포함할 시간(초)
    Returns:
        dst_path
    """
    import subprocess
    import sys
    logger = logging.getLogger("pipeline")
    logger.info(f"영상 자르기: {duration_sec}초 (→ {dst_path})")


    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", src_path,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        dst_path,
    ]
    try:
        cflags = 0
        if sys.platform == "win32":
            cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=cflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 실패 (code={result.returncode}): {result.stderr[-500:]}")
        logger.info(f"  자르기 완료: {dst_path}")
        return dst_path
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 을 찾을 수 없습니다. PATH에 등록하세요.")
