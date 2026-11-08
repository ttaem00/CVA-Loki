"""Pure identity and public-link handling for individual replay sources."""

from dataclasses import dataclass

import math

import re

from urllib.parse import parse_qsl, urlsplit

@dataclass(frozen=True)
class SourceReference:
    platform: str
    native_id: str
    url: str
    work_id: str

_NATIVE_IDS = {
    "chzzk": r"[0-9]{1,16}",
    "youtube": r"[A-Za-z0-9_-]{11}",
    "soop": r"[0-9]{1,16}",
}

_SOOP_HOSTS = {"vod.sooplive.com", "vod.sooplive.co.kr", "vod.afreecatv.com", "vod.afreecatv.co.kr"}

def _reference(platform: str, native_id: str) -> SourceReference:
    if (not isinstance(platform, str) or platform not in _NATIVE_IDS
            or not isinstance(native_id, str) or not re.fullmatch(_NATIVE_IDS[platform], native_id)):
        raise ValueError("source_platform_or_native_id_invalid")
    if platform == "chzzk":
        return SourceReference(platform, native_id, f"https://chzzk.naver.com/video/{native_id}", native_id)
    if platform == "youtube":
        url = f"https://www.youtube.com/watch?v={native_id}"
    else:
        url = f"https://vod.sooplive.com/player/{native_id}"
    return SourceReference(platform, native_id, url, f"{platform}-{native_id}")

def _query(value: str) -> dict[str, str]:
    pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    if len(query) != len(pairs) or any(not value for value in query.values()):
        raise ValueError("source_query_duplicate_or_empty")
    return query

def resolve_source(value: str) -> SourceReference:
    """Resolve a bare CHZZK number or an allowlisted, individual HTTPS replay URL.

    A YouTube live-replay URL identifies a video, but only acquisition metadata
    can establish whether that video has finished broadcasting.
    """
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("single_public_replay_url_required")
    if re.fullmatch(_NATIVE_IDS["chzzk"], value):
        return _reference("chzzk", value)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.netloc.lower() != parsed.hostname
            or "#" in value or "\\" in value or ("?" in value and not parsed.query)):
        raise ValueError("single_public_replay_url_required")
    query = _query(parsed.query)
    host = parsed.hostname
    if host == "chzzk.naver.com":
        match = re.fullmatch(r"/video/([0-9]{1,16})/?", parsed.path)
        if (not match or set(query) - {"currentTime"}
                or (query and (not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", query["currentTime"])
                               or not math.isfinite(float(query["currentTime"]))))):
            raise ValueError("single_public_chzzk_replay_required")
        return _reference("chzzk", match[1])
    if host in {"www.youtube.com", "youtube.com", "youtu.be"}:
        if host == "youtu.be":
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
        else:
            match = re.fullmatch(r"/(?:live|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
        watch = host != "youtu.be" and parsed.path == "/watch"
        allowed = {"si", "feature", "t", "start", "ab_channel"} | ({"v"} if watch else set())
        if (set(query) - allowed or (not watch and not match)
                or ("start" in query and not re.fullmatch(r"[0-9]+", query["start"]))
                or ("t" in query and not re.fullmatch(r"(?:[0-9]+|(?:[0-9]+h)?(?:[0-9]+m)?(?:[0-9]+s)?)", query["t"]))):
            raise ValueError("single_public_youtube_replay_required")
        return _reference("youtube", query.get("v", "") if watch else match[1])
    if host in _SOOP_HOSTS:
        match = re.fullmatch(r"/(?:player|PLAYER/STATION)/([0-9]{1,16})/?", parsed.path)
        if (not match or set(query) - {"change_second"}
                or (query and not re.fullmatch(r"[0-9]+", query["change_second"]))):
            raise ValueError("single_public_soop_replay_required")
        return _reference("soop", match[1])
    raise ValueError("single_public_replay_url_required")

def valid_work_id(value: str) -> bool:
    """A work key is one safe path component, qualified outside CHZZK."""
    return isinstance(value, str) and bool(re.fullmatch(
        r"(?:[0-9]{1,16}|youtube-[A-Za-z0-9_-]{11}|soop-[0-9]{1,16})", value))

def source_from_fields(video_no: str, source_platform: str | None = None,
                       source_id: str = "", source_url: str = "") -> SourceReference:
    """Validate fields together, retaining old explicitly typed native IDs.

    An omitted platform can be inferred from a qualified work key. Explicit
    platforms must agree with that key; new-job work-key requirements belong
    to their binder, since old VODInfo records can still contain native IDs.
    """
    if not all(isinstance(value, str) for value in (video_no, source_id, source_url)):
        raise ValueError("source_identity_fields_invalid")
    if source_platform is not None and (not isinstance(source_platform, str) or source_platform not in _NATIVE_IDS):
        raise ValueError("source_platform_or_native_id_invalid")
    platform = source_platform
    native_id = video_no
    if platform is None or not re.fullmatch(_NATIVE_IDS[platform], video_no):
        for candidate in ("youtube", "soop"):
            if video_no.startswith(candidate + "-"):
                if platform is not None and platform != candidate:
                    raise ValueError("source_work_id_platform_mismatch")
                platform, native_id = candidate, video_no[len(candidate) + 1:]
                break
    reference = _reference(platform or "chzzk", native_id)
    if source_id and source_id != reference.native_id:
        raise ValueError("source_native_id_mismatch")
    if source_url and resolve_source(source_url) != reference:
        raise ValueError("source_url_identity_mismatch")
    return reference

def source_url_at(platform: str, native_id: str, seconds: float = 0) -> str:
    """Build a canonical platform link at a finite, nonnegative video offset."""
    reference = _reference(platform, native_id)
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0
            or (isinstance(seconds, float) and not math.isfinite(seconds))):
        raise ValueError("source_time_must_be_finite_and_nonnegative")
    seconds = int(seconds)
    if platform == "youtube":
        return f"{reference.url}&t={seconds}s"
    key = "currentTime" if platform == "chzzk" else "change_second"
    return f"{reference.url}?{key}={seconds}"
