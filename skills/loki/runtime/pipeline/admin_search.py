"""B41 — Admin search core.

VOD 자막(SRT) / 요약 markdown / 채팅 로그 / 타임라인 섹션을 대상으로 substring 검색.

설계 원칙:
  - 26개 VOD × ~5MB SRT live scan → <1s 예상. SQLite/FTS 도입은 1000+ VOD 부터.
  - 결과는 VOD 단위로 그룹화 → 한 단어가 여러 번 등장해도 VOD 카드 1개에 매치 N개.
  - SRT 매치는 timestamp 포함 → 추후 "타임스탬프로 점프" UX 확장 여지.
  - 요약 매치는 섹션 헤더(##/###/-) 컨텍스트 추출.
  - 출력 dir / site dir / work dir 의 위치는 호출부에서 주입 (config 의존성 격리).
"""

from __future__ import annotations

import json

import re

from dataclasses import dataclass

from pathlib import Path

from typing import Iterable

_TIMELINE_HEADER_RE = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")

_SNIPPET_RADIUS = 60

@dataclass
class SearchMatch:
    source: str
    snippet: str
    timestamp: str = ""
    ts_ms: int = 0
    section: str = ""

def _hms_to_ms(h: int, m: int, s: int, ms: int = 0) -> int:
    return ((h * 3600) + (m * 60) + s) * 1000 + ms

def _make_snippet(text: str, start: int, end: int) -> str:
    """매치 위치 ±radius 컨텍스트 추출 — 단일 라인."""
    lo = max(0, start - _SNIPPET_RADIUS)
    hi = min(len(text), end + _SNIPPET_RADIUS)
    snippet = text[lo:hi].replace("\n", " ").replace("\r", " ").strip()
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(text):
        snippet = snippet + "…"

    return re.sub(r"\s+", " ", snippet)

def _search_summary(md_text: str, needle_lower: str, cap: int) -> list[SearchMatch]:
    """요약 markdown 에서 substring 매치 + 가장 가까운 섹션 헤더 컨텍스트."""
    matches: list[SearchMatch] = []
    if not md_text or not needle_lower:
        return matches

    md_lower = md_text.lower()
    cursor = 0
    while len(matches) < cap:
        idx = md_lower.find(needle_lower, cursor)
        if idx < 0:
            break

        section = ""
        ts_ms = 0
        timestamp = ""
        scan_lo = md_text.rfind("\n", 0, idx)
        head = md_text[: scan_lo if scan_lo > 0 else idx]

        tl = list(_TIMELINE_HEADER_RE.finditer(head))
        if tl:
            last = tl[-1]
            line_start = head.rfind("\n", 0, last.start()) + 1
            line_end = head.find("\n", last.end())
            line = head[line_start : line_end if line_end > 0 else None]
            section = line.strip().lstrip("- ").strip()
            h, m, s = (int(last.group(i)) for i in (1, 2, 3))
            ts_ms = _hms_to_ms(h, m, s)
            timestamp = f"{h:02d}:{m:02d}:{s:02d}"
        else:

            for line in reversed(head.splitlines()):
                ls = line.strip()
                if ls.startswith("#"):
                    section = ls.lstrip("#").strip()
                    break
        matches.append(
            SearchMatch(
                source="summary",
                snippet=_make_snippet(md_text, idx, idx + len(needle_lower)),
                timestamp=timestamp,
                ts_ms=ts_ms,
                section=section,
            )
        )
        cursor = idx + len(needle_lower)
    return matches

def _iter_vod_records(output_dir: Path, site_dir: Path) -> Iterable[dict]:
    """report 메타 인덱스를 가벼운 dict 형태로 yield."""
    for meta_path in sorted(output_dir.glob("*_metadata.json")):
        base = meta_path.name[: -len("_metadata.json")]
        md_path = output_dir / f"{base}.md"
        if not md_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        video_no = str(meta.get("video_no") or base.split("_", 1)[0])
        yield {
            "video_no": video_no,
            "base": base,
            "title": meta.get("title") or base,
            "publish_date": meta.get("publish_date") or "",
            "channel_id": meta.get("channel_id") or "",
            "channel": meta.get("channel") or "",
            "streamer_id": meta.get("streamer_id") or "",
            "md_path": md_path,
            "metadata_path": meta_path,
        }
