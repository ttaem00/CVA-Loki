"""Isolated, task-managed launch contract for the optional Codex CLI route."""

from __future__ import annotations

import sys

from pathlib import Path

_LINUX_RELEASE_MOUNT = "/cva/source"

_LINUX_RELEASE_ROOT = Path(_LINUX_RELEASE_MOUNT)

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def is_native_linux_release() -> bool:
    return sys.platform == "linux" and _repo_root() == _LINUX_RELEASE_ROOT
