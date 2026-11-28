"""Versioned Loki readers with the supplied template kept as the original."""

from __future__ import annotations

import hashlib

import json

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "vendor/loki-page-template/v1"

_PROVENANCE = json.loads((SOURCE.parent / "provenance.json").read_text(encoding="utf-8"))

_PREFIX = "shared/loki/v1/"

COLLABORATION_VIEW_SOURCE = "publish/builder/loki-collaboration-view.js"

def _collaboration_view(source: Path) -> bytes:
    return (source.parents[2] / COLLABORATION_VIEW_SOURCE).read_bytes().replace(b"\r\n", b"\n")

def runtime_names() -> list[str]:
    return [name.removeprefix(_PREFIX) for name in _PROVENANCE["files"] if name.startswith(_PREFIX)]

def runtime_asset(name: str, *, original: bool = False, source: Path = SOURCE) -> bytes:
    """Return the exact asset for the requested reader version."""
    identity = _PROVENANCE["files"].get(_PREFIX + name)
    if identity is None or name not in runtime_names():
        raise FileNotFoundError("loki_runtime_asset_not_found")
    raw = (source / _PREFIX / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise ValueError("loki_template_source_changed")
    if name == "collaboration-view.js" and not original:
        patched = _collaboration_view(source)
        if _runtime_version(patched) != VERSION:
            raise ValueError("loki_collaboration_view_changed")
        return patched
    return raw

def _runtime_version(collaboration_view: bytes) -> str:
    return hashlib.sha256(_PROVENANCE["runtime_version"].encode() + b"\0" + collaboration_view).hexdigest()[:16]

VERSION = _runtime_version(_collaboration_view(SOURCE))

RUNTIME_ROOT = f"loki/{VERSION}"

RUNTIME_PREFIX = f"/{RUNTIME_ROOT}/{_PREFIX}"

ORIGINAL_RUNTIME_PREFIX = f"/loki/{_PROVENANCE['runtime_version']}/{_PREFIX}"

RUNTIME_PREFIXES = (RUNTIME_PREFIX, ORIGINAL_RUNTIME_PREFIX)
