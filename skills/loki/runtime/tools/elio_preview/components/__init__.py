"""Composable source modules for the single public Elio CSS/JS bundle."""

from pathlib import Path

STYLE_COMPONENT_NAMES = (
    "reader_guides", "perspective_timeline", "story_topology",
    "story_layout", "story_tree_reader", "scene_minimap", "time_map", "reader_share", "reader_board",
)

SCRIPT_COMPONENT_NAMES = ("reader_guides", "story_tree_reader", "diagram_geometry", "story_layout", "glossary_navigation", "scene_minimap", "time_map", "reader_share", "reader_board")

def _component_root() -> Path:
    return Path(__file__).parent

def bundled_styles(base_style: str) -> str:
    parts = [base_style.rstrip()]
    parts.extend(
        (_component_root() / f"{name}.css").read_text(encoding="utf-8").rstrip()
        for name in STYLE_COMPONENT_NAMES
    )
    return "\n\n".join(parts) + "\n"

def bundled_scripts(base_script: str) -> str:
    parts = [base_script.rstrip()]
    parts.extend(
        (_component_root() / f"{name}.js").read_text(encoding="utf-8").rstrip()
        for name in SCRIPT_COMPONENT_NAMES
    )
    return "\n\n".join(parts) + "\n"
