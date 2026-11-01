"""Headless 144p VOD downloader."""

import glob

import logging

import math

import os

import re

import subprocess

import sys

import time

from collections import deque

from concurrent.futures import ThreadPoolExecutor

import requests

from requests.adapters import HTTPAdapter

from content.network import NetworkManager

from split_video import FFMPEG, FFPROBE, get_audio_duration, get_duration

from .utils import (
    clip_video,
    format_size,
    retry,
    stable_artifact_key,
    validate_artifact_path_budget,
)

logger = logging.getLogger("pipeline")

HEADERS = {"User-Agent": "Mozilla/5.0"}

CHUNK_SIZE = 1024 * 1024

M3U8_PARALLEL_WORKERS = 8

DIRECT_PROGRESS_LOG_BYTES = 10 * 1024 * 1024

DIRECT_PROGRESS_LOG_SEC = 30

class DownloadSourceUnavailableError(RuntimeError):
    """Raised when CHZZK metadata has no downloadable media source."""

class SegmentedDownloadError(RuntimeError):
    """Manager-safe segmented download failure with no source URL evidence."""

    def __init__(self, stage_code: str, *, ffmpeg_exit: int | None = None, **numeric: float | int):
        self.stage_code = stage_code
        self.ffmpeg_exit = ffmpeg_exit
        self.numeric = {
            key: value for key, value in numeric.items() if isinstance(value, (int, float))
        }
        fields = [f"stage={stage_code}"]
        if ffmpeg_exit is not None:
            fields.append(f"ffmpeg_exit={ffmpeg_exit}")
        fields.extend(f"{key}={value}" for key, value in sorted(self.numeric.items()))
        super().__init__("segmented download failed (" + ", ".join(fields) + ")")

def _download_source_unavailable_message(
    video_no: str,
    *,
    video_id: object,
    in_key: object,
    vod_status: object,
    live_rewind_json: object,
) -> str:
    missing = []
    if not video_id:
        missing.append("videoId")
    if not in_key:
        missing.append("inKey")
    if not live_rewind_json:
        missing.append("liveRewindPlaybackJson")
    missing_text = ", ".join(missing) if missing else "downloadable media route"
    status_text = str(vod_status or "unknown")
    return (
        f"VOD {video_no}의 다운로드 소스를 찾을 수 없습니다 "
        f"(vodStatus={status_text}, missing={missing_text}). "
        "비공개/삭제/플랫폼 미제공/인증 필요 VOD일 수 있습니다."
    )

def _is_interpreter_shutting_down() -> bool:
    if sys.is_finalizing():
        return True
    try:
        import concurrent.futures.thread as thread_state

        return bool(getattr(thread_state, "_shutdown", False))
    except Exception:
        return False

def _is_interpreter_shutdown_error(error: BaseException) -> bool:
    message = str(error).lower()
    return (
        _is_interpreter_shutting_down()
        or "interpreter shutdown" in message
        or "interpreter is shutting down" in message
    )

def _make_segment_session(workers: int) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=workers, pool_maxsize=workers * 2)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session

def _fetch_segment(session: requests.Session, url: str) -> bytes:
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content

def _media_duration_sec(path: str) -> float:
    return float(get_duration(path) or 0)

def _audio_stream_duration_sec(path: str) -> tuple[float, str]:
    duration, source = get_audio_duration(path, with_source=True)
    return float(duration or 0), str(source or "")

def _metadata_duration_sec(metadata: dict | None) -> int:
    if not isinstance(metadata, dict):
        return 0
    for key in ("duration", "duration_sec", "videoDuration"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            continue
    return 0

def _probe_decodable_audio_at_offset(
    path: str,
    offset_sec: float,
    *,
    window_sec: float = 10.0,
) -> bool | None:
    timeout = max(30, min(180, int(window_sec) + 30))
    try:
        result = subprocess.run(
            [
                FFMPEG, "-v", "error",
                "-ss", str(offset_sec),
                "-i", path,
                "-t", str(window_sec),
                "-vn",
                "-map", "0:a:0",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", "16000",
                "-",
            ],
            capture_output=True,
            timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 and len(result.stdout) == 0:
        return None
    return len(result.stdout) > 0

def _has_decodable_audio_tail(
    path: str,
    *,
    expected_duration_sec: int,
    actual_duration_sec: float,
) -> bool | None:
    if expected_duration_sec < 7200:
        return True
    probe_offsets = [
        max(3600.0, float(expected_duration_sec) * 0.50),
        max(3600.0, float(expected_duration_sec) * 0.80),
    ]
    verdicts: list[bool] = []
    for offset in probe_offsets:
        safe_offset = min(offset, max(0.0, actual_duration_sec - 300.0))
        if safe_offset <= 60:
            continue
        verdict = _probe_decodable_audio_at_offset(path, safe_offset)
        if verdict is not None:
            verdicts.append(verdict)
    if any(verdicts):
        return True
    if verdicts and not any(verdicts):
        return False
    return None

def _is_valid_media_file(
    path: str,
    *,
    expected_duration_sec: int = 0,
    duration_tolerance: float = 0.8,
) -> bool:
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        actual_duration = _media_duration_sec(path)
        if actual_duration <= 0:
            return False
        if expected_duration_sec > 0:
            required = max(1.0, float(expected_duration_sec) * duration_tolerance)
            if actual_duration < required:
                logger.warning(
                    "짧은 미디어 캐시 감지, 재다운로드 예정: %s "
                    "(duration=%.1fs < expected=%ss * %.2f)",
                    path,
                    actual_duration,
                    expected_duration_sec,
                    duration_tolerance,
                )
                return False
            audio_duration, audio_source = _audio_stream_duration_sec(path)
            if audio_source == "stream" and audio_duration < required:
                logger.warning(
                    "짧은 오디오 스트림 캐시 감지, 재다운로드 예정: %s "
                    "(audio_duration=%.1fs < expected=%ss * %.2f)",
                    path,
                    audio_duration,
                    expected_duration_sec,
                    duration_tolerance,
                )
                return False
            tail_audio = _has_decodable_audio_tail(
                path,
                expected_duration_sec=expected_duration_sec,
                actual_duration_sec=actual_duration,
            )
            if tail_audio is False:
                logger.warning(
                    "후반 오디오가 비어 있는 미디어 캐시 감지, 재다운로드 예정: %s "
                    "(expected=%ss)",
                    path,
                    expected_duration_sec,
                )
                return False
        return True
    except Exception as exc:
        logger.warning(f"손상된 미디어 캐시 감지, 재다운로드 예정: {path} ({exc})")
        return False

@retry(max_retries=3, backoff_base=2.0, exceptions=(requests.RequestException,))
def _get_content_length(url: str) -> int:
    resp = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
    resp.raise_for_status()
    return int(resp.headers.get("Content-Length", 0))

def _download_direct(url: str, dest_path: str, progress_func=None) -> str:
    total_size = _get_content_length(url)
    if total_size == 0:
        raise RuntimeError("Content-Length가 0입니다. URL을 확인하세요.")

    logger.info(f"다운로드 시작: {format_size(total_size)}")
    tmp_path = dest_path + ".downloading"

    try:
        downloaded = 0
        last_log_at = time.monotonic()
        last_log_bytes = 0
        with requests.get(url, headers=HEADERS, stream=True, timeout=(15, 60)) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_func:
                        progress_func(downloaded, total_size)

                    now = time.monotonic()
                    if (
                        downloaded - last_log_bytes >= DIRECT_PROGRESS_LOG_BYTES
                        or now - last_log_at >= DIRECT_PROGRESS_LOG_SEC
                    ):
                        logger.info(
                            "  다운로드 진행: %s/%s",
                            format_size(downloaded),
                            format_size(total_size),
                        )
                        last_log_at = now
                        last_log_bytes = downloaded

        if downloaded != total_size:
            raise RuntimeError(
                "다운로드 크기 불일치: "
                f"expected={format_size(total_size)}, actual={format_size(downloaded)}"
            )

        os.replace(tmp_path, dest_path)
        logger.info(f"다운로드 완료: {dest_path} ({format_size(total_size)})")
        return dest_path
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
            except OSError as e:
                logger.warning(f"  불완전 파일 삭제 실패: {e}")
        raise

def _download_direct_slice_with_ffmpeg(
    url: str,
    dest_path: str,
    progress_func=None,
    *,
    start_sec: int = 0,
    duration_sec: int = 0,
) -> str:
    if start_sec <= 0 and duration_sec <= 0:
        return _download_direct(url, dest_path, progress_func)

    source_pts_start = _probe_source_pts_start(url)
    tmp_path = dest_path + ".downloading"
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-headers",
        "User-Agent: Mozilla/5.0\r\n",
    ]
    if abs(source_pts_start) > 0.001:
        cmd.extend(["-seek_timestamp", "1", "-ss", str(source_pts_start + start_sec)])
    elif start_sec > 0:
        cmd.extend(["-ss", str(start_sec)])
    cmd.extend(["-i", url])
    if duration_sec > 0:
        cmd.extend(["-t", str(duration_sec)])
    cmd.extend([
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
    ])
    if abs(source_pts_start) > 0.001:
        cmd.extend(["-avoid_negative_ts", "make_zero"])
    cmd.extend([
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        tmp_path,
    ])

    logger.info(
        "DASH 직접 MP4 슬라이스 다운로드 시작: %ss..%s",
        start_sec,
        "end" if duration_sec <= 0 else start_sec + duration_sec,
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(120, int(duration_sec or 0) + 180),
            creationflags=creationflags,
        )
        if result.returncode != 0:
            detail = _redact_urls(((result.stderr or "") + "\n" + (result.stdout or "")).strip())[-1000:]
            raise SegmentedDownloadError(
                "FFMPEG_DIRECT_SLICE_FAILED",
                ffmpeg_exit=int(result.returncode),
                requested_start_sec=int(start_sec),
                requested_duration_sec=int(duration_sec),
                source_pts_start=round(source_pts_start, 6),
            ) from RuntimeError(detail)
        if not _is_valid_media_file(
            tmp_path,
            expected_duration_sec=int(duration_sec or 0),
        ):
            raise SegmentedDownloadError(
                "FFMPEG_DIRECT_SLICE_INVALID_OUTPUT",
                ffmpeg_exit=int(result.returncode),
                requested_start_sec=int(start_sec),
                requested_duration_sec=int(duration_sec),
                source_pts_start=round(source_pts_start, 6),
            )
        os.replace(tmp_path, dest_path)
        if progress_func and duration_sec > 0:
            progress_func(duration_sec, duration_sec)
        logger.info(f"DASH 직접 MP4 슬라이스 다운로드 완료: {dest_path}")
        return dest_path
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
            except OSError as e:
                logger.warning(f"  불완전 파일 삭제 실패: {e}")
        raise

def _probe_source_pts_start(url: str) -> float:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    completed = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=start_time",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=creationflags,
    )
    raw = (completed.stdout or "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SegmentedDownloadError(
            "SOURCE_PTS_MISSING",
            ffmpeg_exit=int(completed.returncode),
        ) from exc
    if completed.returncode != 0 or not math.isfinite(value):
        raise SegmentedDownloadError(
            "SOURCE_PTS_NONFINITE",
            ffmpeg_exit=int(completed.returncode),
        )
    return value

def _load_m3u8_playlist(m3u8_url: str) -> str:
    resp = requests.get(m3u8_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text

def _load_m3u8_media_snapshot(m3u8_url: str) -> tuple[str, str]:
    """Resolve a signed master to one deterministic media playlist snapshot."""

    current_url = m3u8_url
    for _depth in range(3):
        playlist_text = _load_m3u8_playlist(current_url)
        lines = [line.strip() for line in playlist_text.splitlines() if line.strip()]
        variants: list[tuple[int, int, int, str]] = []
        for index, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            attrs = _parse_m3u8_attrs(line.split(":", 1)[1])
            child = next(
                (candidate for candidate in lines[index + 1 :] if not candidate.startswith("#")),
                "",
            )
            if not child:
                continue
            resolution = str(attrs.get("RESOLUTION") or "")
            try:
                height = int(resolution.lower().split("x", 1)[1])
            except (IndexError, ValueError):
                height = 2**31 - 1
            try:
                bandwidth = int(attrs.get("BANDWIDTH") or 0)
            except ValueError:
                bandwidth = 2**31 - 1
            variants.append((height, bandwidth, index, child))
        if not variants:
            return current_url, playlist_text
        below_target = [row for row in variants if row[0] <= 144]
        selected = max(below_target, key=lambda row: (row[0], -row[1], -row[2])) if below_target else min(variants)
        current_url = _resolve_m3u8_child_url(current_url, selected[3])
    raise RuntimeError("HLS_MASTER_DEPTH_EXCEEDED")

def _resolve_m3u8_child_url(m3u8_url: str, child_path: str) -> str:
    from urllib.parse import urljoin, urlparse, urlunparse

    child_url = child_path if child_path.startswith(("http://", "https://")) else urljoin(m3u8_url, child_path)
    if child_path.startswith(("http://", "https://")):
        return child_url

    parent_query = urlparse(m3u8_url).query
    parsed_child = urlparse(child_url)
    if parent_query and not parsed_child.query:
        return urlunparse(parsed_child._replace(query=parent_query))
    return child_url

def _load_m3u8_segments_from_text(m3u8_url: str, playlist_text: str) -> list[tuple[float, str]]:
    segments: list[tuple[float, str]] = []
    pending_duration = 0.0
    for raw in playlist_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"]+)"', line)
            if not match:
                continue
            map_url = match.group(1)
            url = _resolve_m3u8_child_url(m3u8_url, map_url)
            segments.append((0.0, url))
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                pending_duration = 0.0
            continue
        if line.startswith("#"):
            continue
        url = _resolve_m3u8_child_url(m3u8_url, line)
        segments.append((pending_duration, url))
        pending_duration = 0.0
    return segments

def _select_m3u8_segment_urls(
    all_segments: list[tuple[float, str]],
    *,
    start_sec: int = 0,
    duration_sec: int = 0,
) -> list[str]:
    segment_urls: list[str] = []
    if start_sec > 0 or duration_sec > 0:
        end_sec = start_sec + duration_sec if duration_sec > 0 else float("inf")
        cursor = 0.0
        media_started = False
        for seg_duration, seg_url in all_segments:
            if seg_duration <= 0 and not media_started:
                segment_urls.append(seg_url)
                continue
            next_cursor = cursor + max(seg_duration, 0.0)
            overlaps = next_cursor > start_sec and cursor < end_sec
            if overlaps:
                segment_urls.append(seg_url)
                media_started = True
            cursor = next_cursor
            if media_started and cursor >= end_sec:
                break
    else:
        segment_urls = [seg_url for _, seg_url in all_segments]
    return segment_urls

def _redact_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "<url-redacted>", text)

def _parse_m3u8_attrs(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', value):
        raw = match.group(2)
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        attrs[match.group(1)] = raw
    return attrs

def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data

def _parse_encrypted_m3u8_segments(m3u8_url: str, playlist_text: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    media_sequence = 0
    current_sequence = 0
    pending_duration = 0.0
    current_key_url = ""
    current_iv: bytes | None = None

    for raw in playlist_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_sequence = int(line.split(":", 1)[1])
                current_sequence = media_sequence
            except ValueError:
                current_sequence = media_sequence
            continue
        if line.startswith("#EXT-X-KEY:"):
            attrs = _parse_m3u8_attrs(line.split(":", 1)[1])
            if attrs.get("METHOD") != "AES-128":
                raise RuntimeError(f"지원하지 않는 HLS 암호화 방식: {attrs.get('METHOD')}")
            key_uri = attrs.get("URI")
            if not key_uri:
                raise RuntimeError("HLS 암호화 키 URI가 없습니다.")
            current_key_url = _resolve_m3u8_child_url(m3u8_url, key_uri)
            iv_hex = (attrs.get("IV") or "").lower()
            current_iv = bytes.fromhex(iv_hex[2:] if iv_hex.startswith("0x") else iv_hex) if iv_hex else None
            continue
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_m3u8_attrs(line.split(":", 1)[1])
            map_uri = attrs.get("URI")
            if map_uri:
                if not current_key_url:
                    raise RuntimeError("암호화된 HLS EXT-X-MAP 앞에 EXT-X-KEY가 없습니다.")
                segments.append({
                    "duration": 0.0,
                    "url": _resolve_m3u8_child_url(m3u8_url, map_uri),
                    "key_url": current_key_url,
                    "iv": current_iv,
                    "sequence": current_sequence,
                    "is_map": True,
                })
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                pending_duration = 0.0
            continue
        if line.startswith("#"):
            continue

        if not current_key_url:
            raise RuntimeError("암호화된 HLS 세그먼트 앞에 EXT-X-KEY가 없습니다.")
        segments.append({
            "duration": pending_duration,
            "url": _resolve_m3u8_child_url(m3u8_url, line),
            "key_url": current_key_url,
            "iv": current_iv,
            "sequence": current_sequence,
            "is_map": False,
        })
        pending_duration = 0.0
        current_sequence += 1

    if not segments:
        raise RuntimeError("m3u8에서 암호화 세그먼트를 찾을 수 없습니다.")
    return segments

def _download_encrypted_m3u8_with_requests(
    m3u8_url: str,
    playlist_text: str,
    dest_path: str,
    cookies: dict,
    progress_func=None,
    start_sec: int = 0,
    duration_sec: int = 0,
) -> str:
    try:
        from Cryptodome.Cipher import AES
    except Exception as exc:
        raise RuntimeError(
            "Encrypted HLS download requires pycryptodomex (Cryptodome.Cipher.AES)."
        ) from exc

    segments = _parse_encrypted_m3u8_segments(m3u8_url, playlist_text)

    if not segments:
        raise RuntimeError("m3u8에서 암호화 세그먼트를 찾을 수 없습니다.")

    selected: list[dict[str, object]] = []
    if start_sec > 0 or duration_sec > 0:
        end_sec = start_sec + duration_sec if duration_sec > 0 else float("inf")
        cursor = 0.0
        for segment in segments:
            seg_duration = max(float(segment["duration"]), 0.0)
            next_cursor = cursor + seg_duration
            if next_cursor > start_sec and cursor < end_sec:
                selected.append(segment)
            cursor = next_cursor
            if selected and cursor >= end_sec:
                break
    else:
        selected = segments

    if not selected:
        raise RuntimeError("요청한 시간 범위에 해당하는 암호화 m3u8 세그먼트가 없습니다.")

    tmp_path = dest_path + ".downloading"
    key_cache: dict[str, bytes] = {}
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.update(cookies or {})

    try:
        with open(tmp_path, "wb") as out_f:
            total = len(selected)
            for idx, segment in enumerate(selected, start=1):
                key_url = str(segment["key_url"])
                key = key_cache.get(key_url)
                if key is None:
                    key_resp = session.get(key_url, timeout=30)
                    key_resp.raise_for_status()
                    key = key_resp.content
                    if len(key) != 16:
                        raise RuntimeError(f"HLS AES key length invalid: {len(key)}")
                    key_cache[key_url] = key

                seg_resp = session.get(str(segment["url"]), timeout=60)
                seg_resp.raise_for_status()
                iv = segment.get("iv")
                if iv is None and segment.get("is_map"):
                    raise RuntimeError("암호화된 EXT-X-MAP에는 IV가 필요합니다.")
                iv = iv or int(segment["sequence"]).to_bytes(16, "big")
                data = AES.new(key, AES.MODE_CBC, iv).decrypt(seg_resp.content)
                out_f.write(_pkcs7_unpad(data))
                if progress_func:
                    progress_func(idx, total)
                if idx % 100 == 0:
                    logger.info(f"  암호화 m3u8 세그먼트 {idx}/{total} 완료")

        if not _is_valid_media_file(tmp_path):
            raise RuntimeError(f"encrypted m3u8 download produced invalid media file: {tmp_path}")
        os.replace(tmp_path, dest_path)
        logger.info(f"m3u8 다운로드 완료: {dest_path}")
        return dest_path
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
            except OSError as e:
                logger.warning(f"  불완전 파일 삭제 실패: {e}")
        raise
    finally:
        session.close()

def _download_m3u8_with_ffmpeg(
    m3u8_url: str,
    dest_path: str,
    progress_func=None,
    start_sec: int = 0,
    duration_sec: int = 0,
    total_hint: int = 0,
) -> str:
    from split_video import FFMPEG

    tmp_path = dest_path + ".downloading"
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-headers",
        "User-Agent: Mozilla/5.0\r\n",
    ]
    if start_sec > 0:
        cmd.extend(["-ss", str(start_sec)])
    if duration_sec > 0:
        cmd.extend(["-t", str(duration_sec)])
    cmd.extend(["-i", m3u8_url])
    cmd.extend([
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        tmp_path,
    ])

    logger.info("m3u8 암호화 playlist 감지 → ffmpeg 다운로드 사용")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    last_log_at = time.monotonic()
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if line.startswith("out_time_ms="):
                try:
                    out_sec = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    out_sec = 0
                if progress_func:
                    progress_func(int(out_sec), duration_sec or total_hint)
                now = time.monotonic()
                if now - last_log_at >= DIRECT_PROGRESS_LOG_SEC:
                    logger.info("  m3u8 다운로드 진행: %s초 처리", int(out_sec))
                    last_log_at = now
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        returncode = proc.wait()
        if returncode != 0:
            detail = _redact_urls((stderr or "").strip())[-1000:]
            raise SegmentedDownloadError(
                "FFMPEG_HLS_FAILED",
                ffmpeg_exit=int(returncode),
                requested_start_sec=int(start_sec),
                requested_duration_sec=int(duration_sec),
                selected_segment_count=int(total_hint),
            ) from RuntimeError(detail)
        if not _is_valid_media_file(tmp_path):
            raise SegmentedDownloadError(
                "FFMPEG_HLS_INVALID_OUTPUT",
                ffmpeg_exit=int(returncode),
                requested_start_sec=int(start_sec),
                requested_duration_sec=int(duration_sec),
                selected_segment_count=int(total_hint),
            )
        os.replace(tmp_path, dest_path)
        logger.info(f"m3u8 다운로드 완료: {dest_path}")
        return dest_path
    except Exception:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
            except OSError as e:
                logger.warning(f"  불완전 파일 삭제 실패: {e}")
        raise

def _download_m3u8(
    m3u8_url: str,
    dest_path: str,
    progress_func=None,
    start_sec: int = 0,
    duration_sec: int = 0,
    cookies: dict | None = None,
) -> str:
    logger.info("m3u8 다운로드 시작")
    if _is_interpreter_shutting_down():
        raise RuntimeError("m3u8 download aborted: Python interpreter is shutting down")
    media_playlist_url, playlist_text = _load_m3u8_media_snapshot(m3u8_url)
    all_segments = _load_m3u8_segments_from_text(media_playlist_url, playlist_text)
    if not all_segments:
        raise RuntimeError("m3u8에서 세그먼트를 찾을 수 없습니다.")

    segment_urls = _select_m3u8_segment_urls(
        all_segments,
        start_sec=start_sec,
        duration_sec=duration_sec,
    )
    if start_sec > 0 or duration_sec > 0:
        end_sec = start_sec + duration_sec if duration_sec > 0 else "end"
        logger.info(
            f"  세그먼트 {len(segment_urls)}개 선택 "
            f"(slice {start_sec}s..{end_sec})"
        )
    else:
        logger.info(f"  세그먼트 {len(segment_urls)}개 발견")

    if not segment_urls:
        raise RuntimeError("요청한 시간 범위에 해당하는 m3u8 세그먼트가 없습니다.")

    if any(line.strip().startswith("#EXT-X-KEY") for line in playlist_text.splitlines()):
        source_preparation = os.environ.get("LOKI_SOURCE_PREPARATION")
        if source_preparation:
            from content.network import source_root

            source_root()
        if cookies or source_preparation:
            return _download_encrypted_m3u8_with_requests(
                media_playlist_url,
                playlist_text,
                dest_path,
                cookies,
                progress_func,
                start_sec=start_sec,
                duration_sec=duration_sec,
            )
        return _download_m3u8_with_ffmpeg(
            media_playlist_url,
            dest_path,
            progress_func,
            start_sec=start_sec,
            duration_sec=duration_sec,
            total_hint=len(segment_urls),
        )

    tmp_path = dest_path + ".downloading"
    last_segment_error: requests.RequestException | None = None
    for attempt in range(2):
        if attempt > 0:
            logger.warning("m3u8 세그먼트 실패 후 playlist 새로고침 재시도")
            media_playlist_url, playlist_text = _load_m3u8_media_snapshot(m3u8_url)
            all_segments = _load_m3u8_segments_from_text(media_playlist_url, playlist_text)
            segment_urls = _select_m3u8_segment_urls(
                all_segments,
                start_sec=start_sec,
                duration_sec=duration_sec,
            )
            if not segment_urls:
                raise RuntimeError("새로고침한 m3u8에서 요청 범위 세그먼트를 찾을 수 없습니다.") from last_segment_error

        workers = min(M3U8_PARALLEL_WORKERS, len(segment_urls))
        in_flight = workers * 2
        session = _make_segment_session(workers)

        try:
            if _is_interpreter_shutting_down():
                raise RuntimeError("m3u8 download aborted: Python interpreter is shutting down")
            with open(tmp_path, "wb") as out_f, ThreadPoolExecutor(max_workers=workers) as pool:
                futures: deque = deque()
                next_submit = 0
                written = 0
                total = len(segment_urls)

                while written < total:
                    while len(futures) < in_flight and next_submit < total:
                        if _is_interpreter_shutting_down():
                            raise RuntimeError("m3u8 download aborted: Python interpreter is shutting down")
                        try:
                            futures.append(pool.submit(_fetch_segment, session, segment_urls[next_submit]))
                        except RuntimeError as exc:
                            if _is_interpreter_shutdown_error(exc):
                                raise RuntimeError(
                                    "m3u8 download aborted: Python interpreter is shutting down"
                                ) from exc
                            raise
                        next_submit += 1

                    fut = futures.popleft()
                    try:
                        data = fut.result()
                    except requests.RequestException as e:
                        logger.error(f"  세그먼트 {written} 다운로드 실패: {e}")
                        last_segment_error = e
                        raise
                    out_f.write(data)
                    written += 1

                    if progress_func:
                        progress_func(written, total)
                    if written % 100 == 0:
                        logger.info(f"  세그먼트 {written}/{total} 완료")

            if not _is_valid_media_file(tmp_path):
                raise RuntimeError(f"m3u8 download produced invalid media file: {tmp_path}")
            os.replace(tmp_path, dest_path)
            logger.info(f"m3u8 다운로드 완료: {dest_path}")
            return dest_path
        except requests.RequestException:
            if attempt == 0:
                continue
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
                except OSError as e:
                    logger.warning(f"  불완전 파일 삭제 실패: {e}")
            raise
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.info(f"  불완전 다운로드 파일 삭제: {tmp_path}")
                except OSError as e:
                    logger.warning(f"  불완전 파일 삭제 실패: {e}")
            raise
        finally:
            session.close()

    raise RuntimeError("m3u8 segment download failed after playlist refresh") from last_segment_error

def _download_live_rewind_m3u8(
    live_rewind_json: str,
    dest_path: str,
    progress_func=None,
    start_sec: int = 0,
    duration_sec: int = 0,
    cookies: dict | None = None,
    target_height: int = 144,
) -> str:
    sorted_reps, _, _ = NetworkManager.get_video_m3u8_manifest(live_rewind_json)
    if not sorted_reps:
        raise RuntimeError("live rewind m3u8 매니페스트에 해상도 정보가 없습니다.")



    target_res = _prefer_resolution_height([int(rep[0]) for rep in sorted_reps], target_height)
    m3u8_url = NetworkManager.get_video_m3u8_base_url(live_rewind_json, target_res)
    logger.info(f"m3u8: {target_res}p 해상도 선택")
    return _download_m3u8(
        m3u8_url,
        dest_path,
        progress_func,
        start_sec=start_sec,
        duration_sec=duration_sec,
        cookies=cookies,
    )

def _prefer_resolution_height(heights: list[int], target_height: int) -> int:
    if isinstance(target_height, bool) or not isinstance(target_height, int) or target_height <= 0:
        raise ValueError("target_height must be a positive integer")
    positive_heights = [height for height in heights if height > 0]
    if not positive_heights:
        raise ValueError("media representations are empty")
    below_target = [height for height in positive_heights if height <= target_height]
    return max(below_target or positive_heights)

def _prefer_resolution(sorted_reps: list, target_height: int) -> tuple[int, str]:
    normalized = [
        (int(rep[0]), rep[-1])
        for rep in sorted_reps
        if isinstance(rep, (list, tuple))
        and len(rep) >= 2
        and isinstance(rep[-1], str)
        and int(rep[0]) > 0
    ]
    height = _prefer_resolution_height([rep[0] for rep in normalized], target_height)
    return next(rep for rep in normalized if rep[0] == height)

def _prefer_m3u8_representation(
    sorted_reps: list,
    target_height: int = 144,
) -> tuple[int, str] | None:
    m3u8_reps = [
        (int(resolution), url)
        for resolution, url in sorted_reps
        if isinstance(url, str) and url.lower().split("?", 1)[0].endswith(".m3u8")
    ]
    if not m3u8_reps:
        return None
    return _prefer_resolution(m3u8_reps, target_height)

def download_vod_media(
    video_no: str,
    cookies: dict,
    output_dir: str,
    progress_func=None,
    start_sec: int = 0,
    duration_sec: int = 0,
    filename_suffix: str = "",
    expected_duration_sec: int = 0,
    target_height: int = 144,
) -> str:
    """Download one CHZZK replay or bounded slice through the shared cache path."""

    if isinstance(target_height, bool) or not isinstance(target_height, int) or target_height <= 0:
        raise ValueError("target_height must be a positive integer")
    suffix = filename_suffix or ""
    artifact_base = f"{stable_artifact_key(video_no, 'media')}_{target_height}p{suffix}"
    validate_artifact_path_budget(
        output_dir,
        artifact_base,
        suffixes=(".mp4", ".mp4.downloading"),
    )
    filename = f"{artifact_base}.mp4"
    dest_path = os.path.join(output_dir, filename)
    stale_tmp = dest_path + ".downloading"

    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"VOD 정보 조회: {video_no}")
    video_id, in_key, adult, vod_status, live_rewind_json, metadata = NetworkManager.get_video_info(video_no, cookies)
    del adult

    cache_expected_duration_sec = int(duration_sec or expected_duration_sec or _metadata_duration_sec(metadata))

    if os.path.exists(stale_tmp):
        try:
            stale_size = os.path.getsize(stale_tmp)
            if (
                stale_size > 0
                and not os.path.exists(dest_path)
                and _is_valid_media_file(
                    stale_tmp,
                    expected_duration_sec=cache_expected_duration_sec,
                )
            ):
                os.replace(stale_tmp, dest_path)
                logger.info(f"기존 임시 파일 복구: {dest_path} ({format_size(stale_size)})")
                return dest_path
            os.remove(stale_tmp)
            logger.info(f"이전 불완전 다운로드 삭제: {stale_tmp}")
        except OSError as e:
            raise RuntimeError(
                f"이전 불완전 다운로드 파일을 정리할 수 없습니다: {stale_tmp} ({e})"
            ) from e

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        if not _is_valid_media_file(
            dest_path,
            expected_duration_sec=cache_expected_duration_sec,
        ):
            try:
                os.remove(dest_path)
            except OSError as exc:
                raise RuntimeError(f"invalid cached media could not be removed: {dest_path} ({exc})") from exc
        else:
            logger.info(f"이미 다운로드됨: {dest_path}")
            return dest_path

    slice_requested = start_sec > 0 or duration_sec > 0
    import glob

    legacy_full_candidates = [
        path
        for path in glob.glob(
            os.path.join(output_dir, f"{video_no}_*_{target_height}p.mp4")
        )
        if path != dest_path
        and _is_valid_media_file(
            path,
            expected_duration_sec=cache_expected_duration_sec,
        )
    ]
    if legacy_full_candidates:
        legacy_full_candidates.sort(key=os.path.getmtime, reverse=True)
        source_path = legacy_full_candidates[0]
        if not slice_requested:
            logger.info(f"기존 전체 mp4 재사용: {source_path}")
            return source_path
        if duration_sec > 0:
            logger.info(f"기존 전체 mp4에서 슬라이스 재사용: {source_path}")
            return clip_video(source_path, dest_path, duration_sec, start_sec=start_sec)

    long_full_vod = (not slice_requested) and cache_expected_duration_sec >= 3600
    if bool(live_rewind_json) and long_full_vod:
        try:
            logger.info("긴 VOD → m3u8 세그먼트 병렬 다운로드 우선")
            return _download_live_rewind_m3u8(
                live_rewind_json,
                dest_path,
                progress_func,
                start_sec=start_sec,
                duration_sec=duration_sec,
                cookies=cookies,
                target_height=target_height,
            )
        except Exception as e:
            if _is_interpreter_shutdown_error(e):
                logger.warning(f"m3u8 우선 다운로드 중단: {e}")
                raise
            logger.warning(f"m3u8 우선 다운로드 실패, DASH 직접 다운로드 폴백: {e}")

    if video_id and in_key:
        try:
            sorted_reps, auto_res, auto_url = NetworkManager.get_video_dash_manifest(video_id, in_key)
            del auto_res, auto_url
            if not sorted_reps:
                raise RuntimeError("DASH 매니페스트에 해상도 정보가 없습니다.")

            target_res, target_url = _prefer_resolution(sorted_reps, target_height)
            direct_fallback = (target_res, target_url)
            if not slice_requested:
                m3u8_rep = _prefer_m3u8_representation(sorted_reps, target_height)
                if m3u8_rep is not None:
                    target_res, target_url = m3u8_rep
                    logger.info(f"full VOD → DASH m3u8 representation 우선: {target_res}p")
            logger.info(f"DASH: {target_res}p 해상도 선택")
            if target_url.lower().split("?", 1)[0].endswith(".m3u8"):
                try:
                    return _download_m3u8(
                        target_url,
                        dest_path,
                        progress_func,
                        start_sec=start_sec,
                        duration_sec=duration_sec,
                        cookies=cookies,
                    )
                except Exception as e:
                    if _is_interpreter_shutdown_error(e):
                        raise
                    fallback_res, fallback_url = direct_fallback
                    if fallback_url != target_url and not fallback_url.lower().split("?", 1)[0].endswith(".m3u8"):
                        logger.warning(
                            "DASH m3u8 다운로드 실패, direct MP4 폴백: %s",
                            e,
                        )
                        logger.info(f"DASH: {fallback_res}p direct MP4 해상도 선택")
                        return _download_direct(fallback_url, dest_path, progress_func)
                    raise
            if slice_requested:
                return _download_direct_slice_with_ffmpeg(
                    target_url,
                    dest_path,
                    progress_func,
                    start_sec=start_sec,
                    duration_sec=duration_sec,
                )
            return _download_direct(target_url, dest_path, progress_func)
        except SegmentedDownloadError:
            raise
        except Exception as e:
            logger.warning(f"DASH 다운로드 실패, m3u8 폴백: {e}")

    if live_rewind_json:
        try:
            return _download_live_rewind_m3u8(
                live_rewind_json,
                dest_path,
                progress_func,
                start_sec=start_sec,
                duration_sec=duration_sec,
                cookies=cookies,
                target_height=target_height,
            )
        except Exception as e:
            if _is_interpreter_shutdown_error(e):
                logger.warning(f"m3u8 다운로드 중단: {e}")
            else:
                logger.error(f"m3u8 다운로드도 실패: {e}")
            raise

    raise DownloadSourceUnavailableError(
        _download_source_unavailable_message(
            video_no,
            video_id=video_id,
            in_key=in_key,
            vod_status=vod_status,
            live_rewind_json=live_rewind_json,
        )
    )
