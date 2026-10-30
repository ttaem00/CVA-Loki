"""Small shared primitives for summary document and viewer rendering."""

from __future__ import annotations

import re

def _audio_peak_delta_text(tc_text: str, audio_peak_secs: list[float], tolerance_sec: int = 30) -> str:
    if not audio_peak_secs:
        return ""
    try:
        from ..timeline_alignment import hms_to_sec

        parts = [part.strip() for part in (tc_text or "").split("~") if part.strip()]
        start = float(hms_to_sec(parts[0]))
        end = float(hms_to_sec(parts[1])) if len(parts) > 1 else start
    except (IndexError, TypeError, ValueError):
        return ""
    if end < start:
        start, end = end, start

    best_delta: float | None = None
    for peak_sec in audio_peak_secs:
        if start <= peak_sec <= end:
            delta = 0.0
        else:
            delta = min(abs(peak_sec - start), abs(peak_sec - end))
        if best_delta is None or delta < best_delta:
            best_delta = delta

    if best_delta is None or best_delta > tolerance_sec:
        return ""
    return "구간 내" if best_delta == 0 else f"±{int(round(best_delta))}s"

def _build_public_vod_url(public_url_base: str = "", video_no: str = "") -> str:
    if not public_url_base or not video_no:
        return ""
    return f"{public_url_base.rstrip('/')}/vods/{video_no}/report"

def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))

def _hms_to_seconds(hms: str) -> int | None:
    """`HH:MM:SS` 또는 `MM:SS` → 초. 파싱 실패 시 None."""
    text = (hms or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None
    return None

def _chzzk_video_url_at(video_no: str, tc_or_range: str) -> str:
    """치지직 다시보기 특정 시점 링크.

    `HH:MM:SS` 또는 `HH:MM:SS~HH:MM:SS` (range 일 때 시작 tc 사용) 둘 다 처리.
    유효한 video_no + 파싱 가능한 tc 가 아니면 빈 문자열을 반환해 호출부에서
    링크 없이 fallback 하도록 한다.
    """
    video_no = str(video_no or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", video_no) or not tc_or_range:
        return ""
    start_tc = tc_or_range.split("~", 1)[0].strip() if "~" in tc_or_range else tc_or_range.strip()
    secs = _hms_to_seconds(start_tc)
    if secs is None:
        return ""
    return f"https://chzzk.naver.com/video/{video_no}?currentTime={secs}"
