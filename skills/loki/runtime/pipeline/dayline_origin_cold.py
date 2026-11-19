"""Read retained DAYLINE inputs through Loom's registered Docker storage owner."""

from __future__ import annotations

import json

from pathlib import Path

import re

import subprocess

import time

class ColdInputUnavailable(ValueError):
    pass

def _native_bind(source):
    desktop = re.fullmatch(r"/run/desktop/mnt/host/([a-zA-Z])/(.+)", source)
    return Path(f"{desktop[1]}:/{desktop[2]}" if desktop else source).resolve()

def _container(root):
    """Bind the fixed application to this runtime before supplying any inputs."""
    query = ('{{.Id}} {{index .Config.Labels "chzz.contract"}} {{.State.Running}}'
             '{{range .Mounts}}{{if and (eq .Destination "/runtime") (eq .Type "bind")}}'
             '{{printf "\\n"}}runtime {{json .Source}}{{end}}'
             '{{if and (eq .Destination "/archive/live") (eq .Type "bind")}}'
             '{{printf "\\n"}}cold {{json .Source}}{{end}}{{end}}')
    result = subprocess.run(["docker", "inspect", "--format", query, "cva-always-on"],
                            capture_output=True, timeout=4, check=False)
    if result.returncode:
        raise ColdInputUnavailable("cold_archive_owner_unavailable")
    header, *mounts = result.stdout.splitlines()
    match = re.fullmatch(rb'([a-f0-9]{64}) loom-single-app-v1 true', header)
    binds = {name.decode(): _native_bind(json.loads(path))
             for name, _, path in (line.partition(b" ") for line in mounts)}
    if (not match or len(mounts) != 2 or "runtime" not in binds or "cold" not in binds
            or not binds["runtime"].samefile(root)):
        raise ColdInputUnavailable("cold_archive_runtime_mismatch")
    return match[1].decode(), binds["cold"]

def _native_original(owner, cold_root, source, deadline, *, expected_sha256=None):
    from pipeline.live_original_vod_archive import (
        ORIGINAL_ARCHIVE_ROW_SQL, ORIGINAL_ARCHIVE_TABLE_SQL, OriginalVodUnavailable,
        verify_original_registration, verify_saved_original_registration,
    )

    vod = source.get("vodNo")
    if not isinstance(vod, str) or not re.fullmatch(r"[1-9]\d{0,15}", vod):
        return {}


    command = f"""import json,sys
from pathlib import Path
from pipeline.evidence_world.temporal_source import _reader
with _reader(Path('/runtime/work/live-engine/catalog.sqlite')) as db:
    row = db.execute({ORIGINAL_ARCHIVE_ROW_SQL!r}, (sys.argv[1],)).fetchone() if db.execute({ORIGINAL_ARCHIVE_TABLE_SQL!r}).fetchone() else None
    print(json.dumps(dict(row) if row is not None else {{}}))
"""
    result = subprocess.run(["docker", "exec", "--workdir", "/app", owner, "python", "-B", "-c", command, vod],
                            capture_output=True, timeout=max(1, deadline - time.monotonic()), check=False)
    if result.returncode or len(result.stdout) > 8192:
        raise ColdInputUnavailable("cold_archive_owner_unavailable")
    row = json.loads(result.stdout)
    if not isinstance(row, dict):
        raise ColdInputUnavailable("cold_archive_read_failed")
    if not row:
        return {}
    try:
        if expected_sha256 is not None:
            return verify_saved_original_registration(cold_root, row, video_no=vod,
                expected_sha256=expected_sha256, deadline=deadline)
        return verify_original_registration(cold_root, row, source=source, deadline=deadline)
    except (OriginalVodUnavailable, KeyError) as exc:
        raise ColdInputUnavailable("cold_archive_original_vod_unavailable") from exc
