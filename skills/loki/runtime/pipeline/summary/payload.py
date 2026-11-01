"""Pure rendered-summary evidence payload helpers.

This module owns the compact, public-safe data projection embedded in summary
report HTML. Rendering and report orchestration remain in ``pipeline.summarizer``.
"""

from __future__ import annotations

from pathlib import Path

from typing import Optional

from ..models import UserClipsBundle, VODInfo

from ..user_clips_filter import is_eligible_in_vod_clip

_MERGE_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "highlight" / "editorial_full_author_302.md"

_USER_CLIPS_ANCHOR_BUCKET_SEC = 60

_ANCHORED_UPPER_ESCAPE_PLAY = 1000

def _clip_engagement_tuple(clip) -> tuple[int, int]:
    like = getattr(clip, "like_count", None)
    like_norm = int(like) if isinstance(like, (int, float)) and like >= 0 else 0
    play = getattr(clip, "play_count", None)
    play_norm = int(play) if isinstance(play, (int, float)) and play >= 0 else 0
    return like_norm, play_norm

def _clip_offset_int(clip) -> Optional[int]:
    try:
        offset = int(getattr(clip, "offset_sec", None))
    except (TypeError, ValueError):
        return None
    return offset if offset >= 0 else None

def _bucket_winner_is_better(cur, prev) -> bool:
    cur_off = _clip_offset_int(cur)
    prev_off = _clip_offset_int(prev)
    if cur_off is None:
        return False
    if prev_off is None:
        return True

    cur_like, cur_play = _clip_engagement_tuple(cur)
    prev_like, prev_play = _clip_engagement_tuple(prev)
    cur_escape = cur_play >= _ANCHORED_UPPER_ESCAPE_PLAY
    prev_escape = prev_play >= _ANCHORED_UPPER_ESCAPE_PLAY

    if cur_escape != prev_escape:
        return cur_escape
    if cur_like != prev_like:
        return cur_like > prev_like
    if cur_play != prev_play:
        return cur_play > prev_play
    if cur_off != prev_off:
        return cur_off < prev_off
    return (getattr(cur, "clip_uid", "") or "") < (getattr(prev, "clip_uid", "") or "")

def _bucketed_eligible_clip_objects(
    bundle: Optional[UserClipsBundle],
    vod_info: VODInfo,
    *,
    bucket_sec: int = _USER_CLIPS_ANCHOR_BUCKET_SEC,
) -> list:
    """eligible in-VOD clips deduped — within bucket_sec proximity, keep best.

    Uses proximity-based dedup: clips are sorted by offset, then each clip is
    compared against the last accepted clip. If within bucket_sec, the better
    one (by _bucket_winner_is_better) survives; otherwise both are kept.
    This avoids the floor-division boundary problem where two clips 50s apart
    could land in different buckets (e.g. 48:10→bucket48, 49:00→bucket49).
    """
    if bundle is None:
        return []
    clips = getattr(bundle, "clips", None) or []
    if not clips:
        return []
    target_vno = str(getattr(vod_info, "video_no", "") or "")

    eligible: list[tuple[int, object]] = []
    for c in clips:
        if not is_eligible_in_vod_clip(c, target_vno):
            continue
        off = _clip_offset_int(c)
        if off is None:
            continue
        eligible.append((off, c))
    if not eligible:
        return []

    bs = max(1, int(bucket_sec))
    eligible.sort(key=lambda t: t[0])

    result: list[object] = [eligible[0][1]]
    window_starts: list[int] = [eligible[0][0]]
    for off, c in eligible[1:]:
        if off - window_starts[-1] < bs:
            if _bucket_winner_is_better(c, result[-1]):
                result[-1] = c
        else:
            result.append(c)
            window_starts.append(off)
    return result

def _compact_rendered_evidence_ref(ref: dict) -> dict:
    return {
        "evidence_id": ref.get("evidence_id") or "",
        "rank": ref.get("rank"),
        "score": ref.get("score"),
        "semantic_score": ref.get("semantic_score"),
        "match_reason": ref.get("match_reason") or "",
        "source_type": ref.get("source_type") or "",
        "source_ref": ref.get("source_ref") or "",
        "route_ref": ref.get("route_ref") or "",
        "start_sec": ref.get("start_sec"),
        "end_sec": ref.get("end_sec"),
        "text_hash": ref.get("text_hash") or "",
        "confidence": ref.get("confidence"),
        "public_safe": bool(ref.get("public_safe") is True),
        "raw_content_included": bool(ref.get("raw_content_included") or False),
        "path_ref": ref.get("path_ref") or "",
        "token_count": ref.get("token_count"),
    }

def _build_rendered_summary_evidence_payload(summary_evidence_pack: dict | None) -> dict:
    if not isinstance(summary_evidence_pack, dict) or not summary_evidence_pack:
        return {}
    items = []
    for item in summary_evidence_pack.get("items") or []:
        if not isinstance(item, dict):
            continue
        refs = [
            _compact_rendered_evidence_ref(ref)
            for ref in (item.get("evidence_refs") or [])
            if isinstance(ref, dict)
        ]
        items.append(
            {
                "segment_id": item.get("segment_id") or "",
                "kind": item.get("kind") or "",
                "section": item.get("section") or "",
                "start_sec": item.get("start_sec"),
                "end_sec": item.get("end_sec"),
                "retrieval_status": item.get("retrieval_status") or "",
                "evidence_refs": refs,
            }
        )
    return {
        "schema_version": "rendered_summary_evidence_payload.v1",
        "source_schema_version": summary_evidence_pack.get("schema_version"),
        "render_target": "report_html_script#summaryEvidencePackData",
        "raw_content_included": False,
        "summary": (
            summary_evidence_pack.get("summary")
            if isinstance(summary_evidence_pack.get("summary"), dict)
            else {}
        ),
        "items": items,
    }
