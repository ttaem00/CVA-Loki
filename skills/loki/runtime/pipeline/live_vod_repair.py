"""Bounded public VOD repair with two independent, hash-bound audio anchors.

This helper never changes STOP, runs a model, or edits the source database.
Only the existing host engine can adopt the returned fragments.
"""

from __future__ import annotations

import hashlib

from pathlib import Path

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def local_file(folder, relative):
    if not isinstance(relative, str) or ':' in relative or '\\' in relative or any(part in {'', '.', '..'} for part in relative.split('/')):
        raise ValueError('repair_path_invalid')
    path = folder / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('repair_path_invalid')
    for part in [path, *path.parents]:
        if part == folder.parent:
            break
        if part.is_symlink():
            raise ValueError('repair_path_invalid')
    path.resolve().relative_to(folder.resolve())
    return path
