"""Chzzk VOD 채팅 리플레이 수집 (chzzk_editor.py 로직 기반)"""

import json

import logging

import math

from pathlib import Path

logger = logging.getLogger("pipeline")

CHAT_PARTIAL_GAP_SEC = 600

CHAT_PARTIAL_RATIO_THRESHOLD = 0.85

def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number

def latest_chat_sec(chats: list[dict] | None) -> float | None:
    seconds = [
        ms / 1000.0
        for ms in (_safe_float(row.get("ms")) for row in (chats or []) if isinstance(row, dict))
        if ms is not None
    ]
    return max(seconds) if seconds else None

def is_partial_chat_coverage(chats: list[dict] | None, expected_duration_sec: int | float | None) -> bool:
    latest_sec = latest_chat_sec(chats)
    expected = _safe_float(expected_duration_sec)
    if latest_sec is None or not expected:
        return False
    gap = max(0.0, expected - latest_sec)
    ratio = min(1.0, latest_sec / expected) if expected > 0 else 1.0
    return gap >= CHAT_PARTIAL_GAP_SEC and ratio < CHAT_PARTIAL_RATIO_THRESHOLD

def has_complete_chat_cache(video_no: str, work_dir: str) -> bool:
    """A complete collector receipt, rather than a quiet-tail coverage guess."""
    try:
        value = json.loads((Path(work_dir) / f"{video_no}_chat.log.meta.json").read_text(encoding="utf-8"))
        return (isinstance(value, dict) and value.get("status") == "complete"
                and str(value.get("video_no", video_no)) == str(video_no))
    except (OSError, ValueError):
        return False

def load_chat_log_json(
    video_no: str,
    work_dir: str,
    expected_duration_sec: int | float | None = None,
) -> list[dict] | None:
    """save_chat_log 이 남긴 사이드카 JSON 을 로드. 없으면 None.

    RESUME 용: 재시도 시 API 재호출 (수 분 ~ 수십 분) 을 피하기 위해 사용.
    """
    import os as _os
    json_path = _os.path.join(work_dir, f"{video_no}_chat.log.json")
    meta_path = _os.path.join(work_dir, f"{video_no}_chat.log.meta.json")
    if not _os.path.isfile(json_path) or _os.path.getsize(json_path) == 0:
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and (not data or isinstance(data[0], dict)):
            if _os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                    if isinstance(meta, dict) and meta.get("status") in {"partial", "failed"}:
                        logger.warning(
                            f"채팅 JSON 이전 수집 상태={meta.get('status')} → 재수집: {json_path}"
                        )
                        return None
                    if isinstance(meta, dict) and meta.get("status") == "complete":
                        return data if str(meta.get("video_no", video_no)) == str(video_no) else None
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"채팅 JSON 메타 로드 실패 → 커버리지 검사로 진행: {e}")
            if is_partial_chat_coverage(data, expected_duration_sec):
                latest = latest_chat_sec(data)
                logger.warning(
                    "채팅 JSON 후반 누락 감지 → 재수집: %s (latest=%.0fs, expected=%.0fs)",
                    json_path,
                    latest or 0,
                    float(expected_duration_sec or 0),
                )
                return None
            return data
        logger.warning(f"채팅 JSON 형식 이상 → 재수집: {json_path}")
        return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"채팅 JSON 로드 실패 → 재수집: {e}")
        return None
