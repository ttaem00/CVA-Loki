"""Content-addressed, non-destructive subtitle evidence and derived artifacts."""

from __future__ import annotations

import hashlib

import json

import math

import os

from pathlib import Path

import uuid

SCHEMA = "chzz.subtitle-timing-evidence.v1"

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def atomic_bytes(path, content):
    """Atomic replace only within this artifact's explicitly owned directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".w-" + uuid.uuid4().hex[:12] + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:

        if temp.exists():
            temp.unlink()

def write_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2,
                                  allow_nan=False) + "\n").encode("utf-8"))

def evidence_path(srt):
    return Path(srt).with_suffix(".subtitle_timing.json")

def write_timing_evidence(srt, *, words, episodes, locked_boundaries, duration,
                          source_path, source_offset=0.0, anonymous_turn_routes=None):
    """Persist existing words/anonymous spans, not another ASR/alignment pass."""
    if not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("invalid_source_duration")
    payload = {
        "schema_version": SCHEMA, "baseline_sha256": sha256(srt),
        "source_sha256": sha256(source_path), "duration": float(duration),
        "clock": {"unit": "seconds", "origin": "input_media", "source_offset": source_offset},
        "producer": "whisper_existing_tokens_and_sherpa",
        "words": [{"text": str(w["text"]), "start": float(w["start"]),
                   "end": float(w["end"])} for w in words],
        "episodes": [{k: float(e[k]) for k in ("start", "end", "overlap_sec")} for e in episodes],
        "locked_boundaries": list(locked_boundaries),
    }
    if anonymous_turn_routes is not None:
        encoded = json.dumps(anonymous_turn_routes, ensure_ascii=False, allow_nan=False)
        if any(key in encoded.casefold() for key in ('"speaker"', 'speaker_', '화자 a', '화자 b')):
            raise ValueError("identity-bearing turn evidence is forbidden")
        payload["anonymous_turn_routes"] = anonymous_turn_routes
    write_json(evidence_path(srt), payload)
    return payload
