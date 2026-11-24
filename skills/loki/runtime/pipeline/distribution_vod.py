"""Admission for one public replay in an installed Linux distribution."""

from __future__ import annotations

from dataclasses import dataclass

import os

from pathlib import Path

from typing import Mapping

from .codex_runtime import is_native_linux_release

from .local_install import installation

KEY = "_distribution_vod_source"

_DATA_ROOT = Path("/cva-data")

_MINT = object()

@dataclass(frozen=True)
class _Grant:
    authority: object
    installation_id: str
    video_no: str
    channel_id: str
    started_at: str
    published_at: str
    duration_sec: int

    def __deepcopy__(self, memo):
        return self

def distribution_runtime() -> str | None:
    if os.environ.get("CVA_LINUX_VOD_WRITER") != "codex":
        return None
    if not is_native_linux_release() or any(
        os.environ.get(key) != str(_DATA_ROOT)
        for key in ("CHZZ_PIPELINE_RUNTIME_ROOT", "CHZZ_RUNTIME_DATA_ROOT")
    ):
        raise ValueError("distribution_vod_runtime_invalid")
    marker = installation(_DATA_ROOT, code_root=Path(__file__).resolve().parents[1])
    if marker.get("initialized") is not True:
        raise ValueError("distribution_vod_installation_incomplete")
    return marker["installationId"]

def distribution_source(cfg: Mapping, video_no: str | None = None) -> dict | None:
    if KEY not in cfg:
        return None
    grant = cfg[KEY]
    if (type(grant) is not _Grant or grant.authority is not _MINT
            or grant.installation_id != distribution_runtime()
            or grant.channel_id != cfg.get("target_channel_id")
            or (video_no is not None and grant.video_no != video_no)):
        raise ValueError("distribution_vod_source_mismatch")
    return {
        "installation_id": grant.installation_id, "video_no": grant.video_no,
        "channel_id": grant.channel_id, "started_at": grant.started_at,
        "published_at": grant.published_at, "duration_sec": grant.duration_sec,
    }
