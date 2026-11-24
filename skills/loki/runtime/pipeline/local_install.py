"""Identity boundary for a deliberately initialized, site-free local installation."""

from __future__ import annotations

import json

import os

from pathlib import Path

import re

MARKER = ".cva-local-install.json"

SCHEMA = "chzz.local-install.v1"

def plain_root(value: str | Path) -> Path:
    path = Path(os.path.abspath(value))
    if path == Path(path.anchor) or path == Path.home() or any(
        part.lower() in {".codex", ".claude", ".ssh", ".aws", ".azure", ".config",
                         "secrets", "profiles", "user data", "codex-home", "codex-work-home"}
        or part.lower().startswith(".env") for part in path.parts
    ):
        raise ValueError("local_install_root_forbidden")
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("local_install_root_redirected")
    return path

def installation(runtime: str | Path, *, code_root: Path) -> dict:
    root = plain_root(runtime)
    path = plain_root(root / MARKER)
    if not path.is_file():
        raise ValueError("local_install_marker_missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("dataRoot") != str(root)
            or value.get("codeRoot") != str(code_root.resolve())
            or not isinstance(value.get("installationId"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["installationId"])
            or type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535):
        raise ValueError("local_install_marker_invalid")
    return value
