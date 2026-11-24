"""Private CHZZK playback boundary for the Report Admin review workspace.

The browser receives only same-origin playlist and media URLs. Signed CHZZK
URLs and configured cookies stay inside this module and are never serialized.
"""

from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime

from functools import lru_cache

from http import HTTPStatus

import hashlib

import json

import math

import os

from pathlib import Path

import re

import secrets

import sqlite3

import subprocess

import threading

import time

from urllib.parse import urlparse

import requests

from content.network import NetworkManager

from pipeline.downloader import _load_m3u8_media_snapshot, _resolve_m3u8_child_url

VIDEO_NO_PATTERN = re.compile(r"^[0-9]{6,20}$")

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,32}$")

SESSION_TTL_SEC = 15 * 60

ARCHIVE_INDEX_BUDGET_SEC = 40

ARCHIVE_MP4_RANGE_LIMIT = 8 * 1024 * 1024

ARCHIVE_BYTE_RANGE = re.compile(r"bytes=([0-9]{0,20})-([0-9]{0,20})", re.IGNORECASE)

def read_terminal_hls_metadata(playlist_text: str) -> dict:
    """Read a full upstream media playlist; never infer ENDLIST or retain URLs."""
    def invalid():
        raise ValueError("terminal_playlist_unverified")

    if not isinstance(playlist_text, str):
        invalid()
    lines = [line.strip() for line in playlist_text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        invalid()
    sequence, discontinuity, pending, program_at_ms, ended = 0, 0, None, None, False
    rows, sequence_tags = [], set()
    for line in lines[1:]:
        if line.startswith(("#EXT-X-MEDIA-SEQUENCE:", "#EXT-X-DISCONTINUITY-SEQUENCE:")):
            tag, value = line.split(":", 1)
            if rows or pending is not None or tag in sequence_tags or not re.fullmatch(r"\d+", value):
                invalid()
            sequence_tags.add(tag)
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                sequence = int(value)
            else:
                discontinuity = int(value)
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity += 1
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                parsed = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    invalid()
                program_at_ms = round(parsed.timestamp() * 1000)
            except (ValueError, OverflowError):
                invalid()
        elif line.startswith("#EXTINF:"):
            if pending is not None:
                invalid()
            try:
                duration = float(line.split(":", 1)[1].split(",", 1)[0])
                if not math.isfinite(duration) or duration <= 0:
                    invalid()
                pending = round(duration * 1000)
                if pending < 1:
                    invalid()
            except (ValueError, OverflowError):
                invalid()
        elif line == "#EXT-X-ENDLIST":
            if pending is not None:
                invalid()
            ended = True
        elif not line.startswith("#"):
            if pending is None or max(sequence, discontinuity) > 2**53 - 1:
                invalid()
            rows.append([sequence, discontinuity, pending, program_at_ms])
            sequence += 1
            if program_at_ms is not None:
                program_at_ms += pending
            pending = None
    if not ended or not rows or pending is not None:
        invalid()
    result = {"kind": "full_vod_hls", "ended": True, "segmentCount": len(rows),
              "firstSequence": rows[0][0], "lastSequence": rows[-1][0],
              "discontinuitySequence": rows[-1][1], "totalDurationMs": sum(row[2] for row in rows)}
    canonical = json.dumps({**result, "segments": rows}, sort_keys=True, separators=(",", ":"))
    return {**result, "manifestSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}

class AuthenticationRequired(RuntimeError):
    """Playback requires the existing dedicated CHZZK login contract."""

class ArchivedRangeUnavailable(ValueError):
    def __init__(self, size: int) -> None:
        super().__init__("archived_playback_range_unavailable")
        self.size = size

@dataclass(frozen=True)
class PlaybackBinary:
    body: bytes
    content_type: str

@dataclass(frozen=True)
class ArchivedPlayback:
    path: Path
    identity: tuple[int, int, int, int, int]
    duration: float
    height: int
    ranges: tuple[tuple[int, int, float], ...]
    transport: str = "hls"

@lru_cache(maxsize=4)
def _index_archived_original(
    path: Path, size: int, sha256: str, duration: float, identity: tuple[int, int, int, int, int]
) -> ArchivedPlayback:
    from pipeline.live_original_vod_archive import _fingerprint, _physical_identity
    from scripts.admin.report_archive_playback import index_archived_transport, inspect_archived_mp4

    deadline = time.monotonic() + ARCHIVE_INDEX_BUDGET_SEC
    if identity[2] != size or _fingerprint(path, deadline) != (size, sha256):
        raise ValueError("archived_playback_bytes_changed")
    height = inspect_archived_mp4(path, duration=duration, expected_size=size, deadline=deadline)
    if height is None:
        height, ranges = index_archived_transport(path, duration=duration, expected_size=size, deadline=deadline)
        transport = "hls"
    else:
        ranges, transport = (), "mp4"
    if _physical_identity(path.stat()) != identity:
        raise ValueError("archived_playback_bytes_changed")
    return ArchivedPlayback(path, identity, duration, height, ranges, transport)

class ReportPlaybackService:
    """Resolve and proxy one VOD without becoming an editorial state owner."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[float, list[str]]] = {}
        self._archive_sessions: dict[str, tuple[float, ArchivedPlayback]] = {}
        self._lock = threading.Lock()
        self._archive_scan = threading.Lock()

    @staticmethod
    def validate_video_no(value: object) -> str:
        video_no = str(value or "").strip()
        if not VIDEO_NO_PATTERN.fullmatch(video_no):
            raise ValueError("VOD 번호는 숫자 6~20자리여야 합니다.")
        return video_no

    @staticmethod
    def _configured_cookie_jar():
        """Use the existing project cookie contract without exposing its values."""
        try:
            from pipeline.config import get_cookies, load_config

            cookies = get_cookies(load_config())
        except Exception:
            cookies = {}
        if not isinstance(cookies, dict) or len(cookies) != 2:
            return None
        return requests.cookies.cookiejar_from_dict(cookies)

    def _streams(self, video_no: str, *, public_only: bool = False) -> tuple[dict, list[dict], str, bool]:
        cookie_jar = None if public_only else self._configured_cookie_jar()
        try:
            if public_only:
                video_id, in_key, adult, vod_status, live_rewind, metadata = NetworkManager.get_video_info(
                    video_no, None, public_only=True)
            else:
                video_id, in_key, adult, vod_status, live_rewind, metadata = NetworkManager.get_video_info(
                    video_no, cookie_jar)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise AuthenticationRequired("로그인 또는 성인 인증이 필요합니다.") from exc
            raise
        if adult and cookie_jar is None:
            raise AuthenticationRequired("로그인 또는 성인 인증이 필요한 VOD입니다.")
        if not video_id or not in_key:
            if live_rewind:
                reps, _auto_height, _auto_url = NetworkManager.get_video_m3u8_manifest(live_rewind)
                rows = [
                    {"height": int(height), "live_rewind": live_rewind}
                    for height, _url in reps
                    if int(height) > 0
                ]
                if rows:
                    return metadata if isinstance(metadata, dict) else {}, rows, str(vod_status or ""), bool(adult)
            raise RuntimeError("재생 정보를 확인할 수 없습니다.")
        try:
            reps, _auto_height, _auto_url = NetworkManager.get_video_dash_manifest(video_id, in_key)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise AuthenticationRequired("저장된 로그인 세션이 만료됐습니다.") from exc
            raise
        hls_rows = [
            {"height": int(height), "url": str(url)}
            for height, url in reps
            if isinstance(url, str) and urlparse(url).path.casefold().endswith(".m3u8")
        ]
        if not hls_rows:
            raise RuntimeError("이 VOD에서 HLS 재생 정보를 찾지 못했습니다.")
        return metadata if isinstance(metadata, dict) else {}, hls_rows, str(vod_status or ""), bool(adult)

    @staticmethod
    def _select_stream(rows: list[dict], requested_height: object) -> dict:
        height = max(144, min(int(requested_height), 2160))
        below = [row for row in rows if row["height"] <= height]
        return max(below or rows, key=lambda row: row["height"])

    def describe(self, video_no: object, requested_height: object = 720) -> dict:
        safe_video_no = self.validate_video_no(video_no)
        metadata, rows, vod_status, adult = self._streams(safe_video_no)
        selected = self._select_stream(rows, requested_height)
        return {
            "video_no": safe_video_no,
            "title": str(metadata.get("title") or f"CHZZK VOD {safe_video_no}"),
            "channel_name": str(metadata.get("channelName") or "CHZZK"),
            "duration_sec": int(metadata.get("duration") or 0),
            "vod_status": vod_status,
            "access": "manager_authenticated" if adult else "public",
            "selected_height": selected["height"],
            "available_heights": sorted({row["height"] for row in rows}),
            "stream_url": (
                f"/api/report-playback/manifest?video_no={safe_video_no}"
                f"&quality={selected['height']}"
            ),
            "original_url": f"https://chzzk.naver.com/video/{safe_video_no}",
        }

    def _archived_source(self, video_no: object) -> ArchivedPlayback | None:
        from pipeline.live_original_vod_archive import _physical_identity, registered_original_binding

        generation = os.getenv("CHZZ_LIVE_RUNTIME_ROOT")
        cold = os.getenv("CHZZ_COLD_ARCHIVE_ROOT")
        if not generation or not cold:
            return None
        binding = registered_original_binding(Path(generation), Path(cold), self.validate_video_no(video_no))
        if binding is None:
            return None
        path, size, sha256, duration = binding
        identity = _physical_identity(path.stat())
        if not self._archive_scan.acquire(timeout=5):
            return None
        try:
            return _index_archived_original(path, size, sha256, duration, identity)
        finally:
            self._archive_scan.release()

    def describe_archive(self, video_no: object) -> dict | None:
        try:
            source = self._archived_source(video_no)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError):
            return None
        if source is None:
            return None
        video_no = self.validate_video_no(video_no)
        playback = {
            "video_no": video_no, "duration_sec": source.duration, "source_kind": "retained_vod",
            "selected_height": source.height, "available_heights": [source.height],
            "stream_url": f"/api/report-playback/manifest?video_no={video_no}&source=archive",
            "original_url": f"https://chzzk.naver.com/video/{video_no}",
        }
        if source.transport == "mp4":
            token = self._remember_archive(source)
            playback.update(transport="mp4", stream_url=f"/api/report-playback/archive/{token}/media.mp4")
        return playback

    def _remember_archive(self, source: ArchivedPlayback) -> str:
        token = secrets.token_urlsafe(15)
        now = time.monotonic()
        with self._lock:
            for key, (created, _) in list(self._archive_sessions.items()):
                if now - created > SESSION_TTL_SEC:
                    del self._archive_sessions[key]
            if len(self._archive_sessions) >= 32:
                del self._archive_sessions[next(iter(self._archive_sessions))]
            self._archive_sessions[token] = (now, source)
        return token

    def archive_manifest(self, video_no: object) -> str:
        source = self._archived_source(video_no)
        if source is None or source.transport != "hls":
            raise KeyError("archived_playback_unavailable")
        token = self._remember_archive(source)
        target = math.ceil(max(seconds for _, _, seconds in source.ranges))
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0",
                 f"#EXT-X-TARGETDURATION:{target}"]
        for index, (_, _, seconds) in enumerate(source.ranges):
            lines.extend((f"#EXTINF:{seconds:.6f},", f"/api/report-playback/archive/{token}/{index}.ts"))
        return "\n".join([*lines, "#EXT-X-ENDLIST", ""])

    def _archive_source_for_token(self, token: str, transport: str) -> ArchivedPlayback:
        with self._lock:
            created, source = self._archive_sessions.get(token, (0, None))
        if source is None or source.transport != transport or time.monotonic() - created > SESSION_TTL_SEC:
            raise KeyError("archived_playback_unavailable")
        return source

    def _read_archive_bytes(self, token: str, source: ArchivedPlayback, start: int, end: int) -> bytes:
        from pipeline.live_original_vod_archive import _physical_identity
        from pipeline.vod_search_index import _plain

        path = _plain(source.path)
        if _physical_identity(path.stat()) != source.identity:
            raise ValueError("archived_playback_bytes_changed")
        with path.open("rb") as stream:
            opened = _physical_identity(os.fstat(stream.fileno()))

            identity_length = 4 if os.name == "nt" else 5
            if opened[:identity_length] != source.identity[:identity_length]:
                raise ValueError("archived_playback_bytes_changed")
            stream.seek(start)
            data = stream.read(end - start)
            if (len(data) != end - start or _physical_identity(os.fstat(stream.fileno())) != opened
                    or _physical_identity(path.stat()) != source.identity):
                raise ValueError("archived_playback_bytes_changed")
        with self._lock:
            session = self._archive_sessions.get(token)
            if session is not None and session[1] is source:
                del self._archive_sessions[token]
                self._archive_sessions[token] = (time.monotonic(), source)
        return data

    def archive_segment(self, token: str, index: int) -> PlaybackBinary:
        source = self._archive_source_for_token(token, "hls")
        if not 0 <= index < len(source.ranges):
            raise KeyError("archived_playback_unavailable")
        start, end, _ = source.ranges[index]
        return PlaybackBinary(self._read_archive_bytes(token, source, start, end), "video/mp2t")

    def archive_mp4(self, token: str, range_header: str) -> tuple[bytes, str]:
        source = self._archive_source_for_token(token, "mp4")
        size = source.identity[2]
        match = ARCHIVE_BYTE_RANGE.fullmatch(range_header)
        if match is None or not any(match.groups()):
            raise ArchivedRangeUnavailable(size)
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(size, int(last) + 1) if last else size
            end = min(end, start + ARCHIVE_MP4_RANGE_LIMIT)
        else:
            start, end = size - min(size, int(last), ARCHIVE_MP4_RANGE_LIMIT), size
        if not 0 <= start < end <= size:
            raise ArchivedRangeUnavailable(size)
        data = self._read_archive_bytes(token, source, start, end)
        return data, f"bytes {start}-{end - 1}/{size}"

    def _remember(self, urls: list[str]) -> str:
        now = time.monotonic()
        session_id = secrets.token_urlsafe(15)
        with self._lock:
            expired = [key for key, (created, _urls) in self._sessions.items() if now - created > SESSION_TTL_SEC]
            for key in expired:
                self._sessions.pop(key, None)
            self._sessions[session_id] = (now, urls)
        return session_id

    def stage_playlist(self, media_url: str, playlist_text: str) -> str:
        resources: list[str] = []
        staged_lines: list[str] = []
        uri_pattern = re.compile(r'URI="([^"]+)"')

        def stage_url(raw_url: str) -> str:
            resources.append(_resolve_m3u8_child_url(media_url, raw_url))
            return str(len(resources) - 1)

        for raw in playlist_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:")):
                line = uri_pattern.sub(
                    lambda match: f'URI="__REPORT_SEGMENT__/{stage_url(match.group(1))}"',
                    line,
                )
            elif not line.startswith("#"):
                line = f"__REPORT_SEGMENT__/{stage_url(line)}"
            staged_lines.append(line)
        if not staged_lines or staged_lines[0] != "#EXTM3U" or not resources:
            raise RuntimeError("유효한 HLS 미디어 목록이 아닙니다.")
        session_id = self._remember(resources)
        prefix = f"/api/report-playback/segment/{session_id}"
        return "\n".join(line.replace("__REPORT_SEGMENT__", prefix) for line in staged_lines) + "\n"

    def manifest(self, video_no: object, requested_height: object = 720, *, public_only: bool = False) -> str:
        safe_video_no = self.validate_video_no(video_no)
        _metadata, rows, _vod_status, _adult = (self._streams(safe_video_no, public_only=True)
                                               if public_only else self._streams(safe_video_no))
        selected = self._select_stream(rows, requested_height)
        if "live_rewind" in selected:
            live_rewind = selected["live_rewind"]
            try:
                stream_url = NetworkManager.get_video_m3u8_base_url(live_rewind, selected["height"])
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise AuthenticationRequired("로그인 또는 성인 인증이 필요합니다.") from exc
                raise


            master_url = json.loads(live_rewind)["media"][0]["path"]
            master = urlparse(master_url)
            child = urlparse(stream_url)
            if (child.scheme, child.netloc) == (master.scheme, master.netloc):
                child_path = child._replace(scheme="", netloc="").geturl()
                stream_url = _resolve_m3u8_child_url(master_url, child_path)
        else:
            stream_url = selected["url"]
        media_url, playlist_text = _load_m3u8_media_snapshot(stream_url)
        return self.stage_playlist(media_url, playlist_text)

    def terminal_metadata(self, video_no: object, requested_height: object = 360, *, public_only: bool = False) -> dict:
        return read_terminal_hls_metadata(self.manifest(video_no, requested_height, public_only=public_only))

    def _segment_url(self, session_id: object, index: object) -> str:
        safe_session = str(session_id or "")
        if not SESSION_ID_PATTERN.fullmatch(safe_session):
            raise KeyError("재생 세션이 올바르지 않습니다.")
        safe_index = int(index)
        with self._lock:
            stored = self._sessions.get(safe_session)
        if stored is None:
            raise KeyError("재생 세션이 없거나 만료됐습니다.")
        created_at, urls = stored
        if time.monotonic() - created_at > SESSION_TTL_SEC:
            with self._lock:
                self._sessions.pop(safe_session, None)
            raise KeyError("재생 세션이 만료됐습니다.")
        if safe_index < 0 or safe_index >= len(urls):
            raise IndexError("세그먼트 번호가 범위를 벗어났습니다.")
        return urls[safe_index]

    def segment(self, session_id: object, index: object) -> PlaybackBinary:
        upstream_url = self._segment_url(session_id, index)
        response = requests.get(upstream_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise AuthenticationRequired("저장된 로그인 세션이 만료됐습니다.")
        response.raise_for_status()
        suffix = urlparse(upstream_url).path.casefold()
        content_type = "video/mp2t" if suffix.endswith(".ts") else "application/octet-stream"
        return PlaybackBinary(body=response.content, content_type=content_type)
