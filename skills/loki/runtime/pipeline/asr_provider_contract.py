"""ASR provider contract and stable artifact helpers.

The product path still consumes SRT. This module adds a small provider boundary
around that SRT so future engines can be swapped without rewriting summary or
report code.
"""

from __future__ import annotations

import json

import time

from dataclasses import dataclass, field

from pathlib import Path

from typing import Any

from .asr_segments import (
    AsrSegmentsError,
    build_asr_segments_sidecar_from_path,
    count_non_positive_duration_blocks,
    write_asr_segments_sidecar,
)

ASR_PROVIDER_METADATA_SCHEMA_VERSION = "asr_provider_metadata.v1"

class AsrProviderError(ValueError):
    """Raised when an ASR provider/model cannot be resolved safely."""

class AsrProviderArtifactError(RuntimeError):
    """Raised when a provider returns artifacts that violate the contract."""

@dataclass(frozen=True)
class AsrProviderSpec:
    provider_id: str
    family: str
    display_name: str
    model_names: tuple[str, ...] = ()
    model_prefixes: tuple[str, ...] = ()
    capabilities: dict[str, Any] = field(default_factory=dict)
    policy_state: dict[str, Any] = field(default_factory=dict)
    default_model: str = ""

    def supports_model(self, model: str | None) -> bool:
        if not isinstance(model, str):
            return False
        normalized = (model or "").strip()
        if not normalized:
            return False
        if normalized in self.model_names:
            return True
        return any(normalized.startswith(prefix) for prefix in self.model_prefixes)

    def provider_metadata(self, model: str) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "family": self.family,
            "display_name": self.display_name,
            "model": model,
        }

_PROVIDERS: list[AsrProviderSpec] = [
    AsrProviderSpec(
        provider_id="hf-whisper-transformers",
        family="local_whisper",
        display_name="Hugging Face Whisper",
        model_names=("large-v3-turbo", "large-v3"),
        default_model="large-v3-turbo",
        capabilities={
            "srt": True,
            "asr_segments_v1": True,
            "segment_confidence": False,
            "word_timestamps": False,
            "anonymous_speaker_turns": True,
            "local_execution": True,
            "cloud_fallback_supported": False,
            "shadow_comparison_supported": True,
        },
        policy_state={
            "execution_policy": "local_only",
            "cloud_fallback_enabled": False,
            "billing_payer": None,
            "paid_api_required": False,
            "default_eligible": True,
        },
    ),
    AsrProviderSpec(
        provider_id="chrome-web-speech",
        family="browser_web_speech",
        display_name="Chrome Web Speech",
        model_names=("chrome-web-speech-local",),
        default_model="chrome-web-speech-local",
        capabilities={
            "srt": True,
            "asr_segments_v1": True,
            "segment_confidence": True,
            "word_timestamps": False,
            "anonymous_speaker_turns": False,
            "local_execution": True,
            "cloud_fallback_supported": True,
            "shadow_comparison_supported": True,
        },
        policy_state={
            "execution_policy": "strict_local_by_default",
            "cloud_fallback_enabled": False,
            "billing_payer": None,
            "paid_api_required": False,
            "default_eligible": False,
        },
    ),
]

def resolve_asr_provider(model: str | None) -> AsrProviderSpec:
    for provider in _PROVIDERS:
        if provider.supports_model(model):
            return provider
    allowed = ", ".join(sorted(known_asr_model_patterns()))
    raise AsrProviderError(
        f"unknown_asr_model: {model!r}. Registered ASR models/prefixes: {allowed}"
    )

def known_asr_model_patterns() -> tuple[str, ...]:
    patterns: list[str] = []
    for provider in _PROVIDERS:
        patterns.extend(provider.model_names)
        patterns.extend(f"{prefix}*" for prefix in provider.model_prefixes)
    return tuple(patterns)

def asr_segments_sidecar_path(srt_path: str | Path) -> Path:
    path = Path(srt_path)
    return path.with_name(f"{path.stem}.asr_segments.json")

def asr_provider_metadata_path(srt_path: str | Path) -> Path:
    path = Path(srt_path)
    return path.with_name(f"{path.stem}.asr_provider_metadata.json")

def _safe_error_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return text[:300]

def _build_metadata(
    *,
    srt_path: Path,
    provider: AsrProviderSpec,
    model: str,
    status: str,
    segment_count: int = 0,
    duration_sec: float | None = None,
    error: str = "",
    provider_specific_metadata_path: str | Path | None = None,
    skipped_non_positive_duration_segments: int = 0,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {
        "srt": str(srt_path),
        "asr_segments": str(asr_segments_sidecar_path(srt_path)),
    }
    if provider_specific_metadata_path is not None:
        artifacts["provider_specific_metadata"] = str(provider_specific_metadata_path)

    diagnostics: dict[str, Any] = {
        "status": status,
        "segment_count": segment_count,
        "empty_output": segment_count == 0 and status == "ok",
        "duration_sec": duration_sec,
        "raw_transcript_printed": False,
        "raw_audio_stored": False,
        "raw_media_path_stored": False,
    }
    if error:
        diagnostics["manager_safe_error"] = error
    if skipped_non_positive_duration_segments:
        diagnostics["skipped_non_positive_duration_segments"] = (
            skipped_non_positive_duration_segments
        )

    return {
        "schema_version": ASR_PROVIDER_METADATA_SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": provider.provider_metadata(model),
        "capabilities": dict(provider.capabilities),
        "policy_state": dict(provider.policy_state),
        "artifacts": artifacts,
        "diagnostics": diagnostics,
        "metadata_only": True,
    }

def _write_metadata(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path

def write_asr_contract_artifacts(
    srt_path: str | Path,
    *,
    provider: AsrProviderSpec,
    model: str,
    video_no: str | None = None,
    provider_specific_metadata_path: str | Path | None = None,
) -> dict[str, Path]:
    """Validate SRT and write stable provider artifacts.

    The sidecar contains transcript text because ``asr_segments.v1`` is an
    internal runtime artifact. The provider metadata is safe to summarize in
    issue/PR text because it contains counts, policy, capabilities, and paths
    only.
    """

    srt = Path(srt_path)
    metadata_path = asr_provider_metadata_path(srt)
    segments_path = asr_segments_sidecar_path(srt)
    try:
        sidecar = build_asr_segments_sidecar_from_path(
            srt,
            video_no=video_no,
            engine=provider.family,
            model=model,
            provider_id=provider.provider_id,
            provider_metadata=provider.provider_metadata(model),
            confidence_default=None,
            skip_non_positive_duration=True,
        )
        skipped_count = count_non_positive_duration_blocks(
            srt.read_text(encoding="utf-8", errors="replace")
        )
        segments = sidecar.get("segments") or []
        duration_sec = None
        if segments:
            duration_sec = max(float(row.get("end_sec") or 0.0) for row in segments)
        write_asr_segments_sidecar(segments_path, sidecar)
        metadata = _build_metadata(
            srt_path=srt,
            provider=provider,
            model=model,
            status="ok",
            segment_count=len(segments),
            duration_sec=duration_sec,
            provider_specific_metadata_path=provider_specific_metadata_path,
            skipped_non_positive_duration_segments=skipped_count,
        )
        _write_metadata(metadata_path, metadata)
        return {"asr_segments": segments_path, "provider_metadata": metadata_path}
    except AsrSegmentsError as exc:
        metadata = _build_metadata(
            srt_path=srt,
            provider=provider,
            model=model,
            status="malformed_output",
            error=_safe_error_message(exc),
            provider_specific_metadata_path=provider_specific_metadata_path,
        )
        _write_metadata(metadata_path, metadata)
        raise AsrProviderArtifactError(
            f"ASR provider returned malformed SRT ({provider.provider_id})"
        ) from exc
