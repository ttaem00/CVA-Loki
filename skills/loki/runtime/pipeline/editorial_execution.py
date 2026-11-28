"""Item-scoped editorial recovery. Source and semantic authority stay with callers."""

from __future__ import annotations

import json

from pathlib import Path

LIVE_AUTOMATIC_WRITER_RECIPE = "live_automatic_writer_recipe"

CHRONOLOGY_MEDIUM_WRITER_RECIPE = "chronology_medium_v1"

FRESH_LIVE_AUTOMATIC_WRITER_RECIPES = {"low": "chronology_low_v8", "medium": "chronology_medium_v8"}

LIVE_AUTOMATIC_WRITER_EFFORTS = {
    CHRONOLOGY_MEDIUM_WRITER_RECIPE: "medium",
    "chronology_low_v2": "low",
    "chronology_medium_v2": "medium",
    "chronology_low_v3": "low",
    "chronology_medium_v3": "medium",
    "chronology_low_v4": "low",
    "chronology_medium_v4": "medium",
    "chronology_low_v5": "low",
    "chronology_medium_v5": "medium",
    "chronology_low_v6": "low",
    "chronology_medium_v6": "medium",
    "chronology_low_v7": "low",
    "chronology_medium_v7": "medium",
    **{recipe: effort for effort, recipe in FRESH_LIVE_AUTOMATIC_WRITER_RECIPES.items()},
}

def canonical_author_range_level(level):
    """Return the existing D1/D2 token for an unambiguous JSON integer level."""
    return f"D{level}" if type(level) is int and level in (1, 2) else level

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
