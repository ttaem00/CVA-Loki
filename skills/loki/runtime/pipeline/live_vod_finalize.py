"""Continue a naturally ended live archive through its pinned normal version.

The live host owns scheduling and STOP. This helper reuses verified originals,
never writes live summary/ledger rows, and never retries an unknown full job.
"""

from __future__ import annotations

from contextlib import closing

from datetime import datetime, timedelta, timezone

import json

import os

from pathlib import Path

import re

import sqlite3

import stat

import time

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

def _replay_qualified_handover_end(db, folder, broadcast):
    """Mirror live-run.replayQualifiedHandoverEnd at this Python RO boundary.

    Technical collector shutdown stays distinct from provider broadcast EOF.
    The retained official close and complete same-VOD replay qualify only the
    end proof; consent, manager STOP, frozen inputs and budgets remain separate.
    """
    def integer(value):
        return type(value) is int and abs(value) <= 9007199254740991

    def read(name, limit):
        path = local_file(folder, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('finalize_end_unverified')
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('finalize_end_unverified')
        return raw.decode('utf-8')

    def provider_time(value):
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', value):
            parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=9)))
        else:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)

    try:
        status = json.loads(read('status.json', 128000))
        receipt = json.loads(read('broadcast-end.json', 4096))
        if read('MODELS_STOP', 128).strip() != 'broadcast_ended':
            return False
        identity = broadcast.get('broadcast_id')
        match = re.fullmatch(r'chzzk:([a-f0-9]{32}):([1-9]\d{0,15})', identity or '')
        if (not match or not isinstance(status, dict) or not isinstance(receipt, dict)
                or status.get('channelId') != match[1] or str(status.get('liveId')) != match[2]
                or status.get('stopping') is not True or status.get('connected') is not False
                or not integer(status.get('pid')) or status['pid'] < 1
                or not integer(status.get('startedAt')) or status['startedAt'] < 1):
            return False
        ended, now = broadcast.get('ended_at_ms'), int(time.time() * 1000)
        if (broadcast.get('open_at_source') != 'live_detail.openDate'
                or broadcast.get('open_at_confidence') != 'provider'
                or not integer(ended) or not status['startedAt'] < ended <= now
                or receipt.get('schema') != 'loom.official-broadcast-end.v1'
                or receipt.get('broadcastId') != identity or receipt.get('source') != 'live_detail.closeDate'
                or receipt.get('openDate') != broadcast.get('open_at_raw')
                or provider_time(receipt.get('openDate')) != broadcast.get('open_at_utc_ms')
                or receipt.get('endedAtMs') != ended or provider_time(receipt.get('closeDate')) != ended
                or not integer(receipt.get('observedAtMs')) or not ended <= receipt['observedAtMs'] <= now):
            return False
        collectors = [db.execute('SELECT started_at_ms,ended_at_ms,end_reason FROM collector_runs '
            'WHERE broadcast_id=? AND source=? ORDER BY run_id DESC LIMIT 1', (identity, source)).fetchone()
            for source in ('chat', 'metadata')]
        if any(row is None or row['started_at_ms'] != status['startedAt'] or row['end_reason'] != 'runtime_shutdown'
                or not integer(row['ended_at_ms']) or not row['started_at_ms'] <= row['ended_at_ms'] <= ended
                for row in collectors):
            return False
        if not integer(status.get('updatedAt')) or status['updatedAt'] < max(row['ended_at_ms'] for row in collectors):
            return False
        metadata = db.execute('SELECT max(observed_at_ms) FROM metadata_observations WHERE broadcast_id=?', (identity,)).fetchone()[0]
        if metadata is not None and metadata > ended:
            return False
        channels = {row[0] for row in db.execute('SELECT DISTINCT chat_channel_id FROM chat_channel_epochs WHERE broadcast_id=?', (identity,))}
        recovery = json.loads(read('vod-recovery-state.json', 2_000_000))
        if (not isinstance(recovery, dict) or recovery.get('archive') != f'{match[1]}-{match[2]}'
                or not re.fullmatch(r'[1-9]\d{0,19}', str(recovery.get('videoNo') or ''))):
            return False
        links = db.execute("SELECT * FROM vod_links WHERE broadcast_id=? AND status='verified' LIMIT 2", (identity,)).fetchall()
        if len(links) != 1:
            return False
        link = dict(links[0])
        if (str(link.get('video_no')) != str(recovery['videoNo']) or link.get('match_proof') != 'channel_start_and_chat'
                or link.get('replay_chat_channel_id') not in channels
                or not integer(link.get('broadcast_offset_ms')) or abs(link['broadcast_offset_ms']) > 2000):
            return False
        row = db.execute('SELECT b.*,r.broadcast_id,r.source,r.ended_at_ms,r.end_reason '
            'FROM backfill_runs b JOIN collector_runs r ON r.run_id=b.collector_run_id '
            'WHERE b.vod_link_id=? ORDER BY b.backfill_id DESC LIMIT 1', (link['vod_link_id'],)).fetchone()
        replay = dict(row) if row else {}
        if (replay.get('status') != 'complete' or replay.get('broadcast_id') != identity or replay.get('source') != 'replay'
                or replay.get('end_reason') != 'replay_complete' or not integer(replay.get('ended_at_ms'))
                or replay['ended_at_ms'] < ended or replay['ended_at_ms'] != replay.get('updated_at_ms')
                or not integer(replay.get('page_count')) or replay['page_count'] < 1):
            return False
        pages = db.execute("SELECT count(*),sum(result_state='complete') FROM replay_pages WHERE backfill_id=?", (replay['backfill_id'],)).fetchone()
        last = db.execute('SELECT * FROM replay_pages WHERE backfill_id=? ORDER BY page_id DESC LIMIT 1', (replay['backfill_id'],)).fetchone()
        return (pages[0] == replay['page_count'] and pages[1] == pages[0] and last is not None
                and last['result_state'] == 'complete' and (last['cursor_next'] is None or last['message_count'] == 0)
                and last['committed_at_ms'] == replay['ended_at_ms'])
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, OverflowError):
        return False

def guard(folder):
    """Keep manual STOP, consent and both original end proofs authoritative."""
    stop = local_file(folder, "STOP").read_text(encoding="utf-8").strip()
    models = local_file(folder, "MODELS_STOP").read_text(encoding="utf-8").strip()
    if stop not in {"broadcast_ended", "broadcast_changed"} or models != stop:
        raise ValueError("finalize_manual_stop")




    try:
        choice_path = local_file(Path(folder), "summary-exclusion.json")
    except ValueError:
        raise ValueError("finalize_manager_stopped") from None
    if choice_path.exists():
        try:
            choice = json.loads(choice_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            choice = None
        if (not isinstance(choice, dict) or choice.get("version") != 1 or type(choice.get("revision")) is not int
                or choice["revision"] < 1 or not isinstance(choice.get("excluded"), bool) or choice["excluded"]):
            raise ValueError("finalize_manager_stopped")
    with closing(sqlite3.connect(local_file(folder, "session.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM broadcasts").fetchall()
        if len(rows) != 1 or rows[0]["state"] != "ended":
            raise ValueError("finalize_end_unverified")
        broadcast = dict(rows[0])
        settings = json.loads(db.execute("SELECT value FROM meta WHERE name='settings'").fetchone()[0])
        if settings.get("aiConsent") is not True:
            raise ValueError("finalize_consent_required")
        collector_end = True
        for source in ("chat", "metadata"):
            row = db.execute("SELECT end_reason FROM collector_runs WHERE broadcast_id=? AND source=? "
                "AND ended_at_ms=? ORDER BY run_id DESC LIMIT 1",
                (broadcast["broadcast_id"], source, broadcast["ended_at_ms"])).fetchone()
            if not row or row[0] != stop:
                collector_end = False
        if not collector_end and not (stop == 'broadcast_ended' and _replay_qualified_handover_end(db, folder, broadcast)):
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
