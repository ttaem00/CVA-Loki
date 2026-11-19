"""The byte-exact runtime from the manager-supplied Loki template ZIP."""

from __future__ import annotations

import hashlib

import json

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "vendor/loki-page-template/v1"

_PROVENANCE = json.loads((SOURCE.parent / "provenance.json").read_text(encoding="utf-8"))

_PREFIX = "shared/loki/v1/"

def runtime_names() -> list[str]:
    return [name.removeprefix(_PREFIX) for name in _PROVENANCE["files"] if name.startswith(_PREFIX)]

def runtime_asset(name: str) -> bytes:
    """Verify provenance and return the supplied asset without transformation."""
    identity = _PROVENANCE["files"].get(_PREFIX + name)
    if identity is None or name not in runtime_names():
        raise FileNotFoundError("loki_runtime_asset_not_found")
    raw = (SOURCE / _PREFIX / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise ValueError("loki_template_source_changed")
    return raw

VERSION = _PROVENANCE["runtime_version"]

RUNTIME_ROOT = f"loki/{VERSION}"

RUNTIME_PREFIX = f"/{RUNTIME_ROOT}/{_PREFIX}"
