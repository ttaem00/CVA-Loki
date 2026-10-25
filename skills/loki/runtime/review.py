"""Open a workspace's reports for local review, preview and explicit revision saving."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit


HERE = Path(__file__).resolve().parent
MAX_REQUEST = 50_000_000


def load_runtime():
    spec = importlib.util.spec_from_file_location("loki_review_entry", HERE / "loki.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


def handler(store, *, assets=HERE):
    media_checked = {}
    class ReviewHandler(BaseHTTPRequestHandler):
        server_version = "LokiReview"

        def log_message(self, *args):
            pass

        def _host(self):
            value = self.headers.get("Host", "")
            try:
                parsed = urlsplit("http://" + value)
                if (parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                        or parsed.port is None):
                    return None
            except ValueError:
                return None
            return value

        def _send(self, status, data, content_type="application/json; charset=utf-8", *, report=False):
            if not isinstance(data, bytes):
                data = (json.dumps(data, ensure_ascii=False) if content_type.startswith("application/json") else data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", (
                "default-src 'none'; img-src data: 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; sandbox allow-scripts"
                if report else "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _media(self, media):
            path = Path(media["path"])
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns, media["sha256"])
            if key not in media_checked:
                value = hashlib.sha256()
                with path.open("rb") as stream:
                    for part in iter(lambda: stream.read(1024 * 1024), b""):
                        value.update(part)
                if value.hexdigest() != media["sha256"]:
                    raise ValueError("source_media_changed")
                media_checked[key] = True
            start, end = 0, stat.st_size - 1
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", requested)
                if not match or not any(match.groups()):
                    self._send(416, {"error": "video_range_invalid"})
                    return
                left, right = match.groups()
                start = int(left) if left else max(0, stat.st_size - int(right))
                end = min(stat.st_size - 1, int(right)) if left and right else stat.st_size - 1
                if not 0 <= start <= end < stat.st_size:
                    self._send(416, {"error": "video_range_invalid"})
                    return
            self.send_response(206 if requested else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if requested:
                self.send_header("Content-Range", f"bytes {start}-{end}/{stat.st_size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        data = stream.read(min(remaining, 64 * 1024))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _route(self):
            if not self._host():
                self._send(403, {"error": "로컬 주소로 열어 주세요."})
                return None
            path = urlsplit(self.path).path
            if "%" in path or "\\" in path or ".." in path:
                self._send(404, {"error": "없는 주소입니다."})
                return None
            return path

        def do_GET(self):
            path = self._route()
            if path is None:
                return
            try:
                query = parse_qs(urlsplit(self.path).query)
                parameter = lambda name: query.get(name, [""])[0]
                if path == "/api/report":
                    self._send(200, store.workspace_report(parameter("base"), revision_only=parameter("view") == "revision"))
                elif path == "/api/report-catalog":
                    self._send(200, store.workspace_catalog(q=parameter("q"), streamer=parameter("streamer") or None,
                        offset=int(parameter("offset") or 0), limit=int(parameter("limit") or 100)))
                elif path == "/api/saved-html":
                    self._send(200, store.report_html(store.workspace_id(parameter("base"))), "text/html; charset=utf-8", report=True)
                elif path == "/source-manifest":
                    sources = store.originals(store.workspace_id(parameter("base")))["sources"]
                    self._send(200, "\n\n".join(row["text"] for row in sources), "text/plain; charset=utf-8")
                elif path == "/api/publish-revisions":
                    self._send(200, store.workspace_revisions(parameter("base")))
                elif path == "/api/publish-operation":
                    self._send(200, {"operation": None})
                elif path == "/api/publish-revision-html":
                    self._send(200, store.workspace_revision_html(parameter("base"), parameter("revision_id")),
                        "text/html; charset=utf-8", report=True)
                elif path == "/api/report-playback":
                    media = (store.workspace_media(parameter("base"), by_base=True) if parameter("base")
                             else store.workspace_media(parameter("video_no")))
                    self._send(200, {"playback": {"original_url": media["original_url"],
                        "stream_url": "/api/report-media/" + media["id"] + ".mp4"}})
                elif match := re.fullmatch(r"/api/report-media/([0-9a-f]{20})\.mp4", path):
                    self._media(store.workspace_media(match.group(1), by_id=True))
                else:
                    loader = importlib.util.spec_from_file_location("loki_public_pages", HERE / "public_ui.py")
                    ui = importlib.util.module_from_spec(loader)
                    loader.loader.exec_module(ui)
                    resource = ui.response(assets, path)
                    if resource is None:
                        self._send(404, {"error": "없는 주소입니다."})
                    else:
                        self._send(200, *resource)
            except (ValueError, KeyError, OSError, RuntimeError) as error:
                self._error(error)

        do_HEAD = do_GET

        def _error(self, error):
            conflict = getattr(error, "status", None) == 409 or type(error).__name__ in {"ReviewConflict", "ManagerEditConflict"}
            self._send(409 if conflict else 400, {"error": str(error)[:1200]})

        def do_POST(self):
            path = self._route()
            if path is None:
                return
            if self.headers.get("Origin") != "http://" + self._host():
                self._send(403, {"error": "화면을 다시 열고 저장해 주세요."})
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON 요청이 필요합니다.")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_REQUEST:
                    raise ValueError("편집 요청의 크기를 확인해 주세요.")
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("편집 요청의 형식을 확인해 주세요.")
                canonical_methods = {"/api/preview": "workspace_preview", "/api/manager-outline/save": "workspace_save",
                    "/api/manager-outline/restore": "workspace_restore", "/api/manager-outline/sync": "workspace_sync", "/api/save": "workspace_save_markdown",
                    "/api/publish-revision-draft": "workspace_revision_draft"}
                if path in canonical_methods:
                    self._send(200, getattr(store, canonical_methods[path])(payload))
                else:
                    self._send(404, {"error": "없는 주소입니다."})
            except (ValueError, KeyError, OSError, RuntimeError) as error:
                self._error(error)

    return ReviewHandler


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1",
        help="Use 0.0.0.0 only inside a container with a host-loopback port mapping")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    entry = load_runtime()
    entry.verify_release()
    root, _, _ = entry.configuration(args.config)
    from review_store import ReviewStore
    store = ReviewStore(root, config_path=args.config)
    server = ThreadingHTTPServer((args.host, args.port), handler(store))
    entry.isolate(root, args.config)
    print(json.dumps({"status": "ready", "url": f"http://127.0.0.1:{args.port}/", "publish": False}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
