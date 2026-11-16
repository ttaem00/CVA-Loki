"""Story reader display mode: first-paint boot script and no-script palette copy.

The saved choice is a browser preference only. It never enters story data,
share URLs or hashes. The head boot runs before the reader stylesheet so an
explicit light/dark choice is honored from the first paint; without script the
device palette applies through ``prefers-color-scheme``.
"""

from __future__ import annotations

DARK_SCOPE = ":root[data-reader-theme=dark]"

NO_SCRIPT_SCOPE = ":root:not([data-reader-theme]):has(#spoiler-toggle)"

DARK_START = "/* reader-theme:dark:start */"

DARK_END = "/* reader-theme:dark:end */"

def no_script_styles(theme_css: str) -> str:
    """Return the dark block re-scoped to device preference when script is absent."""
    if theme_css.count(DARK_START) != 1 or theme_css.count(DARK_END) != 1:
        raise ValueError("reader theme dark block markers are missing or duplicated")
    start = theme_css.index(DARK_START) + len(DARK_START)
    end = theme_css.index(DARK_END)
    if end <= start or NO_SCRIPT_SCOPE in theme_css:
        raise ValueError("reader theme dark block is malformed")
    block = theme_css[start:end].strip().replace(DARK_SCOPE, NO_SCRIPT_SCOPE)
    return "@media (prefers-color-scheme:dark){\n" + block + "\n}"
