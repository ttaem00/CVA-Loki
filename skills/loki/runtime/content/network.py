"""Anonymous CHZZK source preparation; all authoring network clients stay disabled."""

from contextlib import contextmanager

import hashlib

import json

import os

from pathlib import Path

import re

import threading

import time

from urllib.parse import quote, urlencode, urljoin, urlsplit

import xml.etree.ElementTree as ET

import requests

def source_root():
    value = os.environ.get("LOKI_SOURCE_PREPARATION")
    if not value or value != os.environ.get("LOKI_WORKSPACE"):
        raise PermissionError("loki_source_preparation_required")
    root = Path(value).resolve(strict=True)
    if not (root / "loki.json").is_file():
        raise PermissionError("loki_source_workspace_missing")
    return root

def public_url(value):
    parsed = urlsplit(str(value))
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443) or not host
            or not any(host == name or host.endswith("." + name) for name in ("naver.com", "pstatic.net"))):
        raise PermissionError("loki_public_source_url_forbidden")
    return str(value)

@contextmanager
def public_requests(max_bytes, max_seconds):
    """Bound this preparation process's existing requests downloader, without host auth."""
    source_root()
    deadline, used, lock = time.monotonic() + max_seconds, [0], threading.Lock()
    original_init, original_send = requests.Session.__init__, requests.Session.send

    def check():
        if time.monotonic() > deadline:
            raise TimeoutError("loki_source_download_timeout")

    def initialize(session, *args, **kwargs):
        original_init(session, *args, **kwargs)
        session.trust_env = False
        session.auth = None
        session.cookies.clear()

    def send(session, request, **kwargs):
        source_root()
        check()
        public_url(request.url)
        if request.method not in {"GET", "HEAD"} or any(name.lower() in {
                "authorization", "proxy-authorization", "cookie"} for name in request.headers):
            raise PermissionError("loki_public_source_auth_forbidden")
        requested_stream = bool(kwargs.get("stream"))
        kwargs.update(allow_redirects=False, stream=True, timeout=(15, 60), proxies={})
        for _ in range(4):
            check()
            public_url(request.url)
            try:
                response = original_send(session, request, **kwargs)
            except requests.RequestException as error:
                raise RuntimeError("loki_public_source_request_failed: " + type(error).__name__) from None
            session.cookies.clear()
            if response.status_code in {301, 302, 303, 307, 308}:
                destination = public_url(urljoin(request.url, response.headers.get("Location", "")))
                response.close()
                request = requests.Request(request.method, destination, headers=dict(request.headers)).prepare()
                continue
            if response.status_code >= 400:
                response.close()
                raise RuntimeError("loki_public_source_http_" + str(response.status_code))
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                response.close()
                raise ValueError("loki_source_download_exceeds_limit")
            original_iter = response.iter_content

            def bounded_content(chunk_size=1, decode_unicode=False):
                for chunk in original_iter(chunk_size=chunk_size, decode_unicode=decode_unicode):
                    check()
                    with lock:
                        used[0] += len(chunk)
                        if used[0] > max_bytes:
                            raise ValueError("loki_source_download_exceeds_limit")
                    yield chunk

            response.iter_content = bounded_content
            if not requested_stream:
                response.content
            return response
        raise RuntimeError("loki_public_source_redirect_limit")

    requests.Session.__init__, requests.Session.send = initialize, send
    try:
        yield check
    finally:
        requests.Session.__init__, requests.Session.send = original_init, original_send

def public_document(url, *, xml=False):
    source_root()
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(public_url(url), headers={"User-Agent": "Mozilla/5.0",
            "Accept": "application/dash+xml" if xml else "application/json"}, stream=True, timeout=(15, 30), allow_redirects=False)
        response.raise_for_status()
        body = bytearray()
        for part in response.iter_content(64 * 1024):
            body.extend(part)
            if len(body) > 16_000_000:
                raise ValueError("loki_source_document_exceeds_limit")
        return bytes(body)

class NetworkManager:
    def __init__(self, *args, **kwargs):
        raise ValueError("loki_source_preparation_required: browser sessions are unavailable")

    @staticmethod
    def get_video_info(video_no, cookies=None, *, public_only=True):
        source_root()
        if cookies or not re.fullmatch(r"[0-9]{1,16}", str(video_no)):
            raise PermissionError("loki_public_source_identity_invalid")
        payload = json.loads(public_document("https://api.chzzk.naver.com/service/v2/videos/" + str(video_no)))
        content = payload.get("content")
        if not isinstance(content, dict) or str(content.get("videoNo")) != str(video_no):
            raise ValueError("loki_public_vod_unavailable_or_mismatched")
        channel = content.get("channel") or {}
        metadata = dict(title=content.get("videoTitle", ""), duration=content.get("duration", 0),
            channelId=channel.get("channelId", ""), channelName=channel.get("channelName", ""),
            category=content.get("videoCategoryValue", ""), thumbnailImageUrl=content.get("thumbnailImageUrl", ""),
            createdDate=content.get("liveOpenDate", ""), liveOpenDate=content.get("liveOpenDate", ""),
            publishDate=content.get("publishDate", ""))
        return content.get("videoId"), content.get("inKey"), bool(content.get("adult")), content.get("vodStatus"), None, metadata

    @staticmethod
    def get_video_dash_media_manifest(video_id, in_key):
        source_root()
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", video_id) or not isinstance(in_key, str) or not 1 <= len(in_key) <= 8192:
            raise ValueError("loki_public_playback_unavailable")
        raw = public_document("https://apis.naver.com/neonplayer/vodplay/v2/playback/" + quote(video_id, safe="")
                              + "?" + urlencode({"key": in_key}), xml=True)
        root = ET.fromstring(raw)
        ns, video, audio = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}, [], []
        for adaptation in root.findall(".//mpd:AdaptationSet", ns):
            for rep in adaptation.findall("mpd:Representation", ns):
                base = rep.find("mpd:BaseURL", ns)
                url = (base.text or "").strip() if base is not None else ""
                if not url or url.endswith("/hls/"):
                    url = rep.get("{urn:naver:vod:2020}m3u") or ""
                if not url:
                    continue
                public_url(url)
                mime = rep.get("mimeType") or adaptation.get("mimeType", "")
                if rep.get("width") and rep.get("height") and (not mime or mime.startswith("video/")):
                    video.append([min(int(rep.get("width")), int(rep.get("height"))), url])
                elif mime.startswith("audio/") or rep.get("audioSamplingRate"):
                    audio.append(dict(bandwidth=int(rep.get("bandwidth") or 0), url=url,
                                      codec=rep.get("codecs") or adaptation.get("codecs", "")))
        return dict(video=sorted(video), audio=sorted(audio, key=lambda row: row["bandwidth"]),
                    seeking_thumbnail=None, manifest_sha256=hashlib.sha256(raw).hexdigest())

    @staticmethod
    def get_video_dash_manifest(video_id, in_key):
        rows = NetworkManager.get_video_dash_media_manifest(video_id, in_key)["video"]

        direct = [row for row in rows if not urlsplit(row[1]).path.lower().endswith(".m3u8")]
        rows = direct or rows
        return rows, rows[-1][0] if rows else None, rows[-1][1] if rows else None
