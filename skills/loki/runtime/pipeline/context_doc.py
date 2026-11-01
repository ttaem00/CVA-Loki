'B35: 맥락 문서 (Context Document)'

from __future__ import annotations

import logging

import os

from typing import Optional

logger = logging.getLogger("pipeline")

CAP_CHARS = 8000

def context_path(video_no: str, work_dir: str) -> str:
    """work/<video_no>/<video_no>_context.md 경로."""
    return os.path.join(work_dir, f"{video_no}_context.md")

def load_context_doc(video_no: str, work_dir: str) -> Optional[str]:
    """저장된 context.md 본문을 반환. 없거나 비어있으면 None.

    cap 적용은 호출자(summarizer) 가 build 시점에 수행하지 않고 여기서 미리
    잘라 넘겨주는 게 더 명료 — load 시점에 cap 보장.
    """
    path = context_path(video_no, work_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        logger.warning(f"context.md 로드 실패 ({path}): {e}")
        return None
    if not text:
        return None
    if len(text) > CAP_CHARS:
        logger.info(
            f"context.md cap 초과 ({len(text)} > {CAP_CHARS}): 처음 {CAP_CHARS}자만 사용"
        )
        text = text[:CAP_CHARS]
    return text
