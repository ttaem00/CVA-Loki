"""Versioned identity and transport contracts for original-source evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import hashlib

import json

import os

from pathlib import Path

import unicodedata

AVAILABILITY = frozenset({
    "present", "partial", "not_collected", "missing", "collection_failed",
    "absent_confirmed", "excluded_by_policy", "unsupported", "conflict",
})

RECORD_SCHEMA = "ew.record.v1"

INDEX_SCHEMA = "ew.index.v1"

PARSER_REVISION = "ew-native-v1"

FAMILIES = frozenset({"stt", "chat", "comments", "chat_aggregate", "frame",
                      "ocr", "caption", "audio", "audio_features", "clip", "live_metadata"})

UNIT_POLICY = {
    "schema": "ew.unit.v1", "max_cues": 12, "max_chars": 1200,
    "max_duration_sec": 45, "max_gap_sec": 5, "context_cues": 2,
    "fragment_overlap": 128, "unicode": unicodedata.unidata_version,
    "normalizer": "nfkc-casefold-whitespace-v1", "grams": [2, 3],
    "tokenizer": "unicode61", "gram_encoding": "utf8-hex-v1",
}

def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def file_hash(path):
    with native_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def native_path(path):
    """Keep full identity paths without relying on host-global long-path policy."""
    value = str(Path(path).absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)

class EvidenceError(ValueError):
    """A source/protocol error, never a no-editorial-value verdict."""

@dataclass(frozen=True)
class SourcePolicy:
    allowed_families: tuple[str, ...] = ("stt", "chat")
    required_families: tuple[str, ...] = ("stt",)
    revision: str = "ew.policy.v1"

    def __post_init__(self):
        if not self.allowed_families or not set(self.allowed_families) <= FAMILIES:
            raise EvidenceError("unsupported_family")
        if self.revision != "ew.policy.v1":
            raise EvidenceError("unsupported_policy_revision")
        if not set(self.required_families) <= set(self.allowed_families):
            raise EvidenceError("required_family_excluded")
        if len(set(self.allowed_families)) != len(self.allowed_families):
            raise EvidenceError("duplicate_policy_family")

    def payload(self):
        return dict(revision=self.revision, allowed=sorted(self.allowed_families),
                    required=sorted(self.required_families))

@dataclass(frozen=True)
class AssetBinding:
    asset_id: str
    source_id: str
    video_no: str
    family: str
    asset_key: str
    source_locator: dict
    bytes_sha256: str | None
    parser_revision: str
    availability: str
    collection_status: dict
    clock_authority: dict
    policy_scope: str

    def __post_init__(self):
        if self.availability not in AVAILABILITY:
            raise EvidenceError("unknown_availability")

    def internal(self):
        return asdict(self)

    def public(self):
        return {key: value for key, value in asdict(self).items()
                if key != "source_locator"}

def time_projection(start, end, *, unit, duration_sec, instant=False):
    """Index only. Never swap, clamp, or repair an original source clock."""
    try:
        if unit not in {"ms", "sec"}:
            raise ValueError
        scale = Decimal(1000) if unit == "ms" else Decimal(1000000)
        begin, finish = Decimal(str(start)) * scale, Decimal(str(end)) * scale
        duration = Decimal(str(duration_sec)) * Decimal(1000000)
        if not all(value.is_finite() for value in (begin, finish, duration)):
            raise ValueError
        if duration <= 0 or begin < 0 or finish < begin or finish > duration:
            raise ValueError
        if (instant and (begin != finish or begin >= duration)) or (not instant and finish == begin):
            raise ValueError
        return dict(start_us=int(begin.to_integral_value(rounding=ROUND_FLOOR)),
                    end_us=int(finish.to_integral_value(rounding=ROUND_CEILING)),
                    kind="instant" if instant else "interval"), "verified_against_declared_source_clock"
    except (ValueError, TypeError, ArithmeticError):
        return None, "conflict"

def overlaps(time_index, start_us, end_us):
    if time_index is None:
        return False
    if time_index["kind"] == "instant":
        return start_us <= time_index["start_us"] < end_us
    return time_index["start_us"] < end_us and time_index["end_us"] > start_us
