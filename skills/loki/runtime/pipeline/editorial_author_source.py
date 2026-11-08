"""Optional EW tool turns by the existing author, not another semantic owner."""

from __future__ import annotations

from copy import deepcopy

import json

from .evidence_world.contracts import digest

NATIVE_RANGE_CONTRACT = "selected_native_range_refs_v1"

def _read_continuation_action(result, action, tool_root):
    """Project an existing query; a saved page ID alone is not its selector."""
    if not result.get("next_cursor"):
        return None
    if not action or "read_id" in action.get("args", {}):
        action = None
        for path in sorted((tool_root / "tools").glob("*.json")):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(saved, dict):
                continue
            read = saved.get("result", {})
            candidate = saved.get("action", {})
            if (not isinstance(read, dict) or not isinstance(candidate, dict)
                    or read.get("read_id") != result.get("read_id")
                    or read.get("read_query_id") != result.get("read_query_id")
                    or "read_id" in candidate.get("args", {})):
                continue
            if saved.get("sha256") != digest(read) or path.stem != digest(candidate):
                raise ValueError("author_source_tool_result_changed")
            action = read.get("question_navigation", {}).get("next_original_action", candidate)
            break
    if (not action or action.get("action") != "read_evidence"
            or not any(key in action.get("args", {})
                       for key in ("record_ids", "intervals_sec", "intervals_utc_ms"))):
        return None
    return {"schema_version": "ew.tool.v1", "source_id": result["source_id"],
        "world_revision": result["world_revision"], "question_id": result["question_id"],
        **deepcopy(action), "args": {**action["args"], "cursor": result["next_cursor"]}}
