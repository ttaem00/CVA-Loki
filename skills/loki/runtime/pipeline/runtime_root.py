"""The standalone workspace is explicit and never inferred from a host profile."""

import os

from pathlib import Path

class RuntimeRootError(RuntimeError):
    pass

def runtime_data_root(**kwargs):
    value = os.environ.get("LOKI_WORKSPACE")
    if not value:
        raise RuntimeRootError("Run the Loki entrypoint with --config <workspace/loki.json>.")
    return Path(value).resolve(strict=True)

def runtime_data_path(*parts, **kwargs):
    return runtime_data_root() / Path(*parts)
