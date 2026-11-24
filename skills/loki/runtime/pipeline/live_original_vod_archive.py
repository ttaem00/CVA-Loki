"""Retain one verified official replay in the existing Loom archive catalogue."""

from __future__ import annotations

from datetime import datetime

import hashlib

import math

import os

from pathlib import Path

import re

import sqlite3

import subprocess

import time

from .evidence_world.contracts import file_hash, native_path

from .evidence_world.temporal_source import _reader

from .vod_search_index import _plain

_VOD_NO = re.compile(r"[1-9]\d{0,15}")

_SHA = re.compile(r"[a-f0-9]{64}")

class OriginalVodUnavailable(ValueError):
    """An existing VOD source or its evidence could not safely be used."""

def _reject(reason):
    raise OriginalVodUnavailable("original_vod_" + reason)

def _path(cold, video_no):
    if not isinstance(video_no, str) or not _VOD_NO.fullmatch(video_no):
        _reject("identity_unverified")
    return _plain(cold / "vod" / (video_no + ".mp4"))

ORIGINAL_ARCHIVE_TABLE_SQL = "SELECT 1 FROM sqlite_master WHERE name='original_vod_archives' AND type='table'"

ORIGINAL_ARCHIVE_ROW_SQL = """SELECT a.video_no, a.broadcast_id, a.open_at_utc_ms, a.duration_ms, a.size_bytes,
        a.sha256, b.channel_uid, b.live_id, b.open_at_utc_ms AS current_open
        FROM original_vod_archives a JOIN broadcasts b USING (broadcast_id) WHERE a.video_no=?"""

def _registered(db, video_no):
    if not db.execute(ORIGINAL_ARCHIVE_TABLE_SQL).fetchone():
        return None
    return db.execute(ORIGINAL_ARCHIVE_ROW_SQL, (video_no,)).fetchone()

def _physical_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

def _fingerprint(path, deadline=None):
    before = path.stat()
    if deadline is None:
        sha = file_hash(path)
    else:
        hashed = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                if time.monotonic() >= deadline:
                    _reject("deadline_exhausted")
                hashed.update(block)
        sha = hashed.hexdigest()
    after = path.stat()
    if _physical_identity(before) != _physical_identity(after):
        _reject("bytes_changed")
    return after.st_size, sha

def _checked_file(cold, row, *, deadline):
    path = _path(cold, row["video_no"])
    if not path.is_file():
        _reject("file_missing")
    if path.stat().st_size != row["size_bytes"] or _fingerprint(path, deadline) != (row["size_bytes"], row["sha256"]):
        _reject("bytes_changed")
    return {"archive": row["channel_uid"] + "-" + row["live_id"], "relative": path.relative_to(cold).as_posix(),
            "duration": row["duration_ms"] / 1_000, "size": row["size_bytes"], "sha256": row["sha256"],
            "format": "original"}

def _registered_identity_matches(row, video_no):
    return (row["video_no"] == video_no
            and re.fullmatch(r"[a-f0-9]{32}", str(row["channel_uid"]))
            and _VOD_NO.fullmatch(str(row["live_id"]))
            and row["broadcast_id"] == f'chzzk:{row["channel_uid"]}:{row["live_id"]}'
            and type(row["open_at_utc_ms"]) is int and row["open_at_utc_ms"] > 0
            and row["current_open"] == row["open_at_utc_ms"]
            and type(row["duration_ms"]) is int and row["duration_ms"] > 0
            and _SHA.fullmatch(str(row["sha256"]))
            and type(row["size_bytes"]) is int and row["size_bytes"] > 0)

def verify_saved_original_registration(cold, row, *, video_no, expected_sha256, deadline):


    if not _registered_identity_matches(row, video_no) or row["sha256"] != expected_sha256:
        _reject("identity_unverified")
    return _checked_file(cold, row, deadline=deadline)

def registered_original_binding(generation, cold, video_no):
    """Return the existing catalogue binding for a private playback owner."""
    if not isinstance(video_no, str) or not _VOD_NO.fullmatch(video_no):
        return None
    catalog = _plain(generation / "catalog.sqlite")
    if not catalog.is_file():
        return None
    with _reader(catalog) as db:
        row = _registered(db, video_no)
    if row is None:
        return None
    if not _registered_identity_matches(row, video_no):
        _reject("identity_unverified")
    return _path(cold, video_no), row["size_bytes"], row["sha256"], row["duration_ms"] / 1000

def resolve_saved_original(path, *, video_no, work_dir, expected_sha256, deadline=None):
    """Resolve one frozen full MP4 through the existing Loom owner at read time."""
    if (not isinstance(video_no, str) or not _VOD_NO.fullmatch(video_no)
            or not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256)):
        return None
    try:
        if not path or not Path(path).is_absolute():
            return None
        work = _plain(native_path(work_dir))
        original = _plain(native_path(path))
        if (original.parent != work / video_no or original.suffix.lower() != ".mp4"
                or original.exists()):
            return None
        generation = work / "live-engine"
        catalog = generation / "catalog.sqlite"
        if not catalog.is_file():
            return None
        now = time.monotonic()
        deadline = now + 120 if deadline is None else min(deadline, now + 120)
        if deadline <= now:
            return None
        if os.name == "nt":
            from .dayline_origin_cold import ColdInputUnavailable, _container, _native_original

            try:
                owner, cold = _container(work.parent)
                result = _native_original(owner, cold, {"vodNo": video_no}, deadline,
                                          expected_sha256=expected_sha256)
            except ColdInputUnavailable:
                return None
            return _path(cold, video_no) if result else None
        mounted = os.getenv("CHZZ_COLD_ARCHIVE_ROOT")
        if not mounted:
            return None
        cold = _plain(mounted)
        with _reader(_plain(catalog)) as db:
            row = _registered(db, video_no)
        if row is None:
            return None
        verify_saved_original_registration(cold, row, video_no=video_no,
                                          expected_sha256=expected_sha256, deadline=deadline)
        return _path(cold, video_no)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError):


        return None

def verify_original_registration(cold, row, *, source, deadline=None):
    try:
        opened = datetime.fromisoformat(source["liveOpenAt"])
        source_open = round(opened.timestamp() * 1_000) if opened.utcoffset() is not None else None
    except (KeyError, TypeError, ValueError, OverflowError):
        _reject("identity_unverified")
    duration = source.get("duration")
    if (not _registered_identity_matches(row, source.get("vodNo")) or row["channel_uid"] != source.get("channelId")
            or source_open is None or abs(source_open - row["open_at_utc_ms"]) > 2_000
            or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
            or abs(duration * 1_000 - row["duration_ms"]) > 2_000):
        _reject("identity_unverified")
    return _checked_file(cold, row, deadline=deadline)
