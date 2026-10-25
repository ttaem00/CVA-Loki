"""Private, source-bound SQLite storage for newly committed VOD reports.

No encoder, producer migration, raw-source rewrite, or retention scheduler lives
here. Callers own canonical report selection and trusted hot/cold roots. Stored
hashing text vectors never enter the Gemma multimodal ranking space.
"""

from __future__ import annotations

from contextlib import closing

import hashlib

import json

from pathlib import Path

import re

import sqlite3

import stat

import os

import tempfile

import time

from collections import OrderedDict

from threading import Lock

from urllib.parse import quote

from .summary_local_vector_index import EMBEDDING_MODEL, EMBEDDING_REVISION, validate_summary_local_vector_index

SCHEMA = "vod_search_index.v1"

DATABASE_NAME = "vod-search.sqlite"

RETENTION_MS = 30 * 24 * 60 * 60 * 1000

_ROOTS = frozenset({"output", "site", "work"})

_REGISTERED_CATALOG_CACHE = OrderedDict()

_REGISTERED_CATALOG_LOCK = Lock()

def _report_file_revision(path):
    value = path.stat()
    return (str(path), value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)

def _registered_catalog_metadata(metadata):
    keys = ('video_no', 'title', 'channel', 'channel_id', 'streamer_id',
            'publish_date', 'source_platform', 'summary_pipeline')
    result = {key: metadata[key] for key in keys if key in metadata}
    if isinstance(metadata.get('structured_outline'), dict):
        result['structured_outline'] = {}
    family = metadata.get('report_artifact_family') or {}
    result['report_artifact_family'] = {key: family.get(key) for key in
        ('generation_id', 'generation_sequence', 'authority_revision_id')}
    return result

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()

def _plain(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("search_root_absolute_required")
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("search_reparse_path_rejected")
    return path.resolve()

def _roots(roots):
    if not isinstance(roots, dict) or set(roots) - _ROOTS or not roots:
        raise ValueError("search_roots_invalid")
    return {key: _plain(value) for key, value in roots.items()}

def _ref(path, roots):
    path = _plain(path)
    for name, root in roots.items():
        if path.is_relative_to(root):
            return dict(root=name, relative=path.relative_to(root).as_posix())
    raise ValueError("search_source_outside_roots")

def _resolve(ref, roots):
    if not isinstance(ref, dict) or set(ref) != {"root", "relative"} or ref["root"] not in roots:
        raise ValueError("search_ref_invalid")
    relative = ref["relative"]
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("search_ref_invalid")
    path = Path(relative)
    if path.is_absolute() or any(p in {".", ".."} for p in path.parts):
        raise ValueError("search_ref_invalid")
    resolved = _plain(roots[ref["root"]] / path)
    if not resolved.is_relative_to(roots[ref["root"]]):
        raise ValueError("search_source_outside_roots")
    return resolved

def _capture(path, roots):
    ref = _ref(path, roots)
    data = _resolve(ref, roots).read_bytes()
    return dict(ref=ref, sha256=hashlib.sha256(data).hexdigest()), data

def _fresh(binding, roots):
    content = {}
    for source in binding["files"].values():
        data = _resolve(source["ref"], roots).read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("search_source_revision_changed")
        content[_json(source["ref"])] = data
    return content

def _registration_binding(row, tier):
    if row["vector_mode"] != "producer_memory" or not row["family_id"]:
        raise ValueError("search_historical_report_unsupported")
    if (not str(row["video_no"]).isdigit() or not isinstance(row["base"], str)
            or row["base"].split("_", 1)[0] != row["video_no"] or any(c in row["base"] for c in "\\/\x00")):
        raise ValueError("search_report_identity_invalid")
    binding = json.loads(row["binding_json"])
    if (binding.get("schema_version") != SCHEMA or binding.get("video_no") != row["video_no"]
            or binding.get("base") != row["base"] or binding.get("family_id") != row["family_id"]
            or binding.get("tier") != tier or not {"metadata", "markdown", "html"} <= set(binding["files"])):
        raise ValueError("search_report_identity_invalid")
    for name in ("metadata", "markdown", "html"):
        if not re.fullmatch(r"[0-9a-f]{64}", binding["files"][name]["sha256"]):
            raise ValueError("search_report_identity_invalid")
    if row["report_key"] != _digest([row["video_no"], row["base"], row["family_id"],
            binding["files"]["metadata"]["sha256"], binding["files"]["markdown"]["sha256"]]):
        raise ValueError("search_report_identity_invalid")
    return binding

def default_database_path(work_dir, video_no=""):
    """Both established callers supply either work root or work/<video>."""
    root = _plain(work_dir)
    if video_no and root.name == str(video_no):
        root = root.parent
    return root / "_vod-search" / DATABASE_NAME

def write_status(database_path, base, status):
    """Private derived receipt; failure state does not alter the report family."""
    parent = _plain(database_path).parent
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("status-" + _digest(str(base)) + ".json")
    descriptor, temporary = tempfile.mkstemp(prefix="status-", suffix=".part", dir=parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(_json(status))
    os.replace(temporary, destination)

def _connect(path, *, readonly=False):
    path = _plain(path)
    if readonly:
        connection = sqlite3.connect("file:" + quote(str(path), safe="/:") + "?mode=ro", uri=True, timeout=2)
        connection.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection

def _check_schema(db, tier=None):
    try:
        values = dict(db.execute("SELECT key,value FROM index_metadata"))
    except sqlite3.Error as exc:
        raise ValueError("search_schema_invalid") from exc
    if values.get("schema") != SCHEMA or values.get("tier") not in {"hot", "cold"} or (tier and values["tier"] != tier):
        raise ValueError("search_schema_invalid")
    return values["tier"]

def initialize_index(database_path, *, tier="hot"):
    if tier not in {"hot", "cold"}:
        raise ValueError("search_tier_invalid")
    with closing(_connect(database_path)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            _check_schema(db, tier)
        else:
            db.executescript("""
                CREATE TABLE index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE reports(
                    report_key TEXT PRIMARY KEY, video_no TEXT NOT NULL, base TEXT NOT NULL,
                    family_id TEXT NOT NULL, binding_json TEXT NOT NULL,
                    presentation_json TEXT NOT NULL, markdown_text TEXT NOT NULL,
                    markdown_fold TEXT NOT NULL, vector_json TEXT, manifest_json TEXT,
                    vector_revision TEXT, vector_status TEXT NOT NULL,
                    vector_mode TEXT NOT NULL);
                CREATE INDEX reports_identity ON reports(video_no,base);
            """)
            db.executemany("INSERT INTO index_metadata VALUES(?,?)", [("schema", SCHEMA), ("tier", tier)])
    return dict(schema_version=SCHEMA, tier=tier, status="ready")

def validate_cold_end_proof(proof, *, video_no, now_ms):
    """Validate owner-confirmed end provenance; publication age is insufficient."""
    if isinstance(proof, dict) and proof.get("time_basis") == "verified_terminal_observation":
        from .vod_retention_observation import validate_terminal_observation
        return validate_terminal_observation(proof, video_no=video_no, now_ms=now_ms)
    if not isinstance(proof, dict) or proof.get("video_no") != video_no or proof.get("state") != "ended":
        raise ValueError("search_cold_end_unverified")
    end = proof.get("ended_at_ms")
    if type(end) is not int or end <= 0 or type(now_ms) is not int or end > now_ms:
        raise ValueError("search_cold_end_unverified")
    if not isinstance(proof.get("producer_revision"), str) or not proof["producer_revision"]:
        raise ValueError("search_cold_end_unverified")

    if proof.get("confirmed") is not True or not isinstance(proof.get("source_id"), str) or not proof["source_id"]:
        raise ValueError("search_cold_end_unverified")
    if now_ms - end < RETENTION_MS:
        raise ValueError("search_cold_age_unverified")
    return dict(proof)

def register_report(*, database_path, roots, metadata_path, markdown_path, html_path,
                    base=None, local_vector_index=None, timed_evidence_manifest=None,
                    srt_path=None, tier="hot", end_proof=None, now_ms=None,
                    vector_mode="producer_memory", expected_source_md5=None,
                    expected_source_sha256=None, expected_metadata_sha256=None,
                    retention_enrollment=None):
    """Persist one newly committed family from the normal in-memory producer.

    Historical reconstruction/import is unsupported. A new summary-only family
    keeps producer provenance without claiming that vectors were persisted.
    """
    if vector_mode != "producer_memory":
        raise ValueError("search_historical_report_unsupported")
    roots = _roots(roots)
    files, content = {}, {}
    for name, path in (("metadata", metadata_path), ("markdown", markdown_path), ("html", html_path)):
        files[name], content[name] = _capture(path, roots)
    meta = json.loads(content["metadata"].decode("utf-8-sig"))
    if not isinstance(meta, dict) or not str(meta.get("video_no") or "").isdigit():
        raise ValueError("search_report_identity_invalid")
    video = str(meta["video_no"])
    if retention_enrollment is not None:
        required = {"schema_version", "video_no", "channel_id", "source_platform"}
        if (not isinstance(retention_enrollment, dict) or set(retention_enrollment) != required
                or retention_enrollment.get("schema_version") != "vod.retention-enrollment.v1"
                or retention_enrollment.get("video_no") != video
                or retention_enrollment.get("source_platform") != "chzzk"
                or not re.fullmatch(r"[a-f0-9]{32}", str(retention_enrollment.get("channel_id") or ""))
                or retention_enrollment["channel_id"] != meta.get("channel_id")
                or meta.get("source_platform") != "chzzk"):
            raise ValueError("search_retention_enrollment_invalid")
        retention_enrollment = dict(retention_enrollment)
    if expected_metadata_sha256 and files["metadata"]["sha256"] != expected_metadata_sha256:
        raise ValueError("search_source_revision_changed")
    family = meta.get("report_artifact_family")
    if family is None:
        raise ValueError("search_historical_report_unsupported")
    from .report_artifact_family import ReportArtifactFamilyError, validate_report_artifact_family
    try:
        verified = validate_report_artifact_family(markdown_path, html_path, metadata_path)
    except ReportArtifactFamilyError as error:
        raise ValueError("search_report_family_invalid") from error
    family_id = verified["generation_id"]
    if tier == "cold":
        end_proof = validate_cold_end_proof(end_proof, video_no=video, now_ms=now_ms)
    elif tier != "hot":
        raise ValueError("search_tier_invalid")
    base = str(base or Path(markdown_path).stem)
    if not base or base.split("_", 1)[0] != video or any(c in base for c in "\\/\x00"):
        raise ValueError("search_report_identity_invalid")
    vector, manifest = None, None
    status = "not_persisted"
    if local_vector_index is not None:
        if not srt_path:
            raise ValueError("search_vector_source_required")
        if not isinstance(local_vector_index, dict):
            raise ValueError("summary_vector_invalid")
        files["stt"], stt = _capture(srt_path, roots)
        expected_source_sha256 = expected_source_sha256 or local_vector_index.get("source_sha256")
        if expected_source_sha256 and files["stt"]["sha256"] != expected_source_sha256:
            raise ValueError("search_source_revision_changed")
        if expected_source_md5 and hashlib.md5(stt).hexdigest()[:12] != expected_source_md5:
            raise ValueError("search_source_revision_changed")
        manifest = timed_evidence_manifest
        if not isinstance(manifest, dict) or str(manifest.get("video_id")) != video:
            raise ValueError("search_vector_source_invalid")
        vector = validate_summary_local_vector_index(local_vector_index,
            timed_evidence_manifest=manifest, duration_sec=meta.get("duration"))
        status = "ready_text_hash_vector"
    presentation = {key: str(meta.get(key) or "") for key in
                    ("title", "publish_date", "streamer_id", "channel")}
    binding = dict(schema_version=SCHEMA, video_no=video, base=base, family_id=family_id,
                   files=files, tier=tier, end_proof=end_proof if tier == "cold" else None,
                   vector_model=EMBEDDING_MODEL if vector else None,
                   vector_model_revision=EMBEDDING_REVISION if vector else None,
                   vector_dimensions=vector["dimensions"] if vector else None,
                   vector_digest=_digest(vector) if vector else None,
                   manifest_digest=_digest(manifest) if manifest else None)
    if retention_enrollment is not None:


        binding["retention_enrollment"] = retention_enrollment
    key = _digest([video, base, family_id, files["metadata"]["sha256"], files["markdown"]["sha256"]])
    markdown = content["markdown"].decode("utf-8-sig")
    initialize_index(database_path, tier=tier)
    with closing(_connect(database_path)) as db, db:
        _check_schema(db, tier)
        db.execute("BEGIN IMMEDIATE")
        _fresh(binding, roots)
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            key, video, base, family_id, _json(binding), _json(presentation), markdown, markdown.casefold(),
            _json(vector) if vector else None, _json(manifest) if manifest else None,
            EMBEDDING_REVISION if vector else None, status, vector_mode))
    return dict(schema_version=SCHEMA, status="ready", tier=tier, video_no=video, base=base,
                report_key=key, vector_status=status, vector_mode=vector_mode,
                vector_count=len(vector["items"]) if vector else 0, provider_calls=0)

def list_registered_reports(*, database_path, roots, base=None, video_no=None, catalog_cache=False, include_context=False):
    """Internal current report locations, independent of old vector freshness.

    A normal committed Save may replace the family at its registered paths.
    This opens its current source; it never refreshes stored words or vectors.
    Resolved paths are private and must not be serialized to HTTP.
    Optional source_sha256 is the initial producer's STT binding, not a claim
    that the current STT or stored vector body passed freshness validation.
    """
    from .report_artifact_family import ReportArtifactFamilyError, validate_report_artifact_family
    roots = _roots(roots)
    records, unavailable, seen = [], 0, set()
    with closing(_connect(database_path, readonly=True)) as db:
        tier = _check_schema(db)
        query = "SELECT * FROM reports WHERE vector_mode='producer_memory' AND family_id<>''"
        parameters = ()
        if base is not None:
            if not isinstance(base, str) or not base or any(c in base for c in '/\\\x00'):
                raise ValueError("search_report_identity_invalid")
            query += " AND base=?"
            parameters = (base,)
        if video_no is not None:
            if not isinstance(video_no, str) or not re.fullmatch(r"[0-9]+", video_no):
                raise ValueError("search_report_identity_invalid")
            query += " AND video_no=?"
            parameters += (video_no,)
        query += " ORDER BY video_no,base,report_key"
        for row in db.execute(query, parameters):
            try:
                binding = _registration_binding(row, tier)
                source = binding["files"].get("stt")
                source_sha256 = None
                if source is not None:
                    if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
                        raise ValueError("search_report_identity_invalid")
                    _resolve(source["ref"], roots)
                    source_sha256 = source["sha256"]
                if tier == "cold":
                    validate_cold_end_proof(binding["end_proof"], video_no=row["video_no"], now_ms=time.time_ns() // 1_000_000)
                paths = {name: _resolve(binding["files"][name]["ref"], roots)
                         for name in ("metadata", "markdown", "html")}
                names = tuple(paths[name].name for name in ("markdown", "html", "metadata"))
                expected = (row["base"] + ".md", row["base"] + ".html", row["base"] + "_metadata.json")
                if (len({path.parent for path in paths.values()}) != 1
                        or names not in (expected, ("report.md", "report.html", "metadata.json"))):
                    raise ValueError("search_report_location_invalid")
                cache_key = (str(database_path), row['report_key'], row['binding_json'],
                             tuple(str(paths[name]) for name in ('metadata', 'markdown', 'html')))
                before = tuple(_report_file_revision(path) for path in paths.values())
                if catalog_cache and tier != 'cold':
                    with _REGISTERED_CATALOG_LOCK:
                        cached = _REGISTERED_CATALOG_CACHE.get(cache_key)
                    if cached is not None:
                        alias_path, signature, cached_record = cached
                        current_signature = before + ((_report_file_revision(alias_path),) if alias_path else ())
                        if current_signature == signature:
                            records.append(dict(cached_record))
                            continue
                captured = {name: _capture(path, roots) for name, path in paths.items()}
                current_files = {name: value[0] for name, value in captured.items()}
                metadata = json.loads(captured["metadata"][1].decode("utf-8"))
                alias = metadata["report_artifact_family"].get("artifacts", {}).get("candidate_alias")
                alias_before = None
                if alias is not None:
                    name = alias["name"]
                    if not isinstance(name, str) or not name or Path(name).name != name or any(c in name for c in "\\/:\x00"):
                        raise ValueError("search_report_location_invalid")
                    alias_before = _report_file_revision(paths['metadata'].parent / name)
                    current_files["candidate_alias"], _ = _capture(paths["metadata"].parent / name, roots)
                current = validate_report_artifact_family(paths["markdown"], paths["html"], paths["metadata"])
                if str(current["metadata"].get("video_no")) != row["video_no"]:
                    raise ValueError("search_report_identity_invalid")
                _fresh({"files": current_files}, roots)
                alias_path = paths['metadata'].parent / alias['name'] if alias is not None else None
                after = tuple(_report_file_revision(path) for path in paths.values())
                alias_after = _report_file_revision(alias_path) if alias_path else None
                if before != after or alias_before != alias_after:
                    raise ValueError('search_source_revision_changed')
                identity = (row["video_no"], row["base"], *paths.values())
                if identity in seen:
                    continue
                seen.add(identity)
                record = dict(video_no=row["video_no"], base=row["base"], report_key=row["report_key"],
                    family_id=current["generation_id"], tier=tier, vector_status=row["vector_status"],
                    metadata_path=paths["metadata"], markdown_path=paths["markdown"], html_path=paths["html"])
                if source_sha256 is not None:
                    record["source_sha256"] = source_sha256
                record['catalog_metadata'] = _registered_catalog_metadata(current['metadata'])
                if include_context:
                    record['_validated_context'] = {
                        'metadata': current['metadata'], 'family': current,
                        'markdown': captured['markdown'][1].decode('utf-8'),
                        'html': captured['html'][1].decode('utf-8'), 'signature': after,
                        'alias_path': alias_path,
                        'alias_revision': alias_after,
                    }
                if catalog_cache and tier != 'cold':
                    signature = after + ((alias_after,) if alias_path else ())
                    with _REGISTERED_CATALOG_LOCK:
                        _REGISTERED_CATALOG_CACHE[cache_key] = (alias_path, signature, dict(record))
                        _REGISTERED_CATALOG_CACHE.move_to_end(cache_key)
                        while len(_REGISTERED_CATALOG_CACHE) > 128:
                            _REGISTERED_CATALOG_CACHE.popitem(last=False)
                records.append(record)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, ReportArtifactFamilyError):
                unavailable += 1
    return dict(schema_version=SCHEMA, status="partial" if unavailable else "ready",
                reports=records, unavailable_reports=unavailable, tier=tier)
