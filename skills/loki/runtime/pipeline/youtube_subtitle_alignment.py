"""Public subtitle/text alignment pilot for YouTube edits and CHZZK transcripts."""

from __future__ import annotations

import html

import json

import re

import xml.etree.ElementTree as ET

from dataclasses import dataclass

from pathlib import Path

from typing import Any

@dataclass(frozen=True)
class CaptionCue:
    start_sec: float
    end_sec: float
    text: str

def load_caption_file(path: str | Path) -> list[CaptionCue]:
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig")
    if source.suffix.lower() == ".json":
        try:
            return parse_json_cues(json.loads(text))
        except json.JSONDecodeError:
            pass
    return parse_caption_text(text)

def parse_json_cues(data: Any) -> list[CaptionCue]:
    rows = data
    if isinstance(data, dict):
        for key in ("cues", "segments", "items", "subtitles", "transcript"):
            value = data.get(key)
            if isinstance(value, list):
                rows = value
                break
    if not isinstance(rows, list):
        return []

    cues: list[CaptionCue] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _number_or_none(_first_present(row, "start_sec", "start", "from"))
        end = _number_or_none(_first_present(row, "end_sec", "end", "to"))
        duration = _number_or_none(_first_present(row, "duration", "duration_sec"))
        text = normalize_caption_text(row.get("text") or row.get("caption") or row.get("line") or "")
        if start is None or not text:
            continue
        if end is None and duration is not None:
            end = start + duration
        if end is None:
            end = start + 1.0
        if end > start:
            cues.append(CaptionCue(round(start, 3), round(end, 3), text))
    return cues

def parse_caption_text(text: str) -> list[CaptionCue]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    xml_cues = _parse_youtube_timedtext_xml(normalized)
    if xml_cues:
        return xml_cues
    blocks = re.split(r"\n\s*\n", normalized)
    cues: list[CaptionCue] = []
    block_index = 0
    while block_index < len(blocks):
        block = blocks[block_index]
        block_index += 1
        lines = [line.strip("\ufeff ") for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        timing_index = next((idx for idx, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            cue = _parse_inline_timestamp(lines)
            if cue is not None:
                cues.append(cue)
            continue
        start, end = _parse_timing_line(lines[timing_index])
        if start is None or end is None or end <= start:
            continue
        cue_text = normalize_caption_text(" ".join(lines[timing_index + 1 :]))
        if not cue_text and block_index < len(blocks):



            following = [line.strip("\ufeff ") for line in blocks[block_index].split("\n") if line.strip()]
            if (following and not any("-->" in line for line in following)
                    and following[0].split(maxsplit=1)[0] not in {"WEBVTT", "NOTE", "STYLE", "REGION"}):
                cue_text = normalize_caption_text(" ".join(following))
                block_index += 1
        if cue_text:
            cues.append(CaptionCue(round(start, 3), round(end, 3), cue_text))
    return cues

def _parse_youtube_timedtext_xml(text: str) -> list[CaptionCue]:
    if "<text" not in text:
        return []
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        return []
    cues: list[CaptionCue] = []
    for node in root.iter("text"):
        start = _number_or_none(node.attrib.get("start"))
        duration = _number_or_none(node.attrib.get("dur"))
        cue_text = normalize_caption_text("".join(node.itertext()))
        if start is None or duration is None or duration <= 0 or not cue_text:
            continue
        cues.append(CaptionCue(round(start, 3), round(start + duration, 3), cue_text))
    return cues

def normalize_caption_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{\\[^}]+\}", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _parse_timing_line(line: str) -> tuple[float | None, float | None]:
    left, _, right = line.partition("-->")
    return _parse_timestamp(left), _parse_timestamp(right.split()[0] if right.split() else right)

def _parse_inline_timestamp(lines: list[str]) -> CaptionCue | None:
    joined = " ".join(lines)
    match = re.match(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\]?\s+(.+)$", joined)
    if not match:
        return None
    start = _parse_timestamp(match.group(1))
    text = normalize_caption_text(match.group(2))
    if start is None or not text:
        return None
    return CaptionCue(round(start, 3), round(start + 1.0, 3), text)

def _parse_timestamp(value: str) -> float | None:
    text = value.strip().replace(",", ".")
    text = re.sub(r"\s+.*$", "", text)
    parts = text.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return None
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None

def _number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None
