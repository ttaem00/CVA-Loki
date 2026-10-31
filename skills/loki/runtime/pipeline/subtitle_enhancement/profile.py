"""Exact selected-channel configuration; resolving never loads an encoder."""

from __future__ import annotations

from dataclasses import dataclass, field

import hashlib

from pathlib import Path

from types import MappingProxyType

from typing import Mapping

DEFAULT_REFERENCE_MIN_COSINE = 0.45

DEFAULT_REFERENCE_MIN_MARGIN = 0.18

REFERENCE_TURN_ASSIST_SCHEMA = "chzz.reference-turn-assist.v1"

REFERENCE_TURN_ASSIST_POLICY_VERSION = "existing_strict_target_flip.v3"

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

@dataclass(frozen=True)
class ResolvedProfile:
    platform: str
    channel_id: str
    enabled: bool = False
    reason: str = "not_selected"
    timing_enabled: bool = False
    turn_display_enabled: bool = False
    audio_events_enabled: bool = False
    music_enabled: bool = False
    laughter_enabled: bool = False
    reference_enabled: bool = False
    reference_turn_assist_enabled: bool = False
    reference_mode: str = "observe"
    reference_reason: str = "not_selected"
    reference_min_cosine: float = DEFAULT_REFERENCE_MIN_COSINE
    reference_min_margin: float = DEFAULT_REFERENCE_MIN_MARGIN
    profile_fingerprint: str = ""
    reference_fingerprint: str = ""
    voice_manifest_path: Path | None = None
    voice_manifest_sha256: str = ""
    embedding_path: Path | None = None
    embedding_sha256: str = ""
    encoder_path: Path | None = None
    encoder_sha256: str = ""
    policies: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    configured_modules: frozenset[str] = frozenset()

def reference_turn_assist_provenance(profile: object | None) -> dict | None:
    """Return the stable cache identity for an actually enabled exact profile."""
    if not (
        profile is not None
        and getattr(profile, "reference_enabled", False) is True
        and getattr(profile, "reference_turn_assist_enabled", False) is True
    ):
        return None
    reference_fingerprint = str(
        getattr(profile, "reference_fingerprint", "") or ""
    )
    if not reference_fingerprint:
        return None
    return {
        "schema": REFERENCE_TURN_ASSIST_SCHEMA,
        "policy_version": REFERENCE_TURN_ASSIST_POLICY_VERSION,
        "reference_fingerprint": reference_fingerprint,
        "min_cosine": float(getattr(profile, "reference_min_cosine", DEFAULT_REFERENCE_MIN_COSINE)),
        "min_margin": float(getattr(profile, "reference_min_margin", DEFAULT_REFERENCE_MIN_MARGIN)),
    }
