"""Source-backed additions and targeted returns to the existing T2/T3 owners.

Legacy supplementation remains append-only. The explicit normal-return profile
can revise named Points/Stories; other rows and unresolved range edits stay fixed.
"""

import json

from pathlib import Path

from ..evidence_world.contracts import file_hash, native_path

def _load_bound(item):
    path = native_path(item["path"])
    if not path.is_relative_to(native_path(Path.cwd())) or file_hash(path) != item["sha256"]:
        raise ValueError("continuation_input_changed_or_outside_checkout")
    return json.loads(path.read_text(encoding="utf-8"))
