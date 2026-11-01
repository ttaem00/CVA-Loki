"""Existing Whisper transcription, timestamps and F2 postprocessing with pinned local model loading and serial VAD."""

import subprocess

import sys

import os

import math

import time

import re

import zlib

import numpy as np

import wave

from collections import Counter

from split_video import find_bin, get_duration, get_audio_duration, probe_audio_stream, FFMPEG

from merge import merge_files

FFPROBE = find_bin('ffprobe')

SEGMENT_SECONDS = 3600

SAMPLE_RATE = 16000

WHISPER_MAX_CHUNK_SEC = 15

WHISPER_BATCH_CANDIDATES = (1, 2, 4)

WHISPER_BATCH_BYTES_PER_ITEM = 512 * 1024 * 1024

WHISPER_BATCH_MIN_RESERVE_BYTES = 2 * 1024 * 1024 * 1024

class _CombinedCancelEvent:
    """Read-only cancellation view; never mutates the manager's stop event."""

    def __init__(self, *events):
        self._events = tuple((event for event in events if event is not None))

    def is_set(self):
        return any((event.is_set() for event in self._events))

def compact_speaker_turn_metadata(payload):
    """Bound persisted turn provenance independently of long-VOD decision count."""

    def without_decisions(value):
        if not isinstance(value, dict):
            return value
        decisions = value.get('decisions')
        compact = {key: item for key, item in value.items() if key != 'decisions'}
        if isinstance(decisions, list):
            compact['decision_count'] = len(decisions)
            compact['applied_decision_count'] = sum((1 for item in decisions if isinstance(item, dict) and item.get('applied') is True))
            compact['decision_reason_counts'] = dict(sorted(Counter((str(item.get('reason') or 'unknown') for item in decisions if isinstance(item, dict))).items()))
        return compact
    result = {key: value for key, value in payload.items() if key not in {'parts', 'composition'}}
    result['metadata_compacted'] = True
    result['parts'] = [without_decisions(part) for part in payload.get('parts', [])]
    if isinstance(payload.get('composition'), dict):
        result['composition'] = without_decisions(payload['composition'])
    return result

def aggregate_reference_assist_status(parts, expected_parts, *, turn_detection_status, composition_status, reference_assisted_splits=0):
    """Summarize completed per-part assist and its downstream composition."""
    results = [part.get('reference_assist') for part in parts if isinstance(part, dict) and isinstance(part.get('reference_assist'), dict)]
    counts = Counter((str(item.get('status') or 'unknown') for item in results))
    if len(results) != expected_parts:
        return ('fallback', 'missing_reference_assist_part_result', counts)
    if counts.get('fallback', 0):
        return ('fallback', 'part_reference_assist_fallback', counts)
    if composition_status not in {'applied', 'unchanged'}:
        return ('fallback', 'speaker_turn_composition_fallback', counts)
    if turn_detection_status not in {'applied', 'unchanged'}:
        return ('fallback', 'speaker_turn_pipeline_fallback', counts)
    expected_statuses = {'confirmed', 'unchanged', 'partial_bounded'}
    if not set(counts) <= expected_statuses:
        return ('fallback', 'unexpected_reference_assist_part_status', counts)
    if counts.get('partial_bounded', 0):
        return ('partial_bounded', 'planned_reference_budget_exhausted', counts)
    if counts.get('confirmed', 0):
        if int(reference_assisted_splits or 0) > 0:
            return ('applied', 'reference_assisted_split_applied', counts)
        return ('confirmed_no_change', 'confirmed_transition_did_not_change_subtitle', counts)
    if set(counts) <= {'unchanged'}:
        return ('unchanged', 'no_confirmed_reference_transition', counts)
    return ('fallback', 'unexpected_reference_assist_part_status', counts)

MAX_CHARS_PER_LINE = 42

MAX_LINES = 2

MIN_SUBTITLE_SEC = 0.5

MAX_SUBTITLE_SEC = 7.0

MIN_GAP_SEC = 0.1

COMPRESSION_RATIO_THRESHOLD = 2.0

PART_SUFFIX_PATTERN = re.compile('\\s*-\\s*Part\\s+\\d+\\s+\\(\\d{2}-\\d{2}-\\d{2}\\s+to\\s+\\d{2}-\\d{2}-\\d{2}\\)')

SPLIT_PATTERNS = [re.compile('[.!?。~]\\s*'), re.compile('[,、]\\s*'), re.compile('\\s+')]

def resolve_vad_prescan_workers(n_parts, configured_workers=None):
    return 1

def resolve_whisper_batch_size(device, torch_module, environ=None):
    """Whisper batch를 명시적 override 또는 현재 여유 VRAM으로 결정한다.

    반환값은 ``(batch_size, reason)``. GPU 제품명은 판단에 사용하지 않는다.
    ``WHISPER_BATCH_SIZE``가 있으면 기존 운영 계약대로 그 값을 우선한다.
    """
    env = os.environ if environ is None else environ
    override = env.get('WHISPER_BATCH_SIZE')
    if override is not None:
        try:
            requested = max(1, int(override))
        except (TypeError, ValueError):
            requested = 4
            return (requested, f'override_invalid={override!r}; fallback={requested}')
        return (requested, f'override={requested}')
    if device != 'cuda':
        return (1, 'cpu_safe_default')
    try:
        free_bytes, total_bytes = torch_module.cuda.mem_get_info()
    except Exception as exc:
        return (4, f'cuda_memory_unavailable={type(exc).__name__}; fallback=4')
    reserve_bytes = max(WHISPER_BATCH_MIN_RESERVE_BYTES, int(total_bytes * 0.15))
    usable_bytes = max(0, int(free_bytes) - reserve_bytes)
    capacity = max(1, usable_bytes // WHISPER_BATCH_BYTES_PER_ITEM)
    selected = max((candidate for candidate in WHISPER_BATCH_CANDIDATES if candidate <= capacity))
    reason = f'adaptive free={free_bytes / 1024 ** 3:.1f}GiB total={total_bytes / 1024 ** 3:.1f}GiB reserve={reserve_bytes / 1024 ** 3:.1f}GiB'
    return (selected, reason)

def next_lower_whisper_batch_size(current):
    """OOM 후 같은 청크를 재시도할 절반 이하의 안전 후보를 반환한다."""
    if current <= 1:
        return 1
    ceiling = max(1, current // 2)
    return max((candidate for candidate in WHISPER_BATCH_CANDIDATES if candidate <= ceiling))

def is_cuda_oom(exc, torch_module):
    """PyTorch 버전 차이를 흡수한 CUDA OOM 판정."""
    oom_types = []
    for owner in (torch_module, getattr(torch_module, 'cuda', None)):
        oom_type = getattr(owner, 'OutOfMemoryError', None)
        if isinstance(oom_type, type):
            oom_types.append(oom_type)
    if oom_types and isinstance(exc, tuple(oom_types)):
        return True
    return isinstance(exc, RuntimeError) and 'out of memory' in str(exc).lower()

def _probe_mp4_duration(path):
    """B40: ffprobe 로 mp4 duration(초) 조회. 실패 시 None.

    캐시된 part 파일이 손상되었는지 빠르게 식별하는 데 사용. ffprobe 가
    duration 못 읽으면 손상으로 간주.
    """
    try:
        result = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10, creationflags=_ffmpeg_creationflags())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or '').strip()
    if not out:
        return None
    try:
        return float(out)
    except ValueError:
        return None

def _is_part_intact(path, expected_sec, min_size_bytes=1024 * 1024, duration_tolerance=0.5):
    """B40: 캐시된 part mp4 가 정상인지 검증.

    조건 (둘 다 만족해야 정상):
      1. 파일 크기 ≥ min_size_bytes (기본 1MB) — 잘린 stub 제외
      2. ffprobe duration ≥ expected_sec * duration_tolerance — 잘린 콘텐츠 제외

    Args:
        expected_sec: 이 part 의 기대 duration (초). 마지막 part 는 잔여 길이.
        min_size_bytes: 최소 파일 크기 (이하면 손상 판정)
        duration_tolerance: 기대 duration 의 N배 이상이면 정상 (기본 0.5)

    Returns:
        True = 정상 (캐시 재사용), False = 손상 (재분할 필요)
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < min_size_bytes:
        return False
    dur = _probe_mp4_duration(path)
    if dur is None:
        return False
    return dur >= expected_sec * duration_tolerance

_GHOST_MAX_BYTES = 4096

def _remove_ghost_part(part_path, log_func=print):
    """Ghost part mp4 + 동일 basename wav sidecar 삭제."""
    for _p in (part_path, os.path.splitext(part_path)[0] + '.wav'):
        if os.path.isfile(_p):
            try:
                os.remove(_p)
            except OSError as _e:
                log_func(f'  [경고] ghost 잔존물 삭제 실패 (무시): {_p} — {_e}')

def _confirm_source_eof(input_path, start, duration, log_func=print):
    'Reliable-duration ghost tail 삭제 전 원본 EOF 독립 증거 확보.\n\n    start >= duration 이면 stream duration 기준 EOF 이후 → True (ghost 확정).\n    start < duration 이면 실제 오디오 잔여분이 있을 수 있으므로 원본 probe.\n    Returns: True (ghost 확정, 삭제 안전), False (오디오 존재, 삭제 금지),\n             None (probe 불가, 보존 필요).\n    '
    if start >= duration:
        return True
    _window = min(max(duration - start, 2.0), 300.0)
    _verdict = _probe_decodable_audio(input_path, start, window_sec=_window)
    if _verdict is True:
        return False
    if _verdict is None:
        return None
    return True

def split_video(input_path, log_func=print):
    duration, dur_source = get_audio_duration(input_path, with_source=True)
    format_duration = None
    try:
        format_duration = get_duration(input_path)
    except RuntimeError:
        if dur_source != 'stream':
            raise
        log_func('[참고] format duration 조회 실패 — stream duration 을 사용하므로 분할에 영향 없음')
    if dur_source == 'stream' and format_duration is not None and (abs(duration - format_duration) > 60):
        log_func(f'[참고] audio stream duration({duration:.0f}s) ≠ format duration({format_duration:.0f}s) — audio 기준 분할')
    duration_reliable = dur_source == 'stream'
    if not duration_reliable:
        log_func(f'[참고] audio stream duration 사용 불가 — format duration ({duration:.0f}s) fallback 사용 (ghost 판정 시 probe 증거만 사용)')
    total_parts = math.ceil(duration / SEGMENT_SECONDS)
    name, ext = os.path.splitext(input_path)
    log_func(f'총 길이: {duration:.0f}초 ({duration / 3600:.1f}시간)')
    log_func(f'분할 개수: {total_parts}개\n')
    part_paths = []
    _deferred_ghost_candidates = []
    for i in range(total_parts):
        start = i * SEGMENT_SECONDS
        output_path = f'{name}_part{i + 1:03d}{ext}'
        if os.path.isfile(output_path):
            is_last = i == total_parts - 1
            expected_sec = duration - i * SEGMENT_SECONDS if is_last else SEGMENT_SECONDS
            if _is_part_intact(output_path, expected_sec):
                cache_probe = probe_audio_stream(output_path)
                if cache_probe.state == 'no_audio':
                    is_tail = i == total_parts - 1
                    _cache_size = None
                    try:
                        _cache_size = os.path.getsize(output_path)
                    except OSError:
                        pass
                    _cache_size_known = _cache_size is not None
                    if duration_reliable:
                        is_cache_ghost = is_tail and _cache_size_known and (_cache_size <= _GHOST_MAX_BYTES)
                    else:
                        is_cache_ghost = _cache_size_known and _cache_size <= _GHOST_MAX_BYTES
                    if is_cache_ghost:
                        if duration_reliable:
                            _eof_ok = _confirm_source_eof(input_path, start, duration, log_func)
                            if _eof_ok is False:
                                raise RuntimeError(f'캐시 no_audio tail 이나 원본에 오디오 존재 [{i + 1}/{total_parts}] (start={start}s) — 파일 보존됨: {output_path}')
                            if _eof_ok is None:
                                raise RuntimeError(f'캐시 no_audio tail EOF probe 불가 [{i + 1}/{total_parts}] (start={start}s) — 파일 보존됨: {output_path}')
                            log_func(f'[{i + 1}/{total_parts}] [경고] 캐시 파트에 오디오 없음 (ghost: start={start}s, duration={duration:.0f}s, size={_cache_size}B, tail={is_tail}) → 삭제: {output_path}')
                            _remove_ghost_part(output_path, log_func)
                            continue
                        _deferred_ghost_candidates.append((i, output_path, f'cached no_audio size={_cache_size}B start={start}s'))
                        continue
                    raise RuntimeError(f'캐시 파트 오디오 없음 [{i + 1}/{total_parts}] — 데이터 손실 위험 (tail={is_tail}, start={start}s, duration={duration:.0f}s)')
                elif cache_probe.state == 'probe_failed':
                    raise RuntimeError(f'캐시 파트 probe 실패 [{i + 1}/{total_parts}]: {cache_probe.detail} — 파일 보존됨: {output_path}')
                else:
                    if _deferred_ghost_candidates:
                        _deferred_indices = [d[0] for d in _deferred_ghost_candidates]
                        raise RuntimeError(f'ghost suffix 불일치: part {i + 1} 에 유효 오디오가 있으나 이전 파트 {_deferred_indices} 가 ghost candidate — 데이터 손실 가능 (파일 보존됨: {output_path})')
                    log_func(f'[{i + 1}/{total_parts}] 이미 존재: {output_path}')
                    part_paths.append(output_path)
                    continue
            _damaged_size = None
            try:
                _damaged_size = os.path.getsize(output_path)
            except OSError:
                pass
            if _damaged_size is not None and _damaged_size <= _GHOST_MAX_BYTES:
                _damaged_probe = probe_audio_stream(output_path)
                if _damaged_probe.state == 'probe_failed':
                    raise RuntimeError(f'캐시 소형 파트 probe 실패 [{i + 1}/{total_parts}]: {_damaged_probe.detail} — 파일 보존됨: {output_path}')
                if _damaged_probe.state == 'has_audio':
                    _cached_pkt = _probe_audio_at_offset(output_path, 0.0, window_sec=2.0)
                    if _cached_pkt is None:
                        raise RuntimeError(f'캐시 소형 has_audio 파트 패킷 probe 불가 [{i + 1}/{total_parts}] ({_damaged_size}B, start={start}s) — 재시도 필요 (파일 보존됨: {output_path})')
                    if _cached_pkt is True:
                        log_func(f'[{i + 1}/{total_parts}] [경고] 캐시 소형 파트 ({_damaged_size}B) 패킷 확인됨 → 재분할: {output_path}')
                    else:
                        is_tail = i == total_parts - 1
                        if duration_reliable:
                            if is_tail:
                                _eof_ok = _confirm_source_eof(input_path, start, duration, log_func)
                                if _eof_ok is False:
                                    raise RuntimeError(f'캐시 소형 has_audio tail 패킷 없으나 원본에 오디오 존재 [{i + 1}/{total_parts}] — 파일 보존됨: {output_path}')
                                if _eof_ok is None:
                                    raise RuntimeError(f'캐시 소형 has_audio tail EOF probe 불가 [{i + 1}/{total_parts}] — 파일 보존됨: {output_path}')
                                log_func(f'[{i + 1}/{total_parts}] [경고] 캐시 has_audio stub 패킷 없음 + 원본 EOF 확인 ({_damaged_size}B) → 삭제: {output_path}')
                                _remove_ghost_part(output_path, log_func)
                                continue
                            raise RuntimeError(f'캐시 소형 has_audio 파트에 패킷 없음 [{i + 1}/{total_parts}] ({_damaged_size}B, start={start}s) — 파일 보존됨: {output_path}')
                        _deferred_ghost_candidates.append((i, output_path, f'cached has_audio packetless size={_damaged_size}B start={start}s'))
                        continue
                elif _damaged_probe.state == 'no_audio':
                    is_tail = i == total_parts - 1
                    if duration_reliable:
                        _is_damaged_ghost = is_tail
                    else:
                        _is_damaged_ghost = True
                    if _is_damaged_ghost:
                        if duration_reliable:
                            _eof_ok = _confirm_source_eof(input_path, start, duration, log_func)
                            if _eof_ok is False:
                                raise RuntimeError(f'캐시 stub no_audio tail 이나 원본에 오디오 존재 [{i + 1}/{total_parts}] — 파일 보존됨: {output_path}')
                            if _eof_ok is None:
                                raise RuntimeError(f'캐시 stub no_audio tail EOF probe 불가 [{i + 1}/{total_parts}] — 파일 보존됨: {output_path}')
                            log_func(f'[{i + 1}/{total_parts}] [경고] 캐시 stub에 오디오 없음 (ghost: start={start}s, size={_damaged_size}B, tail={is_tail}) → 삭제: {output_path}')
                            _remove_ghost_part(output_path, log_func)
                            continue
                        _deferred_ghost_candidates.append((i, output_path, f'cached stub no_audio size={_damaged_size}B start={start}s'))
                        continue
            is_tail = i == total_parts - 1
            if not duration_reliable and is_tail:
                _cache_recheck = probe_audio_stream(output_path)
                if _cache_recheck.state == 'has_audio':
                    _cache_recheck_pkt = _probe_audio_at_offset(output_path, 0.0, window_sec=2.0)
                    if _cache_recheck_pkt is True:
                        log_func(f'[{i + 1}/{total_parts}] [경고] 캐시 tail duration 검증 실패 (format fallback 과장 가능) 이나 오디오 패킷 존재 → 캐시 유지: {output_path}')
                        if _deferred_ghost_candidates:
                            _deferred_indices = [d[0] for d in _deferred_ghost_candidates]
                            raise RuntimeError(f'ghost suffix 불일치: part {i + 1} 에 유효 오디오가 있으나 이전 파트 {_deferred_indices} 가 ghost candidate — 파일 보존됨: {output_path}')
                        part_paths.append(output_path)
                        continue
                    if _cache_recheck_pkt is None:
                        raise RuntimeError(f'캐시 tail 패킷 probe 불가 [{i + 1}/{total_parts}] (start={start}s) — 재시도 필요 (파일 보존됨: {output_path})')
                    _deferred_ghost_candidates.append((i, output_path, f'cached has_audio packetless (>4KB) start={start}s'))
                    continue
                if _cache_recheck.state == 'probe_failed':
                    raise RuntimeError(f'캐시 파트 probe 실패 [{i + 1}/{total_parts}]: {_cache_recheck.detail} — 파일 보존됨: {output_path}')
                _deferred_ghost_candidates.append((i, output_path, f'cached no_audio (>4KB) start={start}s'))
                continue
            log_func(f'[{i + 1}/{total_parts}] [경고] 기존 part 손상 감지 (크기/duration 검증 실패) → 재분할: {output_path}')
            try:
                os.remove(output_path)
            except OSError as e:
                log_func(f'  [경고] 손상 part 삭제 실패 (무시): {e}')
            wav_path = os.path.splitext(output_path)[0] + '.wav'
            if os.path.isfile(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
        cmd = [FFMPEG, '-y', '-i', input_path, '-ss', str(start), '-t', str(SEGMENT_SECONDS), '-c', 'copy', output_path]
        log_func(f'[{i + 1}/{total_parts}] 분할 중: {output_path}')
        result = subprocess.run(cmd, capture_output=True, creationflags=_ffmpeg_creationflags())
        is_tail = i == total_parts - 1
        if result.returncode != 0:
            _raw_stderr = result.stderr.decode('utf-8', errors='replace')
            _stripped_stderr = _strip_paths_from_stderr(_raw_stderr, input_path, output_path)
            stderr_tail = _stripped_stderr[-200:]
            has_ghost_pattern = bool(_NO_AUDIO_RE.search(stderr_tail))
            _rc_part_size = None
            if os.path.isfile(output_path):
                try:
                    _rc_part_size = os.path.getsize(output_path)
                except OSError:
                    pass
            _rc_size_known = _rc_part_size is not None
            if duration_reliable:
                is_ghost_tail = is_tail and has_ghost_pattern and _rc_size_known and (_rc_part_size <= _GHOST_MAX_BYTES)
            else:
                is_ghost_tail = has_ghost_pattern and _rc_size_known and (_rc_part_size <= _GHOST_MAX_BYTES)
            if is_ghost_tail:
                if duration_reliable:
                    _rc_eof = _confirm_source_eof(input_path, start, duration, log_func)
                    if _rc_eof is False:
                        raise RuntimeError(f'RC 실패 tail 이나 원본에 오디오 존재 [{i + 1}/{total_parts}] (start={start}s, size={_rc_part_size}B) — 분할 실패 (파일 보존됨: {output_path})')
                    if _rc_eof is None:
                        raise RuntimeError(f'RC 실패 tail EOF probe 불가 [{i + 1}/{total_parts}] (start={start}s, size={_rc_part_size}B) — 재시도 필요 (파일 보존됨: {output_path})')
                    _remove_ghost_part(output_path, log_func)
                    log_func(f'[{i + 1}/{total_parts}] [경고] 파트 분할 실패 (ghost: stderr 패턴 + start={start}s, duration={duration:.0f}s, size={_rc_part_size}B, tail={is_tail}, reliable={duration_reliable}, source_eof=True) → 건너뜀: {_raw_stderr[-200:]}')
                    continue
                _deferred_ghost_candidates.append((i, output_path, f'rc={result.returncode} stderr_pattern size={_rc_part_size}B start={start}s'))
                continue
            raise RuntimeError(f'ffmpeg 분할 실패 [{i + 1}/{total_parts}] (rc={result.returncode}): {_raw_stderr[-200:]}')
        if os.path.isfile(output_path):
            probe = probe_audio_stream(output_path)
            if probe.state == 'no_audio':
                _part_size = None
                try:
                    _part_size = os.path.getsize(output_path)
                except OSError:
                    pass
                _part_size_known = _part_size is not None
                if duration_reliable:
                    is_ghost_tail = is_tail and _part_size_known and (_part_size <= _GHOST_MAX_BYTES)
                else:
                    is_ghost_tail = _part_size_known and _part_size <= _GHOST_MAX_BYTES
                if is_ghost_tail:
                    if duration_reliable:
                        _na_eof = _confirm_source_eof(input_path, start, duration, log_func)
                        if _na_eof is False:
                            raise RuntimeError(f'no_audio tail 이나 원본에 오디오 존재 [{i + 1}/{total_parts}] (start={start}s, size={_part_size}B) — 분할 실패 (파일 보존됨: {output_path})')
                        if _na_eof is None:
                            raise RuntimeError(f'no_audio tail EOF probe 불가 [{i + 1}/{total_parts}] (start={start}s, size={_part_size}B) — 재시도 필요 (파일 보존됨: {output_path})')
                        log_func(f'[{i + 1}/{total_parts}] [경고] 오디오 스트림 없음 (ghost: start={start}s, duration={duration:.0f}s, size={_part_size}B, tail={is_tail}, source_eof=True) → 건너뜀: {output_path}')
                        _remove_ghost_part(output_path, log_func)
                        continue
                    _deferred_ghost_candidates.append((i, output_path, f'probe no_audio size={_part_size}B start={start}s'))
                    continue
                _na_src = _probe_decodable_audio(input_path, float(start), window_sec=min(float(SEGMENT_SECONDS), 300.0))
                if _na_src is False:
                    log_func(f'[{i + 1}/{total_parts}] [경고] 오디오 스트림 없음 (소스 결손: start={start}s, duration={duration:.0f}s, tail={is_tail}) — 원본에도 오디오 없음 확인 → 건너뜀: {output_path}')
                    _remove_ghost_part(output_path, log_func)
                    continue
                if _na_src is None:
                    raise RuntimeError(f'오디오 스트림 없음 [{i + 1}/{total_parts}] 원본 probe 불가 — 재시도 필요 (tail={is_tail}, start={start}s, duration={duration:.0f}s, 파일 보존됨: {output_path})')
                log_func(f'[{i + 1}/{total_parts}] [경고] -c copy 오디오 누락 (start={start}s) — re-encode fallback 시도')
                _remove_ghost_part(output_path, log_func)
                _re_cmd = [FFMPEG, '-y', '-i', input_path, '-ss', str(start), '-t', str(SEGMENT_SECONDS), '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', output_path]
                _re_result = subprocess.run(_re_cmd, capture_output=True, creationflags=_ffmpeg_creationflags())
                if _re_result.returncode != 0:
                    _re_stderr = _re_result.stderr.decode('utf-8', errors='replace')[-200:]
                    _narrow_ff = _probe_decodable_audio(input_path, float(start) + 30.0, window_sec=min(float(SEGMENT_SECONDS) - 60, 300.0))
                    if _narrow_ff is None:
                        raise RuntimeError(f're-encode fallback 실패 [{i + 1}/{total_parts}] 원본 좁은 probe 불가 — 재시도 필요 (start={start}s, stderr={_re_stderr})')
                    if _narrow_ff is False:
                        is_tail = i == total_parts - 1
                        if is_tail:
                            log_func(f'[{i + 1}/{total_parts}] [경고] re-encode fallback 실패 + 원본 좁은 probe {_narrow_ff} (start={start}s, tail) → 소스 결손 판정, 건너뜀: {output_path}')
                            _remove_ghost_part(output_path, log_func)
                        else:
                            log_func(f'[{i + 1}/{total_parts}] [경고] re-encode fallback 실패 + 원본 좁은 probe {_narrow_ff} (start={start}s) → deferred ghost candidate')
                            _deferred_ghost_candidates.append((i, output_path, f're-encode fail narrow={_narrow_ff} start={start}s stderr={_re_stderr[:80]}'))
                        continue
                    raise RuntimeError(f're-encode fallback 실패 [{i + 1}/{total_parts}] (start={start}s): {_re_stderr}')
                _re_probe = probe_audio_stream(output_path)
                if _re_probe.state != 'has_audio':
                    _narrow = _probe_decodable_audio(input_path, float(start) + 30.0, window_sec=min(float(SEGMENT_SECONDS) - 60, 300.0))
                    if _narrow is None:
                        raise RuntimeError(f're-encode 후 오디오 probe 실패 [{i + 1}/{total_parts}] 원본 좁은 probe 불가 — 재시도 필요 (start={start}s, state={_re_probe.state})')
                    if _narrow is False:
                        is_tail = i == total_parts - 1
                        if is_tail:
                            log_func(f'[{i + 1}/{total_parts}] [경고] re-encode 후에도 오디오 없음 + 원본 좁은 probe {_narrow} (start={start}s, tail) → 소스 결손 판정, 건너뜀: {output_path}')
                            _remove_ghost_part(output_path, log_func)
                        else:
                            log_func(f'[{i + 1}/{total_parts}] [경고] re-encode 후에도 오디오 없음 + 원본 좁은 probe {_narrow} (start={start}s) → deferred ghost candidate')
                            _deferred_ghost_candidates.append((i, output_path, f're-encode no_audio narrow={_narrow} start={start}s'))
                        continue
                    raise RuntimeError(f're-encode 후에도 오디오 없음 [{i + 1}/{total_parts}] (start={start}s, state={_re_probe.state}, narrow_probe=True)')
                log_func(f'[{i + 1}/{total_parts}] ✓ re-encode fallback 성공')
            elif probe.state == 'probe_failed':
                raise RuntimeError(f'오디오 probe 실패 [{i + 1}/{total_parts}]: {probe.detail} — probe 실패는 재시도 필요 (파일 보존됨: {output_path})')
            else:
                try:
                    _ha_size = os.path.getsize(output_path)
                except OSError:
                    _ha_size = None
                if _ha_size is not None and _ha_size <= _GHOST_MAX_BYTES:
                    _pkt_verdict = _probe_audio_at_offset(output_path, 0.0, window_sec=2.0)
                    if _pkt_verdict is None:
                        raise RuntimeError(f'has_audio 소형 파트 패킷 probe 불가 [{i + 1}/{total_parts}] ({_ha_size}B, start={start}s) — 재시도 필요 (파일 보존됨: {output_path})')
                    if _pkt_verdict is False:
                        is_tail = i == total_parts - 1
                        if duration_reliable:
                            if is_tail:
                                _src_eof = _confirm_source_eof(input_path, start, duration, log_func)
                                if _src_eof is False:
                                    raise RuntimeError(f'has_audio 소형 tail 패킷 없으나 원본에 오디오 존재 [{i + 1}/{total_parts}] (start={start}s) — 분할 실패 (파일 보존됨: {output_path})')
                                if _src_eof is None:
                                    raise RuntimeError(f'has_audio 소형 tail EOF probe 불가 [{i + 1}/{total_parts}] (start={start}s) — 재시도 필요 (파일 보존됨: {output_path})')
                                log_func(f'[{i + 1}/{total_parts}] [경고] has_audio 이나 패킷 없음 + 원본 EOF 확인 ({_ha_size}B, start={start}s) → ghost 삭제: {output_path}')
                                _remove_ghost_part(output_path, log_func)
                                continue
                            raise RuntimeError(f'has_audio 파트에 패킷 없음 [{i + 1}/{total_parts}] ({_ha_size}B, start={start}s) — 파일 보존됨: {output_path}')
                        _deferred_ghost_candidates.append((i, output_path, f'has_audio packetless size={_ha_size}B start={start}s'))
                        continue
        if _deferred_ghost_candidates:
            _deferred_indices = [d[0] for d in _deferred_ghost_candidates]
            raise RuntimeError(f'ghost suffix 불일치: part {i + 1} 에 유효 오디오가 있으나 이전 파트 {_deferred_indices} 가 ghost candidate — 데이터 손실 가능 (파일 보존됨: {output_path})')
        part_paths.append(output_path)
    if _deferred_ghost_candidates:
        _first_deferred_start = _deferred_ghost_candidates[0][0] * SEGMENT_SECONDS
        _last_deferred_end = (_deferred_ghost_candidates[-1][0] + 1) * SEGMENT_SECONDS
        _suffix_span = min(max(_last_deferred_end - _first_deferred_start, 2.0), 300.0)
        _eof_verdict = _probe_decodable_audio(input_path, _first_deferred_start, window_sec=_suffix_span)
        _deferred_indices = [d[0] for d in _deferred_ghost_candidates]
        if _eof_verdict is True:
            raise RuntimeError(f'ghost suffix 의심 파트 {_deferred_indices} 의 원본 오프셋 {_first_deferred_start}s~{_last_deferred_end}s 에 오디오 패킷 존재 — ghost 가 아닌 분할 실패 (파일 보존됨)')
        if _eof_verdict is None:
            raise RuntimeError(f'ghost suffix 의심 파트 {_deferred_indices}: EOF probe 결론 불가 (오프셋 {_first_deferred_start}s~{_last_deferred_end}s, span={_suffix_span:.0f}s) — 재시도 필요 (파일 보존됨)')
        for _di, _dp, _dd in _deferred_ghost_candidates:
            log_func(f'[{_di + 1}/{total_parts}] [경고] ghost suffix 확정 (원본 오프셋 {_first_deferred_start}s~{_last_deferred_end}s 에 오디오 없음 확인) → 건너뜀: {_dp} ({_dd})')
            _remove_ghost_part(_dp, log_func)
    return (part_paths, total_parts)

def _ffmpeg_creationflags() -> int:
    """Windows 에서 ffmpeg subprocess 를 조용히 띄우기 위한 flag.

    `CREATE_NO_WINDOW` (0x08000000) 로 콘솔 subsystem 할당을 건너뛰면
    긴 VOD 를 여러 파트로 연속 처리할 때 발생하는 0xC0000142
    (STATUS_DLL_INIT_FAILED; "DLL init failed") 의 빈도를 줄일 수 있다.
    이 에러는 짧은 시간 내에 child 프로세스를 많이 띄우면 Windows
    desktop heap / csrss 자원 경합으로 간헐 발생.
    """
    if sys.platform != 'win32':
        return 0
    return getattr(subprocess, 'CREATE_NO_WINDOW', 134217728)

def _probe_decodable_audio(input_path: str, offset_sec: float, window_sec: float=10.0):
    '원본의 특정 오프셋에서 오디오가 실제 decode 가능한지 확인 (tri-state).\n\n    _probe_audio_at_offset 은 패킷 헤더만 확인하므로 HLS concatenated MP4 에서\n    decodable 하지 않은 잔여 패킷에 대해 True 반환할 수 있다.\n    이 함수는 ffmpeg 으로 실제 decode + raw PCM stdout 출력을 시도하여\n    출력 바이트가 존재하는지 확인.\n\n    Returns:\n        True  — decode 된 오디오 데이터 존재 (PCM bytes > 0)\n        False — decode 결과 empty (0 bytes, 오디오 없음 또는 undecodable)\n        None  — timeout/error (결론 불가)\n    '
    _timeout = max(30, min(180, int(window_sec) + 30))
    try:
        result = subprocess.run([FFMPEG, '-v', 'error', '-ss', str(offset_sec), '-i', input_path, '-t', str(window_sec), '-vn', '-map', '0:a:0', '-f', 's16le', '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', '-'], capture_output=True, timeout=_timeout, creationflags=_ffmpeg_creationflags())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 and len(result.stdout) == 0:
        return None
    return len(result.stdout) > 0

def _probe_audio_at_offset(input_path: str, offset_sec: float, window_sec: float=2.0):
    '원본 입력 파일의 특정 오프셋에 오디오 패킷이 존재하는지 확인 (tri-state).\n\n    Returns:\n        True  — 오디오 패킷 존재 확인 (offset 이후 pts_time 패킷 발견)\n        False — 오디오 없음 확인 (패킷 없거나 모두 offset 이전)\n        None  — 결론 불가 (timeout, error, 파싱 불가 pts_time)\n\n    ffprobe 로 *offset_sec* 부터 *window_sec* 간 오디오 프레임을 읽어,\n    반환된 패킷의 pts_time 이 요청 오프셋 이후인 것이 1개 이상이면 True.\n    ffprobe 의 seek 부정확성으로 요청 이전 패킷이 반환될 수 있으므로\n    pts_time 을 파싱하여 offset_sec 이후 패킷만 카운트.\n    threshold = offset_sec (tolerance 없음: offset 이전 패킷은 suffix 증거 아님,\n     round 18).\n\n    pts_time 이 파싱 불가한 행(예: N/A)이 있으면 None.\n    타임아웃/에러 시 None.\n    '
    _timeout = max(15, min(120, int(window_sec / 10) + 15))
    try:
        result = subprocess.run([FFPROBE, '-v', 'error', '-read_intervals', f'{offset_sec:.3f}%+{window_sec:.3f}', '-select_streams', 'a:0', '-show_entries', 'packet=pts_time', '-of', 'csv=p=0', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=_timeout, creationflags=_ffmpeg_creationflags())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    stdout = (result.stdout or '').strip()
    if not stdout:
        return False
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pts = float(line)
        except ValueError:
            return None
        if pts >= offset_sec:
            return True
    return False

class NoAudioStreamError(RuntimeError):
    '입력 파일에 오디오 스트림이 없어 음성 추출이 불가능할 때 발생.\n\n    ghost part (container duration 초과 분할) 등에서 나타나며,\n    호출부에서 이 예외만 선택적으로 catch 해 건너뛸 수 있다.\n    '

_NO_AUDIO_RE = re.compile('does not contain any stream|output file is empty|\\bno streams\\b|\\bempty output\\b', re.IGNORECASE)

_STDERR_QUOTED_PATH_RE = re.compile('\'[^\']*\'|"[^"]*"')

def _strip_paths_from_stderr(stderr: str, *known_paths: str) -> str:
    """Remove file paths from ffmpeg stderr to prevent classifier contamination.

    Strips quoted substrings (single/double) and any explicitly supplied
    *known_paths* (input/output file paths used in the ffmpeg command).
    This prevents filenames like ``output file is empty.mp4`` from matching
    the ghost-evidence regex.
    """
    result = _STDERR_QUOTED_PATH_RE.sub('', stderr)
    for p in known_paths:
        if p:
            for variant in {p, p.replace('\\', '/'), p.replace('/', '\\'), os.path.basename(p)}:
                result = result.replace(variant, '')
    return result

def extract_audio(video_path, log_func=print):
    audio_path = os.path.splitext(video_path)[0] + '.wav'
    _pre_probe = probe_audio_stream(video_path)
    if _pre_probe.state == 'no_audio':
        if os.path.isfile(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        raise NoAudioStreamError(f'오디오 스트림 없음 (ghost part): {os.path.basename(video_path)}')
    if _pre_probe.state == 'probe_failed':
        _WAV_MIN_BYTES_PROBE_FAIL = 1024
        _source_exists = os.path.isfile(video_path)
        if _source_exists and os.path.isfile(audio_path):
            try:
                _cached_size = os.path.getsize(audio_path)
            except OSError:
                _cached_size = 0
            try:
                _wav_mtime = os.path.getmtime(audio_path)
                _src_mtime = os.path.getmtime(video_path)
                _mtime_fresh = _wav_mtime >= _src_mtime
            except OSError:
                _mtime_fresh = False
            if _cached_size >= _WAV_MIN_BYTES_PROBE_FAIL and _mtime_fresh:
                log_func(f'  [경고] probe 실패 ({_pre_probe.detail}) 이지만 기존 WAV 캐시 사용 (source 존재 + mtime 유효): {audio_path} ({_cached_size}B)')
                return audio_path
        raise RuntimeError(f'오디오 probe 실패 (재시도 필요): {os.path.basename(video_path)} — {_pre_probe.detail}')
    _WAV_MIN_BYTES = 1024
    if os.path.isfile(audio_path):
        try:
            size = os.path.getsize(audio_path)
        except OSError:
            size = 0
        if size >= _WAV_MIN_BYTES:
            log_func(f'  음성 이미 존재: {audio_path}')
            return audio_path
        log_func(f'  [경고] 기존 wav 가 너무 작음 ({size}B) → 재추출: {audio_path}')
        try:
            os.remove(audio_path)
        except OSError as e:
            log_func(f'  [경고] 기존 wav 삭제 실패 (무시): {e}')
    cmd = [FFMPEG, '-y', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', str(SAMPLE_RATE), '-ac', '1', audio_path]
    log_func(f'  음성 추출 중: {audio_path}')
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            result = subprocess.run(cmd, capture_output=True, creationflags=_ffmpeg_creationflags())
        except OSError as e:
            last_err = e
            log_func(f'  [시도 {attempt}/3] subprocess.run OSError: {e}')
            time.sleep(2 * attempt)
            continue
        if result.returncode == 0:
            try:
                out_size = os.path.getsize(audio_path)
            except OSError:
                out_size = 0
            if out_size < 1024:
                log_func(f'  [시도 {attempt}/3] ffmpeg 성공 코드지만 결과물이 비어있음 ({out_size}B) → 재시도')
                last_err = RuntimeError(f'ffmpeg 결과물 스텁: {os.path.basename(audio_path)} ({out_size}B)')
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                break
            if attempt > 1:
                log_func(f'  음성 추출 성공 (재시도 {attempt} 회 만에)')
            return audio_path
        _raw_ext_stderr = result.stderr.decode('utf-8', errors='replace')
        _stripped_ext_stderr = _strip_paths_from_stderr(_raw_ext_stderr, video_path, audio_path)
        stderr_tail = _raw_ext_stderr[-400:]
        log_func(f'  [시도 {attempt}/3] ffmpeg returncode={result.returncode} (0x{result.returncode & 4294967295:08X}) stderr_empty={not stderr_tail.strip()}')
        if _NO_AUDIO_RE.search(_stripped_ext_stderr[-400:]):
            raise NoAudioStreamError(f'오디오 스트림 없음: {os.path.basename(video_path)}\nstderr_tail={stderr_tail}')
        last_err = RuntimeError(f'ffmpeg 음성 추출 실패 (returncode={result.returncode}): {os.path.basename(video_path)}\ncmd={cmd}\nstderr_tail={stderr_tail}')
        is_windows_init_fail = sys.platform == 'win32' and result.returncode in (-1073741502, -1073741510, 3221225794, 1073807364)
        if attempt < 3 and (is_windows_init_fail or not stderr_tail.strip()):
            time.sleep(2 * attempt)
            continue
        break
    assert last_err is not None
    raise last_err

def _ensure_audio_cache_available(audio_info, log_func=print):
    """Return a usable WAV path, regenerating it from the part video if needed.

    Resume can race with cleanup or external repair work: `extract_audio` may
    have returned a cached WAV path, but the file can disappear before VAD
    prescan opens it. Regenerate from the corresponding part mp4 instead of
    failing the whole VOD with FileNotFoundError.
    """
    audio_path = audio_info['audio_path']
    try:
        if os.path.isfile(audio_path) and os.path.getsize(audio_path) >= 1024:
            return audio_path
    except OSError:
        pass
    part_path = audio_info.get('part_path')
    if part_path and os.path.isfile(part_path):
        log_func(f'  [경고] WAV 캐시 누락/손상 → 재추출: {os.path.basename(audio_path)}')
        regenerated = extract_audio(part_path, log_func)
        audio_info['audio_path'] = regenerated
        return regenerated
    raise FileNotFoundError(f'WAV 캐시를 열 수 없고 원본 part도 없습니다: audio={audio_path!r}, part={part_path!r}')

def load_models(log_func=print, whisper_model: str='large-v3-turbo'):
    from speech_setup import load_models as load_pinned_models
    return load_pinned_models(log_func=log_func, whisper_model=whisper_model)

def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = int(seconds % 60)
    ms = int(seconds % 1 * 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def load_audio(audio_path):
    with wave.open(audio_path, 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) == 0:
        raise ValueError(f'오디오 파일이 비어 있습니다: {audio_path}')
    return audio

def compression_ratio(text):
    """텍스트의 zlib 압축률. 높을수록 반복적(환각 가능성)."""
    text_bytes = text.encode('utf-8')
    return len(text_bytes) / len(zlib.compress(text_bytes))

def is_hallucination(text):
    """환각 여부를 종합 판단한다."""
    text = text.strip()
    if not text:
        return True
    if len(text) > 10 and compression_ratio(text) > COMPRESSION_RATIO_THRESHOLD:
        return True
    words = text.split()
    if len(words) >= 6:
        for n in range(2, min(6, len(words) // 2 + 1)):
            ngrams = [' '.join(words[i:i + n]) for i in range(len(words) - n + 1)]
            counts = Counter(ngrams)
            if counts.most_common(1)[0][1] >= 3:
                return True
    if len(set(words)) == 1 and len(words) >= 3:
        return True
    return False

def get_speech_segments(audio, vad_model, vad_utils):
    """Silero VAD로 음성 구간만 추출한다. [(start_sample, end_sample), ...]"""
    import torch
    get_speech_timestamps = vad_utils[0]
    audio_tensor = torch.from_numpy(audio).float()
    speech_timestamps = get_speech_timestamps(audio_tensor, vad_model, sampling_rate=SAMPLE_RATE, threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=500, speech_pad_ms=300)
    segments = []
    for ts in speech_timestamps:
        segments.append((ts['start'], ts['end']))
    return segments

def merge_vad_into_chunks(speech_segments, total_samples, max_chunk_sec=WHISPER_MAX_CHUNK_SEC):
    """VAD 결과를 설정된 최대 길이 이하의 chunk로 묶는다."""
    max_samples = max_chunk_sec * SAMPLE_RATE
    if max_samples <= 0:
        raise ValueError('max_chunk_sec must be greater than zero')
    chunks = []
    current_start = None
    current_end = None
    for raw_start, raw_end in speech_segments:
        seg_start = max(0, min(int(raw_start), total_samples))
        seg_end = max(seg_start, min(int(raw_end), total_samples))
        cursor = seg_start
        while cursor < seg_end:
            if current_start is None:
                current_start = cursor
                current_end = cursor
            chunk_limit = current_start + max_samples
            if cursor >= chunk_limit:
                if current_end > current_start:
                    chunks.append((current_start, current_end))
                current_start = cursor
                current_end = cursor
                chunk_limit = current_start + max_samples
            current_end = max(current_end, min(seg_end, chunk_limit))
            cursor = current_end
            if current_end >= chunk_limit:
                chunks.append((current_start, current_end))
                current_start = None
                current_end = None
    if current_start is not None:
        chunks.append((current_start, current_end))
    return chunks

def parse_whisper_tokens(decoded_text, audio_duration_sec=30.0):
    """Whisper 출력에서 timestamp 토큰을 파싱한다.
    반환: (segments, last_timestamp)"""
    segments = []
    last_ts = 0.0
    audio_duration_sec = max(0.0, float(audio_duration_sec))
    pattern = '<\\|(\\d+\\.\\d+)\\|>(.*?)<\\|(\\d+\\.\\d+)\\|>'
    matches = re.findall(pattern, decoded_text)
    for start_str, text, end_str in matches:
        text = text.strip()
        if not text:
            continue
        start = min(float(start_str), audio_duration_sec)
        end = min(float(end_str), audio_duration_sec)
        if end <= start:
            continue
        segments.append({'start': start, 'end': end, 'text': text})
        last_ts = max(last_ts, end)
    if not segments:
        clean = re.sub('<\\|.*?\\|>', '', decoded_text).strip()
        if clean:
            segments.append({'start': 0, 'end': audio_duration_sec, 'text': clean})
            last_ts = audio_duration_sec
    all_ts = re.findall('<\\|(\\d+\\.\\d+)\\|>', decoded_text)
    if all_ts:
        last_ts = max(last_ts, min(audio_duration_sec, max((float(t) for t in all_ts))))
    return (segments, last_ts)

def split_long_text(text, start, end):
    """긴 텍스트를 표시 길이와 지속시간 상한에 맞춰 균등 분할한다."""
    duration = end - start
    max_chars = MAX_CHARS_PER_LINE * MAX_LINES
    if duration <= MAX_SUBTITLE_SEC and len(text) <= max_chars:
        return [{'start': start, 'end': end, 'text': text}]
    duration_parts = max(1, math.ceil(duration / MAX_SUBTITLE_SEC))
    text_parts = max(1, math.ceil(len(text) / max_chars))
    part_count = min(len(text), max(duration_parts, text_parts))
    if part_count <= 1:
        return [{'start': start, 'end': min(end, start + MAX_SUBTITLE_SEC), 'text': text}]
    boundaries = [0]
    search_start = 0
    for index in range(1, part_count):
        remaining_parts = part_count - index
        minimum = max(search_start + 1, len(text) - remaining_parts * max_chars)
        maximum = min(search_start + max_chars, len(text) - remaining_parts)
        target = min(maximum, max(minimum, round(len(text) * index / part_count)))
        best_pos = -1
        for pattern in SPLIT_PATTERNS:
            for match in pattern.finditer(text, minimum, maximum + 1):
                pos = match.end()
                if pos <= search_start or pos >= len(text):
                    continue
                if best_pos == -1 or abs(pos - target) < abs(best_pos - target):
                    best_pos = pos
            if best_pos != -1:
                break
        if best_pos == -1:
            best_pos = min(maximum, max(minimum, target))
        boundaries.append(best_pos)
        search_start = best_pos
    boundaries.append(len(text))
    part_duration = duration / part_count
    parts = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        part_text = text[left:right].strip()
        if not part_text:
            continue
        part_start = start + part_duration * index
        part_end = min(end, part_start + MAX_SUBTITLE_SEC)
        parts.append({'start': part_start, 'end': part_end, 'text': part_text})
    return parts

def postprocess_entries(entries, source_duration_sec=None):
    """자막 엔트리를 시간순으로 정규화하고 표시 품질을 높인다."""
    if not entries:
        return entries
    result = []
    for entry in sorted(entries, key=lambda item: (item['start'], item['end'])):
        text = re.sub('\\s+', ' ', entry['text'].replace('�', ' ')).strip()
        if not text:
            continue
        start = max(0.0, float(entry['start']))
        end = max(0.0, float(entry['end']))
        if source_duration_sec is not None:
            source_duration_sec = max(0.0, float(source_duration_sec))
            start = min(start, source_duration_sec)
            end = min(end, source_duration_sec)
        duration = end - start
        if duration < MIN_SUBTITLE_SEC:
            continue
        parts = split_long_text(text, start, end)
        result.extend(parts)
    result.sort(key=lambda item: (item['start'], item['end']))
    merged = []
    for entry in result:
        if merged and entry['start'] - merged[-1]['end'] < MIN_GAP_SEC:
            prev = merged[-1]
            combined = prev['text'] + ' ' + entry['text']
            combined_dur = entry['end'] - prev['start']
            if combined_dur <= MAX_SUBTITLE_SEC and len(combined) <= MAX_CHARS_PER_LINE * MAX_LINES:
                prev['end'] = entry['end']
                prev['text'] = combined
                continue
        merged.append(dict(entry))
    for i in range(1, len(merged)):
        if merged[i]['start'] < merged[i - 1]['end']:
            if merged[i]['start'] > merged[i - 1]['start']:
                adjusted_end = merged[i]['start'] - 0.001
                if adjusted_end > merged[i - 1]['start']:
                    merged[i - 1]['end'] = adjusted_end
    return [entry for entry in merged if entry['end'] > entry['start']]

def _append_existing_whisper_word_alignment(model, processor, device, torch_dtype, audio, retained_batches, word_alignment_sink, time_offset=0, log_func=print, heartbeat_func=None):
    """Align already-generated Whisper tokens without a second ASR decode.

    The primary transcript remains authoritative. This optional pass only
    exposes word times from decoder cross-attention so anonymous speaker turns
    can split an existing cue. A failed batch leaves the baseline SRT intact.
    """
    import torch
    from transformers.utils import ModelOutput
    if not retained_batches:
        return 0
    if not hasattr(model, '_extract_token_timestamps'):
        raise RuntimeError('Whisper token timestamp extraction is unavailable')
    if not hasattr(processor.tokenizer, '_decode_asr'):
        raise RuntimeError('Whisper word timestamp decoding is unavailable')
    alignment_heads = getattr(model.generation_config, 'alignment_heads', None)
    if alignment_heads is None:
        raise RuntimeError('Whisper alignment heads are unavailable')
    config = model.config
    had_attention_setting = hasattr(config, '_attn_implementation')
    original_attention_setting = getattr(config, '_attn_implementation', None)
    aligned_words = 0
    total_batches = len(retained_batches)
    total_chunks = sum((len(batch['chunks']) for batch in retained_batches))
    completed_chunks = 0
    skipped_batches = 0
    log_func(f'    [화자 전환 정렬] 시작: {total_batches}개 배치, {total_chunks}개 청크 (기존 Whisper 토큰으로 단어 시각 계산)')
    try:
        config._attn_implementation = 'eager'
        for batch_number, retained in enumerate(retained_batches, start=1):
            try:
                chunk_audios = [audio[start:end] for start, end in retained['chunks']]
                processed = processor(chunk_audios, sampling_rate=SAMPLE_RATE, return_tensors='pt', return_attention_mask=True)
                input_features = processed.input_features.to(device, dtype=torch_dtype)
                attention_mask = processed.attention_mask.to(device)
                sequences = retained['sequences'].to(device)
                with torch.no_grad():
                    teacher = model(input_features=input_features, attention_mask=attention_mask, decoder_input_ids=sequences[:, :-1], output_attentions=True, use_cache=False, return_dict=True)
                token_timestamps = model._extract_token_timestamps(ModelOutput(cross_attentions=(teacher.cross_attentions,), sequences=sequences), alignment_heads, time_precision=0.02, num_frames=attention_mask.sum(-1), num_input_ids=0)
                decode_time_precision = processor.feature_extractor.chunk_length / model.config.max_source_positions
                for index, (start_sample, _) in enumerate(retained['chunks']):
                    _, optional = processor.tokenizer._decode_asr([{'tokens': sequences[index:index + 1], 'token_timestamps': token_timestamps[index:index + 1]}], return_timestamps='word', return_language=False, time_precision=decode_time_precision)
                    absolute_chunk_start = start_sample / SAMPLE_RATE + time_offset
                    for raw_word in optional.get('chunks', []):
                        timestamp = raw_word.get('timestamp') or (None, None)
                        if timestamp[0] is None or timestamp[1] is None:
                            continue
                        word_alignment_sink.append({'text': str(raw_word.get('text') or ''), 'start': absolute_chunk_start + float(timestamp[0]), 'end': absolute_chunk_start + float(timestamp[1])})
                        aligned_words += 1
                completed_chunks += len(retained['chunks'])
                log_func(f'    [화자 전환 정렬] 진행: {batch_number}/{total_batches} 배치, {completed_chunks}/{total_chunks} 청크, {aligned_words}개 단어')
                if heartbeat_func is not None:
                    heartbeat_func()
            except Exception as exc:
                skipped_batches += 1
                log_func(f"    [화자 전환 정렬] {batch_number}/{total_batches} 배치 건너뜀 ({len(retained['chunks'])}개 청크): {exc}")
    finally:
        if had_attention_setting:
            config._attn_implementation = original_attention_setting
        else:
            try:
                delattr(config, '_attn_implementation')
            except AttributeError:
                pass
    log_func(f'    [화자 전환 정렬] 완료: {completed_chunks}/{total_chunks} 청크, {aligned_words}개 단어, 건너뛴 배치 {skipped_batches}개')
    return aligned_words

def transcribe_audio(model, processor, device, torch_dtype, vad_model, vad_utils, audio_path, time_offset=0, part_num=0, total_parts=0, log_func=print, progress_func=None, chunk_offset=0, total_chunks_global=0, precomputed_speech_segments=None, preloaded_audio=None, stop_event=None, initial_prompt_text: str | None=None, turn_candidate_times=None, word_alignment_sink=None, retain_word_alignment: bool=False, defer_word_alignment: bool=False, alignment_request_sink=None):
    """
    반환: (entries, total_chunks_in_this_part)
    preloaded_audio: VAD prescan 시 이미 로드한 ndarray → 재로드 생략
    stop_event: threading.Event — set() 시 루프 중단 후 부분 결과 반환
    """
    import torch
    log_func(f'\n  [{part_num}/{total_parts}] 자막 생성 중: {audio_path}')
    log_func(f'  시간 오프셋: {format_timestamp(time_offset)}')
    t_start = time.time()
    audio = preloaded_audio if preloaded_audio is not None else load_audio(audio_path)
    total_samples = len(audio)
    total_duration = total_samples / SAMPLE_RATE
    if precomputed_speech_segments is not None:
        speech_segments = precomputed_speech_segments
        log_func('  VAD 결과 재사용 (사전 분석)')
    else:
        log_func(f'  VAD 분석 중... (총 {total_duration:.0f}초)')
        speech_segments = get_speech_segments(audio, vad_model, vad_utils)
    speech_duration = sum((e - s for s, e in speech_segments)) / SAMPLE_RATE
    if total_duration > 0:
        log_func(f'  음성 구간: {speech_duration:.0f}초 / {total_duration:.0f}초 ({speech_duration / total_duration * 100:.0f}%)')
    else:
        log_func(f'  음성 구간: {speech_duration:.0f}초')
    if not speech_segments:
        log_func('  음성 없음, 건너뜀')
        return ([], 0)
    chunks = merge_vad_into_chunks(speech_segments, total_samples)
    total_chunks = len(chunks)
    entries = []
    count = 0
    turn_candidate_times = sorted((float(value) for value in turn_candidate_times or []))
    word_alignment_sink = word_alignment_sink if word_alignment_sink is not None else []
    turn_alignment_requests = []
    _prompt_text = initial_prompt_text or '안녕하세요, 환영합니다. 오늘도 재밌게 해봅시다! 자, 그러면 시작할게요.'
    prompt_ids = processor.get_prompt_ids(_prompt_text, return_tensors='pt').to(device)
    batch_size, batch_reason = resolve_whisper_batch_size(device, torch)
    log_func(f'  Whisper GPU 배치: {batch_size} ({batch_reason})')
    bi = 0
    while bi < len(chunks):
        if stop_event is not None and stop_event.is_set():
            log_func('  [취소] 처리 중단됨')
            break
        batch = chunks[bi:bi + batch_size]
        chunk_audios = [audio[s:e] for s, e in batch]
        chunk_start_secs = [s / SAMPLE_RATE for s, _ in batch]
        input_features = None
        predicted_ids = None
        try:
            inputs = processor(chunk_audios, sampling_rate=SAMPLE_RATE, return_tensors='pt')
            input_features = inputs.input_features.to(device, dtype=torch_dtype)
            with torch.no_grad():
                predicted_ids = model.generate(input_features, language='ko', task='transcribe', return_timestamps=True, prompt_ids=prompt_ids, condition_on_prev_tokens=False, compression_ratio_threshold=1.35, no_speech_threshold=0.6, logprob_threshold=-1.0, temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0), num_beams=5, no_repeat_ngram_size=3, repetition_penalty=1.2, length_penalty=1.0)
            decoded_all = processor.batch_decode(predicted_ids, skip_special_tokens=False)
            if turn_candidate_times or retain_word_alignment:
                turn_alignment_requests.append({'chunks': list(batch), 'sequences': predicted_ids.detach().cpu()})
            del input_features, predicted_ids
        except Exception as exc:
            if device != 'cuda' or not is_cuda_oom(exc, torch) or batch_size <= 1:
                raise
            previous_batch = batch_size
            batch_size = next_lower_whisper_batch_size(batch_size)
            del input_features, predicted_ids
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            log_func(f'  [GPU 메모리 자동 조정] batch {previous_batch} → {batch_size}; 같은 청크부터 재시도')
            continue
        for bj, (decoded, chunk_start_sec, chunk_bounds) in enumerate(zip(decoded_all, chunk_start_secs, batch)):
            ci = bi + bj
            chunk_duration_sec = (chunk_bounds[1] - chunk_bounds[0]) / SAMPLE_RATE
            segments, _ = parse_whisper_tokens(decoded, audio_duration_sec=chunk_duration_sec)
            for seg in segments:
                if is_hallucination(seg['text']):
                    log_func(f"    [환각 필터] {seg['text'][:50]}...")
                    continue
                count += 1
                abs_start = seg['start'] + chunk_start_sec + time_offset
                abs_end = seg['end'] + chunk_start_sec + time_offset
                entries.append({'start': abs_start, 'end': abs_end, 'text': seg['text']})
                log_func(f"    #{count} [{format_timestamp(abs_start)} --> {format_timestamp(abs_end)}] {seg['text']}")
            elapsed = time.time() - t_start
            current_chunk = chunk_offset + ci + 1
            effective_total = total_chunks_global if total_chunks_global > 0 else total_chunks
            progress_pct = current_chunk / effective_total * 100 if effective_total > 0 else 0
            log_func(f'    -- chunk {ci + 1}/{total_chunks} ({progress_pct:.0f}%) | {elapsed:.0f}초 경과')
            if progress_func is not None:
                progress_func(current_chunk, effective_total)
        bi += len(batch)
    if defer_word_alignment and alignment_request_sink is not None:
        alignment_request_sink.extend(turn_alignment_requests)
    elif turn_alignment_requests:
        try:
            aligned_word_count = _append_existing_whisper_word_alignment(model, processor, device, torch_dtype, audio, turn_alignment_requests, word_alignment_sink, time_offset=time_offset, log_func=log_func)
            log_func(f'  화자 전환 단어 정렬: {aligned_word_count}개')
        except Exception as exc:
            log_func(f'  [화자 전환 정렬 미적용] {exc}')
    elapsed = time.time() - t_start
    log_func(f'  완료: {len(entries)}개 자막 ({elapsed:.1f}초 소요)')
    if device == 'cuda':
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    return (entries, total_chunks)

def write_srt(entries, output_path):
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, entry in enumerate(entries, 1):
            f.write(f'{i}\n')
            f.write(f"{format_timestamp(entry['start'])} --> {format_timestamp(entry['end'])}\n")
            f.write(f"{entry['text']}\n\n")

def _write_new_srt_atomically(entries, output_path):
    """Publish one new SRT atomically without replacing an existing file."""
    from pipeline.subtitle_publication import publish_new_file
    return str(publish_new_file(output_path, lambda stage: write_srt(entries, stage)))

def _fallback_srt_path(original_input, attempted_output):
    """Allocate a non-overwriting ordinary SRT for pre-assist fallback text."""
    attempted = os.path.abspath(attempted_output)
    output_dir = os.path.dirname(attempted)
    attempted_stem = os.path.splitext(os.path.basename(attempted))[0]
    marker_at = attempted_stem.lower().rfind('.reference-turn-')
    if marker_at > 0:
        baseline_stem = attempted_stem[:marker_at]
    else:
        baseline_stem = os.path.splitext(os.path.basename(original_input))[0]

    def available(candidate):
        root, _ = os.path.splitext(candidate)
        return not any((os.path.exists(path) for path in (candidate, root + '.speaker_turns.json', root + '.subtitle_timing.json', root + '.chrome_stt_metadata.json')))
    default_output = os.path.join(output_dir, baseline_stem + '.srt')
    if available(default_output):
        return default_output
    token = zlib.crc32(os.path.basename(attempted).encode('utf-8')) & 4294967295
    base = os.path.join(output_dir, f'{baseline_stem}.subtitle-retry-{token:08x}.srt')
    if available(base):
        return base
    base_stem = os.path.splitext(base)[0]
    for revision in range(2, 1000):
        candidate = f'{base_stem}-{revision:02d}.srt'
        if available(candidate):
            return candidate
    raise RuntimeError('Unable to allocate a preserved baseline fallback SRT path')

def _publish_pre_turn_fallback(entries, original_input, attempted_output):
    """Retry path allocation if another overlapping run wins publication."""
    for _attempt in range(100):
        fallback_output = _fallback_srt_path(original_input, attempted_output)
        try:
            _write_new_srt_atomically(entries, fallback_output)
            return fallback_output
        except FileExistsError:
            continue
    raise RuntimeError('Unable to exclusively publish a baseline fallback SRT')

def determine_srt_output(original_input: str) -> str:
    """원본 파일 경로의 확장자를 .srt로 변경한다."""
    return os.path.splitext(original_input)[0] + '.srt'

def _get_merge_output_path(files_info: list) -> str:
    """
    분할 파일 목록으로부터 병합 출력 경로를 결정한다.

    - Part 패턴이 있으면 제거한 깔끔한 이름 사용
      ex) "[옵시온] 도태의 왕 - Part 1 (00-00-00 to 01-00-01).mp3"
          → "[옵시온] 도태의 왕.mp3"

    - Part 패턴이 없으면 (예: 1.mp4, 2.mp4 ...) _merged 접미사 추가
      ex) "1.mp4" → "1_merged.mp4"
      (입력 파일 자체와 경로가 겹치는 것을 방지)
    """
    first_path = files_info[0]['path']
    _, ext = os.path.splitext(first_path)
    name = os.path.splitext(os.path.basename(first_path))[0]
    stripped = PART_SUFFIX_PATTERN.sub('', name).strip()
    if stripped != name:
        name = stripped
    else:
        name = name + '_merged'
    output_dir = os.path.dirname(os.path.abspath(first_path))
    return os.path.join(output_dir, name + ext)

def run_caption_generation(files_info, is_split, log_func=print, progress_func=None, cleanup=False, stop_event=None, initial_prompt_text: str | None=None, vad_prescan_workers: int | None=None, whisper_model: str='large-v3-turbo', speaker_turn_split_enabled: bool=True, nemotron_turns_enabled: bool=False, speaker_turn_executable_path: str | None=None, speaker_turn_model_path: str | None=None, speaker_reference_profile=None, srt_output_path: str | None=None):
    """
    files_info: [{"path": str, ...}, ...]
    is_split=False: files_info[0] 단일 파일 → 1시간 단위 분할 후 처리
    is_split=True:  분할 파일 목록 → 손실 없이 병합 → 동일 파이프라인으로 처리

    진행률 2패스 방식:
      1. VAD 선행 스캔 → total_chunks_global 계산 (오디오 캐싱으로 이중 로드 방지)
      2. transcribe_audio에 precomputed_speech_segments + preloaded_audio 전달

    cleanup=True: SRT 저장 후 중간 생성 파일(분할 파트, WAV, 병합 파일) 자동 삭제
    stop_event: threading.Event — set() 시 다음 청크에서 중단 후 부분 SRT 저장

    반환: SRT 파일 경로 (str)
    """
    if nemotron_turns_enabled:
        raise ValueError('loki_optional_nemotron_not_installed')
    temp_files = []
    if is_split:
        log_func('=' * 50)
        log_func('0단계: 분할 파일 병합')
        log_func('=' * 50)
        merged_path = _get_merge_output_path(files_info)
        input_abspaths = {os.path.abspath(fi['path']) for fi in files_info}
        already_merged = os.path.isfile(merged_path) and os.path.abspath(merged_path) not in input_abspaths
        if already_merged:
            log_func(f'이미 병합된 파일 존재, 재사용: {os.path.basename(merged_path)}')
        else:
            file_paths = [fi['path'] for fi in files_info]
            merge_files(file_paths, merged_path, log_func)
        temp_files.append(merged_path)
        original_input = merged_path
    else:
        original_input = files_info[0]['path']
    try:
        source_duration_sec = get_audio_duration(original_input)
        log_func(f'원본 오디오 길이: {source_duration_sec:.3f}초')
    except Exception as exc:
        source_duration_sec = None
        log_func(f'[경고] 원본 오디오 길이 확인 실패, 자막 끝 clamp 생략: {exc}')
    log_func('\n' + '=' * 50)
    log_func('1단계: 영상/음성 분할')
    log_func('=' * 50)
    part_paths, original_total_parts = split_video(original_input, log_func)
    temp_files.extend(part_paths)
    log_func('\n' + '=' * 50)
    log_func('2단계: 음성 추출')
    log_func('=' * 50)
    audio_infos = []
    _part_num_re = re.compile('_part(\\d{3})\\.[^.]+$')
    for i, part_path in enumerate(part_paths):
        m = _part_num_re.search(os.path.basename(part_path))
        part_idx = int(m.group(1)) - 1 if m else i
        time_offset = part_idx * SEGMENT_SECONDS
        is_original_tail = part_idx == original_total_parts - 1
        try:
            audio_path = extract_audio(part_path, log_func)
        except NoAudioStreamError as e:
            _ext_part_size = None
            try:
                _ext_part_size = os.path.getsize(part_path)
            except OSError:
                pass
            _ext_size_known = _ext_part_size is not None
            _is_ghost_stub = is_original_tail and _ext_size_known and (_ext_part_size <= _GHOST_MAX_BYTES)
            if _is_ghost_stub:
                log_func(f'  [경고] 마지막 파트 음성 추출 건너뜀 (오디오 없음, size={_ext_part_size}B ≤ {_GHOST_MAX_BYTES}B): {e}')
                continue
            _ext_src = _probe_decodable_audio(original_input, float(time_offset), window_sec=min(float(SEGMENT_SECONDS), 300.0))
            if _ext_src is False:
                log_func(f'  [경고] 파트 음성 추출 건너뜀 (소스 결손: offset={time_offset}s, part={part_idx + 1}): {e}')
                continue
            raise
        temp_files.append(audio_path)
        audio_infos.append({'audio_path': audio_path, 'part_path': part_path, 'time_offset': time_offset, 'part_num': part_idx + 1, 'total_parts': len(part_paths)})
    if not audio_infos:
        raise RuntimeError('모든 파트에서 음성 추출 실패 — 유효한 오디오 없음')
    log_func('\n' + '=' * 50)
    log_func('3단계: 모델 로드 (Whisper + Silero VAD)')
    log_func('=' * 50)
    model, processor, device, torch_dtype, vad_model, vad_utils = load_models(log_func, whisper_model=whisper_model)
    n_parts = len(audio_infos)
    vad_workers = resolve_vad_prescan_workers(n_parts, configured_workers=vad_prescan_workers)
    if vad_workers == 1:
        log_func('\nVAD 사전 분석 중 (안전 모드, workers=1)...')
    all_precomputed_segments = [None] * n_parts
    all_preloaded_audios = [None] * n_parts
    all_chunk_counts = [0] * n_parts
    if vad_workers == 1:
        for i, afi in enumerate(audio_infos):
            audio_path = _ensure_audio_cache_available(afi, log_func)
            audio = load_audio(audio_path)
            speech_segs = get_speech_segments(audio, vad_model, vad_utils)
            chunks = merge_vad_into_chunks(speech_segs, len(audio))
            all_precomputed_segments[i] = speech_segs
            all_preloaded_audios[i] = audio
            all_chunk_counts[i] = len(chunks)
            log_func(f"  [{i + 1}/{n_parts}] {os.path.basename(audio_infos[i]['audio_path'])}: {len(chunks)}개 청크")
    total_chunks_global = sum(all_chunk_counts)
    log_func(f'총 청크 수: {total_chunks_global}')
    turn_transitions = []
    turn_strict_transitions = []
    turn_reference_confirmed = []
    turn_overlaps = []
    turn_sherpa_evidence_parts = [None] * n_parts
    nemotron_turn_package = {'status': 'disabled', 'reason': 'setting_disabled'}
    turn_detection = {'schema_version': 'chzz.anonymous_speaker_turns.v1', 'enabled': bool(speaker_turn_split_enabled), 'status': 'disabled', 'alignment_mode': 'teacher_forced_existing_tokens', 'parts': []}
    turn_detection_pool = None
    turn_detection_futures = []
    turn_detection_cancel_event = None
    turn_detection_cancel_signal = None
    reference_score_fn = None
    if speaker_turn_split_enabled:
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from pipeline.speaker_turns import detect_anonymous_turns
        turn_detection_cancel_event = threading.Event()
        turn_detection_cancel_signal = _CombinedCancelEvent(turn_detection_cancel_event, stop_event)
        if speaker_reference_profile is not None and getattr(speaker_reference_profile, 'reference_enabled', False) is True and (getattr(speaker_reference_profile, 'reference_turn_assist_enabled', False) is True):
            try:
                from pipeline.subtitle_enhancement.reference import build_reference_window_scorer
                part_budget = max(1, len(audio_infos))
                reference_score_fn = build_reference_window_scorer(speaker_reference_profile, max_windows=min(960, 96 * part_budget), max_seconds=min(600, 60 * part_budget))
            except Exception as exc:
                log_func(f'[목소리 전환 보조 준비 미적용] {type(exc).__name__}')
        log_func('\n익명 화자 전환 후보 감지 시작 (sherpa CPU, Whisper와 병렬)...')
        turn_detection_pool = ThreadPoolExecutor(max_workers=1)
        for afi in audio_infos:
            raw_prefix = afi['audio_path'] + '.speaker_diarization'
            turn_detection_futures.append(turn_detection_pool.submit(detect_anonymous_turns, afi['audio_path'], raw_prefix, time_offset=float(afi['time_offset']), sortformer_executable_path=speaker_turn_executable_path, sortformer_model_path=speaker_turn_model_path, reference_profile=speaker_reference_profile, reference_score_fn=reference_score_fn, cancel_event=turn_detection_cancel_signal))
    log_func('\n' + '=' * 50)
    log_func('4단계: 자막 생성')
    log_func('=' * 50)
    all_entries = []
    turn_words = []
    subtitle_episodes = []
    chunk_offset = 0
    total_start = time.time()
    try:
        for i, (afi, precomp_segs, preloaded) in enumerate(zip(audio_infos, all_precomputed_segments, all_preloaded_audios)):
            if stop_event is not None and stop_event.is_set():
                if turn_detection_cancel_event is not None:
                    turn_detection_cancel_event.set()
                log_func('[취소] 파트 처리 중단됨')
                break
            retained_alignment = []
            entries, part_chunks = transcribe_audio(model, processor, device, torch_dtype, vad_model, vad_utils, afi['audio_path'], time_offset=afi['time_offset'], part_num=afi['part_num'], total_parts=afi['total_parts'], log_func=log_func, progress_func=progress_func, chunk_offset=chunk_offset, total_chunks_global=total_chunks_global, precomputed_speech_segments=precomp_segs, preloaded_audio=preloaded, stop_event=stop_event, initial_prompt_text=initial_prompt_text, retain_word_alignment=speaker_turn_split_enabled, defer_word_alignment=speaker_turn_split_enabled, alignment_request_sink=retained_alignment, word_alignment_sink=turn_words)
            all_entries.extend(entries)
            chunk_offset += part_chunks
            if speaker_turn_split_enabled:
                part_failure_stage = 'detector'
                try:
                    evidence = turn_detection_futures[i].result()
                    if turn_detection_cancel_signal is not None and turn_detection_cancel_signal.is_set():
                        raise RuntimeError('speaker_turn_detection_cancelled')
                    part_episodes = [{'start': float(row['start']), 'end': float(row['end']), 'overlap_sec': float(row['overlap_sec'])} for row in evidence.get('anonymous_episodes', []) if isinstance(row, dict) and all((key in row for key in ('start', 'end', 'overlap_sec')))]
                    part_turn_words = []
                    part_failure_stage = 'alignment'
                    _append_existing_whisper_word_alignment(model, processor, device, torch_dtype, preloaded, retained_alignment, part_turn_words, time_offset=float(afi['time_offset']), log_func=log_func, heartbeat_func=(lambda: progress_func(chunk_offset, total_chunks_global)) if progress_func is not None else None)
                    turn_transitions.extend(evidence['transitions'])
                    turn_strict_transitions.extend(evidence.get('strict_transitions', []))
                    turn_reference_confirmed.extend(evidence.get('reference_confirmed_transitions', []))
                    turn_overlaps.extend(evidence['overlaps'])
                    backend_evidence = evidence.get('backend_evidence', {})
                    if isinstance(backend_evidence, dict) and isinstance(backend_evidence.get('sherpa_cpu'), dict):
                        turn_sherpa_evidence_parts[i] = backend_evidence['sherpa_cpu']
                    turn_words.extend(part_turn_words)
                    subtitle_episodes.extend(part_episodes)
                    turn_detection['parts'].append({'part_num': afi['part_num'], 'backends': evidence.get('backends', []), 'backend_counts': evidence.get('backend_counts', {}), 'backend_errors': evidence.get('errors', {}), 'transition_count': len(evidence['transitions']), 'strict_transition_count': len(evidence.get('strict_transitions', [])), 'reference_assist': evidence.get('reference_assist', {}), 'overlap_count': len(evidence['overlaps']), 'decisions': evidence['decisions']})
                    turn_detection['status'] = 'detected'
                except Exception as exc:
                    failed_part = {'part_num': afi['part_num'], 'status': 'fallback', 'reason': f'{part_failure_stage}_failed:{type(exc).__name__}'}
                    if speaker_reference_profile is not None and getattr(speaker_reference_profile, 'reference_enabled', False) is True and (getattr(speaker_reference_profile, 'reference_turn_assist_enabled', False) is True):
                        failed_part['reference_assist'] = {'status': 'fallback', 'reason': f'{part_failure_stage}_failed'}
                    turn_detection['parts'].append(failed_part)
                    log_func(f'[화자 전환 파트 미적용] {exc}')
            elapsed_total = time.time() - total_start
            if i + 1 < len(audio_infos):
                avg_per_part = elapsed_total / (i + 1)
                remaining = avg_per_part * (len(audio_infos) - i - 1)
                log_func(f'  전체 진행: {i + 1}/{len(audio_infos)} | 경과: {elapsed_total:.0f}초 | 예상 남은 시간: {remaining:.0f}초')
    finally:
        if turn_detection_cancel_event is not None:
            turn_detection_cancel_event.set()
        if turn_detection_pool is not None:
            turn_detection_pool.shutdown(wait=True, cancel_futures=True)
    if speaker_turn_split_enabled and turn_detection['status'] == 'detected':
        turn_detection['transition_count'] = len(turn_transitions)
        turn_detection['overlap_count'] = len(turn_overlaps)
        log_func(f'화자 전환 후보 {len(turn_transitions)}개, 겹침 구간 {len(turn_overlaps)}개')
    elif speaker_turn_split_enabled:
        turn_detection['status'] = 'fallback'
        turn_detection['reason'] = 'no_part_detector_succeeded'
    log_func('\n' + '=' * 50)
    log_func('5단계: 자막 후처리')
    log_func('=' * 50)
    before_count = len(all_entries)
    all_entries = postprocess_entries(all_entries, source_duration_sec=source_duration_sec)
    log_func(f'후처리 완료: {before_count}개 → {len(all_entries)}개')
    pre_turn_entries = [dict(entry) for entry in all_entries]
    if speaker_turn_split_enabled and turn_detection['status'] == 'detected':
        try:
            from pipeline.speaker_turns import split_entries_at_anonymous_turns
            all_entries, composition = split_entries_at_anonymous_turns(all_entries, turn_transitions, turn_words, turn_overlaps, turn_strict_transitions, turn_reference_confirmed)
            turn_detection['composition'] = composition
            turn_detection['status'] = composition['status']
            log_func(f"익명 화자 전환 시간 분리: {composition.get('applied_splits', 0)}개 적용")
        except Exception as exc:
            turn_detection['status'] = 'fallback'
            turn_detection['reason'] = f'composition_failed:{type(exc).__name__}'
            log_func(f'[화자 전환 합성 미적용] {exc}')
    reference_provenance = None
    if speaker_turn_split_enabled:
        from pipeline.subtitle_enhancement.profile import reference_turn_assist_provenance
        reference_provenance = reference_turn_assist_provenance(speaker_reference_profile)
        if reference_provenance is not None:
            composition = turn_detection.get('composition', {})
            aggregate_status, aggregate_reason, part_status_counts = aggregate_reference_assist_status(turn_detection.get('parts', []), len(audio_infos), turn_detection_status=turn_detection.get('status'), composition_status=composition.get('status'), reference_assisted_splits=int(composition.get('reference_assisted_splits', 0) or 0))
            turn_detection['reference_assist'] = {**reference_provenance, 'status': aggregate_status, 'reason': aggregate_reason, 'part_status_counts': dict(sorted(part_status_counts.items())), 'confirmed_count': len(turn_reference_confirmed), 'confirmed_transitions': sorted((round(float(value), 3) for value in turn_reference_confirmed)), 'applied_splits': int(composition.get('reference_assisted_splits', 0) or 0), 'raw_scores_exported': False}
    log_func('\n' + '=' * 50)
    log_func('6단계: SRT 파일 저장')
    log_func('=' * 50)
    if srt_output_path is None:
        srt_output = determine_srt_output(original_input)
    else:
        requested_output = os.path.abspath(srt_output_path)
        if os.path.splitext(requested_output)[1].lower() != '.srt' or os.path.dirname(requested_output) != os.path.dirname(os.path.abspath(original_input)):
            raise ValueError('Requested SRT output must be a sibling .srt file')
        srt_output = requested_output
    reference_metadata_required = bool(speaker_turn_split_enabled and reference_provenance is not None)
    metadata_output = os.path.splitext(srt_output)[0] + '.speaker_turns.json'
    reference_metadata_failed = False
    if reference_metadata_required:
        try:
            from pipeline.subtitle_publication import publish_reference_pair
            publish_reference_pair(srt_output, lambda stage: write_srt(all_entries, stage), compact_speaker_turn_metadata(turn_detection))
        except Exception as exc:
            reference_metadata_failed = True
            fallback_output = _publish_pre_turn_fallback(pre_turn_entries, original_input, srt_output)
            all_entries = pre_turn_entries
            srt_output = fallback_output
            log_func(f'[화자 전환 결과 게시 실패] {exc}; 보정 전 자막으로 안전 복구: {srt_output}')
        else:
            log_func(f'화자 전환 메타데이터 저장: {metadata_output}')
    else:
        try:
            _write_new_srt_atomically(all_entries, srt_output)
        except FileExistsError:
            srt_output = _publish_pre_turn_fallback(all_entries, original_input, srt_output)
    log_func(f'저장 완료: {srt_output} (총 {len(all_entries)}개 자막)')
    if speaker_turn_split_enabled and (not reference_metadata_failed) and turn_words and source_duration_sec:
        try:
            from pipeline.subtitle_enhancement.cache import write_timing_evidence
            locked = [d['at'] for d in turn_detection.get('composition', {}).get('decisions', []) if d.get('applied')]
            write_timing_evidence(srt_output, words=turn_words, episodes=subtitle_episodes, locked_boundaries=locked, duration=source_duration_sec, source_path=original_input, anonymous_turn_routes=nemotron_turn_package if nemotron_turns_enabled else None)
        except Exception as exc:
            log_func(f'[자막 보조 근거 저장 미적용] {type(exc).__name__}')
    if speaker_turn_split_enabled and (not reference_metadata_required):
        try:
            from pipeline.speaker_turns import write_metadata
            metadata_output = os.path.splitext(srt_output)[0] + '.speaker_turns.json'
            write_metadata(metadata_output, compact_speaker_turn_metadata(turn_detection))
            log_func(f'화자 전환 메타데이터 저장: {metadata_output}')
        except Exception as exc:
            log_func(f'[화자 전환 메타데이터 저장 실패] {exc}')
    if cleanup:
        log_func('\n' + '=' * 50)
        log_func('7단계: 임시 파일 정리')
        log_func('=' * 50)
        deleted, failed = (0, 0)
        for fp in temp_files:
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    log_func(f'삭제: {os.path.basename(fp)}')
                    deleted += 1
                except Exception as e:
                    log_func(f'삭제 실패: {os.path.basename(fp)} ({e})')
                    failed += 1
        log_func(f'정리 완료: {deleted}개 삭제' + (f', {failed}개 실패' if failed else ''))
    return srt_output
