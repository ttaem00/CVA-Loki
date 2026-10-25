'Editor brief v0 — YouTube 편집자용 컷 후보 산출물.\n\n`pipeline.summarizer._parse_summary_sections()` 가 추출한 `editor_brief_points`\n를 디스크에 별도 사이드카(`<base>_editor_brief.json`) 로 저장하고, 관리자 admin\n서버의 `/editor` GET 라우트가 그 데이터를 로드해 표시한다.\n\n시청자 surface (report.md / report.html / site/) 와 완전히 분리된 산출물:\n- 같은 chunk 통합 LLM 호출의 추가 섹션 출력만 사용 (신규 LLM call 0).\n- 시청자 .md / .html 본문 byte-스테이블 유지 (`_strip_editor_brief_section`).\n- 기본 binding 127.0.0.1 (admin 서버 inherits) — 외부 공개 X.\n\nSchema (sidecar JSON):\n{\n  "schema_version": "editor-brief/1",\n  "video_no": "...",\n  "base": "...",\n  "title": "...",\n  "channel": "...",\n  "publish_date": "...",\n  "generated_at": "...",\n  "source": "chunk-integration",   # 통합 프롬프트 1회 호출 기준\n  "points": [\n    {"tc_range": "00:12:34~00:13:45",\n     "title": "...",\n     "cut_reason": "...",\n     "context_warning": "..."}\n  ]\n}\n'

from __future__ import annotations

import json

import os

import re

from datetime import datetime, timezone, timedelta

from pathlib import Path

from typing import Iterable, Optional

SCHEMA_VERSION = "editor-brief/1"

SIDECAR_SUFFIX = "_editor_brief.json"

KST = timezone(timedelta(hours=9))

def sidecar_path_for_base(output_dir: str | os.PathLike, base: str) -> Path:
    """`<output_dir>/<base>_editor_brief.json` 경로."""
    return Path(output_dir) / f"{base}{SIDECAR_SUFFIX}"

def _normalize_point(raw: dict) -> Optional[dict]:
    """`_parse_summary_sections` 가 만든 dict 를 사이드카 schema 로 정리.

    필드 누락/공백은 빈 문자열로 정규화. tc_range 가 비면 drop.
    """
    if not isinstance(raw, dict):
        return None
    tc_range = (raw.get("tc_range") or "").strip().strip("[] ")
    if not tc_range:
        return None
    title = (raw.get("title") or "").strip().rstrip(" *`")
    cut_reason = (raw.get("cut_reason") or "").strip().rstrip(" .。")
    context_warning = (raw.get("context_warning") or "").strip().rstrip(" .。")
    return {
        "tc_range": tc_range,
        "title": title,
        "cut_reason": cut_reason,
        "context_warning": context_warning,
    }

def build_payload(
    base: str,
    video_no: str,
    title: str,
    channel: str,
    publish_date: str,
    points: Iterable[dict],
    *,
    source: str = "chunk-integration",
) -> dict:
    """사이드카 payload dict 생성."""
    norm_points: list[dict] = []
    for p in points or []:
        np = _normalize_point(p)
        if np is not None:
            norm_points.append(np)
    return {
        "schema_version": SCHEMA_VERSION,
        "video_no": video_no or "",
        "base": base,
        "title": title or "",
        "channel": channel or "",
        "publish_date": publish_date or "",
        "generated_at": datetime.now(KST).isoformat(),
        "source": source,
        "points": norm_points,
    }

def save_sidecar(output_dir: str | os.PathLike, base: str, payload: dict) -> Path:
    """사이드카 JSON 을 atomic-ish 로 저장.

    빈 points 배열이라도 파일 자체는 생성 (재생성 사이클 추적용). 호출자는
    빈 points 시 호출 자체를 스킵해도 OK.
    """
    target = sidecar_path_for_base(output_dir, base)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target

def load_sidecar(output_dir: str | os.PathLike, base: str) -> Optional[dict]:
    """저장된 사이드카를 dict 로 반환. 없으면 None.

    JSON corrupt / schema 다름 등은 caller 에서 graceful 처리.
    """
    path = sidecar_path_for_base(output_dir, base)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def load_sidecar_by_video_no(output_dir: str | os.PathLike, video_no: str) -> Optional[dict]:
    """`video_no` 만 알 때 `<video_no>_*_editor_brief.json` 파일을 찾아 로드.

    `generate_reports` 가 만드는 base 는
    `<video_no>_<YYYYMMDD>_<sanitized_title>` 형식이지만, 외부에서는 video_no
    만 알 가능성이 높다. 매칭이 여러 개면 mtime 최신 1개 사용.
    """
    if not video_no:
        return None
    out_root = Path(output_dir)
    if not out_root.exists():
        return None
    candidates = sorted(
        out_root.glob(f"{video_no}_*{SIDECAR_SUFFIX}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def render_download_text(payload: dict) -> str:
    '`.txt` 다운로드용 plain text.\n\n    각 항목은 한 줄: `[HH:MM:SS] 제목 — 컷 이유`.\n    tc_range 가 range 형식이면 시작 tc 만 노출.\n    '
    lines: list[str] = []
    for p in payload.get("points") or []:
        tc_range = str(p.get("tc_range") or "").strip()
        if not tc_range:
            continue
        start_tc = tc_range.split("~", 1)[0].strip()
        title = str(p.get("title") or "").strip()
        cut_reason = str(p.get("cut_reason") or "").strip()

        if title and cut_reason:
            lines.append(f"[{start_tc}] {title} — {cut_reason}")
        elif title:
            lines.append(f"[{start_tc}] {title}")
        else:
            lines.append(f"[{start_tc}]")
    return "\n".join(lines) + ("\n" if lines else "")

def is_valid_payload(payload: Optional[dict]) -> bool:
    """최소한의 schema check (안전 가드용)."""
    if not isinstance(payload, dict):
        return False
    pts = payload.get("points")
    return isinstance(pts, list)

_BASE_RE = re.compile(r"^[A-Za-z0-9_\-가-힣\. ]+$")

_WIN_RESERVED_BASES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

def is_safe_base(base: str) -> bool:
    """admin 라우트 경로 traversal 방어용. `..` 차단 + 가벼운 whitelist."""
    if not base or len(base) > 256:
        return False
    if ".." in base or "/" in base or "\\" in base:
        return False
    if not _BASE_RE.match(base):
        return False
    stem = base.rstrip(" .").split(".", 1)[0].upper()
    return stem not in _WIN_RESERVED_BASES
