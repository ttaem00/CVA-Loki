"""Check a public CHZZK replay against one saved live broadcast identity."""

from __future__ import annotations

import json

import re

from datetime import datetime, timedelta, timezone

from urllib.error import HTTPError, URLError

from urllib.request import HTTPRedirectHandler, Request, build_opener

_VIDEO_NO = re.compile(r"[1-9][0-9]{0,11}\Z")

_KST = timezone(timedelta(hours=9))

class VodLookupUnavailable(Exception):
    """The public metadata endpoint could not be checked safely."""

class VodIdentityMismatch(ValueError):
    "A replay that is not the saved broadcast; the message stays the manager's text.\n\n    The bounded ``code`` lets repair holds name the cause instead of a generic\n    ``repair_unavailable``. Callers that show the message are unchanged.\n    "

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None

_OPENER = build_opener(_NoRedirect())

def public_vod_metadata(video_no: str) -> dict:
    if not isinstance(video_no, str) or not _VIDEO_NO.fullmatch(video_no):
        raise ValueError("치지직 VOD 번호를 확인하세요.")
    request = Request(
        f"https://api.chzzk.naver.com/service/v2/videos/{video_no}",
        headers={"Accept": "application/json", "User-Agent": "CHZZ-ReportAdmin/1"},
        method="GET",
    )
    try:
        with _OPENER.open(request, timeout=10) as response:
            if response.status != 200 or "application/json" not in response.headers.get("Content-Type", ""):
                raise VodLookupUnavailable("vod_metadata_response_invalid")
            raw = response.read(128_001)
            if len(raw) > 128_000:
                raise VodLookupUnavailable("vod_metadata_too_large")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise VodLookupUnavailable("vod_metadata_unavailable") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise VodLookupUnavailable("vod_metadata_invalid") from exc
    if not isinstance(data, dict) or data.get("code") != 200 or not isinstance(data.get("content"), dict):
        raise VodLookupUnavailable("vod_metadata_invalid")
    return data["content"]

def verify_live_vod(archive_id: str, open_at_ms: int | None,
                    open_at_confidence: str | None, video_no: str, metadata: dict) -> dict:
    """Verify broadcast identity only; leave every VOD seek offset unverified."""
    if not isinstance(video_no, str) or not _VIDEO_NO.fullmatch(video_no):
        raise VodIdentityMismatch("vod_number_invalid", "치지직 VOD 번호를 확인하세요.")
    if not isinstance(metadata, dict) or str(metadata.get("videoNo")) != video_no:
        raise VodIdentityMismatch("vod_response_mismatch", "요청한 VOD와 원천 응답이 다릅니다.")
    if not isinstance(archive_id, str) or not re.fullmatch(r"[a-f0-9]{32}-[1-9][0-9]*", archive_id):
        raise VodIdentityMismatch("vod_archive_invalid", "방송 선택을 확인하세요.")
    if open_at_confidence != "provider" or not isinstance(open_at_ms, int) or open_at_ms <= 0:
        raise VodIdentityMismatch("vod_open_time_unverified", "방송 시작 시각의 원천 확인이 필요합니다.")
    channel = metadata.get("channel")
    if (metadata.get("videoType") != "REPLAY" or not isinstance(channel, dict)
            or channel.get("channelId") != archive_id[:32]):
        raise VodIdentityMismatch("vod_channel_mismatch", "선택한 방송 채널의 다시보기가 아닙니다.")
    raw_start = metadata.get("liveOpenDate")
    try:
        replay_start = datetime.strptime(raw_start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
    except (TypeError, ValueError) as exc:
        raise VodIdentityMismatch("vod_open_time_missing", "VOD의 원천 방송 시작 시각을 확인할 수 없습니다.") from exc
    replay_open_ms = int(replay_start.timestamp() * 1000)


    if abs(replay_open_ms - open_at_ms) > 2000:
        raise VodIdentityMismatch("vod_other_broadcast", "같은 채널이지만 다른 방송의 다시보기입니다.")
    duration = metadata.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise VodIdentityMismatch("vod_duration_unknown", "VOD 길이를 확인할 수 없습니다.")
    return {
        "verified": True,
        "videoNo": video_no,
        "url": f"https://chzzk.naver.com/video/{video_no}",
        "channelId": archive_id[:32],
        "broadcastStartAtMs": replay_open_ms,
        "durationSec": duration,
        "adult": metadata.get("adult") is True,
        "vodStatus": str(metadata.get("vodStatus") or "unknown")[:30],
        "identityEvidence": "official_channel_and_provider_broadcast_start",
        "timeMapping": "unverified",
    }
