"""Normal local Whisper watchdog and ASR artifact writing without browser or remote-provider execution."""

import logging

import threading

import time

from pathlib import Path

from typing import Iterable

logger = logging.getLogger('pipeline')

DEFAULT_STALL_SEC = 600

DEFAULT_TIMEOUT_SEC = 0

WATCHDOG_POLL_SEC = 10

def _video_no_from_srt_path(srt_path: str | Path) -> str | None:
    for part in reversed(Path(srt_path).parts):
        if part.isdigit():
            return part
    return None

def transcribe_video(video_path: str, progress_func=None, stall_sec: int=DEFAULT_STALL_SEC, timeout_sec: int=DEFAULT_TIMEOUT_SEC, initial_prompt_text: str | None=None, vad_prescan_workers: int | None=None, cancel_check=None, whisper_model: str='large-v3-turbo', speaker_turn_split_enabled: bool=True, nemotron_turns_enabled: bool=False, subtitle_profile=None, srt_output_path: str | None=None, chrome_stt_lang: str='ko-KR', chrome_stt_process_locally: bool=True, chrome_stt_cloud_fallback_enabled: bool=False, chrome_stt_quality_preference: str='conversation', chrome_stt_phrases: Iterable[object] | None=None, chrome_stt_timeout_sec: int=0) -> str:
    """비디오 → SRT.

    Args:
        video_path: 입력 비디오 경로
        progress_func: 외부 progress callback (current, total)
        stall_sec: 진행 콜백이 N초간 없으면 TimeoutError. 0 = 비활성.
        timeout_sec: 전체 실행 시간 상한. 0 = 비활성.
        cancel_check: 호출 시 True 반환하면 cooperative cancel.
            transcribe.py 내부 stop_event 를 set 하여 다음 batch 경계에서 종료
            시키고, 전체 실행은 RuntimeError("cancelled") 로 raise.
            호출자(process_vod) 는 이를 SkipRequested 흐름과 결합.
        whisper_model: "chrome-web-speech-local" 이면 Whisper 대신 Chrome
            Web Speech helper 를 열어 브라우저가 SRT 를 생성한다.

    Returns:
        SRT 파일 경로

    Raises:
        TimeoutError: stall 또는 전체 timeout 도달
        RuntimeError("cancelled"): cancel_check 가 True 반환
        Exception: Whisper 내부 에러를 그대로 전달
    """
    if whisper_model != 'large-v3-turbo' or nemotron_turns_enabled or subtitle_profile is not None:
        raise ValueError('loki_only_pinned_local_whisper_is_installed')
    logger.info(f'자막 생성 시작: {video_path}')
    if srt_output_path is not None:
        requested_output = Path(srt_output_path).resolve()
        media_parent = Path(video_path).resolve().parent
        if requested_output.suffix.lower() != '.srt' or requested_output.parent != media_parent:
            raise ValueError('Requested SRT output must be a sibling .srt file')
        if requested_output.exists():
            raise FileExistsError(f'Requested SRT output already exists and will not be overwritten: {requested_output}')
        srt_output_path = str(requested_output)
    from .asr_provider_contract import resolve_asr_provider, write_asr_contract_artifacts
    provider = resolve_asr_provider(whisper_model)
    if provider.family != 'local_whisper':
        raise RuntimeError(f'ASR provider is registered but not implemented: {provider.provider_id}')
    from transcribe import run_caption_generation
    state = {'srt': None, 'error': None}
    last_progress_ts = [time.time()]
    progress_seen = [False]
    last_progress = [(0, 0)]
    last_logged_progress = [None]
    last_external_ping_ts = [time.time()]
    stop_event = threading.Event()

    def log_func(msg):
        logger.info(f'  [Whisper] {msg}')

    def prog_func(current, total):
        last_progress_ts[0] = time.time()
        progress_seen[0] = True
        last_progress[0] = (current, total)
        last_external_ping_ts[0] = time.time()
        if progress_func:
            try:
                progress_func(current, total)
            except Exception as cb_err:
                logger.warning(f'progress_func 콜백 에러 무시: {cb_err}')
        if (current % 10 == 0 or current == total) and last_logged_progress[0] != (current, total):
            logger.info(f'  [Whisper] 진행: {current}/{total} 청크')
            last_logged_progress[0] = (current, total)
    files_info = [{'path': video_path, 'time_offset': 0.0, 'part_num': 1, 'total_parts': 1}]

    def worker():
        try:
            state['srt'] = run_caption_generation(files_info=files_info, is_split=False, log_func=log_func, progress_func=prog_func, initial_prompt_text=initial_prompt_text, vad_prescan_workers=vad_prescan_workers, stop_event=stop_event, whisper_model=whisper_model, speaker_turn_split_enabled=speaker_turn_split_enabled, nemotron_turns_enabled=nemotron_turns_enabled, speaker_reference_profile=subtitle_profile, srt_output_path=srt_output_path)
        except Exception as e:
            import traceback
            state['error'] = e
            state['traceback'] = traceback.format_exc()
    t = threading.Thread(target=worker, name='whisper-worker', daemon=True)
    start = time.time()
    t.start()
    while True:
        t.join(timeout=WATCHDOG_POLL_SEC)
        if not t.is_alive():
            break
        elapsed = time.time() - start
        idle = time.time() - last_progress_ts[0]
        if timeout_sec and elapsed > timeout_sec:
            msg = f'Whisper 전체 시간 초과: {elapsed:.0f}s > {timeout_sec}s'
            logger.error(msg)
            raise TimeoutError(msg)
        if stall_sec and progress_seen[0] and (idle > stall_sec):
            msg = f'Whisper 진행 정체: {idle:.0f}s 동안 청크 진행 없음 (>{stall_sec}s)'
            logger.error(msg)
            raise TimeoutError(msg)
        if cancel_check is not None:
            try:
                if cancel_check():
                    if not stop_event.is_set():
                        stop_event.set()
                        logger.info('Whisper cancel 요청 — 다음 batch 경계에서 종료')
            except Exception as cb_err:
                logger.warning(f'cancel_check 콜백 에러 무시: {cb_err}')
        if progress_func and time.time() - last_external_ping_ts[0] >= 30:
            try:
                current, total = last_progress[0]
                progress_func(current, total)
                last_external_ping_ts[0] = time.time()
            except Exception as cb_err:
                logger.warning(f'progress heartbeat 콜백 에러 무시: {cb_err}')
    if state['error'] is not None:
        logger.error(f"Whisper 실행 에러: {state['error']}")
        tb = state.get('traceback')
        if tb:
            logger.error(f'Whisper 실행 트레이스백:\n{tb}')
        raise state['error']
    if stop_event.is_set():
        raise RuntimeError('cancelled')
    srt_path = state['srt']
    if not srt_path:
        raise RuntimeError('Whisper 가 SRT 경로를 반환하지 않음 (알 수 없는 실패)')
    write_asr_contract_artifacts(srt_path, provider=provider, model=whisper_model, video_no=_video_no_from_srt_path(srt_path))
    logger.info(f'ASR provider contract artifacts written: provider={provider.provider_id}, model={whisper_model}')
    logger.info(f'자막 생성 완료: {srt_path}')
    return srt_path
