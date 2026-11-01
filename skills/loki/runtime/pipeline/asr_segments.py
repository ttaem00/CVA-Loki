"""Internal ASR segment sidecar helpers.

The public report path still uses SRT by default. This module provides a
deterministic JSON-compatible shape for diagnostics and future alignment work.
"""

from __future__ import annotations

import json

import re

from pathlib import Path

from typing import Any

SCHEMA_VERSION = "asr_segments.v1"

_SRT_TIME_RE = re.compile(
    r"(?P<h1>\d{2,}):(?P<m1>\d{2}):(?P<s1>\d{2})[,\.](?P<ms1>\d{3})"
    r"\s*-->\s*"
    r"(?P<h2>\d{2,}):(?P<m2>\d{2}):(?P<s2>\d{2})[,\.](?P<ms2>\d{3})"
)

_SRT_BLOCK_RE = re.compile(r"\r?\n[ \t]*\r?\n")

class AsrSegmentsError(ValueError):
    """Raised when non-empty SRT input cannot be converted safely."""

def _time_to_seconds(match: re.Match[str], suffix: str) -> float:
    millis = (
        int(match.group(f"h{suffix}")) * 3_600_000
        + int(match.group(f"m{suffix}")) * 60_000
        + int(match.group(f"s{suffix}")) * 1_000
        + int(match.group(f"ms{suffix}"))
    )
    return round(millis / 1000.0, 3)

def _caption_blocks(srt_text: str) -> list[str]:
    normalized = srt_text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    return [
        block.strip("\n")
        for block in _SRT_BLOCK_RE.split(normalized.strip())
        if block.strip()
    ]

def parse_srt_segments(
    srt_text: str,
    *,
    skip_non_positive_duration: bool = False,
) -> list[dict[str, Any]]:
    """Convert SRT text to ordered JSON-compatible ASR segment rows.

    Empty or whitespace-only input returns an empty list. Non-empty input with
    malformed blocks raises ``AsrSegmentsError`` instead of silently succeeding.
    """

    if not srt_text or not srt_text.strip():
        return []

    segments: list[dict[str, Any]] = []
    for block_no, block in enumerate(_caption_blocks(srt_text), start=1):
        lines = block.split("\n")
        if not lines:
            continue

        timestamp_line_index = 1 if lines[0].strip().isdigit() and len(lines) > 1 else 0
        timestamp_line = (
            lines[timestamp_line_index].strip()
            if len(lines) > timestamp_line_index
            else ""
        )
        time_match = _SRT_TIME_RE.search(timestamp_line)
        if not time_match:
            raise AsrSegmentsError(f"malformed_srt: block {block_no} missing timestamp")

        start_sec = _time_to_seconds(time_match, "1")
        end_sec = _time_to_seconds(time_match, "2")
        if end_sec <= start_sec:
            if skip_non_positive_duration:
                continue
            raise AsrSegmentsError(
                f"malformed_srt: block {block_no} end_sec must be greater than start_sec"
            )

        text_lines = lines[timestamp_line_index + 1 :]
        text = "\n".join(line.strip() for line in text_lines).strip()
        segments.append(
            {
                "index": len(segments) + 1,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "text": text,
            }
        )

    if not segments:
        raise AsrSegmentsError("malformed_srt: no caption segments found")

    return segments

def count_non_positive_duration_blocks(srt_text: str) -> int:
    """Count SRT blocks with parseable timestamps whose end is not after start."""

    if not srt_text or not srt_text.strip():
        return 0

    count = 0
    for block in _caption_blocks(srt_text):
        lines = block.split("\n")
        if not lines:
            continue
        timestamp_line_index = 1 if lines[0].strip().isdigit() and len(lines) > 1 else 0
        timestamp_line = (
            lines[timestamp_line_index].strip()
            if len(lines) > timestamp_line_index
            else ""
        )
        time_match = _SRT_TIME_RE.search(timestamp_line)
        if not time_match:
            continue
        if _time_to_seconds(time_match, "2") <= _time_to_seconds(time_match, "1"):
            count += 1
    return count

def _apply_provider_segment_metadata(
    segments: list[dict[str, Any]],
    *,
    provider_id: str | None = None,
    confidence_default: float | None = None,
) -> list[dict[str, Any]]:
    if provider_id is None and confidence_default is None:
        return segments
    enriched: list[dict[str, Any]] = []
    for segment in segments:
        row = dict(segment)
        if confidence_default is not None:
            row["confidence"] = confidence_default
        elif provider_id is not None:
            row["confidence"] = None
        if provider_id is not None:
            row["provider_id"] = provider_id
        enriched.append(row)
    return enriched

def build_asr_segments_sidecar(
    srt_text: str,
    *,
    video_no: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    source_srt: str | Path | None = None,
    provider_id: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
    confidence_default: float | None = None,
    skip_non_positive_duration: bool = False,
) -> dict[str, Any]:
    """Build an ``asr_segments.v1`` sidecar payload from SRT text."""

    segments = _apply_provider_segment_metadata(
        parse_srt_segments(
            srt_text,
            skip_non_positive_duration=skip_non_positive_duration,
        ),
        provider_id=provider_id,
        confidence_default=confidence_default,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "segments": segments,
    }
    if video_no is not None:
        payload["video_no"] = str(video_no)
    if engine is not None:
        payload["engine"] = str(engine)
    if model is not None:
        payload["model"] = str(model)
    if source_srt is not None:
        payload["source_srt"] = str(source_srt)
    if provider_id is not None:
        payload["provider_id"] = str(provider_id)
    if provider_metadata is not None:
        payload["provider"] = provider_metadata
    if skip_non_positive_duration:
        skipped_count = count_non_positive_duration_blocks(srt_text)
        if skipped_count:
            payload["diagnostics"] = {
                "skipped_non_positive_duration_segments": skipped_count,
            }
    return payload

def build_asr_segments_sidecar_from_path(
    srt_path: str | Path,
    *,
    video_no: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    source_srt: str | Path | None = None,
    provider_id: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
    confidence_default: float | None = None,
    skip_non_positive_duration: bool = False,
) -> dict[str, Any]:
    """Read an SRT file and build an ``asr_segments.v1`` sidecar payload."""

    path = Path(srt_path)
    return build_asr_segments_sidecar(
        path.read_text(encoding="utf-8", errors="replace"),
        video_no=video_no,
        engine=engine,
        model=model,
        source_srt=source_srt if source_srt is not None else path,
        provider_id=provider_id,
        provider_metadata=provider_metadata,
        confidence_default=confidence_default,
        skip_non_positive_duration=skip_non_positive_duration,
    )

def dumps_asr_segments_sidecar(payload: dict[str, Any]) -> str:
    """Serialize an ``asr_segments.v1`` payload deterministically."""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

def write_asr_segments_sidecar(
    output_path: str | Path,
    payload: dict[str, Any],
) -> Path:
    """Write an ``asr_segments.v1`` payload as deterministic UTF-8 JSON."""

    path = Path(output_path)
    path.write_text(dumps_asr_segments_sidecar(payload), encoding="utf-8")
    return path
