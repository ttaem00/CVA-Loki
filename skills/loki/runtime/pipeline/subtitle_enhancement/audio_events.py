"""Optional, cached-only AST observations; never a speech/ASR exclusion gate.

The E1 thresholds are the manager-selected listening policy after the E1/E2
cross-check. They intentionally favor useful recall for laughter and music;
window centers remain coarse observations, not accurate sound onsets or
speaker identities.
"""

from __future__ import annotations

import math

MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"

MODEL_REVISION = "f826b80d28226b62986cc218e5cec390b1096902"

MODEL_SHA256 = "ae0c1e2ad4e1381d851fa9bf298ba13ebc9c5a914cdee2dbe427a6583869924d"

POLICY_ID = "E1-manager-selected-v1"

POLICY = {"laughter_threshold": .04, "music_onset": .5, "music_release": .35}

def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None

def _clean_rows(rows, category, duration):
    """Ignore invalid observations and coalesce duplicate chunk-seam windows."""
    unique = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        start, end, center, score = (_number(raw.get(k)) for k in
                                     ("start", "end", "center", category))
        if None in (start, end, center, score):
            continue
        if not (0 <= start < end <= duration + 1e-6 and start <= center <= end and 0 <= score <= 1):
            continue
        row = dict(raw, start=start, end=min(end, duration), center=center)
        row[category] = score
        row["speech"] = min(1., max(0., _number(raw.get("speech")) or 0.))
        key = (round(start, 6), round(end, 6))
        if key not in unique or score > unique[key][category]:
            unique[key] = row
    return sorted(unique.values(), key=lambda r: (r["center"], r["start"]))

def _adjacent(a, b, hop):
    return 0 < b["center"] - a["center"] <= hop + 1e-5

def scores_to_events(short_rows, long_rows, *, duration, music_enabled=True,
                     laughter_enabled=True):
    """Convert grouped AST rows (including old experiment records) to events.

    Rows must use one clip-relative clock. Repeated windows are deduplicated;
    missing windows break persistence, so unrelated chunks cannot form a run.
    """
    duration = _number(duration)
    if duration is None or duration <= 0:
        return []
    events = []
    if laughter_enabled:
        rows = _clean_rows(short_rows, "laughter", duration)
        candidates = []
        for i, row in enumerate(rows):
            left = rows[i - 1] if i and _adjacent(rows[i - 1], row, 1) else None
            right = rows[i + 1] if i + 1 < len(rows) and _adjacent(row, rows[i + 1], 1) else None
            if (row["laughter"] >= POLICY["laughter_threshold"]
                    and (left is None or row["laughter"] >= left["laughter"])
                    and (right is None or row["laughter"] > right["laughter"])):
                candidates.append(row)
        chosen = []
        for row in sorted(candidates, key=lambda r: (-r["laughter"], r["center"])):
            if all(abs(row["center"] - old["center"]) >= 2 for old in chosen):
                chosen.append(row)
        for row in chosen:
            events.append({"at": row["center"], "category": "laughter", "label": "[웃음]",
                           "score": row["laughter"], "window_start": row["start"],
                           "window_end": row["end"], "role": "audio_event_observation",
                           "timing_precision": "3_second_window_center"})
    if music_enabled:
        rows = _clean_rows(long_rows, "music", duration)
        active = []
        pending = None
        low = []

        def finish(continues):
            if len(active) < 2 or active[-1]["center"] - active[0]["center"] < 2 - 1e-5:
                return
            first, last = active[0], active[-1]
            events.append({"at": first["center"], "end": last["center"],
                           "category": "music", "label": "[음악]",
                           "score": max(r["music"] for r in active),
                           "speech_score": max(r["speech"] for r in active),
                           "window_start": first["start"], "window_end": last["end"],
                           "ongoing_at_clip_start": first["start"] <= 1e-6,
                           "continues_at_analysis_end": continues,
                           "role": "audio_event_observation",
                           "timing_precision": "overlapping_10_second_windows",
                           "onset_proven": False, "speech_exclusion": False})

        for i, row in enumerate(rows):
            if i and not _adjacent(rows[i - 1], row, 2):
                finish(False)
                active, pending, low = [], None, []
            if not active:
                if row["music"] >= POLICY["music_onset"]:
                    if pending is not None and _adjacent(pending, row, 2):
                        active = [pending, row]
                        pending = None
                    else:
                        pending = row
                else:
                    pending = None
                continue
            if row["music"] < POLICY["music_release"]:
                low.append(row)
                if len(low) == 2:
                    finish(False)
                    active, pending, low = [], None, []
            else:
                active.extend(low)
                active.append(row)
                low = []
        finish(bool(active))
    return sorted(events, key=lambda e: (e["at"], e["category"]))

def observation_grid(duration, window, hop):
    """Match the existing 16 kHz producer, including its off-grid tail window."""
    values = [_number(v) for v in (duration, window, hop)]
    if any(v is None or v <= 0 for v in values):
        return None
    samples = [_number(v * 16000) for v in values]
    if any(v is None or v < 1 for v in samples):
        return None


    frames = round(samples[0])
    width, stride = map(int, samples[1:])
    tail = max(0, frames - width)
    count = 1 + tail // stride + bool(tail % stride)
    return frames, width, stride, count

def observation_window_on_grid(row, grid):
    if grid is None:
        return False
    frames, width, stride, _ = grid
    start, end = _number(row.get('start')), _number(row.get('end'))
    if start is None or end is None:
        return False
    sample = round(start * 16000)
    tail = max(0, frames - width)
    return (0 <= sample <= tail and (sample % stride == 0 or sample == tail)
            and abs(start - sample / 16000) <= 1e-6
            and abs(end - min(sample + width, frames) / 16000) <= 1e-6)

def observation_coverage(analyses, *, duration=None):
    """Only completed observation windows; a missing window is unknown audio."""
    result = []
    for analysis in analyses:
        intervals = []
        for row in sorted(analysis.get("rows", []), key=lambda r: r["start"]):
            a, b = row["start"], row["end"]
            if intervals and a <= intervals[-1][1]:
                intervals[-1][1] = max(b, intervals[-1][1])
            else:
                intervals.append([a, b])
        expected = analysis.get("expected_window_count")
        rows = analysis.get("rows", [])
        grid = observation_grid(duration, analysis['window'], analysis['hop'])
        complete = (grid is not None and expected == grid[3] and len(rows) == expected
                    and len({row['start'] for row in rows}) == expected
                    and all(observation_window_on_grid(row, grid) for row in rows))
        result.append(dict(window_sec=analysis["window"], hop_sec=analysis["hop"],
            processed_intervals_sec=intervals, processed_window_count=len(analysis.get("rows", [])),
            expected_window_count=expected,
            complete=complete))
    return result

def singing_candidates(rows, *, duration):
    """Threshold-free local score maxima for rereading, never a singing label."""
    rows = _clean_rows(rows, "singing", duration)
    result = []
    for i, row in enumerate(rows):
        left = rows[i-1] if i and _adjacent(rows[i-1], row, 2) else None
        right = rows[i+1] if i+1 < len(rows) and _adjacent(row, rows[i+1], 2) else None
        if (row["singing"] > 0 and (left is None or row["singing"] >= left["singing"])
                and (right is None or row["singing"] > right["singing"])):
            result.append(dict(at=row["center"], window_start=row["start"], window_end=row["end"],
                category="singing_candidate", score=row["singing"], music_score=row.get("music"),
                speech_score=row.get("speech"), role="audio_event_observation",
                policy_id="singing-local-maxima-shadow-v1", threshold=None,
                singing_confirmed=False, scores_calibrated=False,
                timing_precision="overlapping_10_second_windows"))
    return result
