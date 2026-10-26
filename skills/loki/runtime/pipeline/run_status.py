"""런 단위 sanity gate 실패 누적 — publish 차단 layer.

P3 Wave 2 sanity gate 의 publish-blocking layer (설계 §8.6). 단일 VOD 처리
중 어떤 단계에서든 sanity gate 가 실패한 사실을 기록하고, publish 진입부에서
"하나라도 실패가 있으면 publish abort" 하는 안전장치.

설계 의도:
- summarizer 의 chunk/final merge SanityGateError 는 run-level abort (raise) 라
  publish 까지 도달하지 않음. 하지만 shorts 의 reranker fallback 같은 최종 결과
  검증이 없는 "silent fallback" 경로는 파이프라인을 계속 진행시킨다 — 그 결과를
  publish 하면 wave 1 사고 재발.
- 따라서 publish 해서는 안 되는 fallback 경로는 raise 대신 본 모듈에 record 만
  하고, publish 진입부가 최종 차단한다. 중간 병합처럼 원문 fallback 후 최종
  merge sanity 를 다시 통과해야 하는 복구 경로는 여기에 기록하지 않는다.

스레드 가정: 본 파이프라인은 VOD 1 개를 sequential 처리. 동시 처리는 daemon 의
별 process. 따라서 module-level 단일 state 로 충분.

사용 예:
    from pipeline import run_status

    run_status.start_run("12345678")  # process_vod 진입부
    ...
    try:
        validate_chunk_response(...)
    except SanityGateError as e:
        run_status.record_sanity_failure("summarizer.chunk", str(e))
        raise

    if run_status.has_sanity_failures():
        log.error("publish abort: sanity failures detected")
        return None
"""

from __future__ import annotations

import logging

import os

from threading import RLock

from typing import Optional

logger = logging.getLogger("pipeline.run_status")

_state_lock = RLock()

_current_video_no: Optional[str] = None

_failures: list[dict] = []

def start_run(video_no: str) -> None:
    """새 VOD 처리 시작 — 누적 상태 초기화.

    process_vod 진입부에서 호출. 이전 run 의 잔여 상태를 모두 비운다.
    """
    global _current_video_no, _failures
    with _state_lock:
        _current_video_no = video_no
        _failures = []
    logger.debug("[run_status] start_run video_no=%s", video_no)

def record_sanity_failure(
    stage: str,
    message: str,
    *,
    video_no: Optional[str] = None,
    **extras,
) -> None:
    """sanity gate 실패 1건 기록.

    stage: "summarizer.chunk" / "summarizer.merge" / "shorts.edl" /
        "shorts.reranker" / "shorts.title" 등 단계 식별자
    message: SanityGateError 의 본문 또는 fallback 사유
    video_no: 명시 안 하면 현재 run 의 것 사용
    extras: chunk_idx / candidate_uid / stop_reason 등 contextual info
    """
    with _state_lock:
        vno = video_no or _current_video_no
        record = {
            "video_no": vno,
            "stage": stage,
            "message": message[:500],
            **{k: v for k, v in extras.items() if v is not None},
        }
        _failures.append(record)
    logger.warning("[run_status] sanity FAIL recorded — stage=%s msg=%s",
                   stage, message[:200])

def has_sanity_failures(video_no: Optional[str] = None) -> bool:
    """현재 run (또는 명시된 video_no) 에 sanity fail 이 있는지."""
    with _state_lock:
        if video_no is None:
            return bool(_failures)
        return any(f.get("video_no") == video_no for f in _failures)

def get_failures(video_no: Optional[str] = None) -> list[dict]:
    """누적된 sanity fail 기록 사본."""
    with _state_lock:
        if video_no is None:
            return list(_failures)
        return [f for f in _failures if f.get("video_no") == video_no]

def reset() -> None:
    """테스트용 — 모든 상태 초기화."""
    global _current_video_no, _failures
    with _state_lock:
        _current_video_no = None
        _failures = []

_BLOCKED_PHASES = frozenset({"smoke", "acceptance"})

def is_publish_blocked_by_phase() -> tuple[bool, Optional[str]]:
    """ACGEN_PHASE env 기준 publish 차단 여부. (blocked, phase_name)."""
    phase = os.environ.get("ACGEN_PHASE", "").strip().lower()
    if phase in _BLOCKED_PHASES:
        return True, phase
    return False, phase or None
