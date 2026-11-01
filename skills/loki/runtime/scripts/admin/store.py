"""Recoverable UTF-8 artifact persistence for Report Admin.

This module deliberately accepts only explicit paths and text.  Route handling,
application state, and report rendering remain in their existing owners.
"""

from __future__ import annotations

import os

import tempfile

from pathlib import Path

def safe_backup(path: Path | None) -> None:
    """Keep the first Admin backup of an existing UTF-8 artifact."""
    if path is None or not path.exists():
        return
    backup = path.with_suffix(path.suffix + ".admin.bak")
    if not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

def write_text_artifacts_atomically(writes: list[tuple[Path, str]]) -> None:
    """Commit related UTF-8 artifacts as one recoverable revision boundary."""
    if not writes:
        return
    staged: list[tuple[Path, Path]] = []
    previous: dict[Path, bytes | None] = {}
    committed: list[Path] = []
    try:
        for path, text in writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            previous[path] = path.read_bytes() if path.exists() else None
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".pending",
                delete=False,
            )
            temporary = Path(handle.name)
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
            committed.append(path)
    except Exception as original_error:
        rollback_errors: list[str] = []
        for path in reversed(committed):
            rollback_path: Path | None = None
            try:
                before = previous[path]
                if before is None:
                    path.unlink(missing_ok=True)
                    continue
                rollback = tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".rollback",
                    delete=False,
                )
                rollback_path = Path(rollback.name)
                with rollback:
                    rollback.write(before)
                    rollback.flush()
                    os.fsync(rollback.fileno())
                os.replace(rollback_path, path)
            except Exception as rollback_error:
                rollback_errors.append(f"{path.name}: {rollback_error}")
            finally:
                if rollback_path is not None:
                    rollback_path.unlink(missing_ok=True)
        if rollback_errors:
            raise RuntimeError(
                f"artifact pair commit failed ({original_error}); rollback failed: "
                + "; ".join(rollback_errors)
            ) from original_error
        raise
    finally:
        for temporary, _path in staged:
            temporary.unlink(missing_ok=True)
