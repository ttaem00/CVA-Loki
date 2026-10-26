"""Deterministic search projections and immutable, resumable SQLite snapshots."""

from __future__ import annotations

from collections import defaultdict

from contextlib import contextmanager

import json

import os

from pathlib import Path

import re

import sqlite3

import threading

import tempfile

import time

import unicodedata

from urllib.parse import quote

from .contracts import (AssetBinding, EvidenceError, INDEX_SCHEMA, RECORD_SCHEMA, UNIT_POLICY,
                        canonical_json, digest, file_hash, native_path)

from .records import assert_asset_fresh, iter_records

_LOCKS = defaultdict(threading.Lock)

def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

def word_tokens(text):
    return re.findall(r"[^\W_]+", normalize(text), re.UNICODE)

def gram_tokens(text, policy=None):
    compact = "".join(c for c in normalize(text) if c.isalnum())
    return [f"g{size}" + compact[i:i + size].encode("utf-8").hex()
            for size in (policy or UNIT_POLICY)["grams"] for i in range(len(compact) - size + 1)]

def _member(record, role, start=0, end=None):
    return dict(record_id=record["record_id"], raw_char_start=start,
                raw_char_end=len(record["raw_text"]) if end is None else end, role=role)

def _unit(records, members, policy):
    by_id = {r["record_id"]: r for r in records}
    text = "\n".join(by_id[m["record_id"]]["raw_text"][m["raw_char_start"]:m["raw_char_end"]]
                     for m in members)
    clocks = [r["time_index"] for r in records if r["time_index"]]
    return dict(unit_id=digest([members, policy]), family=records[0]["family"],
                start_us=min(t["start_us"] for t in clocks) if clocks else None,
                end_us=max(t["end_us"] for t in clocks) if clocks else None,
                norm_text=normalize(text), word_tokens=" ".join(word_tokens(text)),
                gram_tokens=" ".join(gram_tokens(text, policy)), members=members)

def iter_units(records, policy=None):
    """Finite transport units, not a cap on content or a claim of events."""
    policy = policy or UNIT_POLICY
    rows = list(records)
    cursor = 0
    gap_us = policy["max_gap_sec"] * 1000000
    while cursor < len(rows):
        row = rows[cursor]
        text = row["raw_text"]
        if text is None or not text:
            cursor += 1
            continue
        if len(text) > policy["max_chars"]:
            start = 0
            while start < len(text):
                end = min(start + policy["max_chars"], len(text))
                yield _unit([row], [_member(row, "core", start, end)], policy)
                if end == len(text):
                    break
                start = end - policy["fragment_overlap"]
            cursor += 1
            continue
        if row["family"] != "stt" or row["time_index"] is None:
            yield _unit([row], [_member(row, "core")], policy)
            cursor += 1
            continue
        finish, chars = cursor + 1, len(text)
        low, high = row["time_index"]["start_us"], row["time_index"]["end_us"]
        while finish < len(rows):
            nxt = rows[finish]
            clock = nxt["time_index"]
            previous = rows[finish - 1]["time_index"]
            if (not nxt["raw_text"] or clock is None or nxt["family"] != "stt"
                    or finish - cursor >= policy["max_cues"]
                    or chars + 1 + len(nxt["raw_text"]) > policy["max_chars"]
                    or clock["start_us"] - previous["end_us"] > gap_us
                    or max(high, clock["end_us"]) - min(low, clock["start_us"])
                    > policy["max_duration_sec"] * 1000000):
                break
            chars += 1 + len(nxt["raw_text"])
            low, high = min(low, clock["start_us"]), max(high, clock["end_us"])
            finish += 1
        left, right = cursor, finish
        for _ in range(policy["context_cues"]):
            if (left == 0 or not rows[left - 1]["time_index"] or not rows[left - 1]["raw_text"]
                    or len(rows[left - 1]["raw_text"]) > policy["max_chars"]
                    or rows[left]["time_index"]["start_us"]
                    - rows[left - 1]["time_index"]["end_us"] > gap_us):
                break
            left -= 1
        for _ in range(policy["context_cues"]):
            if (right == len(rows) or not rows[right]["time_index"] or not rows[right]["raw_text"]
                    or len(rows[right]["raw_text"]) > policy["max_chars"]
                    or rows[right]["time_index"]["start_us"]
                    - rows[right - 1]["time_index"]["end_us"] > gap_us):
                break
            right += 1
        yield _unit(rows[left:right], [
            _member(rows[i], "core" if cursor <= i < finish else "context")
            for i in range(left, right)], policy)
        cursor = finish

def atomic_json(path, value):
    """Publish metadata last; no partially written JSON becomes authoritative."""
    path = native_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)

@contextmanager
def build_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = _LOCKS[str(root.resolve())]
    if not lock.acquire(blocking=False):
        raise EvidenceError("world_build_locked")
    try:
        with (root / "build.lock").open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise EvidenceError("world_build_locked") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets(asset_id TEXT PRIMARY KEY, family TEXT NOT NULL, binding_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(
 record_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(asset_id),
 ordinal INTEGER NOT NULL, family TEXT NOT NULL, start_us INTEGER, end_us INTEGER,
 time_kind TEXT, clock_status TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(asset_id,ordinal));
CREATE INDEX IF NOT EXISTS records_time ON records(family,start_us,end_us);
CREATE TABLE IF NOT EXISTS units(
 unit_pk INTEGER PRIMARY KEY, unit_id TEXT UNIQUE NOT NULL, family TEXT NOT NULL,
 start_us INTEGER, end_us INTEGER, norm_text TEXT NOT NULL,
 word_tokens TEXT NOT NULL, gram_tokens TEXT NOT NULL, members_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS build_progress(asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id),
 last_ordinal INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
"""

class SnapshotConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def open_snapshot(root, *, world_revision=None, source_id=None, verify_sources=True):
    root = native_path(root)
    if not (root / "manifest.json").is_file():
        raise EvidenceError("index_not_published")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    revision = manifest["world_revision"]
    if digest(manifest["identity"]) != revision or (world_revision and revision != world_revision):
        raise EvidenceError("world_revision_mismatch")
    if source_id and any(a["source_id"] != source_id for a in manifest["identity"]["assets"]):
        raise EvidenceError("wrong_source")
    if file_hash(root / "world.sqlite") != manifest["database_sha256"]:
        raise EvidenceError("published_index_changed")
    uri = "file:" + quote(str(root / "world.sqlite"), safe="/:") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, factory=SnapshotConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        row = db.execute("SELECT value_json FROM metadata WHERE key='world_revision'").fetchone()
        if not row or json.loads(row[0]) != revision:
            raise EvidenceError("world_revision_mismatch")
        _validate_required_sources(db, manifest["identity"]["policy"]["required"])
        if verify_sources:
            for row in db.execute("SELECT binding_json FROM assets"):
                assert_asset_fresh(AssetBinding(**json.loads(row[0])))
    except Exception:
        db.close()
        raise
    return db

def _validate(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise EvidenceError("index_integrity_failed")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise EvidenceError("index_foreign_key_failed")
    for table in ("word_fts", "gram_fts"):
        db.execute(f"INSERT INTO {table}({table},rank) VALUES('integrity-check',1)")
    return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("records", "units")}

def _validate_required_sources(db, required_families):
    for family in required_families:
        if not db.execute("SELECT 1 FROM records WHERE family=? LIMIT 1", (family,)).fetchone():
            raise EvidenceError("required_source_empty_or_unlocated")


        if not db.execute("SELECT 1 FROM units WHERE family=? AND norm_text<>'' LIMIT 1",
                          (family,)).fetchone():
            raise EvidenceError("required_source_unsearchable")

def _resume_records(db, assets, checkpoint):
    """Checkpoint raw records only; completion does not publish search units."""
    for asset in assets:
        with db:
            db.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?)",
                       (asset.asset_id, asset.family, canonical_json(asset.internal())))
        saved = db.execute("SELECT last_ordinal,status FROM build_progress WHERE asset_id=?",
                           (asset.asset_id,)).fetchone()
        if saved and saved[1] == "records_complete":
            continue
        last = saved[0] if saved else -1
        batch, ordinal = [], last
        for record in iter_records(asset):
            if record["ordinal"] <= last:
                continue
            clock = record["time_index"] or {}
            ordinal = record["ordinal"]
            batch.append((record["record_id"], asset.asset_id, ordinal, asset.family,
                          clock.get("start_us"), clock.get("end_us"), clock.get("kind"),
                          record["clock_status"], canonical_json(record)))
            if len(batch) >= 1000:
                with db:
                    db.executemany("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)", batch)
                    db.execute("INSERT OR REPLACE INTO build_progress VALUES(?,?,?)",
                               (asset.asset_id, ordinal, "records_partial"))
                if checkpoint:
                    checkpoint(asset.asset_id, ordinal)
                batch.clear()
        with db:
            db.executemany("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)", batch)
            db.execute("INSERT OR REPLACE INTO build_progress VALUES(?,?,?)",
                       (asset.asset_id, ordinal, "records_complete"))

def _rebuild_projections(db, assets, unit_policy, revision):
    """Replace units, both FTS indexes and their revision in one transaction."""
    with db:


        db.execute("BEGIN")
        for table in ("word_fts", "gram_fts"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("DELETE FROM units")
        for asset in assets:
            reader = db.execute("SELECT payload_json FROM records WHERE asset_id=? ORDER BY ordinal",
                                (asset.asset_id,))
            if asset.family == "stt":
                units = iter_units((json.loads(r[0]) for r in reader), unit_policy)
            else:

                units = (unit for r in reader for unit in iter_units([json.loads(r[0])], unit_policy))
            for unit in units:
                db.execute("INSERT INTO units(unit_id,family,start_us,end_us,norm_text,word_tokens,"
                           "gram_tokens,members_json) VALUES(?,?,?,?,?,?,?,?)",
                           tuple(unit[k] for k in ("unit_id", "family", "start_us", "end_us",
                                                  "norm_text", "word_tokens", "gram_tokens"))
                           + (canonical_json(unit["members"]),))
        for table, column in (("word_fts", "word_tokens"), ("gram_fts", "gram_tokens")):
            db.execute(f"CREATE VIRTUAL TABLE {table} USING fts5({column},content='units',"
                       "content_rowid='unit_pk',tokenize='unicode61')")
            db.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
        db.execute("INSERT OR REPLACE INTO metadata VALUES('world_revision',?)",
                   (canonical_json(revision),))

def build_snapshot(*, assets, work_dir, policy, unit_policy=None, checkpoint=None):
    """Resume committed records. Rebuild projections transactionally after a crash."""
    unit_policy = unit_policy or UNIT_POLICY
    if (unit_policy["max_chars"] <= unit_policy["fragment_overlap"] or unit_policy["max_cues"] <= 0
            or not unit_policy["grams"] or any(type(n) is not int or n < 2 or n > 3 for n in unit_policy["grams"])
            or any(unit_policy[k] != UNIT_POLICY[k] for k in
                   ("schema", "unicode", "normalizer", "tokenizer", "gram_encoding"))):
        raise EvidenceError("invalid_representation_policy")
    if not assets or len({a.source_id for a in assets}) != 1 or len({a.video_no for a in assets}) != 1:
        raise EvidenceError("wrong_source")
    if {a.family for a in assets} != set(policy.allowed_families):
        raise EvidenceError("policy_binding_mismatch")
    for asset in assets:
        if asset.policy_scope != digest(policy.payload()):
            raise EvidenceError("policy_binding_mismatch")
        if asset.availability == "conflict":
            raise EvidenceError("wrong_source")
        if asset.family in policy.required_families and asset.availability not in {"present", "partial"}:
            raise EvidenceError("required_source_unavailable")
    identity = dict(assets=[a.public() for a in sorted(assets, key=lambda a: a.asset_id)],
                    policy=policy.payload(), records=RECORD_SCHEMA, index=INDEX_SCHEMA,
                    representation=unit_policy)
    revision = digest(identity)
    video_no = assets[0].video_no
    root = native_path(Path(work_dir) / ".evidence_world" / video_no / digest(policy.payload()) / revision)
    started = time.monotonic()
    for asset in assets:
        assert_asset_fresh(asset)
    with build_lock(root):
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["world_revision"] != revision or file_hash(root / "world.sqlite") != manifest["database_sha256"]:
                raise EvidenceError("published_index_changed")
            with open_snapshot(root, world_revision=revision):
                pass
            return root, dict(manifest, reused=True)


        final_path = root / "world.sqlite"
        build_path = final_path if final_path.exists() else root / "build.sqlite"
        db = sqlite3.connect(build_path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA temp_store=FILE")
            db.executescript(SCHEMA_SQL)
            if not final_path.exists():
                _resume_records(db, assets, checkpoint)
                _rebuild_projections(db, assets, unit_policy, revision)
            stored = db.execute("SELECT value_json FROM metadata WHERE key='world_revision'").fetchone()
            if not stored or json.loads(stored[0]) != revision:
                raise EvidenceError("build_revision_mismatch")
            counts = _validate(db)
            _validate_required_sources(db, policy.required_families)
            db.commit()
            for asset in assets:
                assert_asset_fresh(asset)
        finally:
            db.close()
        if not final_path.exists():
            os.replace(build_path, final_path)
        manifest = dict(schema_version=INDEX_SCHEMA, world_revision=revision, identity=identity,
                        counts=counts, database_sha256=file_hash(final_path),
                        build_elapsed_sec=time.monotonic() - started,
                        indexed_source_bytes=sum(a.bytes_sha256 is not None and
                            native_path(a.source_locator["path"]).stat().st_size or 0 for a in assets))
        atomic_json(manifest_path, manifest)
        return root, dict(manifest, reused=False)
