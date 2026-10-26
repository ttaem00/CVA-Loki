"""Item-scoped editorial recovery. Source and semantic authority stay with callers."""

from __future__ import annotations

import json

from pathlib import Path

def preserve(path: Path, value) -> None:
    """Immutable content-addressed evidence; never overwrite an earlier response."""
    data = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()
    from .evidence_world.contracts import native_path
    path = native_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("editorial_immutable_artifact_conflict")
