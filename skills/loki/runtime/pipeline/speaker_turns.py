"""Existing anonymous F2 speaker-turn algorithms using pinned Sherpa assets; optional Sortformer remains unavailable."""

from __future__ import annotations

import bisect

import difflib

import heapq

import json

import math

import os

import re

import subprocess

import sys

import time

from concurrent.futures import ThreadPoolExecutor

from pathlib import Path

from typing import Iterable, Mapping, Sequence

from pipeline.subtitle_enhancement.profile import REFERENCE_TURN_ASSIST_POLICY_VERSION, REFERENCE_TURN_ASSIST_SCHEMA

FRAME_SEC = 0.08

MIN_RUN_SEC = 0.8

MIN_SIDE_SEC = 0.8

MAX_GAP_SEC = 2.0

MIN_TRANSITION_SEPARATION_SEC = 1.6

MIN_OVERLAP_SEC = 0.24

MAX_OVERLAP_DELAY_SEC = 1.0

EXISTING_BOUNDARY_GUARD_SEC = 0.8

MAX_WORD_SNAP_SEC = 0.7

MIN_WORD_PAUSE_SEC = 0.12

STRICT_MIN_WORD_PAUSE_SEC = 0.5

MIN_APPLIED_SEPARATION_SEC = 1.2

FUSION_MATCH_SEC = 0.7

REFERENCE_WINDOW_SEC = 1.5

REFERENCE_MIN_WINDOW_SEC = 1.0

REFERENCE_MAX_EDGE_DISTANCE_SEC = 1.2

REFERENCE_MAX_CROSSTALK_SEC = FRAME_SEC

REFERENCE_MAX_CANDIDATES_PER_PART = 48

_TOKEN = re.compile('\\S+')

_NORMALIZE = re.compile('[^0-9A-Za-z가-힣]+')

_TERMINAL = re.compile('[.!?。！？]+$')

_DEVICE = re.compile('^(?:auto|cpu|cuda(?::\\d+)?|vulkan(?::\\d+)?|metal)$')

class SpeakerTurnUnavailable(RuntimeError):
    """The optional turn detector cannot run safely in this environment."""

def _stop_process(process) -> None:
    """Bounded termination for an optional detector child process."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)

def _run_cancellable_process(command, *, cwd=None, env=None, timeout_sec: int, cancel_event=None):
    """Run a detector without letting optional work block cancel/fail-open."""
    if cancel_event is not None and cancel_event.is_set():
        raise SpeakerTurnUnavailable('화자 전환 감지가 취소됨')
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', shell=False)
    began = time.monotonic()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _stop_process(process)
                raise SpeakerTurnUnavailable('화자 전환 감지가 취소됨')
            remaining = max(0.0, float(timeout_sec) - (time.monotonic() - began))
            if remaining <= 0:
                _stop_process(process)
                raise SpeakerTurnUnavailable('화자 전환 감지 제한 시간을 초과함')
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            _stop_process(process)

def resolve_device(device: str | None=None) -> str:
    """Prefer an accelerator exposed by the contained runtime, with CPU fallback."""
    value = str(device or os.environ.get('CHZZ_SPEAKER_TURN_DEVICE', 'auto')).strip().lower()
    if not _DEVICE.fullmatch(value):
        raise SpeakerTurnUnavailable(f'지원하지 않는 화자 전환 장치: {value}')
    return value

def resolve_runtime_paths(executable_path=None, model_path=None):
    raise SpeakerTurnUnavailable('Optional Sortformer is not installed in this Loki workspace')

def run_sortformer(audio_path: str | os.PathLike[str], output_path: str | os.PathLike[str], *, executable_path: str | os.PathLike[str] | None=None, model_path: str | os.PathLike[str] | None=None, device: str | None=None, timeout_sec: int=900, cancel_event=None) -> dict:
    """Run the contained NeMo-Speech.cpp CLI and validate its JSON contract."""
    executable, model = resolve_runtime_paths(executable_path, model_path)
    selected_device = resolve_device(device)
    audio = Path(audio_path).resolve()
    output = Path(output_path).resolve()
    if not audio.is_file() or audio.suffix.lower() != '.wav':
        raise SpeakerTurnUnavailable(f'화자 전환 입력은 WAV 파일이어야 함: {audio}')
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(executable), '--json', 'diarize', str(audio), '--model', str(model), '--device', selected_device, '--format', 'json', '--output', str(output), '--force']
    completed = _run_cancellable_process(command, timeout_sec=max(1, int(timeout_sec)), cancel_event=cancel_event)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()[-600:]
        raise SpeakerTurnUnavailable(f'화자 전환 실행 실패 (exit={completed.returncode}): {detail}')
    if not output.is_file():
        raise SpeakerTurnUnavailable('화자 전환 실행이 JSON 결과를 만들지 않음')
    try:
        payload = json.loads(output.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpeakerTurnUnavailable(f'화자 전환 JSON 읽기 실패: {exc}') from exc
    return validate_diarization(payload)

def run_sherpa(audio_path: str | os.PathLike[str], output_path: str | os.PathLike[str], *, segmentation_model_path: str | os.PathLike[str] | None=None, embedding_model_path: str | os.PathLike[str] | None=None, timeout_sec: int=3600, cancel_event=None) -> dict:
    """Run the checksum-pinned sherpa CPU detector outside the Whisper process."""
    if segmentation_model_path is None or embedding_model_path is None:
        from pipeline.speaker_turn_runtime import ensure_sherpa_models
        segmentation, embedding = ensure_sherpa_models()
    else:
        segmentation = Path(segmentation_model_path).expanduser().resolve()
        embedding = Path(embedding_model_path).expanduser().resolve()
    audio = Path(audio_path).resolve()
    output = Path(output_path).resolve()
    if not audio.is_file() or audio.suffix.lower() != '.wav':
        raise SpeakerTurnUnavailable(f'화자 전환 입력은 WAV 파일이어야 함: {audio}')
    if not segmentation.is_file() or not embedding.is_file():
        raise SpeakerTurnUnavailable('sherpa 화자 전환 모델 파일이 없음')
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = _run_cancellable_process([sys.executable, '-m', 'pipeline.sherpa_turn_worker', '--wav', str(audio), '--segmentation-model', str(segmentation), '--embedding-model', str(embedding), '--output', str(output)], cwd=str(Path(__file__).resolve().parent.parent), timeout_sec=max(1, int(timeout_sec)), cancel_event=cancel_event)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()[-600:]
        raise SpeakerTurnUnavailable(f'sherpa 화자 전환 실행 실패 (exit={completed.returncode}): {detail}')
    try:
        payload = json.loads(output.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpeakerTurnUnavailable(f'sherpa 화자 전환 JSON 읽기 실패: {exc}') from exc
    return validate_diarization(payload)

def _merge_intervals(intervals: Sequence[Mapping[str, object]]) -> list[dict]:
    merged: list[dict] = []
    for row in sorted(intervals, key=lambda item: float(item['start'])):
        start = float(row['start'])
        end = float(row['end'])
        if merged and start <= float(merged[-1]['end']) + FRAME_SEC:
            merged[-1]['end'] = round(max(float(merged[-1]['end']), end), 3)
        else:
            merged.append({'start': round(start, 3), 'end': round(end, 3)})
    return merged

def fuse_anonymous_evidence(evidence_by_backend: Mapping[str, Mapping[str, object]], *, match_sec: float=FUSION_MATCH_SEC) -> dict:
    """Fuse detector evidence without exposing or reconciling speaker identities.

    Near-coincident detector transitions are normal candidates. A transition
    found by only one detector is retained as strict and must later pass the
    larger language-agnostic word-pause gate.
    """
    usable = {str(name): payload for name, payload in evidence_by_backend.items() if isinstance(payload, Mapping)}
    if not usable:
        raise SpeakerTurnUnavailable('사용 가능한 화자 전환 감지 결과가 없음')
    names = sorted(usable)
    strict_by_backend = {name: [float(value) for value in payload.get('strict_transitions', [])] for name, payload in usable.items()}

    def _strict_source(name: str, value: float) -> bool:
        return any((abs(value - known) <= FRAME_SEC + 1e-06 for known in strict_by_backend[name]))
    if len(names) == 1:
        name = names[0]
        transitions = sorted((float(value) for value in usable[name].get('transitions', [])))
        return {'transitions': [round(value, 3) for value in transitions], 'strict_transitions': [round(value, 3) for value in transitions], 'overlaps': _merge_intervals(usable[name].get('overlaps', [])), 'decisions': [{'at': round(value, 3), 'accepted': True, 'reason': 'single_detector_strict', 'sources': [name]} for value in transitions], 'backends': names}
    primary_name, secondary_name = names[:2]
    primary = sorted((float(value) for value in usable[primary_name].get('transitions', [])))
    secondary = sorted((float(value) for value in usable[secondary_name].get('transitions', [])))
    available = set(range(len(secondary)))
    transitions: list[float] = []
    strict: list[float] = []
    decisions: list[dict] = []
    for value in primary:
        matches = [index for index in available if abs(secondary[index] - value) <= match_sec]
        if matches:
            selected = min(matches, key=lambda index: abs(secondary[index] - value))
            available.remove(selected)
            fused = round((value + secondary[selected]) / 2, 3)
            transitions.append(fused)
            is_strict = _strict_source(primary_name, value) or _strict_source(secondary_name, secondary[selected])
            if is_strict:
                strict.append(fused)
            decisions.append({'at': fused, 'accepted': True, 'reason': 'detector_consensus_strict' if is_strict else 'detector_consensus', 'sources': [primary_name, secondary_name]})
        else:
            fused = round(value, 3)
            transitions.append(fused)
            strict.append(fused)
            decisions.append({'at': fused, 'accepted': True, 'reason': 'single_detector_strict', 'sources': [primary_name]})
    for index in sorted(available):
        fused = round(secondary[index], 3)
        transitions.append(fused)
        strict.append(fused)
        decisions.append({'at': fused, 'accepted': True, 'reason': 'single_detector_strict', 'sources': [secondary_name]})
    ordered = sorted(zip(transitions, [value in strict for value in transitions]), key=lambda row: row[0])
    deduped: list[float] = []
    deduped_strict: list[float] = []
    for value, is_strict in ordered:
        if deduped and abs(value - deduped[-1]) < MIN_TRANSITION_SEPARATION_SEC:
            if not is_strict and deduped[-1] in deduped_strict:
                deduped_strict.remove(deduped[-1])
                deduped[-1] = value
            continue
        deduped.append(value)
        if is_strict:
            deduped_strict.append(value)
    overlaps = _merge_intervals([row for payload in usable.values() for row in payload.get('overlaps', [])])
    return {'transitions': [round(value, 3) for value in deduped], 'strict_transitions': [round(value, 3) for value in deduped_strict], 'overlaps': overlaps, 'decisions': decisions, 'backends': names}

def _intersection_sec(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))

def _clean_reference_window_pair(payload: Mapping[str, object], transition: float):
    """Return two clean local-clock windows while keeping detector IDs private."""
    segments = validate_diarization(payload)['segments']
    pairs = []
    for left in segments:
        left_end = min(float(left['end']), transition)
        if float(left['start']) >= transition or transition - float(left['end']) > REFERENCE_MAX_EDGE_DISTANCE_SEC:
            continue
        left_start = max(float(left['start']), left_end - REFERENCE_WINDOW_SEC)
        if left_end - left_start < REFERENCE_MIN_WINDOW_SEC:
            continue
        for right in segments:
            if left['speaker'] == right['speaker']:
                continue
            right_start = max(float(right['start']), transition)
            if float(right['end']) <= transition or float(right['start']) - transition > REFERENCE_MAX_EDGE_DISTANCE_SEC:
                continue
            right_end = min(float(right['end']), right_start + REFERENCE_WINDOW_SEC)
            if right_end - right_start < REFERENCE_MIN_WINDOW_SEC:
                continue
            left_crosstalk = sum((_intersection_sec(left_start, left_end, float(row['start']), float(row['end'])) for row in segments if row['speaker'] != left['speaker']))
            right_crosstalk = sum((_intersection_sec(right_start, right_end, float(row['start']), float(row['end'])) for row in segments if row['speaker'] != right['speaker']))
            if left_crosstalk > REFERENCE_MAX_CROSSTALK_SEC + 1e-06 or right_crosstalk > REFERENCE_MAX_CROSSTALK_SEC + 1e-06:
                continue
            pairs.append(((round(left_start, 3), round(left_end, 3)), (round(right_start, 3), round(right_end, 3)), min(left_end - left_start, right_end - right_start), abs(float(left['end']) - transition) + abs(float(right['start']) - transition)))
    if not pairs:
        return None
    left_window, right_window, _duration, _distance = max(pairs, key=lambda row: (row[2], -row[3]))
    return (left_window, right_window)

def confirm_reference_turns(audio_path: str | os.PathLike[str], raw_by_backend: Mapping[str, Mapping[str, object]], fused: Mapping[str, object], profile, *, time_offset: float=0.0, score_fn=None, cancel_event=None) -> tuple[list[float], dict]:
    """Confirm only existing single-detector strict candidates with a target flip.

    This helper never creates/vetoes a detector transition and never exports raw
    scores, detector labels, or a target identity. Failure preserves baseline F2.
    """
    base = {'schema': REFERENCE_TURN_ASSIST_SCHEMA, 'policy': REFERENCE_TURN_ASSIST_POLICY_VERSION, 'policy_version': REFERENCE_TURN_ASSIST_POLICY_VERSION, 'confirmed_count': 0, 'confirmed_transitions': [], 'raw_scores_exported': False}
    if not (profile is not None and getattr(profile, 'reference_enabled', False) is True and (getattr(profile, 'reference_turn_assist_enabled', False) is True)):
        reason = getattr(profile, 'reference_reason', 'profile_unavailable') if profile is not None else 'profile_unavailable'
        return ([], {**base, 'status': 'disabled', 'reason': reason})
    eligible: list[tuple[float, tuple[str, ...]]] = []
    strict = [float(value) for value in fused.get('strict_transitions', [])]
    for value in strict:
        matching = [decision for decision in fused.get('decisions', []) if isinstance(decision, Mapping) and decision.get('accepted') is True and (decision.get('reason') == 'single_detector_strict') and (abs(float(decision.get('at', -1000000000.0)) - value) <= FRAME_SEC + 1e-06)]
        if matching:
            decision = min(matching, key=lambda item: abs(float(item.get('at', -1000000000.0)) - value))
            sources = tuple((source for source in decision.get('sources', []) if isinstance(source, str) and source in raw_by_backend))
            if sources:
                eligible.append((value, sources))
    eligible.sort(key=lambda item: item[0])
    if len(eligible) > REFERENCE_MAX_CANDIDATES_PER_PART:
        indexes = {round(index * (len(eligible) - 1) / (REFERENCE_MAX_CANDIDATES_PER_PART - 1)) for index in range(REFERENCE_MAX_CANDIDATES_PER_PART)}
        eligible = [eligible[index] for index in sorted(indexes)]
    if not eligible:
        return ([], {**base, 'status': 'unchanged', 'reason': 'no_eligible_strict_candidate', 'eligible_candidate_count': 0})
    prepared = []
    for absolute, sources in eligible:
        local = absolute - float(time_offset)
        pair = None
        for backend in sources:
            try:
                candidate = _clean_reference_window_pair(raw_by_backend[backend], local)
            except (SpeakerTurnUnavailable, KeyError, TypeError, ValueError):
                candidate = None
            if candidate is not None:
                pair = candidate
                break
        if pair is not None:
            prepared.append((absolute, pair[0], pair[1]))
    if not prepared:
        return ([], {**base, 'status': 'unchanged', 'reason': 'no_clean_window_pair', 'eligible_candidate_count': len(eligible), 'scored_candidate_count': 0})
    windows = [window for _at, left, right in prepared for window in (left, right)]
    from pipeline.subtitle_enhancement.reference import ReferenceAssistBudgetExhausted
    if score_fn is None:
        from pipeline.subtitle_enhancement.reference import score_reference_windows
        score_fn = score_reference_windows
    try:
        scores = list(score_fn(Path(audio_path), profile, windows=windows, max_windows=len(windows), max_seconds=60, cancel_event=cancel_event))
        if len(scores) != len(windows) or not all((math.isfinite(float(value)) for value in scores)):
            raise ValueError('Invalid reference turn assist score count')
    except ReferenceAssistBudgetExhausted as exc:
        return ([], {**base, 'status': 'partial_bounded', 'reason': exc.reason, 'eligible_candidate_count': len(eligible), 'scored_candidate_count': 0})
    except Exception as exc:
        return ([], {**base, 'status': 'fallback', 'reason': type(exc).__name__, 'eligible_candidate_count': len(eligible), 'scored_candidate_count': 0})
    confirmed = []
    min_cosine = float(getattr(profile, 'reference_min_cosine', 0.45))
    min_margin = float(getattr(profile, 'reference_min_margin', 0.18))
    for index, (absolute, _left, _right) in enumerate(prepared):
        left_score, right_score = map(float, scores[index * 2:index * 2 + 2])
        high, low = (max(left_score, right_score), min(left_score, right_score))
        if high + 1e-09 >= min_cosine and low + 1e-09 < min_cosine and (high - low + 1e-09 >= min_margin):
            confirmed.append(round(absolute, 3))
    return (confirmed, {**base, 'status': 'confirmed' if confirmed else 'unchanged', 'reason': 'bounded_target_flip' if confirmed else 'confidence_or_margin_not_met', 'eligible_candidate_count': len(eligible), 'scored_candidate_count': len(prepared), 'confirmed_count': len(confirmed), 'confirmed_transitions': confirmed})

def detect_anonymous_turns(audio_path: str | os.PathLike[str], output_prefix: str | os.PathLike[str], *, time_offset: float=0.0, sortformer_executable_path: str | os.PathLike[str] | None=None, sortformer_model_path: str | os.PathLike[str] | None=None, reference_profile=None, reference_score_fn=None, cancel_event=None) -> dict:
    """Run detectors, project anonymous evidence, and remove raw speaker IDs.

    The detector CLIs require temporary JSON files.  They are an internal
    implementation detail: all identity-bearing payloads are consumed here and
    the exact files are removed before this function returns or raises.
    """
    prefix = Path(output_prefix)
    raw_paths = (Path(str(prefix) + '.sherpa.raw.json'), Path(str(prefix) + '.sortformer.raw.json'))
    jobs = {}
    errors = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs['sherpa_cpu'] = pool.submit(run_sherpa, audio_path, raw_paths[0], cancel_event=cancel_event)
            try:
                resolve_runtime_paths(sortformer_executable_path, sortformer_model_path)
            except SpeakerTurnUnavailable as exc:
                errors['sortformer'] = str(exc)
            else:
                jobs['sortformer'] = pool.submit(run_sortformer, audio_path, raw_paths[1], executable_path=sortformer_executable_path, model_path=sortformer_model_path, cancel_event=cancel_event)
            evidence = {}
            raw_by_backend = {}
            for name, future in jobs.items():
                try:
                    raw_by_backend[name] = future.result()
                    evidence[name] = derive_anonymous_evidence(raw_by_backend[name], time_offset=time_offset)
                except Exception as exc:
                    errors[name] = str(exc)
        fused = fuse_anonymous_evidence(evidence)
        confirmed, reference_assist = confirm_reference_turns(audio_path, raw_by_backend, fused, reference_profile, time_offset=time_offset, score_fn=reference_score_fn, cancel_event=cancel_event)
        from pipeline.subtitle_enhancement.turn_display import anonymous_episodes
        local_episodes = anonymous_episodes(raw_by_backend.get('sherpa_cpu', {}))
        fused['anonymous_episodes'] = [{'start': round(float(row['start']) + float(time_offset), 3), 'end': round(float(row['end']) + float(time_offset), 3), 'overlap_sec': round(float(row['overlap_sec']), 3)} for row in local_episodes]
        fused['reference_confirmed_transitions'] = confirmed
        fused['reference_assist'] = reference_assist
        fused['backend_evidence'] = evidence
        fused['errors'] = errors
        fused['backend_counts'] = {name: len(payload.get('transitions', [])) for name, payload in evidence.items()}
        return fused
    finally:
        for raw_path in raw_paths:
            try:
                raw_path.unlink(missing_ok=True)
            except OSError:
                try:
                    os.remove(raw_path)
                except FileNotFoundError:
                    pass
                if raw_path.exists():
                    raise SpeakerTurnUnavailable('임시 화자 ID 결과를 제거하지 못해 익명 전환 결과를 폐기함')

def validate_diarization(payload: object) -> dict:
    if not isinstance(payload, Mapping) or not isinstance(payload.get('segments'), list):
        raise SpeakerTurnUnavailable('화자 전환 JSON에 segments 배열이 없음')
    segments = []
    for index, raw in enumerate(payload['segments']):
        if not isinstance(raw, Mapping):
            raise SpeakerTurnUnavailable(f'segments[{index}]가 객체가 아님')
        try:
            start = float(raw['start'])
            end = float(raw['end'])
            speaker = int(raw['speaker'])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeakerTurnUnavailable(f'segments[{index}] 필드가 잘못됨') from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or (end <= start):
            raise SpeakerTurnUnavailable(f'segments[{index}] 시간이 잘못됨')
        segments.append({'start': start, 'end': end, 'speaker': speaker})
    return {'segments': segments}

def _rle(labels: Sequence[int | None]) -> list[dict]:
    if not labels:
        return []
    runs = []
    start = 0
    for index in range(1, len(labels) + 1):
        if index == len(labels) or labels[index] != labels[start]:
            runs.append({'start_index': start, 'end_index': index, 'start': start * FRAME_SEC, 'end': index * FRAME_SEC, 'duration': (index - start) * FRAME_SEC, 'speaker': labels[start]})
            start = index
    return runs

def _frame_state(segments: Sequence[Mapping[str, object]]) -> tuple[list[int | None], list[bool]]:
    end_sec = max((float(row['end']) for row in segments), default=0.0)
    frame_count = int(math.ceil(end_sec / FRAME_SEC))
    active: list[set[int]] = [set() for _ in range(frame_count)]
    remaining: list[dict[int, float]] = [{} for _ in range(frame_count)]
    for segment in segments:
        start_frame = max(0, int(math.floor(float(segment['start']) / FRAME_SEC)))
        end_frame = min(frame_count, int(math.ceil(float(segment['end']) / FRAME_SEC)))
        speaker = int(segment['speaker'])
        for frame in range(start_frame, end_frame):
            active[frame].add(speaker)
            remaining[frame][speaker] = max(remaining[frame].get(speaker, 0.0), float(segment['end']) - frame * FRAME_SEC)
    labels: list[int | None] = []
    previous = None
    for frame, speakers in enumerate(active):
        if not speakers:
            label = None
        elif len(speakers) == 1:
            label = next(iter(speakers))
        elif previous in speakers:
            label = previous
        else:
            label = max(speakers, key=lambda value: remaining[frame].get(value, 0.0))
        labels.append(label)
        if label is not None:
            previous = label
    return (labels, [len(speakers) >= 2 for speakers in active])

def _smooth_labels(labels: Sequence[int | None]) -> list[int | None]:
    output = list(labels)
    runs = _rle(output)
    for index, run in enumerate(runs):
        if run['speaker'] is not None or run['duration'] > 0.48:
            continue
        if index == 0 or index + 1 >= len(runs):
            continue
        before = runs[index - 1]['speaker']
        after = runs[index + 1]['speaker']
        if before is not None and before == after:
            output[run['start_index']:run['end_index']] = [before] * (run['end_index'] - run['start_index'])
    for _ in range(3):
        changed = False
        runs = _rle(output)
        for index, run in enumerate(runs):
            if run['speaker'] is None or run['duration'] >= MIN_RUN_SEC:
                continue
            before = runs[index - 1]['speaker'] if index else None
            after = runs[index + 1]['speaker'] if index + 1 < len(runs) else None
            replacement = before if before is not None and before == after else None
            output[run['start_index']:run['end_index']] = [replacement] * (run['end_index'] - run['start_index'])
            changed = True
        if not changed:
            break
    return output

def _intervals_from_flags(flags: Sequence[bool]) -> list[dict]:
    intervals = []
    start = None
    for index in range(len(flags) + 1):
        active = index < len(flags) and flags[index]
        if active and start is None:
            start = index
        elif not active and start is not None:
            duration = (index - start) * FRAME_SEC
            if duration >= MIN_OVERLAP_SEC:
                intervals.append({'start': round(start * FRAME_SEC, 3), 'end': round(index * FRAME_SEC, 3)})
            start = None
    return intervals

def _short_overlap_tail_transitions(segments: Sequence[Mapping[str, object]], primary_transitions: Sequence[float]) -> list[float]:
    """Recover backend-frame edge cases without weakening ordinary turns.

    CUDA and CPU can differ by one 80 ms frame near an overlap edge.  A speaker
    that continues for 0.4-0.8 seconds after the other speaker stops is retained
    only as a strict candidate.  It still needs a much larger word pause before
    it can change an SRT cue.
    """
    candidates = []
    for ending in segments:
        for continuing in segments:
            if ending is continuing or ending['speaker'] == continuing['speaker']:
                continue
            overlap = min(float(ending['end']), float(continuing['end'])) - max(float(ending['start']), float(continuing['start']))
            tail = float(continuing['end']) - float(ending['end'])
            if overlap < MIN_OVERLAP_SEC or not 0.4 <= tail < MIN_SIDE_SEC:
                continue
            if min(float(ending['end']) - float(ending['start']), float(continuing['end']) - float(continuing['start'])) < MIN_SIDE_SEC:
                continue
            value = round(float(ending['end']) / FRAME_SEC) * FRAME_SEC
            if any((abs(value - known) < MIN_TRANSITION_SEPARATION_SEC for known in primary_transitions)):
                continue
            if not candidates or all((abs(value - known) >= MIN_TRANSITION_SEPARATION_SEC for known in candidates)):
                candidates.append(value)
    return candidates

def derive_anonymous_evidence(payload: Mapping[str, object], *, time_offset: float=0.0) -> dict:
    """Convert raw speaker segments to anonymous transitions and overlap intervals."""
    validated = validate_diarization(payload)
    labels, overlap_flags = _frame_state(validated['segments'])
    speech_runs = [row for row in _rle(_smooth_labels(labels)) if row['speaker'] is not None]
    raw_transitions = []
    for previous, current in zip(speech_runs, speech_runs[1:]):
        if previous['speaker'] == current['speaker']:
            continue
        if current['start'] - previous['end'] > MAX_GAP_SEC:
            continue
        if previous['duration'] < MIN_SIDE_SEC or current['duration'] < MIN_SIDE_SEC:
            continue
        raw_transitions.append(float(current['start']))
    clusters: list[list[float]] = []
    for value in raw_transitions:
        if not clusters or value - clusters[-1][-1] >= MIN_TRANSITION_SEPARATION_SEC:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    transitions = [sum(cluster) / len(cluster) for cluster in clusters]
    strict_transitions = _short_overlap_tail_transitions(validated['segments'], transitions)
    overlaps = _intervals_from_flags(overlap_flags)
    safe = []
    decisions = []
    for transition in transitions:
        adjusted = transition
        reason = 'direct'
        overlap = next((row for row in overlaps if row['start'] <= transition < row['end']), None)
        if overlap is not None:
            delay = float(overlap['end']) - transition
            if delay > MAX_OVERLAP_DELAY_SEC:
                decisions.append({'at': round(transition + time_offset, 3), 'accepted': False, 'reason': 'overlap_veto'})
                continue
            adjusted = float(overlap['end'])
            reason = 'delayed_after_overlap'
        absolute = round(adjusted + time_offset, 3)
        safe.append(absolute)
        decisions.append({'at': absolute, 'accepted': True, 'reason': reason})
    for transition in strict_transitions:
        absolute = round(transition + time_offset, 3)
        safe.append(absolute)
        decisions.append({'at': absolute, 'accepted': True, 'reason': 'short_overlap_tail_strict'})
    safe.sort()
    return {'transitions': safe, 'strict_transitions': [round(value + time_offset, 3) for value in strict_transitions], 'overlaps': [{'start': round(row['start'] + time_offset, 3), 'end': round(row['end'] + time_offset, 3)} for row in overlaps], 'decisions': decisions}

def _normalize(text: object) -> str:
    return _NORMALIZE.sub('', str(text)).lower()

def _cue_word_options_from_nearby(cue: Mapping[str, object], nearby: Sequence[Mapping[str, object]]) -> list[dict]:
    cue_tokens = _TOKEN.findall(str(cue['text']))
    matcher = difflib.SequenceMatcher(a=[_normalize(token) for token in cue_tokens], b=[_normalize(word.get('text', '')) for word in nearby], autojunk=False)
    mapping: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            if _normalize(cue_tokens[block.a + offset]):
                mapping[block.a + offset] = block.b + offset
    options = []
    for split_index in range(1, len(cue_tokens)):
        left_pair = max(((index, mapping[index]) for index in mapping if index < split_index), default=None, key=lambda item: item[0])
        right_pair = min(((index, mapping[index]) for index in mapping if index >= split_index), default=None, key=lambda item: item[0])
        boundary = None
        pause = 0.0
        evidence = ''
        if left_pair and right_pair and (left_pair[0] == split_index - 1) and (right_pair[0] == split_index) and (right_pair[1] == left_pair[1] + 1):
            left_word = nearby[left_pair[1]]
            right_word = nearby[right_pair[1]]
            pause = max(0.0, float(right_word['start']) - float(left_word['end']))
            boundary = (float(left_word['end']) + float(right_word['start'])) / 2
            evidence = 'two_sided_exact'
        elif right_pair and right_pair[0] == split_index and _TERMINAL.search(cue_tokens[split_index - 1]):
            boundary = float(nearby[right_pair[1]]['start'])
            if right_pair[1] > 0:
                pause = max(0.0, boundary - float(nearby[right_pair[1] - 1]['end']))
            evidence = 'terminal_plus_right_exact'
        if boundary is None or pause + 1e-06 < MIN_WORD_PAUSE_SEC:
            continue
        if not float(cue['start']) + MIN_SIDE_SEC <= boundary <= float(cue['end']) - MIN_SIDE_SEC:
            continue
        options.append({'split_index': split_index, 'at': round(boundary, 3), 'pause_sec': round(pause, 3), 'alignment_evidence': evidence})
    return options

def _cue_word_options(cue: Mapping[str, object], words: Sequence[Mapping[str, object]]) -> list[dict]:
    """Legacy full-filter word lookup kept as the parity oracle and fallback."""
    nearby = [word for word in words if float(word['end']) > float(cue['start']) - MAX_WORD_SNAP_SEC and float(word['start']) < float(cue['end']) + MAX_WORD_SNAP_SEC]
    return _cue_word_options_from_nearby(cue, nearby)

def _legacy_cue_index(baseline: Sequence[Mapping[str, object]], transition: float) -> int | None:
    return next((index for index, cue in enumerate(baseline) if float(cue['start']) < transition < float(cue['end'])), None)

def _cue_indexes_for_transitions(baseline: Sequence[Mapping[str, object]], transitions: Sequence[float]) -> list[int | None]:
    """Map sorted transitions to the first matching cue in original order.

    A start-time bisect is not sufficient because subtitle inputs can contain
    overlapping cues. The sweep activates cues by start time but keeps their
    original indexes in a min-heap, exactly preserving ``next(enumerate(...))``.
    """
    if not all((math.isfinite(value) for value in transitions)) or not all((math.isfinite(float(cue['start'])) and math.isfinite(float(cue['end'])) for cue in baseline)):
        return [_legacy_cue_index(baseline, value) for value in transitions]
    by_start = sorted(((float(cue['start']), index) for index, cue in enumerate(baseline)), key=lambda item: (item[0], item[1]))
    active_original_indexes: list[int] = []
    start_cursor = 0
    located: list[int | None] = []
    for transition in transitions:
        while start_cursor < len(by_start) and by_start[start_cursor][0] < transition:
            heapq.heappush(active_original_indexes, by_start[start_cursor][1])
            start_cursor += 1
        while active_original_indexes:
            cue_index = active_original_indexes[0]
            cue = baseline[cue_index]
            if float(cue['start']) < transition < float(cue['end']):
                break
            heapq.heappop(active_original_indexes)
        located.append(active_original_indexes[0] if active_original_indexes else None)
    return located

class _CueWordOptionsIndex:
    """Cache options per cue and slice monotone word timing with dual bisect."""

    def __init__(self, baseline: Sequence[Mapping[str, object]], words: Sequence[Mapping[str, object]], *, enabled: bool) -> None:
        self._baseline = baseline
        self._words = words
        self._cache_enabled = enabled
        self._cache: dict[int, list[dict]] = {}
        self._starts: list[float] | None = None
        self._ends: list[float] | None = None
        if not enabled:
            return
        try:
            starts = [float(word['start']) for word in words]
            ends = [float(word['end']) for word in words]
        except (KeyError, TypeError, ValueError):
            return
        monotone = all((math.isfinite(value) for value in starts)) and all((math.isfinite(value) for value in ends)) and all((left <= right for left, right in zip(starts, starts[1:]))) and all((left <= right for left, right in zip(ends, ends[1:])))
        if monotone:
            self._starts = starts
            self._ends = ends

    @property
    def indexed(self) -> bool:
        return self._starts is not None and self._ends is not None

    def options(self, cue_index: int) -> list[dict]:
        if self._cache_enabled:
            cached = self._cache.get(cue_index)
            if cached is not None:
                return cached
        cue = self._baseline[cue_index]
        if not self.indexed:
            result = _cue_word_options(cue, self._words)
        else:
            assert self._starts is not None and self._ends is not None
            lower = float(cue['start']) - MAX_WORD_SNAP_SEC
            upper = float(cue['end']) + MAX_WORD_SNAP_SEC
            left = bisect.bisect_right(self._ends, lower)
            right = bisect.bisect_left(self._starts, upper)
            nearby = self._words[left:right] if left < right else ()
            result = _cue_word_options_from_nearby(cue, nearby)
        if self._cache_enabled:
            self._cache[cue_index] = result
        return result

class _IntervalMembershipIndex:
    """Exact ``start <= value < end`` lookup with a full-scan fallback."""

    def __init__(self, rows: Sequence[Mapping[str, object]], *, enabled: bool) -> None:
        self._rows = rows
        self._starts: list[float] | None = None
        self._prefix_max_ends: list[float] | None = None
        if not enabled:
            return
        try:
            intervals = [(float(row['start']), float(row['end'])) for row in rows]
        except (KeyError, TypeError, ValueError):
            return
        if not all((math.isfinite(start) and math.isfinite(end) for start, end in intervals)):
            return
        intervals.sort(key=lambda item: item[0])
        self._starts = [start for start, _ in intervals]
        prefix_max_ends = []
        maximum = -math.inf
        for _, end in intervals:
            maximum = max(maximum, end)
            prefix_max_ends.append(maximum)
        self._prefix_max_ends = prefix_max_ends

    def contains(self, value: float) -> bool:
        if self._starts is None or self._prefix_max_ends is None:
            return any((float(row['start']) <= value < float(row['end']) for row in self._rows))
        index = bisect.bisect_right(self._starts, value) - 1
        return index >= 0 and value < self._prefix_max_ends[index]

class _NearbyTimeIndex:
    """Exact inclusive proximity lookup for finite, static timestamps."""

    def __init__(self, values: Sequence[float], *, enabled: bool) -> None:
        self._raw = values
        self._values = sorted(values) if enabled and all((math.isfinite(value) for value in values)) else None

    def contains(self, value: float, tolerance: float) -> bool:
        if self._values is None or not math.isfinite(value):
            return any((abs(value - known) <= tolerance for known in self._raw))
        index = bisect.bisect_left(self._values, value)
        return any((abs(value - self._values[candidate]) <= tolerance for candidate in range(max(0, index - 1), min(len(self._values), index + 1))))

class _AppliedTimeIndex:
    """Dynamic strict-distance lookup while preserving the old applied list."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._buckets: dict[int, list[float]] = {}

    @staticmethod
    def _bucket(value: float) -> int:
        return math.floor(value / MIN_APPLIED_SEPARATION_SEC)

    def is_near(self, value: float, legacy_values: Sequence[float]) -> bool:
        if not self._enabled or not math.isfinite(value):
            return any((abs(value - known) < MIN_APPLIED_SEPARATION_SEC for known in legacy_values))
        bucket = self._bucket(value)
        return any((abs(value - known) < MIN_APPLIED_SEPARATION_SEC for candidate in (bucket - 1, bucket, bucket + 1) for known in self._buckets.get(candidate, ())))

    def add(self, value: float) -> None:
        if self._enabled and math.isfinite(value):
            self._buckets.setdefault(self._bucket(value), []).append(value)

def _split_entries_at_anonymous_turns_impl(entries: Sequence[Mapping[str, object]], transitions: Iterable[float], words: Sequence[Mapping[str, object]], overlaps: Sequence[Mapping[str, object]]=(), strict_transitions: Iterable[float]=(), reference_confirmed_transitions: Iterable[float]=(), *, indexed: bool) -> tuple[list[dict], dict]:
    """Shared decision path; ``indexed=False`` is the whole-video oracle."""
    baseline = [{'start': float(row['start']), 'end': float(row['end']), 'text': str(row['text'])} for row in entries]
    selected_by_cue: dict[int, list[dict]] = {}
    decisions = []
    applied_times: list[float] = []
    strict_transition_times = [float(value) for value in strict_transitions]
    reference_confirmed_times = [float(value) for value in reference_confirmed_transitions]
    ordered_transitions = sorted((float(value) for value in transitions))
    cue_indexes = _cue_indexes_for_transitions(baseline, ordered_transitions) if indexed else [_legacy_cue_index(baseline, value) for value in ordered_transitions]
    word_options = _CueWordOptionsIndex(baseline, words, enabled=indexed)
    overlap_index = _IntervalMembershipIndex(overlaps, enabled=indexed)
    strict_index = _NearbyTimeIndex(strict_transition_times, enabled=indexed)
    reference_index = _NearbyTimeIndex(reference_confirmed_times, enabled=indexed)
    applied_index = _AppliedTimeIndex(enabled=indexed)
    for transition, cue_index in zip(ordered_transitions, cue_indexes):
        if cue_index is None:
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'outside_cue'})
            continue
        cue = baseline[cue_index]
        if min(transition - cue['start'], cue['end'] - transition) < EXISTING_BOUNDARY_GUARD_SEC:
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'existing_boundary_close'})
            continue
        if overlap_index.contains(transition):
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'overlap_veto'})
            continue
        options = word_options.options(cue_index)
        if not options:
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'no_safe_word_boundary'})
            continue
        option = min(options, key=lambda row: abs(float(row['at']) - transition))
        is_strict = strict_index.contains(transition, FRAME_SEC + 1e-06)
        reference_confirmed = is_strict and reference_index.contains(transition, FRAME_SEC + 1e-06)
        reference_relaxed = bool(reference_confirmed and float(option['pause_sec']) + 1e-06 < STRICT_MIN_WORD_PAUSE_SEC)
        if is_strict and (not reference_confirmed) and (float(option['pause_sec']) + 1e-06 < STRICT_MIN_WORD_PAUSE_SEC):
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'strict_word_pause_too_short'})
            continue
        if abs(float(option['at']) - transition) > MAX_WORD_SNAP_SEC:
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'word_boundary_too_far'})
            continue
        if overlap_index.contains(float(option['at'])):
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'snapped_boundary_in_overlap', 'snapped_at': round(float(option['at']), 3)})
            continue
        if applied_index.is_near(float(option['at']), applied_times):
            decisions.append({'at': round(transition, 3), 'applied': False, 'reason': 'duplicate_nearby'})
            continue
        selected = {**option, 'candidate_at': round(transition, 3), 'strict': is_strict, 'reference_confirmed': reference_confirmed, 'reference_relaxed': reference_relaxed, 'applied': True}
        selected_by_cue.setdefault(cue_index, []).append(selected)
        applied_times.append(float(option['at']))
        applied_index.add(float(option['at']))
        decisions.append(selected)
    output = []
    for cue_index, cue in enumerate(baseline):
        selected = sorted(selected_by_cue.get(cue_index, []), key=lambda row: row['split_index'])
        if not selected:
            output.append(dict(cue))
            continue
        cue_tokens = _TOKEN.findall(cue['text'])
        token_start = 0
        time_start = cue['start']
        for split in selected:
            output.append({'start': time_start, 'end': float(split['at']), 'text': ' '.join(cue_tokens[token_start:split['split_index']])})
            token_start = int(split['split_index'])
            time_start = float(split['at'])
        output.append({'start': time_start, 'end': cue['end'], 'text': ' '.join(cue_tokens[token_start:])})
    text_preserved = ' '.join((row['text'] for row in baseline)).split() == ' '.join((row['text'] for row in output)).split()
    intervals_valid = all((row['end'] > row['start'] and (index == 0 or row['start'] >= output[index - 1]['end'] - 0.001) for index, row in enumerate(output)))
    if not text_preserved or not intervals_valid:
        return (baseline, {'status': 'fallback', 'reason': 'output_invariant_failed', 'applied_splits': 0, 'reference_assisted_splits': 0, 'decisions': decisions})
    return (output, {'status': 'applied' if applied_times else 'unchanged', 'candidate_count': len(decisions), 'applied_splits': len(applied_times), 'reference_assisted_splits': sum((1 for rows in selected_by_cue.values() for row in rows if row.get('reference_relaxed'))), 'text_preserved': True, 'intervals_valid': True, 'speaker_labels_exposed': 0, 'decisions': decisions})

def split_entries_at_anonymous_turns(entries: Sequence[Mapping[str, object]], transitions: Iterable[float], words: Sequence[Mapping[str, object]], overlaps: Sequence[Mapping[str, object]]=(), strict_transitions: Iterable[float]=(), reference_confirmed_transitions: Iterable[float]=()) -> tuple[list[dict], dict]:
    """Split existing cue text only at acoustically aligned, non-overlap boundaries."""
    return _split_entries_at_anonymous_turns_impl(entries, transitions, words, overlaps, strict_transitions, reference_confirmed_transitions, indexed=True)

def write_metadata(path: str | os.PathLike[str], payload: Mapping[str, object]) -> None:
    from pipeline.subtitle_enhancement.cache import write_json
    write_json(path, payload)
