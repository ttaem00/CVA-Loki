"""Create-new publication primitives for derived subtitle artifacts.

The normal subtitle pipeline may overlap with a timed-out daemon worker.  These
helpers therefore reserve final names exclusively before replacing only those
reservations.  Existing manager or concurrent-run files are never overwritten.
"""

from __future__ import annotations

import hashlib

import json

import os

from pathlib import Path

from typing import Callable, Mapping

import uuid

REFERENCE_PUBLICATION_SCHEMA = "chzz.reference-turn-publication.v1"

def _stage_path(target: Path) -> Path:
    """Keep temporary names short enough for ordinary Windows path limits."""
    return target.parent / f".w-{uuid.uuid4().hex}.tmp"

def _write_stage(path: Path, writer: Callable[[Path], None]) -> None:
    writer(path)
    if not path.is_file():
        raise OSError(f"Subtitle stage writer did not create a file: {path.name}")

def _reserve(path: Path) -> None:
    with path.open("xb"):
        pass

def _remove_zero_reservation(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()
    except OSError:


        pass

def _remove_matching_bytes(path: Path, expected: bytes) -> None:
    try:
        if path.is_file() and path.read_bytes() == expected:
            path.unlink()
    except OSError:
        pass

def publish_new_file(path: str | Path, writer: Callable[[Path], None]) -> Path:
    """Atomically publish a new file, failing instead of replacing a winner."""
    target = Path(path).resolve()
    stage = _stage_path(target)
    reserved = False
    try:
        _write_stage(stage, writer)
        _reserve(target)
        reserved = True
        os.replace(stage, target)
        reserved = False
        return target
    finally:
        if reserved:
            _remove_zero_reservation(target)
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            pass

def publish_reference_pair(
    srt_path: str | Path,
    srt_writer: Callable[[Path], None],
    metadata_payload: Mapping[str, object],
) -> tuple[Path, Path]:
    """Publish a content-bound reference SRT pair with the SRT appearing last."""
    srt = Path(srt_path).resolve()
    metadata = srt.with_suffix(".speaker_turns.json")
    timing = srt.with_suffix(".subtitle_timing.json")
    chrome_metadata = srt.with_name(f"{srt.stem}.chrome_stt_metadata.json")
    srt_stage = _stage_path(srt)
    metadata_stage = _stage_path(metadata)
    srt_reserved = False
    metadata_reserved = False
    metadata_published = False
    metadata_bytes = b""
    try:
        _write_stage(srt_stage, srt_writer)
        srt_sha256 = hashlib.sha256(srt_stage.read_bytes()).hexdigest()
        payload = dict(metadata_payload)
        payload["publication"] = {
            "schema": REFERENCE_PUBLICATION_SCHEMA,
            "attempt_id": uuid.uuid4().hex,
            "srt_name": srt.name,
            "srt_sha256": srt_sha256,
        }
        metadata_bytes = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        metadata_stage.write_bytes(metadata_bytes)

        _reserve(srt)
        srt_reserved = True
        occupied = next(
            (path for path in (metadata, timing, chrome_metadata) if path.exists()),
            None,
        )
        if occupied is not None:
            raise FileExistsError(f"Derived subtitle sidecar already exists: {occupied}")
        _reserve(metadata)
        metadata_reserved = True



        os.replace(metadata_stage, metadata)
        metadata_reserved = False
        metadata_published = True
        os.replace(srt_stage, srt)
        srt_reserved = False
        return srt, metadata
    except Exception:
        if metadata_reserved:
            _remove_zero_reservation(metadata)
        elif metadata_published:
            _remove_matching_bytes(metadata, metadata_bytes)
        if srt_reserved:
            _remove_zero_reservation(srt)
        raise
    finally:
        for stage in (srt_stage, metadata_stage):
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
