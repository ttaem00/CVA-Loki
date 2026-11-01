"""Frame extraction and compact chronological contact sheets."""

from __future__ import annotations

from pathlib import Path

from typing import Sequence

import math

from PIL import Image, ImageDraw, ImageOps

from .contracts import MediaToolError

def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def build_contact_sheet(
    frame_paths: Sequence[Path],
    timestamps_sec: Sequence[float],
    output_path: Path,
    *,
    columns: int = 3,
    tile_width: int = 854,
    tile_height: int = 480,
    labels: Sequence[str] | None = None,
) -> Path:
    if not 1 <= len(frame_paths) <= 9 or len(timestamps_sec) != len(frame_paths) or labels is not None and len(labels) != len(frame_paths):
        raise MediaToolError("frame_count_mismatch", "contact sheets require 1 to 9 matching frames and labels")
    label_height = 30
    columns = min(columns, len(frame_paths))
    rows = math.ceil(len(frame_paths) / columns)
    sheet = Image.new("RGB", (columns * tile_width, rows * (tile_height + label_height)), "black")
    draw = ImageDraw.Draw(sheet)
    for index, (frame_path, timestamp) in enumerate(zip(frame_paths, timestamps_sec)):
        with Image.open(frame_path) as source:
            tile = ImageOps.pad(source.convert("RGB"), (tile_width, tile_height), color="black")
        x = (index % columns) * tile_width
        y = (index // columns) * (tile_height + label_height)
        sheet.paste(tile, (x, y))
        draw.text((x + 10, y + tile_height + 7), labels[index] if labels is not None else format_timestamp(timestamp), fill="white")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="JPEG", quality=82, optimize=True)
    return output_path
