"""Anonymous, bounded overlapping-interjection display candidates (F2).

These are timing hypotheses, not speaker separation or identities. Raw speaker
labels are consumed only by anonymous_episodes and never leave that boundary.
"""

from __future__ import annotations

from .timing import _finite

def anonymous_episodes(raw):
    """Project raw diarization to only start/end/union-overlap evidence.

    The fixed .6--2-second, >=.6-second overlap policy matches the F2 experiment.
    Malformed evidence fails closed; overlapping same-speaker fragments do not
    count as another voice, and overlapping other voices are not double counted.
    """
    segments = raw.get("segments") if isinstance(raw, dict) else raw
    if not isinstance(segments, (list, tuple)):
        return []
    for s in segments:
        if (not isinstance(s, dict) or not _finite(s.get("start"))
                or not _finite(s.get("end")) or not 0 <= s["start"] < s["end"]
                or isinstance(s.get("speaker"), bool)
                or not isinstance(s.get("speaker"), (str, int))
                or s["speaker"] == ""):
            return []
    eligible = {i: [] for i, s in enumerate(segments) if .6 <= s["end"] - s["start"] <= 2.0}
    active = []


    for index, seg in sorted(enumerate(segments), key=lambda item: item[1]["start"]):
        active = [(j, other) for j, other in active if other["end"] > seg["start"]]
        for j, other in active:
            if other["speaker"] == seg["speaker"]:
                continue
            interval = (seg["start"], min(seg["end"], other["end"]))
            if index in eligible:
                eligible[index].append(interval)
            if j in eligible:
                eligible[j].append(interval)
        active.append((index, seg))
    output = []
    for index, intersections in eligible.items():
        start, end = segments[index]["start"], segments[index]["end"]
        total, previous = 0.0, float("-inf")
        for a, b in sorted(intersections):
            total += max(0.0, b - max(previous, a))
            previous = max(previous, b)
        if total >= .6:
            output.append({"start": start, "end": end, "overlap_sec": total})
    return sorted(output, key=lambda e: e["start"])
