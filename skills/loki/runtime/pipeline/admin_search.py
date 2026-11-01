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

from pathlib import Path

from typing import Iterable

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
