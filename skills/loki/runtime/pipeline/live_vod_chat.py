"""Public VOD snapshots do not consume private live-runtime DB descriptors."""

import json

from .evidence_world.contracts import EvidenceError, native_path

SCHEMA = "chzz.verified_live_vod_chat.v1"

KIND = "verified_live_vod_chat"

def is_bound_chat(path):
    """Recognize private descriptors so they cannot become empty public chat."""
    path = native_path(path)
    if not path.is_file() or path.stat().st_size > 16000:
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get("schema_version") == SCHEMA

def _unsupported():
    raise EvidenceError("loki_private_live_chat_unavailable")

def descriptor_status(path):
    return _unsupported()

def iter_bound_chat(path):
    return _unsupported()

def load_bound_chat(path, *, expected_video_no=None, duration_sec=None):
    return _unsupported()

def read_bound_chat(path, pks):
    return _unsupported()
