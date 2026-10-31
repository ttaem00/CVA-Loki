"""Compact summary run ledger snapshots.

The ledger links technical summary execution state to manager-facing review
state without copying raw transcript, chat, community, prompt, or config values.
It is intentionally a local JSONL artifact in the report output directory.
"""

from __future__ import annotations

import json

from copy import deepcopy

from datetime import datetime, timezone

from pathlib import Path

from typing import Any

SCHEMA_VERSION = "summary_run_ledger.v1"

LEDGER_FILENAME = "summary_run_ledger.jsonl"

_MANIFEST_FINGERPRINT_KEYS = (
    "vod_hash",
    "srt_hash",
    "chunks_hash",
    "chats_hash",
    "highlights_hash",
    "community_hash",
    "lexicon_terms_hash",
    "context_doc_hash",
    "user_clips_hash",
    "chunker_params_md5",
    "llm_provider_order_hash",
    "task_provider_order_hash",
    "engagement_thresholds_signature",
    "selected_promoted_set_hash",
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def default_summary_run_ledger_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / LEDGER_FILENAME

def run_id_from_metadata_path(metadata_path: str | Path) -> str:
    path = Path(metadata_path)
    stem = path.stem
    if stem.endswith("_metadata"):
        stem = stem[: -len("_metadata")]
    return stem or path.name

def _clean_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value or "").strip()]

def _readiness_item_statuses(readiness: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(readiness, dict):
        return {}
    statuses: dict[str, str] = {}
    for item in readiness.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            statuses[key] = str(item.get("status") or "unknown")
    return statuses

def _manifest_fingerprints(source_manifest: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(source_manifest, dict):
        return {}
    manifest = source_manifest.get("manifest")
    if not isinstance(manifest, dict):
        return {}
    fingerprints: dict[str, str] = {}
    for key in _MANIFEST_FINGERPRINT_KEYS:
        value = manifest.get(key)
        if value is not None:
            fingerprints[key] = str(value)
    prompt_hashes = manifest.get("prompt_hashes")
    if isinstance(prompt_hashes, dict):
        for key, value in sorted(prompt_hashes.items()):
            if value is not None:
                fingerprints[f"prompt_hashes.{key}"] = str(value)
    if source_manifest.get("summary_sha256"):
        fingerprints["summary_sha256"] = str(source_manifest["summary_sha256"])
    return fingerprints

def _timed_evidence_summary(timed_evidence_manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(timed_evidence_manifest, dict):
        return {}
    summary = timed_evidence_manifest.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return {
        "schema_version": timed_evidence_manifest.get("schema_version"),
        "item_schema_version": timed_evidence_manifest.get("item_schema_version"),
        "item_count": _as_int(summary.get("item_count")),
        "token_total": _as_int(summary.get("token_total")),
        "start_sec": _as_int(summary.get("start_sec")),
        "end_sec": _as_int(summary.get("end_sec")),
        "path_ref": str(timed_evidence_manifest.get("path_ref") or ""),
        "raw_content_included": bool(timed_evidence_manifest.get("raw_content_included") or False),
    }

def _path_payload(path_value: str | Path | None) -> dict[str, Any]:
    if not path_value:
        return {"path": "", "filename": "", "exists": False, "size_bytes": 0}
    path = Path(path_value)
    payload: dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "exists": False,
        "size_bytes": 0,
    }
    try:
        payload["exists"] = path.is_file()
        payload["size_bytes"] = path.stat().st_size if payload["exists"] else 0
    except OSError:
        payload["stat_error"] = True
    return payload

def build_summary_run_ledger_entry(
    *,
    vod_info: Any,
    summary_md_path: str | Path,
    summary_html_path: str | Path,
    metadata_path: str | Path,
    source_manifest: dict[str, Any] | None = None,
    timed_evidence_manifest: dict[str, Any] | None = None,
    summary_input_readiness: dict[str, Any] | None = None,
    provider_order: list[str] | None = None,
    llm_provider_provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    execution_status: str = "report_generated",
    execution_stage: str = "saving",
    failure_digest_id: str = "",
    admin_edit_state: str = "not_reviewed",
    publish_state: str = "not_published",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a serializable current-state ledger snapshot."""

    effective_run_id = run_id or run_id_from_metadata_path(metadata_path)
    readiness = summary_input_readiness if isinstance(summary_input_readiness, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": effective_run_id,
        "updated_at": generated_at or _now_iso(),
        "video": {
            "video_no": str(getattr(vod_info, "video_no", "") or ""),
            "title": str(getattr(vod_info, "title", "") or ""),
            "channel_id": str(getattr(vod_info, "channel_id", "") or ""),
            "channel_name": str(getattr(vod_info, "channel_name", "") or ""),
            "duration": int(getattr(vod_info, "duration", 0) or 0),
            "publish_date": str(getattr(vod_info, "publish_date", "") or ""),
        },
        "execution": {
            "status": execution_status,
            "stage": execution_stage,
            "failure_digest_id": str(failure_digest_id or ""),
        },
        "inputs": {
            "source_manifest_schema_version": (
                source_manifest.get("schema_version") if isinstance(source_manifest, dict) else None
            ),
            "timed_evidence": _timed_evidence_summary(timed_evidence_manifest),
            "artifact_fingerprints": _manifest_fingerprints(source_manifest),
            "readiness_status": str(readiness.get("status") or "unknown"),
            "readiness_item_statuses": _readiness_item_statuses(readiness),
            "quality_claim": str(readiness.get("quality_claim") or "not_evaluated"),
        },
        "llm": {
            "provider_order": _clean_str_list(provider_order or []),
            "provider_provenance": (
                llm_provider_provenance if isinstance(llm_provider_provenance, dict) else {}
            ),
        },
        "reports": {
            "markdown": _path_payload(summary_md_path),
            "html": _path_payload(summary_html_path),
            "metadata": _path_payload(metadata_path),
        },
        "manager_state": {
            "admin_edit_state": admin_edit_state,
            "publish_state": publish_state,
        },
    }

def append_summary_run_ledger_entry(
    ledger_path: str | Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(entry)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("updated_at", _now_iso())
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload

def record_summary_run_ledger_entry(
    *,
    output_dir: str | Path,
    vod_info: Any,
    summary_md_path: str | Path,
    summary_html_path: str | Path,
    metadata_path: str | Path,
    source_manifest: dict[str, Any] | None = None,
    timed_evidence_manifest: dict[str, Any] | None = None,
    summary_input_readiness: dict[str, Any] | None = None,
    provider_order: list[str] | None = None,
    llm_provider_provenance: dict[str, Any] | None = None,
    failure_digest_id: str = "",
    admin_edit_state: str = "not_reviewed",
    publish_state: str = "not_published",
) -> dict[str, Any]:
    ledger_path = default_summary_run_ledger_path(output_dir)
    entry = build_summary_run_ledger_entry(
        vod_info=vod_info,
        summary_md_path=summary_md_path,
        summary_html_path=summary_html_path,
        metadata_path=metadata_path,
        source_manifest=source_manifest,
        timed_evidence_manifest=timed_evidence_manifest,
        summary_input_readiness=summary_input_readiness,
        provider_order=provider_order,
        llm_provider_provenance=llm_provider_provenance,
        failure_digest_id=failure_digest_id,
        admin_edit_state=admin_edit_state,
        publish_state=publish_state,
    )
    return append_summary_run_ledger_entry(ledger_path, entry)
