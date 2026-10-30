"""Continue a naturally ended live archive through its pinned normal version.

The live host owns scheduling and STOP. This helper reuses verified originals,
never writes live summary/ledger rows, and never retries an unknown full job.
"""

from __future__ import annotations

from contextlib import closing

import json

import os

from pathlib import Path

import re

import sqlite3

from .live_vod_repair import local_file, sha

from .live_editorial_source import _cues

POLICY = "missing-only-vod301-v1"

def archive_folder(archive):
    """Use the same registered location for source, STOP and saved recovery."""
    if not isinstance(archive, str) or not re.fullmatch(r"[a-f0-9]{32}-[1-9]\d{0,15}", archive):
        raise ValueError('finalize_archive_invalid')
    from .evidence_world.temporal_source import resolve_session
    _, database, _ = resolve_session(generation_root=os.environ['CHZZ_LIVE_RUNTIME_ROOT'],
        channel_uid=archive[:32],live_id=archive[33:],
        cold_archive_root=os.environ.get('CHZZ_COLD_ARCHIVE_ROOT') or None)
    return database.parent

def guard(folder):
    """Keep manual STOP, consent and both original end proofs authoritative."""
    stop = local_file(folder, "STOP").read_text(encoding="utf-8").strip()
    models = local_file(folder, "MODELS_STOP").read_text(encoding="utf-8").strip()
    if stop not in {"broadcast_ended", "broadcast_changed"} or models != stop:
        raise ValueError("finalize_manual_stop")
    with closing(sqlite3.connect(local_file(folder, "session.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM broadcasts").fetchall()
        if len(rows) != 1 or rows[0]["state"] != "ended":
            raise ValueError("finalize_end_unverified")
        broadcast = dict(rows[0])
        settings = json.loads(db.execute("SELECT value FROM meta WHERE name='settings'").fetchone()[0])
        if settings.get("aiConsent") is not True:
            raise ValueError("finalize_consent_required")
        for source in ("chat", "metadata"):
            row = db.execute("SELECT end_reason FROM collector_runs WHERE broadcast_id=? AND source=? "
                "AND ended_at_ms=? ORDER BY run_id DESC LIMIT 1",
                (broadcast["broadcast_id"], source, broadcast["ended_at_ms"])).fetchone()
            if not row or row[0] != stop:
                raise ValueError("finalize_end_unverified")
        return broadcast

def check_dispatch(cfg):
    """Called by the normal Free transport before dispatch and after receipt."""
    archive = cfg.get("live_finalize_archive")
    if not archive:
        return
    guard(archive_folder(archive))

def _verified_candidate_live_transcript(cfg, video_no, duration_sec, selected):
    """Verify the unchanged original owner before trusting a relocated view."""
    from copy import deepcopy
    from .evidence_world.contracts import native_path
    origin = selected["source_original"]
    if (not cfg.get("candidate_output_dir") or not isinstance(origin, dict)
            or set(origin) != {"work_dir", "selected"} or not isinstance(origin["selected"], dict)
            or "source_original" in origin["selected"]
            or Path(origin["work_dir"]).resolve() != Path(cfg.get("candidate_source_work_dir", "")).resolve()
            or Path(cfg["work_dir"]).resolve() == Path(origin["work_dir"]).resolve()):
        raise ValueError("finalize_candidate_source_scope_changed")
    original_cfg = dict(cfg, work_dir=origin["work_dir"], live_finalize_source=origin["selected"])
    original = Path(verified_live_transcript(original_cfg, video_no, duration_sec))
    original_work = Path(origin["work_dir"]).resolve() / str(video_no)
    work = Path(cfg["work_dir"]).resolve() / str(video_no)
    output = local_file(work, original.relative_to(original_work).as_posix())
    expected = deepcopy(origin["selected"])
    expected.update(transcript=str(output), source_original=origin)
    if expected.get("manager_context") is not None:
        relative = native_path(expected["manager_context"]["path"]).resolve().relative_to(native_path(original_work).resolve()).as_posix()
        context = native_path(local_file(work, relative)).resolve()
        expected["manager_context"] = dict(path=str(context), sha256=expected["manager_context"]["sha256"])
    if selected != expected:
        raise ValueError("finalize_candidate_source_identity_changed")
    if (sha(output) != expected["source_sha256"]
            or sha(output.with_suffix(".live-vod-source.json")) != expected["provenance_sha256"]):
        raise ValueError("finalize_candidate_subtitles_changed")
    if expected.get("manager_context") is not None:
        from .live_editorial_source import load_live_manager_context
        load_live_manager_context(output, source_clock=expected["manager_context_clock"], binding=expected["manager_context"])
    return str(output)

def verified_live_transcript(cfg, video_no, duration_sec):
    """Verify the selected immutable live result within this VOD's work folder."""
    archive = cfg.get("live_finalize_archive")
    if not archive:
        return None
    selected = cfg.get("live_finalize_source") or {}
    if "source_original" in selected:
        return _verified_candidate_live_transcript(cfg, video_no, duration_sec, selected)
    check_dispatch(cfg)
    work = Path(cfg["work_dir"]).resolve() / str(video_no)
    output = local_file(work, Path(selected.get("transcript") or "").relative_to(work).as_posix())
    provenance = local_file(work, output.with_suffix(".live-vod-source.json").relative_to(work).as_posix())
    if (str(output) != selected.get("transcript") or sha(output) != selected.get("source_sha256")
            or sha(provenance) != selected.get("provenance_sha256")):
        raise ValueError("finalize_subtitles_changed")
    proof = json.loads(provenance.read_text(encoding="utf-8"))
    mapping = proof.get("mapping") or {}
    if (proof.get("policy") != POLICY or proof.get("archive") != archive
            or str(proof.get("video_no")) != str(video_no)
            or proof.get("srt_sha256") != selected["source_sha256"]
            or mapping.get("sourceRevision") != selected.get("source_revision")
            or mapping.get("durationMs") != selected.get("duration_ms")
            or abs(duration_sec * 1000 - selected["duration_ms"]) > 2000):
        raise ValueError("finalize_source_identity_changed")
    folder = archive_folder(archive)
    current = json.loads(local_file(folder, "vod-recovery-state.json").read_text(encoding="utf-8"))
    if current.get("mapping") != mapping:
        raise ValueError("finalize_clock_changed")
    context_revision = selected.get("manager_context_revision_id")
    if context_revision is not None:
        from .live_editorial_source import manager_context_path
        path = manager_context_path(output, revision_id=context_revision)
        binding = selected.get("manager_context")
        if (binding is not None and binding.get("path") != str(path.resolve())
                or binding is None and selected.get("manager_context_clock") is not None):
            raise ValueError("finalize_context_identity_changed")
    elif (proof.get("manager_context") != selected.get("manager_context")
            or proof.get("manager_context_clock") != selected.get("manager_context_clock")):
        raise ValueError("finalize_context_identity_changed")
    if selected.get("manager_context") is not None:
        from .live_editorial_source import load_live_manager_context
        clock = selected.get("manager_context_clock")
        broadcast = guard(folder)
        expected_clock = dict(archive_id=archive, broadcast_start_at_ms=broadcast["open_at_utc_ms"])
        if isinstance(clock, dict) and "context_as_of_ms" in clock:
            expected_clock["context_as_of_ms"] = mapping["originMs"] + max(end for _, end, _ in _cues(output))
        if (clock != expected_clock
                or broadcast["open_at_confidence"] != "provider"):
            raise ValueError("finalize_context_clock_changed")
        load_live_manager_context(output, source_clock=clock, binding=selected["manager_context"])
    return str(output)
