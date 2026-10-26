"""Public, owner-only terminal observations; stored validation is offline.

A recorded VOD is not itself proof that its broadcast has ended. The owner
observes the exact VOD, then its exact channel's CLOSE state. The observation
time is a conservative upper bound, never a reconstructed broadcast end time.
"""

from __future__ import annotations

import hashlib

import json

import re

SCHEMA = "vod.retention-terminal-observation.v1"

TIME_BASIS = "verified_terminal_observation"

RETENTION_MS = 30 * 24 * 60 * 60 * 1000

_VIDEO = re.compile(r"[1-9][0-9]{0,11}\Z")

_CHANNEL = re.compile(r"[a-f0-9]{32}\Z")

_SHA = re.compile(r"[a-f0-9]{64}\Z")

_KEYS = {"schema", "source_id", "video_no", "channel_id", "video_type",
         "vod_status", "duration_sec", "rewind_present", "channel_status",
         "time_basis", "observed_at_ms", "confirmed", "producer_revision"}

def _fail(reason):
    raise ValueError("vod_retention_" + reason)

def _identity(video_no, channel_id=None):
    if not isinstance(video_no, str) or not _VIDEO.fullmatch(video_no):
        _fail("identity_invalid")
    if channel_id is not None and (not isinstance(channel_id, str) or not _CHANNEL.fullmatch(channel_id)):
        _fail("identity_invalid")

def _channel(channel_id):
    if not isinstance(channel_id, str) or not _CHANNEL.fullmatch(channel_id):
        _fail("identity_invalid")

def _revision(proof):
    body = {key: value for key, value in proof.items() if key != "producer_revision"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()

def validate_terminal_observation(proof, *, video_no, channel_id=None, now_ms, require_age=True):
    """Offline strict validation. Age is measured from observation, not end time."""
    _identity(video_no, channel_id)
    if type(now_ms) is not int or now_ms <= 0 or type(require_age) is not bool:
        _fail("time_invalid")
    if not isinstance(proof, dict) or set(proof) != _KEYS:
        _fail("observation_invalid")
    _identity(proof.get("video_no"), proof.get("channel_id"))
    _channel(proof.get("channel_id"))
    observed = proof.get("observed_at_ms")
    if (proof["schema"] != SCHEMA or proof["source_id"] != "chzzk:video:" + video_no
            or proof["video_no"] != video_no or channel_id is not None and proof["channel_id"] != channel_id
            or proof["video_type"] != "REPLAY" or proof["vod_status"] != "ABR_HLS"
            or proof["channel_status"] != "CLOSE" or proof["time_basis"] != TIME_BASIS
            or proof["confirmed"] is not True or proof["rewind_present"] is not False
            or type(proof["duration_sec"]) is not int or proof["duration_sec"] <= 0
            or type(observed) is not int or not 0 < observed <= now_ms
            or not isinstance(proof["producer_revision"], str) or not _SHA.fullmatch(proof["producer_revision"])
            or proof["producer_revision"] != _revision(proof)):
        _fail("observation_invalid")
    if require_age and now_ms - observed < RETENTION_MS:
        _fail("observation_young")
    return dict(proof)
