"""Meaning-first, public-safe timeline evidence presentation adapter."""

from __future__ import annotations

import re

from typing import Any

from urllib.parse import quote

UX_SCHEMA_VERSION = "timeline_evidence_ux.v1"

_PRIVATE_PATH_RE = re.compile(
    r"(?:^|[\s\"'])(?:[A-Za-z]:[\\/]|/(?:home|Users|private|var|tmp)/)",
    re.IGNORECASE,
)

_SAFE_CLIP_URL_RE = re.compile(
    r"^https://chzzk\.naver\.com/clips/[A-Za-z0-9_-]{3,80}$"
)

_SOURCE_PRESENTATION = {
    "subtitle": {"label": "자막", "icon": "▤", "style": "caption"},
    "chat": {"label": "채팅", "icon": "●", "style": "chat"},
    "viewer_clip": {"label": "시청자 클립", "icon": "▶", "style": "clip"},
}

def _safe_sec(value: Any) -> int | None:
    try:
        sec = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return sec if sec >= 0 else None

def _format_time(sec: int) -> str:
    hours, remainder = divmod(sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def _safe_display_text(value: Any) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text or _PRIVATE_PATH_RE.search(text):
        return None
    return text[:280]

def _vod_url(video_no: str, sec: int) -> str:
    safe_video_no = str(video_no or "").strip()
    if not safe_video_no.isdigit():
        return ""
    return (
        f"https://chzzk.naver.com/video/{quote(safe_video_no)}"
        f"?currentTime={sec}"
    )

def _presentation_chip(ref: dict[str, Any], *, video_no: str) -> dict[str, Any] | None:
    source_type = str(ref.get("source_type") or "")
    presentation = _SOURCE_PRESENTATION.get(source_type)
    start_sec = _safe_sec(ref.get("start_sec"))
    if (
        presentation is None
        or start_sec is None
        or ref.get("public_safe") is not True
        or bool(ref.get("raw_content_included"))
        or bool(ref.get("manager_review_required"))
        or bool(ref.get("risk_flags"))
    ):
        return None

    if source_type == "viewer_clip":
        url = str(ref.get("clip_url") or "").strip()
        if not _SAFE_CLIP_URL_RE.fullmatch(url):
            return None
    else:
        url = _vod_url(video_no, start_sec)
        if not url:
            return None

    time_text = _format_time(start_sec)
    label = str(presentation["label"])
    chip = {
        "label": label,
        "icon": presentation["icon"],
        "style": presentation["style"],
        "time": time_text,
        "url": url,
        "accessible_name": (
            f"{label} 근거 {time_text}, "
            + (
                "시청자 클립 열기"
                if source_type == "viewer_clip"
                else "치지직 다시보기에서 이 시각 열기"
            )
        ),
        "display_text": _safe_display_text(ref.get("display_text")),
    }
    if source_type == "viewer_clip":
        chip["title"] = (
            _safe_display_text(ref.get("clip_title")) or "시청자 인기 클립"
        )
    return chip

def build_timeline_evidence_ux_payload(
    shadow_contract: dict[str, Any] | None,
    *,
    video_no: str,
    verifier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate validated shadow refs into manager/public meaning-first rows.

    Internal identifiers, schema labels, match reasons, paths, and raw source
    text are intentionally omitted. The browser adapter additionally requires
    a meaningful existing card evidence sentence before it makes a time chip
    clickable.
    """

    contract = shadow_contract if isinstance(shadow_contract, dict) else {}
    verifier_payload = verifier if isinstance(verifier, dict) else {}
    verifier_passed = verifier_payload.get("status") in {"", None, "pass"}
    rows: list[dict[str, Any]] = []

    for item in contract.get("items") or []:
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "").strip()
        if not segment_id:
            continue
        chips: list[dict[str, Any]] = []
        if verifier_passed and item.get("manager_review_required") is not True:
            for ref in item.get("evidence_refs") or []:
                if not isinstance(ref, dict):
                    continue
                chip = _presentation_chip(ref, video_no=video_no)
                if chip is not None:
                    chips.append(chip)
        rows.append(
            {
                "segment_id": segment_id,
                "state_label": (
                    "근거 확인됨" if chips else "근거 확인 필요"
                ),
                "chips": chips,
            }
        )

    ready_count = sum(1 for row in rows if row["chips"])
    return {
        "schema_version": UX_SCHEMA_VERSION,
        "raw_content_included": False,
        "public_safe": True,
        "summary": {
            "timeline_item_count": len(rows),
            "ready_item_count": ready_count,
            "review_item_count": len(rows) - ready_count,
            "chip_count": sum(len(row["chips"]) for row in rows),
        },
        "items": rows,
    }
