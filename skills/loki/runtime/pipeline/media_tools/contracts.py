"""Validated public contracts for bounded media tool calls."""

from __future__ import annotations

import hashlib

import json

import re

from dataclasses import dataclass

from typing import Any, Literal, Mapping

Action = Literal["audio", "video", "frames"]

AudioProfile = Literal["stt", "listen", "archive"]

InspectionMode = Literal["scene", "ocr"]

_VIDEO_NO_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_ACTIONS = {"audio", "video", "frames"}

_AUDIO_PROFILES = {"stt", "listen", "archive"}

_INSPECTION_MODES = {"scene", "ocr"}

class MediaToolError(RuntimeError):
    """A safe, user-displayable media tool failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message

def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MediaToolError("invalid_request", f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaToolError("invalid_request", f"{field} must be a number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise MediaToolError("invalid_request", f"{field} must be finite")
    return number

@dataclass(frozen=True)
class ToolRequest:
    """One safe media request. Signed URLs and filesystem paths are never inputs."""

    action: Action
    video_no: str
    start_sec: float = 0.0
    duration_sec: float | None = None
    audio_profile: AudioProfile = "stt"
    frame_count: int = 9
    inspection_mode: InspectionMode = "scene"
    request_id: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ToolRequest":
        if not isinstance(payload, Mapping):
            raise MediaToolError("invalid_request", "tool payload must be an object")
        allowed = {
            "action",
            "video_no",
            "start_sec",
            "duration_sec",
            "audio_profile",
            "frame_count",
            "inspection_mode",
            "request_id",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise MediaToolError("invalid_request", f"unknown fields: {', '.join(unknown)}")

        action = str(payload.get("action", "")).strip().lower()
        if action not in _ACTIONS:
            raise MediaToolError("invalid_request", "action must be audio, video, or frames")
        video_no = str(payload.get("video_no", "")).strip()
        if not _VIDEO_NO_RE.fullmatch(video_no):
            raise MediaToolError("invalid_request", "video_no has an invalid format")

        start_sec = _finite_number(payload.get("start_sec", 0), "start_sec")
        if start_sec < 0:
            raise MediaToolError("invalid_request", "start_sec must be zero or greater")

        raw_duration = payload.get("duration_sec")
        duration_sec = None if raw_duration in (None, "") else _finite_number(raw_duration, "duration_sec")
        if duration_sec is not None and duration_sec <= 0:
            raise MediaToolError("invalid_request", "duration_sec must be greater than zero")

        audio_profile = str(payload.get("audio_profile", "stt")).strip().lower()
        if audio_profile not in _AUDIO_PROFILES:
            raise MediaToolError("invalid_request", "audio_profile must be stt, listen, or archive")

        inspection_mode = str(payload.get("inspection_mode", "scene")).strip().lower()
        if inspection_mode not in _INSPECTION_MODES:
            raise MediaToolError("invalid_request", "inspection_mode must be scene or ocr")

        try:
            frame_count = int(payload.get("frame_count", 9))
        except (TypeError, ValueError) as exc:
            raise MediaToolError("invalid_request", "frame_count must be an integer") from exc

        if action in {"video", "frames"} and duration_sec is None:
            raise MediaToolError("invalid_request", f"{action} requires duration_sec")
        if action == "video" and duration_sec is not None and duration_sec > 7200:
            raise MediaToolError("invalid_request", "video windows are limited to 7200 seconds")
        if action == "frames" and duration_sec is not None and duration_sec > 1800:
            raise MediaToolError("invalid_request", "frame windows are limited to 1800 seconds")
        if action == "frames" and frame_count != 9:
            raise MediaToolError("invalid_request", "frames currently requires exactly 9 frames")
        if action != "audio" and "audio_profile" in payload:
            raise MediaToolError("invalid_request", "audio_profile is only valid for audio")
        if action != "frames" and "frame_count" in payload:
            raise MediaToolError("invalid_request", "frame_count is only valid for frames")
        if action != "frames" and "inspection_mode" in payload:
            raise MediaToolError("invalid_request", "inspection_mode is only valid for frames")

        request_id = str(payload.get("request_id", "")).strip()[:120]
        return cls(
            action=action,
            video_no=video_no,
            start_sec=start_sec,
            duration_sec=duration_sec,
            audio_profile=audio_profile,
            frame_count=frame_count,
            inspection_mode=inspection_mode,
            request_id=request_id,
        )

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "action": self.action,
            "video_no": self.video_no,
            "start_sec": round(self.start_sec, 3),
        }
        if self.duration_sec is not None:
            payload["duration_sec"] = round(self.duration_sec, 3)
        if self.action == "audio":
            payload["audio_profile"] = self.audio_profile
        if self.action == "frames":
            payload["frame_count"] = self.frame_count
            payload["inspection_mode"] = self.inspection_mode
        return payload

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def with_request_id(self, request_id: str) -> "ToolRequest":
        return ToolRequest(
            action=self.action,
            video_no=self.video_no,
            start_sec=self.start_sec,
            duration_sec=self.duration_sec,
            audio_profile=self.audio_profile,
            frame_count=self.frame_count,
            inspection_mode=self.inspection_mode,
            request_id=request_id,
        )
