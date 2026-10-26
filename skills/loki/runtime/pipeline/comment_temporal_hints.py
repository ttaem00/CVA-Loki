"""Turn private rewritten VOD comment anchors into shadow temporal hints.

This is intentionally a weak-signal adapter.  It does not turn a viewer
comment into source/time truth, a confirmed highlight, or a learning label.
"""

from __future__ import annotations

SCHEMA_VERSION = "comment_temporal_hints.v1"

SIDECAR_FILENAME = "comment_temporal_hints.json"

CONTEXT_RANGE_SCHEMA_VERSION = "comment_context_range_candidate.v1"

EXACT_MOMENT_SEED_SCHEMA_VERSION = "comment_exact_moment_seed.v1"

STORY_WINDOW_SCHEMA_VERSION = "comment_story_window_candidate.v1"
