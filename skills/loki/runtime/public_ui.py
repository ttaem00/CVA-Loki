"""Serve the canonical Report Admin pages and their explicit local asset set."""
from __future__ import annotations

from pathlib import Path
import re


UI_ASSETS = (
    "scripts/admin/templates/home.html",
    "scripts/admin/templates/report_workspace.html",
    "scripts/admin/templates/report.html",
    "scripts/admin/static/app_shell.css",
    "scripts/admin/static/home.css",
    "scripts/admin/static/home.js",
    "scripts/admin/static/report.css",
    "scripts/admin/static/report.js",
    "scripts/admin/static/report_lifecycle.js",
    "scripts/admin/static/report_adaptive_context.js",
    "scripts/admin/static/report_signal_timeline.js",
    "scripts/admin/static/report_manager_outline.js",
    "scripts/admin/static/report_scene_cockpit.js",
    "scripts/admin/static/approval_state.js",
    "scripts/admin/static/report_review_panels.js",
    "scripts/admin/static/report_distribution.js",
    "scripts/admin/static/report_timeline_candidate.js",
    "scripts/admin/static/report_versions.js",
    "scripts/admin/static/report_workspace.css",
    "scripts/admin/static/report_workspace.js",
    "scripts/admin/static/report_workspace_state.js",
    "scripts/admin/static/report_workspace_history.js",
    "scripts/admin/static/report_player.js",
    "scripts/admin/static/report_workspace_timeline.js",
    "scripts/admin/static/report_workspace_time_scrubber.js",
    "scripts/admin/static/vendor/hls.min.js",
    "scripts/admin/static/vendor/hls.LICENSE.txt",
    "pipeline/summary/viewer.css",
    "pipeline/summary/viewer.js",
)

PAGES = {"/": "home", "/reports": "home", "/report-workspace": "report_workspace", "/report": "report"}
STATIC = {"/static/admin/" + name.removeprefix("scripts/admin/static/"): name
          for name in UI_ASSETS if name.startswith("scripts/admin/static/")}
STATIC.update({"/static/shared-viewer/viewer.css": "pipeline/summary/viewer.css",
               "/static/shared-viewer/viewer.js": "pipeline/summary/viewer.js"})

NAV = """<link rel="stylesheet" href="/static/admin/app_shell.css">
  <nav id="app-shell-navigation" class="app-shell-nav" aria-label="Report Admin 주 메뉴">
    <a class="app-shell-brand" href="/" aria-label="Report Admin 홈"><span aria-hidden="true"></span>Report Admin</a>
    <div class="app-shell-groups">
      <a class="app-shell-tab active" href="/reports" aria-current="page" title="방송 요약을 읽고 편집합니다"><span aria-hidden="true">▤</span>리포트</a>
    </div>
  </nav>"""

LOCAL_STYLE = '<style>.deep-search-link,#bundleModeBtn{display:none!important}</style>'

_FONT_LINK = re.compile(r'^[ \t]*<link\b(?=[^>]*\bhref=["\']https://fonts\.(?:googleapis|gstatic)\.com(?:[/:"\']))[^>]*>\r?\n?', re.IGNORECASE | re.MULTILINE)


def _file(runtime, relative):
    root = Path(runtime).resolve(strict=True)
    current = root
    for part in Path(relative).parts:
        current /= part
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("public_ui_asset_link_forbidden")
    return current


def render_page(runtime, page):
    if page not in set(PAGES.values()):
        raise ValueError("public_ui_page_unknown")
    source = _file(runtime, "scripts/admin/templates/" + page + ".html").read_text(encoding="utf-8-sig")
    return attributed_html(_FONT_LINK.sub("", source).replace("{{NAV}}", NAV)
                           .replace("</head>", LOCAL_STYLE + "</head>"), runtime)


def attributed_html(source, runtime=None):
    runtime = Path(__file__).resolve().parent if runtime is None else runtime
    footer = _file(runtime, "branding.html").read_text(encoding="utf-8")
    return source.replace("</body>", footer + "\n</body>")


def response(runtime, path):
    """Return (body bytes, content type), or None for a route owned by the server."""
    if path in PAGES:
        return render_page(runtime, PAGES[path]).encode("utf-8"), "text/html; charset=utf-8"
    relative = STATIC.get(path)
    if relative is None:
        return None
    suffix = Path(relative).suffix
    kind = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".txt": "text/plain; charset=utf-8"}[suffix]
    return _file(runtime, relative).read_bytes(), kind
