"""Read-only Temporal Loom materialization into the existing Evidence World.

The caller supplies a registered generation root and exact broadcast identity.
No source migration, directory discovery, VOD alias inference or model calls.
"""

from __future__ import annotations

from contextlib import contextmanager

import json

from pathlib import Path

import re

import shutil

import sqlite3

import subprocess

from .contracts import AssetBinding, EvidenceError, canonical_json, digest, file_hash, native_path

from .index import atomic_json, build_lock

REVISION = "ew.temporal-source.v1"

@contextmanager
def _reader(path):
    if not path.is_file():
        raise EvidenceError("temporal_database_missing")
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        raise EvidenceError("temporal_database_unavailable") from exc
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    except sqlite3.Error as exc:
        raise EvidenceError("temporal_database_unavailable") from exc
    finally:
        db.rollback()
        db.close()

def _require(db, table, columns):
    actual = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    if not set(columns) <= actual:
        raise EvidenceError("temporal_schema_unsupported:" + table)

def _identity(channel_uid, live_id):
    channel_uid, live_id = str(channel_uid).lower(), str(live_id)
    if not re.fullmatch(r"[a-f0-9]{32}", channel_uid) or not re.fullmatch(r"\d+", live_id):
        raise EvidenceError("invalid_broadcast_identity")
    return channel_uid, live_id, f"chzzk:{channel_uid}:{live_id}"

def resolve_session(*, generation_root, channel_uid, live_id, cold_archive_root=None, node_executable=None):
    """Catalog registration and producer's modern/legacy ambiguity rule."""
    channel_uid, live_id, source_id = _identity(channel_uid, live_id)
    try:
        root = Path(generation_root).resolve(strict=True)
        catalog = (root / "catalog.sqlite").resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("temporal_catalog_missing") from exc
    if not catalog.is_relative_to(root):
        raise EvidenceError("temporal_catalog_path_escape")
    with _reader(catalog) as db:
        _require(db, "broadcasts", ("broadcast_id", "channel_uid", "live_id", "session_relpath"))
        rows = db.execute("SELECT broadcast_id,channel_uid,live_id,session_relpath FROM broadcasts "
                          "WHERE broadcast_id=?", (source_id,)).fetchall()
        if len(rows) != 1 or rows[0]["channel_uid"] != channel_uid or rows[0]["live_id"] != live_id:
            raise EvidenceError("temporal_catalog_identity_mismatch")
        rel = rows[0]["session_relpath"]
        managed = False
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_locations'").fetchone():
            _require(db, 'archive_locations', ('broadcast_id','root_id','location_revision','receipt_json'))
            managed = bool(db.execute('SELECT 1 FROM archive_locations WHERE broadcast_id=?', (source_id,)).fetchone())
    if rel not in {f"live/{channel_uid}-{live_id}", f"sessions/{channel_uid}-{live_id}"}:
        raise EvidenceError("temporal_session_path_invalid")
    if managed:


        executable = node_executable or shutil.which('node')
        if not executable:
            raise EvidenceError('loom_hints_node_unavailable')
        if cold_archive_root is not None and not Path(cold_archive_root).is_absolute():
            raise EvidenceError('storage_root_absolute_required')
        command = [str(executable), str(Path(__file__).with_name('loom_location.mjs')),
                   str(root), f'{channel_uid}-{live_id}']
        if cold_archive_root is not None:
            command.append(str(cold_archive_root))
        try:
            result = subprocess.run(command, capture_output=True, timeout=30, check=False)
            if len(result.stdout) > 16000:
                raise EvidenceError('temporal_location_unbounded')
            value = json.loads(result.stdout.decode('utf8'))
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
            raise EvidenceError('temporal_database_unavailable') from exc
        if result.returncode or not isinstance(value, dict) or value.get('error'):
            reason = value.get('error') if isinstance(value, dict) else None
            raise EvidenceError(reason if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{1,80}',reason)
                                else 'temporal_database_unavailable')
        if value.get('schema_version') != 'ew.loom-location.v1' or value.get('source_id') != source_id:
            raise EvidenceError('temporal_catalog_identity_mismatch')
        try:
            selected = Path(value['database']).resolve(strict=True)
            expected = Path(cold_archive_root) / f'{channel_uid}-{live_id}' if value['root_id'] == 'cold' else root / rel
            expected = expected.resolve(strict=True)
        except (OSError, TypeError, KeyError) as exc:
            raise EvidenceError('temporal_database_unavailable') from exc
        if selected.parent != expected:
            raise EvidenceError('temporal_database_path_escape')
        return root, selected, source_id
    try:
        session = (root / rel).resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("temporal_session_missing") from exc
    if not session.is_relative_to(root):
        raise EvidenceError("temporal_session_path_escape")
    modern, legacy = session / "session.sqlite", session / "chat.sqlite"
    if modern.exists() and legacy.exists():
        raise EvidenceError("ambiguous_live_session_database")
    selected = modern if modern.exists() else legacy
    try:
        selected = selected.resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("temporal_database_missing") from exc
    if not selected.is_relative_to(session):
        raise EvidenceError("temporal_database_path_escape")
    return root, selected, source_id

def materialize(*, generation_root, channel_uid, live_id, derived_root, policy,
                cold_archive_root=None, node_executable=None):
    """One short WAL-aware read transaction; publish only the allowed projection.

    Epoch times remain searchable original text. They are not VOD seek times.
    Content hashes include corrections/deletions, not just maximum row IDs.
    """
    if not set(policy.allowed_families) <= {"stt", "chat", "live_metadata"}:
        raise EvidenceError("temporal_family_unsupported")
    root, database, source_id = resolve_session(generation_root=generation_root,
        channel_uid=channel_uid, live_id=live_id, cold_archive_root=cold_archive_root,
        node_executable=node_executable)
    derived = Path(derived_root).resolve()
    if derived.is_relative_to(root) or cold_archive_root is not None and derived.is_relative_to(Path(cold_archive_root).resolve()):
        raise EvidenceError("temporal_derived_root_overlaps_producer")
    with _reader(database) as db:
        _require(db, "broadcasts", ("broadcast_id", "channel_uid", "live_id", "open_at_utc_ms",
                                    "open_at_source", "open_at_confidence", "first_capture_at_ms", "state"))
        broadcasts = db.execute("SELECT broadcast_id,channel_uid,live_id,open_at_utc_ms,open_at_source,"
            "open_at_confidence,first_capture_at_ms,state FROM broadcasts").fetchall()
        if len(broadcasts) != 1 or broadcasts[0]["broadcast_id"] != source_id or (
                broadcasts[0]["channel_uid"], broadcasts[0]["live_id"]) != (str(channel_uid).lower(), str(live_id)):
            raise EvidenceError("temporal_session_identity_mismatch")
        broadcast = dict(broadcasts[0])
        families, capabilities = {}, {}
        if "stt" in policy.allowed_families:
            _require(db, "transcript_segments", ("transcript_id", "job_id", "audio_id", "segment_index",
                "start_at_ms", "end_at_ms", "source_start_ms", "source_end_ms", "text", "time_accuracy"))
            _require(db, "audio_chunks", ("audio_id", "broadcast_id"))
            _require(db, "transcription_jobs", ("job_id", "audio_id", "status", "model_id", "model_revision"))
            families["stt"] = [dict(row) for row in db.execute(
                "SELECT t.transcript_id,t.job_id,t.audio_id,t.segment_index,t.start_at_ms,t.end_at_ms,"
                "t.source_start_ms,t.source_end_ms,t.text,t.time_accuracy,j.model_id,j.model_revision "
                "FROM transcript_segments t JOIN audio_chunks a ON a.audio_id=t.audio_id "
                "JOIN transcription_jobs j ON j.job_id=t.job_id AND j.audio_id=t.audio_id "
                "WHERE a.broadcast_id=? AND j.status='done' ORDER BY t.transcript_id", (source_id,))]
            capabilities["stt"] = "completed_transcripts_only"
        if "chat" in policy.allowed_families:
            _require(db, "chat_messages", ("message_pk", "broadcast_id", "sent_at_ms", "text", "hidden",
                                           "time_source", "event_kind"))
            families["chat"] = [dict(row) for row in db.execute(
                "SELECT message_pk,sent_at_ms,text,time_source,event_kind FROM chat_messages "
                "WHERE broadcast_id=? AND hidden=0 ORDER BY message_pk", (source_id,))]
            capabilities["chat"] = "visible_messages_without_viewer_identity"
        if "live_metadata" in policy.allowed_families:


            fields = {
                'metadata_observations': ('observation_id','broadcast_id','observed_at_ms','source',
                    'title','category_type','category_code','game_value','viewer_count'),
                'metadata_changes': ('change_id','broadcast_id','changed_field','previous_observation_id',
                    'new_observation_id','old_value','new_value','earliest_change_at_ms','latest_change_at_ms')}
            supported = all(set(columns) <= {r[1] for r in db.execute('PRAGMA table_info('+table+')')}
                            for table,columns in fields.items())
            families['live_metadata'] = []
            capabilities['live_metadata'] = 'public_observations_and_changes' if supported else 'metadata_schema_unavailable'
            if supported:
                for table,columns in fields.items():
                    safe = [c for c in columns if c != 'broadcast_id']
                    sql = 'SELECT '+','.join(safe)+' FROM '+table+' WHERE broadcast_id=?'
                    if table == 'metadata_changes':
                        sql += " AND changed_field IN ('title','category_type','category_code','game_value')"
                    sql += ' ORDER BY '+safe[0]
                    families['live_metadata'].extend(dict(row,_collection=table) for row in db.execute(sql,(source_id,)))

        coverage_columns = [row[1] for row in db.execute('PRAGMA table_info("coverage_intervals")')]
        safe = [col for col in ("interval_id", "from_ms", "until_ms", "start_at_ms", "end_at_ms",
                               "status", "kind", "reason") if col in coverage_columns]
        coverage = [dict(row) for row in db.execute(
            "SELECT " + ",".join(safe) + " FROM coverage_intervals WHERE broadcast_id=? ORDER BY interval_id",
            (source_id,))] if "broadcast_id" in coverage_columns and "interval_id" in safe else []


    _, current_database, _ = resolve_session(generation_root=generation_root,
        channel_uid=channel_uid, live_id=live_id, cold_archive_root=cold_archive_root,
        node_executable=node_executable)
    if current_database != database:
        raise EvidenceError('storage_location_changed')

    payload = dict(schema_version=REVISION, source_id=source_id, broadcast=broadcast,
                   capabilities=capabilities, coverage=coverage, families=families,
                   vod_identity=None, clock_status="epoch_only_vod_mapping_unverified")
    revision = digest(payload)
    projection_root = native_path(derived / "temporal-source" / digest(source_id) / revision)
    path = projection_root / "source.json"
    with build_lock(native_path(projection_root)):
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != payload:
                raise EvidenceError("temporal_projection_changed")
        else:
            atomic_json(path, payload)
    sha = file_hash(path)
    clock = dict(source_id=source_id, duration_sec_decimal=None, authority="epoch_only_no_vod_mapping",
                 time_domain="utc_epoch_ms", vod_seek_verified=False)
    assets = [AssetBinding(
        digest([source_id, family, revision, REVISION]), source_id, "", family, "temporal:primary",
        dict(path=str(path), kind="temporal_projection", source_revision=revision), sha, REVISION,
        "unsupported" if capabilities[family] == 'metadata_schema_unavailable' else "partial", dict(source_revision=revision, capability=capabilities[family],
                        row_count=len(families[family]), coverage_status="observed_not_complete"),
        clock, digest(policy.payload())) for family in policy.allowed_families]
    return assets, dict(source_id=source_id, source_revision=revision, database=str(database),
                        projection=str(path), families={f: len(rows) for f, rows in families.items()},
                        clock_status=payload["clock_status"])

def iter_temporal_records(asset):
    from .records import _record, assert_asset_fresh
    assert_asset_fresh(asset)
    payload = json.loads(native_path(asset.source_locator["path"]).read_text(encoding="utf-8"))
    for ordinal, row in enumerate(payload["families"][asset.family]):
        table,key,start,end,text = _native_fields(asset.family,row)
        locator = dict(kind="temporal_row", table=table,
                       primary_key=row[key], row_ordinal=ordinal, source_revision=asset.source_locator["source_revision"])
        raw_time = dict(unit="utc_epoch_ms", start=start,end=end)
        record = _record(asset, ordinal, locator, digest(row), text, raw_time,
                         None, "unknown", payload=row, warnings=["vod_clock_unverified"])
        record["transform"] = dict(producer="temporal_source", revision=REVISION, model=None)
        yield record

def read_temporal(asset, record, payload):
    rows = payload["families"][asset.family]
    locator = record["native_locator"]
    row = rows[locator["row_ordinal"]]
    table,key,_,_,text = _native_fields(asset.family,row)
    if table != locator['table'] or row[key] != locator["primary_key"] or digest(row) != record["raw_payload_sha256"]:
        raise EvidenceError("raw_payload_changed")
    return dict(raw_text=text, raw_payload=row,
                bytes_read=len(canonical_json(row).encode("utf-8")))

def _native_fields(family,row):
    if family == 'stt':
        return 'transcript_segments','transcript_id',row.get('start_at_ms'),row.get('end_at_ms'),row['text']
    if family == 'chat':
        return 'chat_messages','message_pk',row.get('sent_at_ms'),row.get('sent_at_ms'),row['text']
    if family == 'live_metadata':
        table=row['_collection']
        if table == 'metadata_observations':
            return table,'observation_id',row.get('observed_at_ms'),row.get('observed_at_ms'),canonical_json(row)
        if table == 'metadata_changes':
            return table,'change_id',row.get('earliest_change_at_ms'),row.get('latest_change_at_ms'),canonical_json(row)
    raise EvidenceError('unsupported_native_locator')
