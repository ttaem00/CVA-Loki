"""Broadcast-bound original 1080 stills from live HLS or an already aligned VOD.

The existing public reader owns URL admission, bounded downloads and in-memory
AES handling. VOD capture reuses the existing owner's verified clock and public
source resolver; changed source clocks are refreshed by that same owner. It
never substitutes a resized preview.
"""

from __future__ import annotations

import math

import re

import subprocess

class OriginalFrameUnavailable(Exception):
    """Only module-owned fixed codes may cross the HTTP boundary."""

    def __init__(self, code):
        self.code = "source_original_" + code
        super().__init__(self.code)

def _mapped_vod_clock(value, archive):
    """Pin only the existing owner's clock, not its unrelated processing state."""
    from pipeline.live_vod_repair import CLOCK_PROOF_VERSION

    mapping = (value.get("manualFrameMapping") or value.get("mapping")) if isinstance(value, dict) else None
    if (not isinstance(mapping, dict) or value.get("archive") != archive
            or mapping.get("archive") != archive or mapping.get("status") != "aligned"
            or not isinstance(value.get("videoNo"), str)
            or not re.fullmatch(r"[1-9][0-9]{0,11}", value["videoNo"])
            or mapping.get("videoNo") != value["videoNo"]
            or mapping.get("clockProofVersion") != CLOCK_PROOF_VERSION
            or type(mapping.get("originMs")) is not int or not 0 < mapping["originMs"] < 2**53
            or type(mapping.get("durationMs")) is not int or not 0 < mapping["durationMs"] < 2**53
            or type(mapping.get("clockToleranceMs")) is not int
            or not 0 <= mapping["clockToleranceMs"] <= 100
            or not isinstance(mapping.get("sourceRevision"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", mapping["sourceRevision"])):
        raise OriginalFrameUnavailable("vod_clock_unverified")

    anchors = mapping.get("anchors")
    if (not isinstance(anchors, list) or len(anchors) != 2
            or any(not isinstance(row, dict) or not row.get("audioId")
                   or not isinstance(row.get("sha256"), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) for row in anchors)):
        raise OriginalFrameUnavailable("vod_clock_unverified")
    return {"videoNo": value["videoNo"], "sourceRevision": mapping["sourceRevision"],
        "version": CLOCK_PROOF_VERSION, "originMs": mapping["originMs"],
        "durationMs": mapping["durationMs"], "toleranceMs": mapping["clockToleranceMs"],
        "anchors": tuple((row["audioId"], row["sha256"]) for row in anchors)}

def _decode_mapped_vod(source_value, vod_time, duration, remaining):
    from pipeline.media_tools.executor import _tool_paths, _window_input

    executable, _ = _tool_paths()
    remaining()
    arguments, playlist = _window_input(executable, source_value.media_url, vod_time, duration)
    remaining()
    if playlist is None:


        seek = float(arguments[arguments.index("-ss") + 1])
        target = vod_time - seek
        base = seek
        normalize = ""
    else:


        trim = float(arguments[-1])
        arguments = arguments[:-2]




        target = trim
        base = vod_time - target
        normalize = "setpts=PTS-STARTPTS,"
    if not math.isfinite(target) or not math.isfinite(base):
        raise OriginalFrameUnavailable("time_unbound")

    input_index = arguments.index("-i")
    arguments[input_index:input_index] = ["-noautorotate", "-max_pixels", "8388608"]
    if playlist is None:
        arguments[input_index:input_index] = ["-t", f"{duration + .5:.6f}"]
    command = [executable, "-nostdin", "-hide_banner", "-loglevel", "info",
        "-threads", "1", "-filter_threads", "1", *arguments,
        "-map", "0:v:0", "-vf",
        f"{normalize}select='gte(t,{target:.9f})*lt(t,{target + duration:.9f})',showinfo",
        "-frames:v", "1", "-vsync", "0", "-an", "-sn", "-dn", "-c:v", "mjpeg",
        "-threads", "1", "-q:v", "2", "-f", "image2pipe", "pipe:1"]
    completed = subprocess.run(command, input=playlist.encode("utf-8") if playlist is not None else None,
        capture_output=True, timeout=min(10, remaining()), check=False)
    remaining()
    if completed.returncode != 0 or not 0 < len(completed.stdout) <= 4_000_000:
        raise OriginalFrameUnavailable("decode_unavailable")
    stamps = re.findall(rb"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:([0-9.eE+-]+)", completed.stderr)
    if not stamps:
        raise OriginalFrameUnavailable("time_unbound")
    measured = float(stamps[0])
    return completed.stdout, (base + measured) * 1000, measured * 1000
