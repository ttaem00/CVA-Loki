"""Answer-free BroadcastMap prompt contract for the normal long-form path.

Owner flow (one way only): raw STT/replay chat/title -> chunk observations ->
final D1/D2/Point outline. Legacy summaries/timelines/highlights are rejected.
"""

from __future__ import annotations

from .outline.check import validate_structured_manager_outline

from .outline.parse import parse_manager_outline, parse_manager_outline_markdown

from .outline.render import project_manager_outline_markdown
