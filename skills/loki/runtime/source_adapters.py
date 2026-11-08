"""Anonymous completed replay adapters feeding Loki's existing source contract.

Video uses the shared yt-dlp whole-VOD path. Chat retains provider originals
under the current attempt and normalizes only clocks bound to that VOD.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET

import requests


class ChatCollectionError(RuntimeError):
    """A bounded, safe reason without provider messages or signed URLs."""


def _write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _public_reference(ref):
    from pipeline.source_platforms import resolve_source
    actual = resolve_source(ref.url)
    if actual.platform not in {"youtube", "soop"} or any(
        getattr(actual, key) != getattr(ref, key) for key in ("platform", "native_id", "work_id")
    ):
        raise ValueError("loki_source_adapter_identity_mismatch")
    return actual


def _utc_stamp(value):
    if type(value) in (int, float) and math.isfinite(value) and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return ""


def acquire_video(ref, attempt_dir: Path, *, height: int, max_bytes: int,
                  max_download_seconds: int, tools: dict) -> dict:
    """Return one complete, losslessly joined media file for parent verification."""
    from pipeline.yt_dlp_acquisition import acquire_public_whole_video
    ref = _public_reference(ref)
    output = Path(attempt_dir).resolve()
    download = output / ("video-" + uuid.uuid4().hex[:12])
    download.mkdir()
    ffmpeg = tools.get("ffmpeg", {}).get("path")
    result = acquire_public_whole_video(ref.url, download, platform=ref.platform,
        max_height=height, max_bytes=min(max_bytes, 16 * 1024**3),
        time_budget_seconds=max_download_seconds, ffmpeg_location=str(ffmpeg) if ffmpeg else None,
        runner=_bounded_runner(max_bytes, time.monotonic() + max_download_seconds))
    media = result.get("results", {}).get("video", {})
    metadata = result.get("results", {}).get("metadata", {})
    if media.get("status") != "PASS" or len(media.get("files", [])) != 1 or metadata.get("status") != "PASS":
        raise ValueError("loki_source_video_acquisition_incomplete")
    media_path = Path(media["files"][0]).resolve(strict=True)
    metadata_path = Path(metadata["files"][0]).resolve(strict=True)
    if (not media_path.is_relative_to(output) or not metadata_path.is_relative_to(output)
            or not 0 < media_path.stat().st_size <= max_bytes):
        raise ValueError("loki_source_video_artifact_invalid")
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))


    start = _utc_stamp(raw.get("release_timestamp")) if ref.platform == "youtube" and raw.get("live_status") == "was_live" else ""
    source = dict(title=raw.get("title") or ref.work_id,
        channelId=raw.get("channel_id") or raw.get("uploader_id") or "",
        channelName=raw.get("channel") or raw.get("uploader") or "",
        duration=raw.get("duration"), liveOpenDate=start,
        publishDate=(_utc_stamp(raw.get("timestamp")) or raw.get("upload_date") or "") if ref.platform == "youtube" else "",
        source_platform=ref.platform, source_id=ref.native_id, source_url=ref.url)
    if raw.get("_type") == "multi_video":
        source["parts"] = [dict(id=row.get("id"), duration=row.get("duration")) for row in raw["entries"]]
    return dict(media_path=str(media_path), metadata=source)


def _message_text(message):
    if not isinstance(message, dict):
        return ""
    if isinstance(message.get("simpleText"), str):
        return message["simpleText"]
    text = []
    for run in message.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if isinstance(run.get("text"), str):
            text.append(run["text"])
        elif isinstance(run.get("emoji"), dict):
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            label = shortcuts[0] if shortcuts else emoji.get("emojiId") or ""
            if isinstance(label, str):
                text.append(label)
    return "".join(text)


def _youtube_rows(paths, duration, max_messages, deadline, meta):
    rows, seen = [], set()
    for path in paths:
        with Path(path).open("rb") as stream:
            for raw_line in stream:
                if time.monotonic() >= deadline:
                    meta["limit_reason"] = "time_limit"
                    return rows
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeError:
                    meta["malformed_record_count"] += 1
                    meta["error_code"] = "youtube_invalid_record_encoding"
                    continue
                if not line.strip():
                    continue
                try:
                    action = json.loads(line)
                except ValueError:
                    meta["malformed_record_count"] += 1
                    continue
                replay = action.get("replayChatItemAction") if isinstance(action, dict) else None
                if not isinstance(replay, dict):
                    continue
                offset = replay.get("videoOffsetTimeMsec")
                bound = type(offset) is int or isinstance(offset, str) and bool(re.fullmatch(r"[0-9]{1,15}", offset))
                ms = int(offset) if bound else -1
                for event in replay.get("actions") or []:
                    addition = event.get("addChatItemAction") if isinstance(event, dict) else None
                    item = addition.get("item", {}) if isinstance(addition, dict) else {}
                    if not isinstance(item, dict):
                        continue
                    for renderer in item.values():
                        if not isinstance(renderer, dict):
                            continue
                        message = _message_text(renderer.get("message"))
                        if not message:
                            continue
                        if not bound or not 0 <= ms <= duration * 1000:
                            meta["unbound_clock_count"] += 1
                            continue
                        identity = renderer.get("id")
                        if isinstance(identity, str) and identity:
                            if identity in seen:
                                meta["duplicate_count"] += 1
                                continue
                            seen.add(identity)
                        if len(rows) >= max_messages:
                            meta["limit_reason"] = "message_limit"
                            return rows
                        rows.append(dict(ms=ms, msg=message))
    return rows


def _bounded_runner(max_bytes, deadline):
    """Bound actual yt-dlp output bytes while its downloader is running."""
    from pipeline.replay_continuity.preparation import _owned_process_tree

    def run(command, cwd, timeout):
        before = {path.resolve() for path in cwd.rglob("*") if path.is_file()}
        stdout = cwd / ("process-" + uuid.uuid4().hex + ".stdout")
        stderr = stdout.with_suffix(".stderr")
        until = min(deadline, time.monotonic() + timeout)
        with stdout.open("xb") as out, stderr.open("xb") as err:
            proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    start_new_session=os.name != "nt")
            with _owned_process_tree(proc):
                while proc.poll() is None:
                    size = sum(path.stat().st_size for path in cwd.rglob("*")
                               if path.is_file() and path.resolve() not in before)
                    if size > max_bytes:
                        raise ChatCollectionError("byte_limit")
                    if time.monotonic() >= until:
                        raise ChatCollectionError("time_limit")
                    time.sleep(.05)
        if sum(path.stat().st_size for path in cwd.rglob("*") if path.is_file() and path.resolve() not in before) > max_bytes:
            raise ChatCollectionError("byte_limit")
        return subprocess.CompletedProcess(command, proc.returncode, stdout.read_text(encoding="utf-8", errors="replace"), "")
    return run


def _official_soop_url(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.netloc != host or parsed.fragment
            or any(ord(char) < 33 for char in value) or "\\" in value
            or not any(host == domain or host.endswith("." + domain)
                       for domain in ("sooplive.com", "sooplive.co.kr", "afreecatv.com", "afreecatv.co.kr"))):
        raise ChatCollectionError("soop_url_not_public_official")
    return value


class _SoopClient:
    def __init__(self, ref, raw, deadline, max_bytes):
        self.ref, self.raw, self.deadline, self.max_bytes = ref, raw, deadline, max_bytes
        self.received_bytes, self.paths = 0, []
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = None

    def fetch(self, url, data=None):
        method = "POST" if data is not None else "GET"
        for redirects in range(4):
            _official_soop_url(url)
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ChatCollectionError("time_limit")
            self.session.cookies.clear()
            response = self.session.request(method, url, data=data,
                headers={"Referer": self.ref.url, "Accept": "application/json, text/xml, */*"},
                timeout=(min(5, remaining), min(5, remaining)), stream=True, allow_redirects=False)
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    if not location or redirects == 3:
                        raise ChatCollectionError("soop_redirect_limit")
                    url = _official_soop_url(urljoin(url, location))
                    if response.status_code == 303:
                        method, data = "GET", None
                    continue
                if response.status_code != 200:
                    raise ChatCollectionError("soop_http_unavailable")
                path = self.raw / f"response-{len(self.paths):04d}.{'json' if method == 'POST' else 'xml'}"
                self.paths.append(str(path))
                body = bytearray()
                with path.open("xb") as stream:
                    for chunk in response.iter_content(chunk_size=16384):
                        if time.monotonic() >= self.deadline:
                            raise ChatCollectionError("time_limit")
                        if not chunk:
                            continue
                        self.received_bytes += len(chunk)
                        if self.received_bytes > self.max_bytes:
                            raise ChatCollectionError("byte_limit")
                        stream.write(chunk)
                        body.extend(chunk)
                return bytes(body)
            finally:
                response.close()
        raise ChatCollectionError("soop_redirect_limit")


def _soop_xml(body):


    declarations = body.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ChatCollectionError("soop_xml_entities_forbidden")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, LookupError) as exc:
        raise ChatCollectionError("soop_xml_invalid") from exc
    if root.tag != "root":
        raise ChatCollectionError("soop_xml_invalid")
    return root


def _soop_rows(ref, duration, raw, deadline, max_bytes, max_messages, meta):
    rows, offset = [], 0.0
    client = _SoopClient(ref, raw, deadline, max_bytes)
    try:
        payload = json.loads(client.fetch("https://api.m.sooplive.com/station/video/a/view",
                                         {"nTitleNo": ref.native_id, "nApiLevel": "10"}))
        data = payload.get("data") or {}
        parts = data.get("files") or []
        if payload.get("result") != 1 or not isinstance(parts, list) or not parts:
            raise ChatCollectionError("soop_metadata_unavailable")
        chunk_duration = float(data.get("chat_duration") or 300)
        if not math.isfinite(chunk_duration) or chunk_duration <= 0:
            raise ChatCollectionError("soop_chunk_duration_invalid")
        meta["chat_chunk_duration_sec"] = chunk_duration
        meta["chat_chunk_duration_defaulted"] = not bool(data.get("chat_duration"))
        meta["part_count"] = len(parts)
        for part_index, part in enumerate(parts):
            part_duration = float(part.get("duration") or 0) / 1000 if isinstance(part, dict) else 0
            if not math.isfinite(part_duration) or part_duration <= 0:
                raise ChatCollectionError("soop_part_duration_unbound")
            start = offset
            offset += part_duration
            url = part.get("chat")
            if not isinstance(url, str) or not url:
                meta["missing_chat_parts"] += 1
                continue
            _official_soop_url(url)
            parsed = urlsplit(url)
            split = bool(re.search(r"/ChatLoadSplit\.php$", parsed.path, re.IGNORECASE))
            if not split and not parsed.path.lower().endswith(".xml"):
                meta["unsupported_chat_parts"] += 1
                continue
            positions = range(math.ceil(part_duration / chunk_duration)) if split else range(1)
            for chunk_index in positions:
                chunk_start = chunk_index * chunk_duration
                fetch_url = url
                if split:
                    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "startTime"]
                    query.append(("startTime", str(math.floor(chunk_start))))
                    fetch_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
                root = _soop_xml(client.fetch(fetch_url))
                for event in root:
                    if event.tag not in {"chat", "ogq"}:
                        continue
                    message = event.findtext("m") or ""
                    if not message:
                        continue
                    try:
                        local = float(event.findtext("t") or "nan")
                    except ValueError:
                        local = float("nan")
                    if (not math.isfinite(local) or not 0 <= local <= part_duration
                            or not 0 <= start + local <= duration):
                        meta["unbound_clock_count"] += 1
                        continue


                    if split and not chunk_start <= local < min(part_duration, chunk_start + chunk_duration):
                        continue
                    if len(rows) >= max_messages:
                        raise ChatCollectionError("message_limit")
                    rows.append(dict(ms=round((start + local) * 1000), msg=message))
                meta["collected_chunks"] += 1
            meta["collected_parts"] += 1
        if abs(offset - duration) > max(1, duration * .005):
            meta["coverage_duration_mismatch"] = True
    except (ChatCollectionError, requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        meta["error_code"] = str(exc) if isinstance(exc, ChatCollectionError) else "soop_collection_failed"
        if meta["error_code"] in {"time_limit", "byte_limit", "message_limit"}:
            meta["limit_reason"] = meta["error_code"]
    finally:
        meta["received_bytes"] = client.received_bytes
        meta["raw_paths"] = client.paths
        client.session.close()
    return rows


def collect_chat(ref, duration_sec: float, target: Path, *, attempt_dir: Path,
                 max_seconds=60, max_bytes=16 * 1024 * 1024, max_messages=200000) -> dict:
    """Optional replay chat is independent of speech/media preparation."""
    target = Path(target)
    meta_path = target.with_suffix(".meta.json")
    if any(path.exists() or path.is_symlink() for path in (target, meta_path)):
        return dict(status="preserved_existing", path=str(target), meta_path=str(meta_path))
    ref = _public_reference(ref)
    if (type(duration_sec) not in (int, float) or not math.isfinite(duration_sec) or duration_sec <= 0
            or type(max_seconds) not in (int, float) or not 0 < max_seconds <= 60
            or type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024
            or type(max_messages) is not int or not 0 < max_messages <= 200000):
        raise ValueError("loki_chat_limits_invalid")
    deadline = time.monotonic() + max_seconds
    raw = Path(attempt_dir) / ("chat-" + uuid.uuid4().hex[:12])
    raw.mkdir()
    meta = dict(status="unavailable", video_no=ref.work_id, source_platform=ref.platform,
        source_id=ref.native_id, clock_basis="vod_player_time", unbound_clock_count=0,
        duplicate_count=0, malformed_record_count=0, missing_chat_parts=0,
        unsupported_chat_parts=0, collected_parts=0, collected_chunks=0, raw_paths=[])
    rows, complete = [], False
    if ref.platform == "soop":
        rows = _soop_rows(ref, duration_sec, raw, deadline, max_bytes, max_messages, meta)
        complete = not any(meta.get(key) for key in ("error_code", "missing_chat_parts", "unsupported_chat_parts", "coverage_duration_mismatch"))
    else:
        from pipeline.yt_dlp_acquisition import acquire_public_video_assets
        try:
            result = acquire_public_video_assets(ref.url, raw, assets=("chat",),
                time_budget_seconds=max(5, max_seconds), runner=_bounded_runner(max_bytes, deadline))
            chat = result.get("results", {}).get("chat", {})
            meta.update(provider_status=chat.get("status"), provider_code=chat.get("code"))
            if chat.get("status") == "FAIL":
                meta["error_code"] = "youtube_chat_acquisition_failed"
            elif chat.get("status") == "MISS":
                meta["unavailable_reason"] = "replay_chat_not_exposed"
            paths = [Path(path).resolve() for path in chat.get("files", [])]
            if any(not path.is_relative_to(raw.resolve()) or path.is_symlink() for path in paths):
                raise ChatCollectionError("chat_artifact_outside_attempt")
            if sum(path.stat().st_size for path in paths) > max_bytes:
                raise ChatCollectionError("byte_limit")
            meta["raw_paths"] = [str(path) for path in paths]
            rows = _youtube_rows(paths, duration_sec, max_messages, deadline, meta)
            complete = chat.get("status") == "PASS" and not meta.get("limit_reason")
        except (ChatCollectionError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            meta["error_code"] = str(exc) if isinstance(exc, ChatCollectionError) else "youtube_collection_failed"


            paths = sorted(raw.glob("replay.live_chat.json*"))
            paths = [path for path in paths if path.is_file() and not path.is_symlink()
                     and path.name in {"replay.live_chat.json", "replay.live_chat.json.part"}
                     and path.stat().st_size <= max_bytes]
            meta["raw_paths"] = [str(path) for path in paths]
            if paths and time.monotonic() < deadline:
                try:
                    rows = _youtube_rows(paths, duration_sec, max_messages, deadline, meta)
                except (ValueError, OSError):
                    meta["error_code"] = "youtube_collection_failed"
    complete = complete and not meta["unbound_clock_count"] and not meta["malformed_record_count"]
    status = "complete" if rows and complete else "partial" if rows else "unavailable"
    if not rows and not meta.get("error_code") and not meta.get("unavailable_reason"):
        meta["unavailable_reason"] = "no_bound_replay_messages"
    rows.sort(key=lambda row: row["ms"])
    meta.update(status=status, message_count=len(rows),
                coverage_start_ms=rows[0]["ms"] if rows else None,
                coverage_end_ms=rows[-1]["ms"] if rows else None)
    _write_new(target, rows)
    _write_new(meta_path, meta)
    return dict(status=status, path=str(target), meta_path=str(meta_path),
                **({"error_code": meta["error_code"]} if meta.get("error_code") else {}))
