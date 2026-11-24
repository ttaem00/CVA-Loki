"""Inspect verified local MP4 and MPEG-TS archived playback sources."""

from __future__ import annotations

import json

import math

import os

from pathlib import Path

import re

import stat

import subprocess

import time

from typing import BinaryIO

_PACKET_SIZE = 188

_TARGET_SEGMENT_SECONDS = 6.0

_MAX_SEGMENT_SECONDS = 12.0

_MAX_SEGMENT_BYTES = 16 * 1024 * 1024

_DURATION_TOLERANCE_SECONDS = 2.0

_START_TOLERANCE_SECONDS = 0.1

_MAX_PES_HEADER_PACKETS = 32

_NAL_START = re.compile(rb"\x00\x00\x01([\x00-\xff])")

def _probe(path: Path, *args: str, timeout: float, mp4: bool = False) -> dict:
    demux = ["-format_whitelist", "mov", "-enable_drefs", "0", "-use_absolute_path", "0"] if mp4 else [
        "-format_whitelist", "mpegts"]
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file",
                *demux, *args, "-of", "json", str(path),
            ],
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        if result.stderr:
            raise ValueError("archived transport contains media errors")
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError("archived transport probe failed") from error

def inspect_archived_mp4(path: Path, *, duration: float, expected_size: int, deadline: float) -> int | None:
    """Return the height of a seekable, zero-based AVC/AAC ISO media file."""
    with path.open("rb") as stream:
        header = stream.read(16)
    if header[4:8] != b"ftyp":
        return None
    if len(header) < 16 or not 16 <= int.from_bytes(header[:4], "big") <= min(expected_size, 65_536):
        raise ValueError("archived MP4 header invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("archived MP4 probe deadline")
    proof = _probe(path, "-show_entries",
        "format=format_name,start_time,duration,size:format_tags=major_brand:"
        "stream=index,codec_type,codec_name,codec_tag_string,pix_fmt,height,start_time,duration:"
        "stream_disposition=attached_pic", timeout=remaining, mp4=True)
    try:
        container, tracks = proof["format"], proof["streams"]
        streams = {(track["codec_type"], track["codec_name"], track["codec_tag_string"]): track for track in tracks}
        video, audio = streams["video", "h264", "avc1"], streams["audio", "aac", "mp4a"]
        height = video["height"]
        if (len(tracks) != 2 or "mp4" not in container["format_name"].split(",")
                or container["tags"]["major_brand"] not in {"isom", "iso2", "mp41", "mp42", "avc1"}
                or int(container["size"]) != expected_size or video["disposition"]["attached_pic"] != 0
                or video["pix_fmt"] != "yuv420p" or type(height) is not int or not 1 <= height <= 2160
                or not all(abs(float(item["start_time"])) <= _START_TOLERANCE_SECONDS
                           and abs(float(item["duration"]) - duration) <= _DURATION_TOLERANCE_SECONDS
                           for item in (container, video, audio))):
            raise ValueError("archived MP4 streams or catalogue clock invalid")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("archived MP4 streams or catalogue clock invalid") from error
    return height

def _payload_offset(packet: bytes, pid: int) -> int:
    if len(packet) != _PACKET_SIZE or packet[0] != 0x47 or packet[1] & 0x80:
        return -1
    if ((packet[1] & 0x1F) << 8 | packet[2]) != pid or packet[3] & 0xC0:
        return -1
    control = (packet[3] >> 4) & 3
    if control == 1:
        return 4
    if control == 3 and packet[4] < 183:
        return 5 + packet[4]
    return -1

def _table(packet: bytes, pid: int, table_id: int) -> bytes:
    offset = _payload_offset(packet, pid)
    if offset < 0 or not packet[1] & 0x40:
        return b""
    start = offset + 1 + packet[offset]
    if start + 8 > _PACKET_SIZE or packet[start] != table_id or not packet[start + 1] & 0x80:
        return b""
    length = (packet[start + 1] & 0x0F) << 8 | packet[start + 2]
    end = start + 3 + length
    if length < 13 or end > _PACKET_SIZE or (packet[start + 5] & 1) == 0:
        return b""
    if packet[start + 6] != 0 or packet[start + 7] != 0:
        return b""
    return packet[start:end]

def _program_tables(stream: BinaryIO, offset: int, pmt_pid: int) -> tuple[bytes, bytes]:
    stream.seek(offset)
    return (
        _table(stream.read(_PACKET_SIZE), 0, 0),
        _table(stream.read(_PACKET_SIZE), pmt_pid, 2),
    )

def _starts_with_avc_keyframe(stream: BinaryIO, video_pid: int) -> bool:
    packet = stream.read(_PACKET_SIZE)
    offset = _payload_offset(packet, video_pid)
    if offset < 0 or not packet[1] & 0x40:
        return False
    if not (packet[3] & 0x20 and packet[4] and packet[5] & 0x40):
        return False
    payload = packet[offset:]
    if len(payload) < 9 or payload[:3] != b"\0\0\1" or not 0xE0 <= payload[3] <= 0xEF:
        return False
    data = bytearray(payload[9 + payload[8]:])
    for _ in range(_MAX_PES_HEADER_PACKETS):
        nal_types = [match[1][0] & 31 for match in _NAL_START.finditer(data)]
        first_slice = next((index for index, kind in enumerate(nal_types) if 1 <= kind <= 5), None)
        if first_slice is not None:
            return nal_types[first_slice] == 5 and {7, 8}.issubset(nal_types[:first_slice])
        packet = stream.read(_PACKET_SIZE)
        offset = _payload_offset(packet, video_pid)
        if offset >= 0:
            if packet[1] & 0x40:
                return False
            data.extend(packet[offset:])
    return False

def _checked_segment(start: int, end: int, duration: float) -> tuple[int, int, float]:
    duration = round(duration, 6)
    if not (0 < end - start <= _MAX_SEGMENT_BYTES and 0 < duration <= _MAX_SEGMENT_SECONDS):
        raise ValueError("archived transport has no bounded random-access segmentation")
    return start, end, duration

def index_archived_transport(
    path: str | Path, *, duration: float, expected_size: int, deadline: float | None = None
) -> tuple[int, tuple[tuple[int, int, float], ...]]:
    """Return coded height and contiguous (start, end-exclusive, seconds) HLS segments.

    The caller verifies catalogue ownership and hash. Reject unsupported streams
    and archive clocks or segments that cannot support seeking without remuxing.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid archived transport duration")
    if expected_size <= 2 * _PACKET_SIZE or expected_size % _PACKET_SIZE:
        raise ValueError("archived transport is not packet-aligned")

    deadline = deadline if deadline is not None else time.monotonic() + 40

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError("archived transport index deadline exceeded")
        return left

    path = Path(path).absolute()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError("archived transport size changed")

        metadata = _probe(
            path, "-show_entries",
            "format=format_name,duration,size:program=program_id,pmt_pid:"
            "program_stream=index,id,codec_name,codec_type,height,start_time,duration",
            timeout=remaining(),
        )
        try:
            file_format = metadata["format"]
            (program,) = metadata["programs"]
            streams = {(s["codec_type"], s["codec_name"]): s for s in program["streams"]}
            video = streams["video", "h264"]
            audio = streams["audio", "aac"]
            pmt_pid = int(program["pmt_pid"])
            program_number = int(program["program_id"])
            video_pid = int(video["id"], 0)
            video_start = float(video["start_time"])
            video_duration = float(video["duration"])
            audio_duration = float(audio["duration"])
            format_duration = float(file_format["duration"])
            height = int(video["height"])
            size = int(file_format["size"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("archived transport needs one H.264 and AAC program") from error
        if (
            len(program["streams"]) != 2
            or height <= 0
            or size != expected_size
            or file_format["format_name"] != "mpegts"
        ):
            raise ValueError("unsupported archived transport format")
        if (
            not math.isfinite(video_start)
            or any(
                not math.isfinite(span) or abs(span - duration) > _DURATION_TOLERANCE_SECONDS
                for span in (video_duration, audio_duration, format_duration)
            )
        ):
            raise ValueError("archived transport duration does not match its catalogue clock")

        pat, pmt = _program_tables(stream, 0, pmt_pid)
        entries = [
            (int.from_bytes(pat[i:i + 2], "big"), int.from_bytes(pat[i + 2:i + 4], "big") & 0x1FFF)
            for i in range(8, len(pat) - 4, 4)
            if pat[i:i + 2] != b"\0\0"
        ]
        if (
            not pat
            or (len(pat) - 12) % 4
            or entries != [(program_number, pmt_pid)]
            or not pmt
            or int.from_bytes(pmt[3:5], "big") != program_number
        ):
            raise ValueError("archived transport must begin with its PAT and PMT")

        keyframes = _probe(
            path, "-select_streams", "v:0", "-skip_frame", "nokey",
            "-show_entries", "frame=key_frame,pts_time,pkt_pos", timeout=remaining(),
        ).get("frames", [])
        if not keyframes:
            raise ValueError("archived transport has no video keyframes")

        segments = []
        segment_offset = 0
        segment_start = 0.0
        previous_pos = 0
        previous_pts = -math.inf
        for index, frame in enumerate(keyframes):
            remaining()
            try:
                position = int(frame["pkt_pos"])
                pts = float(frame["pts_time"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("archived transport keyframe clock missing") from error
            if (
                frame.get("key_frame") != 1
                or not math.isfinite(pts)
                or pts <= previous_pts
                or position <= previous_pos
                or not 2 * _PACKET_SIZE <= position < expected_size
                or position % _PACKET_SIZE
            ):
                raise ValueError("archived transport keyframe clock is discontinuous")
            previous_pos = position
            previous_pts = pts
            elapsed = round(pts - video_start, 6)
            if index == 0:
                stream.seek(position)
                if (
                    position != 2 * _PACKET_SIZE
                    or abs(elapsed) > _START_TOLERANCE_SECONDS
                    or not _starts_with_avc_keyframe(stream, video_pid)
                ):
                    raise ValueError("archived transport does not start with a playable IDR")
                continue
            segment_seconds = elapsed - segment_start
            if segment_seconds < _TARGET_SEGMENT_SECONDS or video_duration - elapsed < _START_TOLERANCE_SECONDS:
                continue
            start = position - 2 * _PACKET_SIZE
            if _program_tables(stream, start, pmt_pid) != (pat, pmt):
                continue
            if not _starts_with_avc_keyframe(stream, video_pid):
                continue
            segments.append(_checked_segment(segment_offset, start, segment_seconds))
            segment_offset, segment_start = start, elapsed

        media_tail = video_duration - (previous_pts - video_start)
        if not 0 <= media_tail <= _MAX_SEGMENT_SECONDS:
            raise ValueError("archived transport keyframes do not cover the end of the video")
        segments.append(_checked_segment(segment_offset, expected_size, video_duration - segment_start))
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("archived transport changed during indexing")
    return height, tuple(segments)
