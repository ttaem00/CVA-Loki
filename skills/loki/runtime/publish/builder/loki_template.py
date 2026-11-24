"""Bind public report data to the manager-supplied Loki template.

The supplied UI runtime is copied byte-for-byte; only hosting addresses are bound.
Report meaning comes from the existing public projection.
"""

from __future__ import annotations

import base64

from dataclasses import dataclass, field

from functools import lru_cache

import hashlib

from html import escape

from html.parser import HTMLParser

from io import BytesIO

import json

import math

from pathlib import Path

import re

from urllib.parse import urlsplit

from PIL import Image

from publish.builder.loki_runtime import RUNTIME_PREFIX, SOURCE, runtime_asset

PAGE_NAME = "loki-page.json"

_RASTER = {"image/png": ("png", "PNG"), "image/jpeg": ("jpg", "JPEG"),
           "image/webp": ("webp", "WEBP"), "image/gif": ("gif", "GIF"),
           "image/avif": ("avif", "AVIF")}

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

_THEME_BOOT = """
(() => {
  const root = document.documentElement;
  const EXPLICIT_KEY = 'loki-theme-explicit-v2';
  const RUNTIME_KEY = 'loki-design-theme-v1';
  let theme = 'dark';
  try {
    const saved = localStorage.getItem(EXPLICIT_KEY);
    if (saved === 'light' || saved === 'dark') theme = saved;
    localStorage.setItem(RUNTIME_KEY, theme);
  } catch (_) {}
  root.dataset.theme = theme;
  const syncColor = () => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = root.dataset.theme === 'dark' ? '#141D2B' : '#FAF5EB';
  };
  syncColor();
  document.addEventListener('loki:report-ready', () => {
    const btn = document.getElementById('themeButton');
    if (root.dataset.theme !== theme) btn?.click();
    syncColor();
    btn?.addEventListener('click', () => {
      syncColor();
      try {
        if (root.dataset.theme === 'light' || root.dataset.theme === 'dark') {
          localStorage.setItem(EXPLICIT_KEY, root.dataset.theme);
        }
      } catch (_) {}
    });
  }, {once: true});
})();
"""

THEME_BOOT_CSP = "sha256-" + base64.b64encode(hashlib.sha256(_THEME_BOOT.encode("utf-8")).digest()).decode("ascii")

_SAVED_SOURCE_START = '<template id="lokiSavedReportSource">\n'

_SAVED_SOURCE_END = '\n</template><!-- loki-saved-report-source -->'

_SAVED_REPORT_BOOT = """
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const main = document.getElementById('top');
  const status = document.getElementById('reportStatus');
  try {
    const packet = JSON.parse(document.getElementById('lokiSavedPageData').textContent);
    const source = window.LokiReportSource;
    // The packet and its assets are embedded. The VOD URL identifies links and
    // bookmarks when the document has no HTTP origin.
    const environment = source.normalize(packet, {
      reportURL: source.sourceURLAt(packet, 0)
    });
    const view = window.LokiReportAdapter.create(packet, environment);
    root.dataset.lokiStatus = 'rendering';
    if (main) main.style.visibility = 'hidden';
    // The standalone viewer has no editor server. Its original VOD links
    // still come from the verified source in the adapter.
    window.LokiApp.mount({...view, videoNo: ''}, environment);
    document.getElementById('viewEditor').title = '편집 도구는 Report Admin에서 열 수 있습니다.';
    document.getElementById('shareButton').hidden = true;
    root.dataset.lokiStatus = 'ready';
    status.hidden = true;
    document.dispatchEvent(new CustomEvent('loki:report-ready', {detail:{reportURL:environment.reportURL}}));
  } catch (error) {
    root.dataset.lokiStatus = 'error';
    status.hidden = false;
    status.setAttribute('role', 'alert');
    document.getElementById('reportStatusTitle').textContent = '저장된 보고서를 표시할 수 없습니다.';
    document.getElementById('reportStatusMessage').textContent = error.message;
  } finally {
    if (main) {
      main.style.removeProperty('visibility');
      main.setAttribute('aria-busy', 'false');
    }
  }
}, {once:true});
"""

@dataclass
class _Node:
    tag: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def nodes(self, *, tag: str = "", cls: str = ""):
        for child in self.children:
            if isinstance(child, _Node):
                if (not tag or child.tag == tag) and (not cls or cls in child.attrs.get("class", "").split()):
                    yield child
                yield from child.nodes(tag=tag, cls=cls)

    def first(self, *, tag: str = "", cls: str = ""):
        return next(self.nodes(tag=tag, cls=cls), None)

    def text(self, *, omit: str = "") -> str:
        return "".join(child if isinstance(child, str) else
                       "" if omit and omit in child.attrs.get("class", "").split() else
                       "\n" if child.tag == "br" else child.text(omit=omit)
                       for child in self.children).strip()

class _PublicHTML(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def _text(node: _Node | None) -> str:
    return node.text() if node else ""

def _public_image(src: str, images: dict[str, bytes]) -> str:
    if not src:
        return ""
    if not src.startswith("data:"):
        url = urlsplit(src)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise ValueError("loki_image_source_invalid")
        return src
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif|avif));base64,([A-Za-z0-9+/=\s]+)", src)
    if not match:
        raise ValueError("loki_image_source_invalid")
    raw = base64.b64decode(re.sub(r"\s", "", match[2]), validate=True)
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ValueError("loki_image_size_invalid")
    extension, image_format = _RASTER[match[1]]
    with Image.open(BytesIO(raw)) as image:
        if image.format != image_format or min(image.size) < 1 or max(image.size) > 16384:
            raise ValueError("loki_image_format_invalid")
        image.verify()
    path = f"images/{hashlib.sha256(raw).hexdigest()}.{extension}"
    images[path] = raw
    if len(images) > 128:
        raise ValueError("loki_image_count_invalid")
    return path

def entry_html() -> str:
    """One identical HTML entry for every VOD, including immutable URLs."""
    original_bytes = (SOURCE / "index.html").read_bytes()
    identity = json.loads((SOURCE.parent / "provenance.json").read_text(encoding="utf-8"))["files"]["index.html"]
    if hashlib.sha256(original_bytes).hexdigest() != identity["sha256"]:
        raise ValueError("loki_template_source_changed")
    original = original_bytes.decode("utf-8")
    html = original.replace('data-default-report="reports/default.json"',
                            f'data-default-report="./{PAGE_NAME}"', 1).replace(
                                "shared/loki/v1/", RUNTIME_PREFIX)
    html = html.replace('data-theme="light"', 'data-theme="dark"', 1)
    return html.replace('<meta name="theme-color" content="#FAF5EB">',
                        '<meta name="theme-color" content="#141D2B">\n'
                        f'<script id="loki-public-theme">{_THEME_BOOT}</script>', 1)

def saved_report_source_html(html_text: str) -> str:
    """Read the original presentation input without changing its saved identity."""
    start = html_text.find(_SAVED_SOURCE_START)
    if start == -1:
        return html_text
    end = html_text.rfind(_SAVED_SOURCE_END)
    if end < start:
        raise ValueError("loki_saved_report_source_incomplete")
    return html_text[start + len(_SAVED_SOURCE_START):end]

def _data_url(raw: bytes, mime: str) -> str:
    return "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")

@lru_cache(maxsize=1)
def _saved_report_entry() -> str:
    """Use the supplied viewer assets in a document that can be opened from disk."""
    def replace_link(match: re.Match) -> str:
        tag, name = match[0], match["name"]
        if name.endswith(".woff2"):
            return ""
        if name.endswith(".css"):
            css = runtime_asset(name).decode("utf-8")
            if "'assets/PretendardVariable.woff2'" in css:
                font = _data_url(runtime_asset("assets/PretendardVariable.woff2"), "font/woff2")
                css = css.replace("'assets/PretendardVariable.woff2'", "'" + font + "'")
            return "<style>" + css + "</style>"
        return tag

    entry = re.sub(
        rf'<link\b[^>]*href="(?P<url>{re.escape(RUNTIME_PREFIX)}(?P<name>[^"?]+)(?:\?[^"]*)?)"[^>]*>',
        replace_link, entry_html(),
    )
    entry = re.sub(
        rf'{re.escape(RUNTIME_PREFIX)}(assets/[^"\s]+\.svg)',
        lambda match: _data_url(runtime_asset(match[1]), "image/svg+xml"),
        entry,
    )

    def replace_script(match: re.Match) -> str:
        name = match["name"]
        body = _SAVED_REPORT_BOOT if name == "bootstrap.js" else runtime_asset(name).decode("utf-8")
        return "<script>" + re.sub(r"</script", r"<\\/script", body, flags=re.IGNORECASE) + "</script>"

    return re.sub(
        rf'<script\s+src="{re.escape(RUNTIME_PREFIX)}(?P<name>[^"?]+)\?[^"]+"\s+defer></script>',
        replace_script, entry,
    )

def saved_report_html(source_html: str, viewer: dict, record: dict) -> str:
    """Write the latest reader with inline data and an inert source for existing readers."""
    source = saved_report_source_html(source_html)
    prepared = prepare_report(source, viewer, record)
    if prepared is None:
        return source_html
    packet, images = prepared
    mime = {extension: kind for kind, (extension, _) in _RASTER.items()}
    rendered = packet["renderedOriginal"]
    for attributes in [
        *(scene["image"]["imageAttributes"] for scene in rendered["highlights"] if scene.get("image")),
        *(clip["imageAttributes"] for clip in rendered["clips"]),
    ]:
        path = attributes.get("src")
        if path in images:
            attributes["src"] = _data_url(images[path], mime[Path(path).suffix[1:]])
    data = json.dumps(packet, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    content = (
        '<script id="lokiSavedPageData" type="application/json">'
        + data.replace("<", "\\u003c") + "</script>\n"
        + _SAVED_SOURCE_START + source + _SAVED_SOURCE_END + "\n"
    )
    title = escape(packet["metadata"]["title"] or "Loki")
    entry = re.sub(r"<title>[^<]*</title>", lambda _: f"<title>{title}</title>", _saved_report_entry(), count=1)
    head, closing_body, tail = entry.rpartition("</body>")
    return head + content + closing_body + tail

def supports_report(viewer: dict) -> bool:
    """Historical reports lacking the common reader contract retain their reader."""
    if not viewer or viewer.get("schema_version") != "viewer_editor_tools.v1":
        return False
    duration = viewer.get("duration_sec")
    return bool(viewer.get("video_no") and isinstance(viewer.get("outline_projection"), list)
                and isinstance(viewer.get("events"), list) and not isinstance(duration, bool)
                and isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0)

def prepare_report(html_text: str, viewer: dict, record: dict) -> tuple[dict, dict[str, bytes]] | None:
    """Project existing public display information without regenerating content."""
    if not supports_report(viewer):
        return None
    duration = viewer["duration_sec"]
    if str(viewer.get("video_no") or "") != str(record["video_no"]):
        raise ValueError("loki_report_identity_mismatch")
    root = _PublicHTML(saved_report_source_html(html_text)).root
    by_id = {node.attrs["id"]: node for node in root.nodes() if node.attrs.get("id")}
    overview = root.first(cls="broadcast-summary")
    paragraphs = [node.text() for node in overview.nodes(tag="p")] if overview else []
    identity = by_id.get("summaryPipelineIdentity")
    identity_attrs = {key: value for key, value in identity.attrs.items() if key.startswith("data-summary-")} if identity else {}
    version = identity_attrs.get("data-summary-mechanism-version", "")
    if not version:
        match = re.search(r"Loki\s+(\d+(?:\.\d+)+)", _text(identity))
        version = match[1] if match else ""
    metadata = {"title": _text(root.first(tag="h1")) or record.get("title", ""),
                "streamer": record.get("streamer_name", ""), "date": record.get("published_at") or record.get("published_date_str", ""),
                "game": record.get("platform_category") or "", "lokiVersion": version,
                "summaryParagraphs": paragraphs, "summary": "\n\n".join(paragraphs),
                "videoNo": viewer["video_no"], "durationSec": viewer.get("duration_sec"),
                "sourceIdentityRawAttributes": identity_attrs}
    for source, target in (("source_platform", "sourcePlatform"), ("source_id", "sourceId"), ("source_url", "sourceUrl")):
        if viewer.get(source):
            metadata[target] = viewer[source]
    images: dict[str, bytes] = {}
    highlights = []
    for highlight in viewer.get("outline_projection", []):
        if highlight.get("kind") != "editorial_highlight":
            continue
        identifier = highlight["id"]
        fragment = "editorial-highlight-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", identifier).strip("-")
        card = by_id.get(fragment)
        reason = card.first(cls="highlight-card-reason") if card else None
        story = card.first(cls="highlight-story-flow") if card else None
        source = card.first(cls="highlight-link") if card else None
        presentation = {"id": identifier, "selectionReason": reason.text(omit="highlight-card-reason-label") if reason else "",
                        "storySummary": _text(story.first(tag="p")) if story else "",
                        "sourceUrl": source.attrs.get("href", "") if source else ""}
        figure = card.first(cls="editorial-scene-image") if card else None
        image = figure.first(tag="img") if figure else None
        if image:
            caption = figure.first(tag="figcaption")
            caption_link = caption.first(tag="a") if caption else None
            attributes = {key: value for key, value in image.attrs.items() if key in {"src", "alt", "width", "height", "loading"}}
            attributes["src"] = _public_image(attributes.get("src", ""), images)
            presentation["image"] = {"figureAttributes": {key: value for key, value in figure.attrs.items() if key.startswith("data-")},
                                     "imageAttributes": attributes, "caption": _text(caption_link),
                                     "captionLinkAttributes": {key: value for key, value in caption_link.attrs.items() if key in {"href", "data-start"}} if caption_link else {},
                                     "sourceIntervalLabel": _text(figure.first(cls="editorial-scene-clock"))}
        elif isinstance(highlight.get("image"), dict) and highlight["image"].get("data_url"):
            from pipeline.editorial_highlight_cards import _source_url_at, sec_to_hms

            stored = highlight["image"]
            sec = stored.get("source_sec")
            if (isinstance(sec, bool) or not isinstance(sec, (int, float))
                    or not math.isfinite(sec) or not 0 <= sec <= duration):
                raise ValueError("loki_image_source_time_invalid")
            image_path = _public_image(stored["data_url"], images)
            if image_path not in images:
                raise ValueError("loki_image_source_invalid")
            digest = Path(image_path).stem
            interval = stored.get("source_interval_sec")
            clock = (f"{sec_to_hms(interval[0])}–{sec_to_hms(interval[1])} 미리보기"
                     if interval else f"{sec_to_hms(sec)} 원본 장면")
            presentation["image"] = {
                "figureAttributes": {"data-frame-ref": str(stored.get("frame_ref") or ""),
                                     "data-image-sha256": str(stored.get("sha256") or ""),
                                     "data-image-delivery-sha256": digest},
                "imageAttributes": {"src": image_path, "alt": str(stored.get("alt") or ""), "loading": "lazy"},
                "caption": str(stored.get("caption") or clock),
                "captionLinkAttributes": {"href": _source_url_at(str(viewer["video_no"]), sec), "data-start": str(sec)},
                "sourceIntervalLabel": clock,
            }
        highlights.append(presentation)
    clips = []
    events = {str(evidence.get("clip_uid")): event for event in viewer.get("events", [])
              if event.get("kind") == "viewer_clip" for evidence in event.get("evidence", []) if evidence.get("clip_uid")}
    for card in root.nodes(cls="user-clip-card"):
        links = list(card.nodes(tag="a"))
        source = next((link for link in links if "/clips/" in link.attrs.get("href", "")), None)
        if source is None:
            continue
        uid = urlsplit(source.attrs["href"]).path.rstrip("/").rsplit("/", 1)[-1]
        if uid not in events:
            continue
        image = card.first(tag="img")
        attributes = {key: value for key, value in image.attrs.items() if key in {"src", "alt", "width", "height", "loading"}} if image else {}
        if attributes.get("src"):
            attributes["src"] = _public_image(attributes["src"], images)
        clips.append({"clipUid": uid, "title": events[uid].get("title", ""), "sourceUrl": source.attrs["href"],
                      "imageAttributes": attributes, "renderedMeta": _text(card.first(cls="user-clip-meta"))})
    rendered = {"highlights": highlights, "clips": clips}
    for key in ("reportDocumentData", "summaryEvidencePackData"):
        node = by_id.get(key)
        if node and node.text():
            from publish.builder.report_sanitizers import _strip_public_viewer_editor_unsafe_fields

            rendered[key] = _strip_public_viewer_editor_unsafe_fields(json.loads(node.text()))
    packet = {"schema_version": "loki_page.v1", "report_id": f"vod:{viewer['video_no']}",
              "asset_base": "./", "metadata": metadata, "viewerOutput": viewer, "renderedOriginal": rendered}


    return packet, images
