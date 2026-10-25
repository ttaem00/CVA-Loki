"""Workspace-local registry for the three generated report files and their provenance."""

from __future__ import annotations

from datetime import datetime, timezone

import hashlib

import json

import os

from pathlib import Path

import re

import stat

import tempfile

ARTIFACT_SCHEMA_VERSION = "runtime_artifact.v1"

PROVENANCE_SCHEMA_VERSION = "runtime_artifact_provenance.v2"

DEFAULT_REGISTRY_PATH = Path("data/artifact_registry/artifacts.jsonl")

DEFAULT_PROVENANCE_PATH = Path("data/artifact_registry/provenance.jsonl")

def _local_path(root: str | Path, path: str | Path) -> Path:
    root = Path(root).resolve()
    candidate = Path(str(path).replace("\\", "/"))
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("artifact path is outside runtime root")
    current = root
    for part in candidate.absolute().relative_to(root).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("artifact path contains a link")
    return resolved

def relative_artifact_path(root: str | Path, path: str | Path) -> str:
    return _local_path(root, path).relative_to(Path(root).resolve()).as_posix()

def artifact_id(relative_path: str) -> str:
    normalized = str(relative_path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return "artifact-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows

def _write_rows(root: Path, relative_path: Path, rows: dict) -> None:
    path = _local_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", encoding="utf-8", newline="\n",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            for key in sorted(key for key in rows if key):
                stream.write(json.dumps(rows[key], ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def register_artifact(root: str | Path, relative_path: str | Path, *, producer: str = "",
                      source_run_id: str = "", expected_tier: str | None = None,
                      max_hash_bytes: int | None = 64 * 1024 * 1024) -> dict:
    root = Path(root).resolve()
    path = _local_path(root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"artifact does not exist: {relative_path}")
    if path.suffix.lower() not in {".md", ".html", ".json"}:
        raise ValueError("artifact must be a generated report file")
    if expected_tier and expected_tier != "review":
        raise ValueError(f"artifact tier mismatch: expected {expected_tier}, got review")
    rel = path.relative_to(root).as_posix()
    info = path.stat()
    digest, hash_status = "", "skipped_large"
    if max_hash_bytes is None or info.st_size <= max_hash_bytes:
        content_hash = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_hash.update(chunk)
        digest, hash_status = content_hash.hexdigest(), "hashed"
    match = re.match(r"^(\d{5,12})_", path.name)
    vod_id = match.group(1) if match else ""
    record = dict(schema_version=ARTIFACT_SCHEMA_VERSION, artifact_id=artifact_id(rel),
        relative_path=rel, root=rel.split("/", 1)[0], kind="report_output", owner="report_admin",
        scope="vod" if vod_id else "global", vod_id=vod_id, channel_id="", role="review_report",
        privacy="private", backup_tier="review", restore_priority=8, rebuildable=False,
        remote_allowed=True, size_bytes=info.st_size, sha256=digest, hash_status=hash_status,
        created_at=_timestamp(info.st_ctime), updated_at=_timestamp(info.st_mtime),
        producer=producer, source_run_id=source_run_id)
    rows = {row.get("relative_path", ""): row
            for row in _read_rows(_local_path(root, DEFAULT_REGISTRY_PATH))}
    rows[rel] = record
    _write_rows(root, DEFAULT_REGISTRY_PATH, rows)
    return record

def _provenance_completeness(record: dict) -> dict[str, bool]:
    return {
        "has_artifact_id": bool(record.get("artifact_id")),
        "has_source": bool(record.get("source_video_no") or record.get("source_url") or record.get("input_artifact_paths")),
        "has_pipeline_run_id": bool(record.get("pipeline_run_id")),
        "has_code_version": bool(record.get("pipeline_git_commit") or record.get("code_version")),
        "has_effective_model": bool(record.get("model") or (record.get("llm") or {}).get("effective_llm_provider_model")),
        "has_prompt_sha256": bool(record.get("prompt_sha256")),
        "has_stt_or_transcript_basis": bool(record.get("stt_model") or (record.get("stt_or_transcription_basis") or {}).get("srt_hash") or record.get("transcript_source")),
        "has_rebuild_pointer": bool(record.get("media_redownload_pointer_id") or record.get("media_source_pointer") or record.get("output_artifact_paths")),
    }

def register_provenance(root: str | Path, record: dict) -> dict:
    root = Path(root).resolve()
    normalized = dict(record)
    normalized["schema_version"] = PROVENANCE_SCHEMA_VERSION
    normalized.setdefault("created_at", _timestamp(datetime.now(timezone.utc).timestamp()))
    normalized.setdefault("pipeline_git_commit", normalized.get("code_version") or "")
    normalized.setdefault("code_version", normalized.get("pipeline_git_commit") or "")
    if not normalized.get("artifact_id"):
        outputs = normalized.get("output_artifact_paths") or []
        if outputs:
            normalized["artifact_id"] = artifact_id(str(outputs[0]))
    normalized["provenance_completeness"] = _provenance_completeness(normalized)
    normalized["legacy_provenance_partial"] = not all(normalized["provenance_completeness"].values())
    def identity(row):
        return str(row.get("artifact_id") or row.get("pipeline_run_id") or row.get("source_video_no") or "")
    key = identity(normalized)
    if not key:
        raise ValueError("provenance record requires artifact_id, pipeline_run_id, or source_video_no")
    rows = {identity(row): row for row in _read_rows(_local_path(root, DEFAULT_PROVENANCE_PATH))}
    rows[key] = normalized
    _write_rows(root, DEFAULT_PROVENANCE_PATH, rows)
    return normalized
