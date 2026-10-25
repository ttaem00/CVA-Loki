"""Resolve short-lived CHZZK playback sources without exposing them publicly."""

from __future__ import annotations

import hashlib

import json

import math

import time

from dataclasses import dataclass, field

from io import BytesIO

from pathlib import Path

from typing import Any, Mapping

from urllib.parse import urlsplit

import requests

from content.network import NetworkManager

from .contracts import MediaToolError

@dataclass(frozen=True)
class ResolvedChzzkSource:
    video_no: str
    video_id: str
    duration_sec: float
    selected_height: int | None
    source_kind: str
    selected_audio_bitrate: int | None
    source_revision: str
    media_url: str = field(repr=False)

def _select_representation(representations: list[list[Any]], target_height: int) -> tuple[int, str]:
    normalized: list[tuple[int, str]] = []
    for row in representations:
        if len(row) < 2:
            continue
        try:
            height = int(row[0])
        except (TypeError, ValueError):
            continue
        url = row[1].strip() if isinstance(row[1], str) else ""
        parts = urlsplit(url)
        if height > 0 and parts.scheme in {"http", "https"} and parts.netloc and not parts.username and not parts.password:
            normalized.append((height, url))
    if not normalized:
        raise MediaToolError("source_unavailable", "CHZZK returned no usable video representation")
    heights = sorted({height for height, _url in normalized})
    not_above = [height for height in heights if height <= target_height]
    selected_height = not_above[-1] if not_above else heights[0]
    same_height = [item for item in normalized if item[0] == selected_height]




    same_height.sort(
        key=lambda item: (
            0 if urlsplit(item[1]).path.lower().endswith(".mp4") else 1,
            item[1],
        )
    )
    return same_height[0]

def _select_audio_representation(representations: list[Mapping[str, Any]]) -> tuple[int, str]:
    normalized: list[tuple[int, str]] = []
    for row in representations:
        try:
            bandwidth = max(0, int(row.get("bandwidth") or 0))
        except (TypeError, ValueError):
            bandwidth = 0
        url = str(row.get("url") or "").strip()
        if url:
            normalized.append((bandwidth, url))
    if not normalized:
        raise MediaToolError("source_unavailable", "CHZZK returned no usable audio representation")
    direct = [row for row in normalized if urlsplit(row[1]).path.lower().endswith((".mp4", ".m4a"))]
    candidates = direct or normalized
    return max(candidates, key=lambda row: (row[0], row[1]))

def resolve_chzzk_source(
    video_no: str,
    *,
    target_height: int,
    prefer_audio_only: bool = False,
    cookies: Mapping[str, str] | None = None,
    public_only: bool = False,
) -> ResolvedChzzkSource:
    """Resolve a fresh source URL and a stable, non-secret source identity."""

    try:
        if public_only:
            video_id, in_key, _adult, _status, live_rewind, metadata = NetworkManager.get_video_info(
                video_no, {}, public_only=True)
        else:
            video_id, in_key, _adult, _status, live_rewind, metadata = NetworkManager.get_video_info(
                video_no, dict(cookies or {}))
        representations: list[list[Any]] = []
        audio_representations: list[Mapping[str, Any]] = []
        if video_id and in_key:
            media_manifest = NetworkManager.get_video_dash_media_manifest(video_id, in_key)
            representations = media_manifest["video"]
            audio_representations = media_manifest["audio"]
        if not representations and live_rewind:
            representations, _auto_height, _auto_url = NetworkManager.get_video_m3u8_manifest(live_rewind)



            heights = sorted({int(row[0]) for row in representations
                              if row and str(row[0]).isdigit() and int(row[0]) > 0})
            if not heights:
                raise MediaToolError("source_unavailable", "CHZZK returned no usable video representation")
            not_above = [height for height in heights if height <= target_height]
            height = not_above[-1] if not_above else heights[0]
            hls_url = NetworkManager.get_video_m3u8_base_url(live_rewind, height)
            representations = [[row[0], hls_url if int(row[0]) == height else row[1]]
                               for row in representations if row and str(row[0]).isdigit()]
    except MediaToolError:
        raise
    except Exception as exc:
        raise MediaToolError("source_unavailable", "CHZZK playback metadata could not be resolved") from exc

    if prefer_audio_only and audio_representations:
        selected_audio_bitrate, media_url = _select_audio_representation(audio_representations)
        selected_height = None
        source_kind = "audio_only"
    else:
        selected_height, media_url = _select_representation(representations, target_height)
        selected_audio_bitrate = None
        source_kind = "muxed_video"
    try:
        duration_sec = float((metadata or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration_sec = 0
    if duration_sec <= 0:
        raise MediaToolError("source_unavailable", "CHZZK returned an invalid VOD duration")

    revision_payload = {
        "video_no": video_no,
        "video_id": str(video_id or "live-rewind"),
        "duration_sec": round(duration_sec, 3),
        "available_heights": sorted({int(row[0]) for row in representations if row and str(row[0]).isdigit()}),
        "available_audio_bitrates": sorted({
            int(row.get("bandwidth") or 0)
            for row in audio_representations
            if str(row.get("bandwidth") or "0").isdigit()
        }),
    }
    source_revision = hashlib.sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ResolvedChzzkSource(
        video_no=video_no,
        video_id=str(video_id or "live-rewind"),
        duration_sec=duration_sec,
        selected_height=selected_height,
        source_kind=source_kind,
        selected_audio_bitrate=selected_audio_bitrate,
        source_revision=source_revision,
        media_url=media_url,
    )

def resolve_chzzk_preview_catalog(video_no: str) -> dict[str, Any]:
    """Resolve public seeking images; the URL pattern stays private tool input."""
    try:
        video_id, in_key, _adult, _status, _rewind, metadata = NetworkManager.get_video_info(
            video_no, {}, public_only=True)
        manifest = NetworkManager.get_video_dash_media_manifest(video_id, in_key) if video_id and in_key else {}
        preview = manifest.get("seeking_thumbnail")
        duration = float(metadata.get("duration") or 0)
        parts = urlsplit(preview["source_pattern"]) if preview else None
        if (not preview or not math.isfinite(duration) or duration <= 0 or
                parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password):
            raise MediaToolError("preview_unavailable", "CHZZK public timeline preview is unavailable")
        public = {k: v for k, v in preview.items() if k != "source_pattern"}
        identity = dict(video_no=str(video_no), video_id=str(video_id), duration_sec=duration, seeking_thumbnail=public)
        return dict(video_no=str(video_no), duration_sec=duration, seeking_thumbnail=preview,
                    source_revision_sha256=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
                    manifest_sha256=manifest["manifest_sha256"])
    except MediaToolError:
        raise
    except Exception:
        raise MediaToolError("preview_unavailable", "CHZZK public timeline preview could not be resolved") from None

def preview_window(start_sec, duration_sec):
    """Provider navigation pictures have no video-decode duration limit."""
    from .contracts import _finite_number
    start, duration = _finite_number(start_sec, "start_sec"), _finite_number(duration_sec, "duration_sec")
    if start < 0 or duration <= 0:
        raise MediaToolError("invalid_request", "preview needs a nonnegative start and positive duration")
    return start, duration

def read_chzzk_preview_frames(catalog: Mapping[str, Any], start_sec: float, duration_sec: float,
                             output_dir: Path, *, max_frames: int = 9) -> list[dict[str, Any]]:
    """Crop existing provider sprites only, keeping their coarse time intervals."""
    from PIL import Image
    start_sec, duration_sec = preview_window(start_sec, duration_sec)
    if type(max_frames) is not int or not 1 <= max_frames <= 9:
        raise MediaToolError("invalid_request", "max_frames must be between 1 and 9")
    preview = catalog["seeking_thumbnail"]
    interval, duration = preview["interval_sec"], catalog["duration_sec"]
    end = min(start_sec + duration_sec, duration)
    indices = [i for i in range(math.ceil(start_sec / interval),
                               min(preview["totalPieceSize"], math.ceil(end / interval)))
               if min((i + 1) * interval, duration) <= end]
    if len(indices) > max_frames:
        indices = ([indices[len(indices) // 2]] if max_frames == 1 else
                   [indices[round(i * (len(indices) - 1) / (max_frames - 1))] for i in range(max_frames)])
    if not indices:
        return []

    columns, rows = preview["row"], preview["column"]
    pages = {i // (rows * columns) for i in indices}
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started, downloaded = time.monotonic(), 0
    sprites = {}
    current_page, image = None, None
    try:
        for page in sorted(pages):
            cache = output / f"sprite-{page}.jpg"
            if cache.is_file():
                data = cache.read_bytes()
            else:
                url = preview["source_pattern"].replace("#.", f"{page}.")
                with requests.get(url, cookies={}, headers={"User-Agent": "Mozilla/5.0"},
                                  timeout=(10, 20), stream=True) as response:
                    response.raise_for_status()
                    buffer = bytearray()
                    for chunk in response.iter_content(65536):
                        buffer.extend(chunk)
                        if downloaded + len(buffer) > 20 * 1024 * 1024 or time.monotonic() - started > 60:
                            raise MediaToolError("preview_budget_exceeded", "Timeline preview image budget exceeded")
                data = bytes(buffer)
            downloaded += len(data)
            if downloaded > 20 * 1024 * 1024 or time.monotonic() - started > 60:
                raise MediaToolError("preview_budget_exceeded", "Timeline preview image budget exceeded")
            if not cache.is_file():
                cache.write_bytes(data)
            sprites[page] = (data, hashlib.sha256(data).hexdigest())
        frames = []
        for index in indices:
            page, local = divmod(index, rows * columns)

            x, y = (local % columns) * preview["width"], (local // columns) * preview["height"]
            box = [x, y, x + preview["width"], y + preview["height"]]
            data, sprite_sha = sprites[page]
            if current_page != page:
                if image is not None:
                    image.close()
                image = Image.open(BytesIO(data))
                image.load()
                if image.size != (preview["width"] * columns, preview["height"] * rows):
                    raise MediaToolError("preview_unavailable", "Timeline preview sprite dimensions changed")
                current_page = page
            path = output / f"preview-{index}.png"
            image.crop(tuple(box)).convert("RGB").save(path)
            data = path.read_bytes()
            source_interval = [index * interval, min((index + 1) * interval, duration)]
            frames.append(dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                mime_type="image/png", source_sec=source_interval[0], source_interval_sec=source_interval,
                frame_index=index, delivered_dimensions=[preview["width"], preview["height"]],
                source_height=preview["height"], tile=dict(page_index=page, crop_box=box, sprite_sha256=sprite_sha),
                time_binding=dict(clock_basis="provider_seeking_thumbnail", actual_frame_pts_verified=False,
                    interval_semantics="nominal provider navigation interval, not verified frame PTS",
                    source_revision_sha256=catalog["source_revision_sha256"],
                    manifest_sha256=catalog["manifest_sha256"])))
        return frames
    except MediaToolError:
        raise
    except Exception:
        raise MediaToolError("preview_unavailable", "CHZZK timeline preview images could not be read") from None
    finally:
        if image is not None:
            image.close()
