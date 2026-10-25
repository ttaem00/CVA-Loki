"""Standalone identity comes from the frozen export manifest, without git probes."""

import json

from pathlib import Path

def get_runtime_code_version():
    manifest = Path(__file__).resolve().parents[1] / "source-manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["source_commit"]
