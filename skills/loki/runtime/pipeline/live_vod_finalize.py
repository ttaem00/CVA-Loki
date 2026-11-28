"""Continue a naturally ended live archive through its pinned normal version.

The live host owns scheduling and STOP. This helper reuses verified originals,
never writes live summary/ledger rows, and never retries an unknown full job.
"""

from __future__ import annotations

from contextlib import closing

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone

import errno

import hashlib

import json

import os

from pathlib import Path

import re

import sqlite3

import stat

import time

from .config import FULL_AUTHOR_PIPELINES

from .editorial_contract import LIVE_VOD_CODEX_PIN_SCOPE

from .live_vod_repair import local_file, sha

from .live_editorial_source import _cues

POLICY = "missing-only-vod301-v1"

DEFAULT_SUMMARY_VERSION = "loki_3_0_1"

SUMMARY_VERSIONS = frozenset({"pipeline_2_5", DEFAULT_SUMMARY_VERSION, *FULL_AUTHOR_PIPELINES})

_LIVE_VOD_CODEX_PIN = "_" + LIVE_VOD_CODEX_PIN_SCOPE

_STANDING_CODEX_APPROVALS = "unattended_vod_codex_approvals"

_AUTHOR_PIN_OWNER = object()

_QUEUED_GENERATION = "_live_queued_summary_generation"

_QUEUED_GENERATION_FIELDS = (
    "id", "archive", "videoNo", "parentVersion", "targetVersion", "parentGenerationId",
    "parentPageSha256", "sourceRevision", "sourceSha256", "requestedAtMs",
)

@dataclass(frozen=True, slots=True)
class _QueuedSummaryGeneration:
    id: str
    archive: str
    video_no: str
    parent_version: str
    target_version: str
    parent_generation_id: str
    parent_page_sha256: str
    source_revision: str
    source_sha256: str
    requested_at_ms: int

def _queued_summary_generation(folder, generation_id, *, read_committed=False):
    if not isinstance(generation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", generation_id):
        raise ValueError("finalize_queued_generation_invalid")
    path = local_file(folder, "vod-summary-queue.json")
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("finalize_queued_generation_invalid")
    queue = json.loads(path.read_bytes())
    if (not isinstance(queue, dict) or type(queue.get("version")) is not int or queue["version"] != 1
            or not isinstance(queue.get("jobs"), list)):
        raise ValueError("finalize_queued_generation_invalid")
    matches = [job for job in queue["jobs"] if isinstance(job, dict) and job.get("id") == generation_id]
    if len(matches) != 1:
        raise ValueError("finalize_queued_generation_invalid")
    job = matches[0]
    if (job.get("state") != "running" or job.get("archive") != folder.name
            or not all(isinstance(job.get(key), str) for key in _QUEUED_GENERATION_FIELDS[:-1])
            or not re.fullmatch(r"[1-9]\d{0,11}", job["videoNo"])
            or job["parentVersion"] not in SUMMARY_VERSIONS or job["targetVersion"] not in FULL_AUTHOR_PIPELINES
            or job["parentVersion"] == job["targetVersion"]
            or not re.fullmatch(r"\d{8}T\d{12}Z-[a-f0-9]{12}", job["parentGenerationId"])
            or any(not re.fullmatch(r"[a-f0-9]{64}", job[key])
                   for key in ("parentPageSha256", "sourceRevision", "sourceSha256"))
            or type(job.get("requestedAtMs")) is not int or not 0 < job["requestedAtMs"] < 2**53):
        raise ValueError("finalize_queued_generation_invalid")
    identity = "\n".join((job["archive"], job["parentPageSha256"], job["targetVersion"]))
    if hashlib.sha256(identity.encode("ascii")).hexdigest()[:32] != generation_id:
        raise ValueError("finalize_queued_generation_invalid")
    if "stopRequestedAtMs" in job:
        stopped = job["stopRequestedAtMs"]
        if type(stopped) is not int or not job["requestedAtMs"] <= stopped < 2**53:
            raise ValueError("finalize_queued_generation_invalid")
        if not read_committed:
            raise ValueError("finalize_manager_stopped")
    return _QueuedSummaryGeneration(*(job[key] for key in _QUEUED_GENERATION_FIELDS))

def _queued_summary_from_config(cfg):
    generation = cfg.get(_QUEUED_GENERATION)
    if generation is not None and (
            type(generation) is not _QueuedSummaryGeneration
            or generation.archive != cfg.get("live_finalize_archive")
            or generation.target_version != cfg.get("summary_pipeline_version")):
        raise ValueError("finalize_queued_generation_invalid")
    return generation

@dataclass(frozen=True, slots=True)
class _LiveVODCodexAuthorPin:
    archive: str
    video_no: str
    source_revision: str
    pipeline_version: str
    model: str
    authorization_id: str
    not_after: datetime | None
    owner: object

    def __deepcopy__(self, memo):
        return self

    def record(self):
        return dict(schemaVersion=1, archive=self.archive, channelId=self.archive.split("-", 1)[0],
            videoNo=self.video_no, sourceRevision=self.source_revision, summaryPipelineVersion=self.pipeline_version,
            provider="codex_cli", model=self.model, authorizationId=self.authorization_id,
            notAfter=self.not_after.isoformat() if self.not_after is not None else None)

def _codex_expiry(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if expiry.tzinfo is not None:
                return expiry.astimezone(timezone.utc)
    raise ValueError("finalize_codex_approval_invalid")

def _standing_codex_approval(cfg, channel):
    approvals = cfg.get(_STANDING_CODEX_APPROVALS, [])
    if not isinstance(approvals, list):
        raise ValueError("finalize_codex_approval_invalid")
    matches = [row for row in approvals if isinstance(row, dict) and row.get("channel_id") == channel]
    if not matches:
        return None
    row = matches[0]
    if (len(matches) != 1 or not set(row) <= {"channel_id", "authorization_id", "not_after"}
            or not isinstance(row.get("authorization_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", row["authorization_id"])):
        raise ValueError("finalize_codex_approval_invalid")
    return row["authorization_id"], _codex_expiry(row.get("not_after"))

def _current_codex_approval(channel):
    from .config import _config_path

    try:
        settings = json.loads(_config_path().read_bytes())
    except (OSError, ValueError):
        raise OSError(errno.EAGAIN, "live writer approval temporarily unavailable") from None
    if not isinstance(settings, dict):
        raise OSError(errno.EAGAIN, "live writer approval temporarily unavailable")
    return _standing_codex_approval(settings, channel)

def live_vod_codex_author_pin(cfg, *, video_no=None):
    """Read typed authority after the existing owner has bound the official source."""
    if _LIVE_VOD_CODEX_PIN not in cfg:
        return None
    pin = cfg[_LIVE_VOD_CODEX_PIN]
    source = cfg.get("live_finalize_source")
    if (type(pin) is not _LiveVODCodexAuthorPin or pin.owner is not _AUTHOR_PIN_OWNER
            or pin.archive != cfg.get("live_finalize_archive") or pin.pipeline_version != cfg.get("summary_pipeline_version")
            or cfg.get("summary_author_mode") != "api_direct"
            or video_no is not None and pin.video_no != str(video_no)
            or not isinstance(source, dict) or source.get("source_revision") != pin.source_revision):
        raise ValueError("finalize_codex_approval_invalid")
    return pin

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

def _summary_choice(folder):
    try:
        path = local_file(Path(folder), "summary-exclusion.json")
        if not path.exists():
            return {}
        choice = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        choice = None
    if (not isinstance(choice, dict) or choice.get("version") != 1
            or type(choice.get("revision")) is not int or choice["revision"] < 1
            or not isinstance(choice.get("excluded"), bool)):
        raise ValueError("finalize_manager_stopped")
    return choice

def _codex_resume_grant(folder, revision, choice):
    from .editorial_candidate_budget import REPORTED_USAGE_STOP

    grant = choice.get("codexResume")
    required = {"videoNo", "summaryPipelineVersion", "sourceRevision", "heldPageSha256", "accounting"}
    if (type(revision) is not int or revision <= 0 or choice.get("revision") != revision
            or choice.get("archive") != folder.name or choice.get("excluded") is not True
            or not isinstance(grant, dict)
            or not required <= set(grant) <= required | {"settledFromRevision", "recoverCollectedScene",
                                                       "rejectedCollectedSceneProof"}
            or "rejectedCollectedSceneProof" in grant and (grant.get("recoverCollectedScene") is not True
                or not isinstance(grant["rejectedCollectedSceneProof"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", grant["rejectedCollectedSceneProof"]))
            or "recoverCollectedScene" in grant and (grant["recoverCollectedScene"] is not True
                or "settledFromRevision" not in grant)
            or "settledFromRevision" in grant and (type(grant["settledFromRevision"]) is not int
                or not 0 < grant["settledFromRevision"] == revision - 1)
            or grant["accounting"] != REPORTED_USAGE_STOP
            or not isinstance(grant["summaryPipelineVersion"], str)
            or grant["summaryPipelineVersion"] not in FULL_AUTHOR_PIPELINES
            or not isinstance(grant["videoNo"], str) or not re.fullmatch(r"[1-9]\d{0,11}", grant["videoNo"])
            or not all(isinstance(grant[key], str) and re.fullmatch(r"[a-f0-9]{64}", grant[key])
                       for key in ("sourceRevision", "heldPageSha256"))):
        raise ValueError("finalize_manager_stopped")
    return grant

def _approved_summary_intent(folder, broadcast, settings):
    intent = settings.get("analysisIntent")
    if not isinstance(intent, dict):
        return None
    first, ended = broadcast.get("first_capture_at_ms"), broadcast.get("ended_at_ms")
    started = intent.get("startedAtMs")
    if (type(intent.get("schemaVersion")) is int and intent["schemaVersion"] == 1
            and intent.get("archive") == folder.name
            and intent.get("aiConsent") is True
            and intent.get("finalSummaryEnabled", intent.get("summaryEnabled")) is True
            and type(started) is int and type(first) is int and type(ended) is int
            and 0 < first <= started <= ended
            and broadcast.get("broadcast_id") == "chzzk:" + folder.name.replace("-", ":", 1)):
        return intent
    return None

def _reapproved_summary_matches(folder, broadcast, intent, recovery, selected_version):
    """Read proof of a later final-only approval without modifying capture consent."""
    if (not intent or intent.get("finalSummaryEnabled") is not True
            or not isinstance(recovery, dict) or recovery.get("archive") != folder.name
            or recovery.get("summaryPipelineVersion") != selected_version):
        return False
    chain = recovery.get("summaryVersionReapprovals")
    previous = intent.get("summaryPipelineVersion")
    if not isinstance(chain, list) or not chain or not isinstance(previous, str) or previous not in SUMMARY_VERSIONS:
        return False
    approved_at = broadcast["ended_at_ms"]
    fields = {"archive", "previousVersion", "targetVersion", "intentStartedAtMs", "approvedAtMs"}
    for approval in chain:
        if not isinstance(approval, dict) or set(approval) != fields:
            return False
        target, when = approval["targetVersion"], approval["approvedAtMs"]
        if (approval["archive"] != folder.name or approval["previousVersion"] != previous
                or type(approval["intentStartedAtMs"]) is not int
                or approval["intentStartedAtMs"] != intent["startedAtMs"]
                or not isinstance(target, str) or target not in SUMMARY_VERSIONS or target == previous
                or type(when) is not int or not approved_at <= when < 2**53):
            return False
        previous, approved_at = target, when
    return previous == selected_version

def _summary_consent(folder, broadcast, settings, *, selected_version=None):
    recorded = settings.get("analysisIntent")
    if isinstance(recorded, dict) and "finalSummaryEnabled" in recorded:
        intent = _approved_summary_intent(folder, broadcast, settings)
        version = (intent or {}).get("summaryPipelineVersion")
        if not isinstance(version, str) or version not in SUMMARY_VERSIONS:
            return False
        if selected_version is None or version == selected_version:
            return True
        try:
            path = local_file(folder, "vod-recovery-state.json")
            if not path.is_file() or path.stat().st_size > 2_000_000:
                return False
            recovery = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return _reapproved_summary_matches(folder, broadcast, intent, recovery, selected_version)
    if settings.get("aiConsent") is True:
        return True


    intent = _approved_summary_intent(folder, broadcast, settings)
    pending = settings.get("analysisPreparation")
    version = (intent or {}).get("summaryPipelineVersion")
    if (not isinstance(version, str) or version not in SUMMARY_VERSIONS
            or type(intent.get("runTokenCap")) is not int or not 1 <= intent["runTokenCap"] <= 14_000_000
            or not isinstance(pending, dict) or type(pending.get("schemaVersion")) is not int
            or pending["schemaVersion"] != 1 or pending.get("state") not in ("pending", "held", "applying")
            or any(type(pending.get(key)) is not int or not 0 <= pending[key] < 2**53
                   for key in ("collectionRevision", "reviewRevision", "attempt"))
            or pending["attempt"] >= 2**53 - 1
            or "targetRevision" not in pending
            or pending["targetRevision"] is not None and
                (type(pending["targetRevision"]) is not int or not 0 <= pending["targetRevision"] < 2**53)
            or "selectedTargetPolicyDigest" in pending and
                (not isinstance(pending["selectedTargetPolicyDigest"], str) or
                 not re.fullmatch(r"[a-f0-9]{64}", pending["selectedTargetPolicyDigest"]))
            or not isinstance(pending.get("reason"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", pending["reason"])):
        return False
    basis = [False, settings.get("liveSummaryEnabled") is True, settings.get("chatReviewEnabled") is True,
             settings.get("liveRunTokenCap"), settings.get("analysisHold")]
    return pending.get("basis") == json.dumps(basis, ensure_ascii=False, separators=(",", ":"))

def guard(folder, *, codex_resume_revision=None, selected_version=None, read_natural_summary_intent=False,
          verify_end=True, queued_generation=None, read_committed=False):
    'Keep manual STOP, consent and both original end proofs authoritative.\n\n    The end proof is an admission fact: an ended broadcast stays ended, so per-send checks\n    (``verify_end=False``) keep STOP, exclusion, grant, consent and approval but do not reread\n    status/replay pages that may legitimately change mid-run.\n    '
    stop = local_file(folder, "STOP").read_text(encoding="utf-8").strip()
    models = local_file(folder, "MODELS_STOP").read_text(encoding="utf-8").strip()
    if stop not in {"broadcast_ended", "broadcast_changed"} or models != stop:
        raise ValueError("finalize_manual_stop")




    choice = _summary_choice(folder)
    if codex_resume_revision is not None:
        _codex_resume_grant(folder, codex_resume_revision, choice)
    elif choice.get("excluded"):
        raise ValueError("finalize_manager_stopped")
    if queued_generation is not None:
        if (selected_version != queued_generation.target_version
                or _queued_summary_generation(folder, queued_generation.id,
                                               read_committed=read_committed) != queued_generation
                or sha(local_file(folder, "vod-final-page.json")) != queued_generation.parent_page_sha256):
            raise ValueError("finalize_queued_generation_changed")
        selected_version = queued_generation.parent_version
    with closing(sqlite3.connect(local_file(folder, "session.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM broadcasts").fetchall()
        if len(rows) != 1 or rows[0]["state"] != "ended":
            raise ValueError("finalize_end_unverified")
        broadcast = dict(rows[0])
        settings = json.loads(db.execute("SELECT value FROM meta WHERE name='settings'").fetchone()[0])
        if not _summary_consent(folder, broadcast, settings, selected_version=selected_version):
            raise ValueError("finalize_consent_required")
        if queued_generation is not None and queued_generation.requested_at_ms < broadcast["ended_at_ms"]:
            raise ValueError("finalize_queued_generation_invalid")
        if verify_end:
            collector_end = True
            for source in ("chat", "metadata"):
                row = db.execute("SELECT end_reason FROM collector_runs WHERE broadcast_id=? AND source=? "
                    "AND ended_at_ms=? ORDER BY run_id DESC LIMIT 1",
                    (broadcast["broadcast_id"], source, broadcast["ended_at_ms"])).fetchone()
                if not row or row[0] != stop:
                    collector_end = False
            if not collector_end and not (stop == 'broadcast_ended' and _replay_qualified_handover_end(db, folder, broadcast)):
                raise ValueError("finalize_end_unverified")
        if read_natural_summary_intent:
            intent = _approved_summary_intent(folder, broadcast, settings)
            return stop == "broadcast_ended" and bool(intent and intent.get("finalSummaryEnabled") is True)
        return broadcast

def check_dispatch(cfg, *, verify_author_approval=True):
    """Check STOP for source reads and the pinned grant for new sends."""
    archive = cfg.get("live_finalize_archive")
    if not archive:
        return
    revision = cfg.get("_live_finalize_codex_revision") if cfg.get("_live_finalize_codex_only") is True else None
    folder = archive_folder(archive)
    queued_generation = _queued_summary_from_config(cfg)
    pin = live_vod_codex_author_pin(cfg)
    if pin is None or not verify_author_approval:
        guard(folder, codex_resume_revision=revision, selected_version=cfg.get("summary_pipeline_version"),
              verify_end=False, queued_generation=queued_generation)
        return
    if not guard(folder, selected_version=pin.pipeline_version, read_natural_summary_intent=True,
                 verify_end=False, queued_generation=queued_generation):
        raise ValueError("finalize_codex_approval_changed")
    if pin.not_after is not None and datetime.now(timezone.utc) >= pin.not_after:
        raise ValueError("finalize_codex_approval_expired")
    if _current_codex_approval(archive.split("-", 1)[0]) != (pin.authorization_id, pin.not_after):
        raise ValueError("finalize_codex_approval_changed")

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
    if _LIVE_VOD_CODEX_PIN in cfg:
        check_dispatch(cfg, verify_author_approval=False)
    else:
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
        revision = cfg.get("_live_finalize_codex_revision")
        broadcast = guard(folder, codex_resume_revision=revision) if revision is not None else guard(folder)
        expected_clock = dict(archive_id=archive, broadcast_start_at_ms=broadcast["open_at_utc_ms"])
        if isinstance(clock, dict) and "context_as_of_ms" in clock:
            expected_clock["context_as_of_ms"] = mapping["originMs"] + max(end for _, end, _ in _cues(output))
        if (clock != expected_clock
                or broadcast["open_at_confidence"] != "provider"):
            raise ValueError("finalize_context_clock_changed")
        load_live_manager_context(output, source_clock=clock, binding=selected["manager_context"])
    return str(output)
