"""Optional collaboration record produced from a committed report and its evidence."""

from __future__ import annotations

import hashlib

import json

import logging

from pathlib import Path

from .chatgpt_export.contract.collaboration_periods import (
    merge_authored_collaboration_periods, public_collaboration_periods,
    validate_collaboration_periods,
)

from .collaboration_candidates import (
    merge_period_roster_candidates, project_live_collaboration_notes, public_collaboration_candidates,
)

VERSION = "loki_3_0_9_2"

_SCHEMA = "loki.collaboration-stage.v1"

_LIMIT = 2 * 1024 * 1024

_LOG = logging.getLogger("pipeline")

def _path(metadata_path):
    metadata = Path(metadata_path)
    if not metadata.name.endswith("_metadata.json"):
        raise ValueError("collaboration_stage_report_path")
    return metadata.with_name(metadata.name.removesuffix("_metadata.json") + ".collaboration.json")

def _binding(metadata):
    family = metadata["report_artifact_family"]
    return {"video_no": str(metadata["video_no"]), "generation_id": family["generation_id"],
            "metadata_payload_sha256": family["metadata_payload_sha256"]}

def _source_proof(metadata):
    owner = (metadata.get("live_report_owner") or {}).get("generation_id")
    if owner:
        return {"live_owner_generation": owner}
    author = metadata.get("llm_provider_provenance") or {}
    request, source = author.get("request_sha256"), author.get("source_revision")
    if all(isinstance(item, str) and len(item) == 64 for item in (request, source)):
        return {"author_request_sha256": request, "source_revision": source}
    return None

def _retained_periods(metadata):
    packet = metadata.get("editorial_review_packet") or {}
    return packet.get("collaboration_periods", metadata.get("collaboration_periods"))

def _compact_new_periods(periods, duration):
    """Adjacent identical activity rosters need one range across child chapters."""
    validate_collaboration_periods(periods, duration)
    compact = []
    for period in periods:
        row = {**period, "id": "live-" + period["id"]}
        prior = compact[-1] if compact else None
        if (prior is not None and row["start_sec"] == prior["end_sec"]
                and row["source_kind"] == prior["source_kind"]
                and row["support_status"] == prior["support_status"]
                and set(row["members"]) == set(prior["members"]) and not row.get("member_vods")
                and not prior.get("member_vods")):
            refs = sorted(set(prior["evidence_refs"]) | set(row["evidence_refs"]))
            if len(refs) <= 128:
                prior.update(end_sec=row["end_sec"], evidence_refs=refs)
                continue
        compact.append(row)
    return compact

def _projection(metadata, live_evidence):
    duration = metadata["duration"]
    retained = _retained_periods(metadata)
    projected = {"periods": [], "candidates": []}
    if live_evidence is not None:
        context, origin_ms = live_evidence()
        if context is not None:
            projected = project_live_collaboration_notes(
                context, str(metadata["video_no"]), duration,
                host_name=metadata.get("channel") or "", vod_origin_ms=origin_ms,
            )
    added = _compact_new_periods(projected["periods"], duration)
    periods = merge_authored_collaboration_periods(retained, added, duration)["periods"]
    candidates = merge_period_roster_candidates(
        [*(metadata.get("collaboration_candidates") or []), *projected["candidates"]], periods,
    )
    return periods, candidates, added

def read_report_collaboration(metadata_path, metadata):
    """Read only the result belonging to the caller's validated report family."""
    if not metadata_path or (metadata.get("summary_pipeline") or {}).get("actual_version") != VERSION:
        return None
    try:
        path = _path(metadata_path)
        if path.is_symlink() or path.stat().st_size > _LIMIT:
            return None
        raw = path.read_bytes()
        if len(raw) > _LIMIT:
            return None
        state = json.loads(raw)
        if not isinstance(state, dict) or state.get("schema_version") != _SCHEMA:
            return None
        duration = metadata["duration"]
        binding = _binding(metadata)
        recorded = state["report"]
        candidates = public_collaboration_candidates(state["candidates"])
        if recorded == binding:
            periods = validate_collaboration_periods(state["periods"], duration, require_evidence_refs=False)
        else:


            proof = _source_proof(metadata)
            if not proof or state.get("source_proof") != proof or recorded["video_no"] != binding["video_no"]:
                return None
            periods = merge_authored_collaboration_periods(
                _retained_periods(metadata), state["derived_periods"], duration,
            )["periods"]
            candidates = merge_period_roster_candidates(
                [*(metadata.get("collaboration_candidates") or []), *candidates], periods,
            )
        return (public_collaboration_periods(periods, duration), candidates, hashlib.sha256(raw).hexdigest())
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
        return None

def finish_report_collaboration(markdown_path, html_path, metadata_path, *, generation_id, live_evidence=None):
    """Settle once after a report save. A bad or interrupted result cannot restart writing."""
    from .evidence_world.index import atomic_json
    from .report_artifact_family import _report_family_lock, validate_report_artifact_family

    try:
        metadata_path = Path(metadata_path)
        family = validate_report_artifact_family(markdown_path, html_path, metadata_path)
        metadata = family["metadata"]
        if (family["generation_id"] != generation_id
                or (metadata.get("summary_pipeline") or {}).get("actual_version") != VERSION):
            return
        progress = metadata.get("manager_outline_review_progress")
        if isinstance(progress, dict):
            if progress.get("pipeline_complete") is not True:
                return
        elif str((metadata.get("editorial_review_packet") or {}).get("completion_state", "")).startswith("partial_"):
            return
        if read_report_collaboration(metadata_path, metadata) is not None:
            return
        periods, candidates, derived = _projection(metadata, live_evidence)
        with _report_family_lock(metadata_path.parent, video_no=metadata["video_no"]):
            current = json.loads(metadata_path.read_text(encoding="utf-8"))
            if _binding(current) != _binding(metadata) or read_report_collaboration(metadata_path, current) is not None:
                return
            atomic_json(_path(metadata_path), dict(schema_version=_SCHEMA, report=_binding(metadata),
                periods=periods, candidates=candidates, derived_periods=derived, source_proof=_source_proof(metadata)))
    except Exception as error:
        _LOG.warning("Loki collaboration record unavailable (%s)", type(error).__name__)
