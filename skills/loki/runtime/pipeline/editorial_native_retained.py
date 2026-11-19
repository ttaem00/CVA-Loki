"""Open exact Windows saved-job material through the active Loom publication."""

from contextlib import contextmanager

from hashlib import file_digest

import json

import os

from pathlib import Path, PurePosixPath, PureWindowsPath

import re

import subprocess

from .evidence_world.contracts import digest, native_path, scoped_path_resolver

from .live_family_retention import MAX_FILES, SCHEMA, _json

from .live_original_vod_archive import _physical_identity

from .vod_search_index import _plain

_ARCHIVE = re.compile(r"[a-f0-9]{32}-[1-9][0-9]{0,15}")

_SHA = re.compile(r"[a-f0-9]{64}")

_OWNER_QUERY = """
import json, sys
from pathlib import Path
from pipeline.evidence_world.temporal_source import _reader
from pipeline.live_original_vod_archive import ORIGINAL_ARCHIVE_TABLE_SQL
with _reader(Path('/runtime/work/live-engine/catalog.sqlite')) as db:
    row = db.execute(
        'SELECT a.broadcast_id, l.root_id, l.location_revision, l.receipt_json '
        'FROM original_vod_archives a JOIN archive_locations l USING (broadcast_id) '
        'WHERE a.video_no=?', (sys.argv[1],)).fetchone() if db.execute(ORIGINAL_ARCHIVE_TABLE_SQL).fetchone() else None
    print(json.dumps(dict(row) if row else None))
"""

def _path(value):
    text = os.fspath(value).removeprefix("\\\\?\\")
    return Path(text)

def _checked_path(path):
    with scoped_path_resolver(None):
        return _plain(native_path(path))

def _catalog(owner, video):
    result = subprocess.run(
        ["docker", "exec", "--workdir", "/app", owner, "python", "-B", "-c", _OWNER_QUERY, video],
        capture_output=True, timeout=8, check=False,
    )
    if result.returncode:
        raise OSError("native_retained_catalog_unavailable")
    if len(result.stdout) > 1_000_000:
        raise ValueError("native_retained_catalog_invalid")
    row = json.loads(result.stdout)
    if row is None:
        return None
    receipt = json.loads(row["receipt_json"]) if row["receipt_json"] else {}
    if not isinstance(receipt, dict):
        raise ValueError("native_retained_catalog_invalid")
    proof = receipt.get("materialFamily")
    if proof is not None and not isinstance(proof, dict):
        raise ValueError("native_retained_catalog_invalid")
    if proof is None or proof.get("state") == "publishing":
        return None
    archive = row["broadcast_id"].removeprefix("chzzk:").replace(":", "-")
    if (not row["broadcast_id"].startswith("chzzk:") or not _ARCHIVE.fullmatch(archive)
            or row["root_id"] != "hot" or type(row["location_revision"]) is not int
            or row["location_revision"] < 1 or proof.get("schema") != "loom.retained-material-publication.v1"
            or proof.get("state") != "published" or proof.get("locationRevision") != row["location_revision"]
            or proof.get("receiptPath") != "/archive/live/work/_vod-search/retention/" + archive + ".json"
            or any(not _SHA.fullmatch(str(proof.get(key) or "")) for key in (
                "receiptSha256", "stagedPlanSha256", "publicationPlanSha256"))):
        raise ValueError("native_retained_material_not_active")
    return archive, proof

def _members(receipt, archive, proof, video):
    plan = receipt.get("plan")
    if (receipt.get("schema_version") != SCHEMA or receipt.get("state") != "published"
            or not isinstance(plan, dict) or plan.get("archive") != archive or plan.get("video_no") != video
            or plan.get("schema_version") != SCHEMA
            or plan.get("plan_sha256") != proof["publicationPlanSha256"]
            or plan.get("staged_plan_sha256") != proof["stagedPlanSha256"]
            or receipt.get("plan_sha256") != proof["publicationPlanSha256"]
            or digest({key: value for key, value in plan.items() if key != "plan_sha256"}) != plan["plan_sha256"]
            or plan.get("hot_roots", {}).get("work") != "/runtime/work"
            or plan.get("cold_roots", {}).get("work") != "/archive/live/work"
            or not 0 < len(plan.get("members", [])) <= MAX_FILES):
        raise ValueError("native_retained_material_changed")
    members = {}
    for member in plan["members"]:
        ref = member["ref"]
        if ref["root"] != "work":
            continue
        raw = ref["relative"]
        relative = PurePosixPath(raw)
        if relative.parts[0:1] != (video,):
            continue
        if (relative.as_posix() != raw or len(relative.parts) < 2
                or any(part in (".", "..") or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved()
                       or any(ord(char) < 32 or char in '\\:<>"|?*' for char in part)
                       for part in relative.parts)
                or type(member["bytes"]) is not int or member["bytes"] < 0
                or not _SHA.fullmatch(str(member["sha256"]))):
            raise ValueError("native_retained_member_invalid")
        path = Path(*relative.parts[1:])
        if path in members:
            raise ValueError("native_retained_member_conflict")
        members[path] = (member["bytes"], member["sha256"])
    return members

class _SavedRead:
    def __init__(self, logical, physical, members):
        self.logical, self.physical, self.members = logical, physical, members
        self.directories = {parent for member in members for parent in member.parents}
        self.observed = {}

    def _observe(self, relative, *, recheck=False):
        physical = _checked_path(self.physical / relative)
        if relative not in self.members or not physical.is_file():
            raise ValueError("native_retained_member_unavailable")
        size, expected = self.members[relative]
        before = physical.stat()
        mark = _physical_identity(before)
        if before.st_size != size or (relative in self.observed and self.observed[relative] != mark):
            raise ValueError("native_retained_member_changed")
        if recheck or relative not in self.observed:
            with physical.open("rb") as stream:
                if file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("native_retained_member_changed")
            if _physical_identity(physical.stat()) != mark:
                raise ValueError("native_retained_member_changed")
            self.observed[relative] = mark
        if physical.suffix == ".sqlite":
            wal = _checked_path(physical.with_name(physical.name + "-wal"))
            if wal.exists() and wal.stat().st_size:
                raise ValueError("native_retained_member_changed")
        return physical

    def resolve(self, value):
        path = _path(value)
        for prefix in (self.logical, self.physical):
            if path.is_relative_to(prefix):
                relative = path.relative_to(prefix)
                if relative in self.members:
                    return self._observe(relative)
                physical = _checked_path(self.physical / relative)
                if relative in self.directories:
                    if not physical.is_dir():
                        raise ValueError("native_retained_member_unavailable")
                elif physical.exists():
                    raise ValueError("native_retained_member_unregistered")
                return physical
        return None

    def bind_saved_pages(self, job):


        state = job / "source-reads" / "state" / "state.json"
        if state in self.members or _checked_path(self.physical / state).exists():
            self._observe(state)
        pages = job / "source-reads" / "read_results"
        folder = _checked_path(self.physical / pages)
        expected = {path for path in self.members if path.parent == pages and path.suffix == ".json"}
        actual = {pages / path.name for path in folder.glob("*.json")}
        if actual != expected:
            raise ValueError("native_retained_pages_changed")
        for path in sorted(expected):
            self._observe(path)

def _recognize(job_root, work):
    logical = _path(job_root)
    if not logical.is_absolute() or not logical.is_relative_to(work):
        return None
    parts = logical.relative_to(work).parts
    if (len(parts) != 3 or not re.fullmatch(r"[1-9][0-9]{0,15}", parts[0])
            or parts[1] != "current-conversation" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", parts[2])
            or parts[2].endswith(".")):
        return None
    return parts[0], Path(*parts[1:])

@contextmanager
def native_retained_saved_read(job_root):
    """Use an active published E copy for the two existing read-only operations."""
    if os.name != "nt":
        yield
        return
    from .config import runtime_data_root
    from .dayline_origin_cold import ColdInputUnavailable, _container

    runtime = _plain(runtime_data_root())
    work = runtime / "work"
    recognized = _recognize(job_root, work)
    if recognized is None or not (work / "live-engine" / "catalog.sqlite").is_file():
        yield
        return
    video, job = recognized
    try:
        owner, cold = _container(runtime)
        current = _catalog(owner, video)
    except (ColdInputUnavailable, OSError, subprocess.SubprocessError):
        current = None
    if current is None:
        yield
        return
    archive, proof = current
    sealed = _plain(cold / "work" / "_vod-search" / "retention" / (archive + ".json"))
    published = _json(sealed, expected_sha256=proof["receiptSha256"])
    members = _members(published, archive, proof, video)
    if job / "prepared.json" not in members:
        yield
        return
    access = _SavedRead(work / video, _plain(cold / "work" / video), members)
    access.bind_saved_pages(job)
    with scoped_path_resolver(access.resolve):
        yield
    access.bind_saved_pages(job)
    for relative in access.observed:
        access._observe(relative, recheck=True)
    _json(sealed, expected_sha256=proof["receiptSha256"])
    if _container(runtime) != (owner, cold) or _catalog(owner, video) != current:
        raise ValueError("native_retained_material_changed")
