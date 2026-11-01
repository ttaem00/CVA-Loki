"""Deterministic, raw-free viewer-clip links for timeline segments."""

from __future__ import annotations

import re

from typing import Any

SCHEMA_VERSION = "viewer_clip_timeline_links.v1"

LINK_SCHEMA_VERSION = "viewer_clip_timeline_link.v1"

NOMINATION_SCHEMA_VERSION = "viewer_clip_missing_card_nomination.v1"

SAFE_TEMPORAL_WINDOW_SEC = 15

TEMPORAL_CANDIDATE_WINDOW_SEC = 60

MISSING_CARD_SCENE_WINDOW_SEC = 45

MAX_MISSING_CARD_NOMINATIONS = 3

_SAFE_CLIP_UID_RE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")

_SAFE_CLIP_URL_RE = re.compile(
    r"^https://chzzk\.naver\.com/clips/[A-Za-z0-9_-]{3,80}$"
)

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")

_WEAK_TOKENS = frozenset(
    {
        "시청자",
        "클립",
        "인기",
        "장면",
        "타임라인",
        "레전드",
        "ㅋㅋ",
        "ㅋㅋㅋ",
    }
)

def _safe_sec(value: Any) -> int | None:
    try:
        sec = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return sec if sec >= 0 else None

def _safe_nonnegative_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, number)

def _tokens(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(str(value or ""))
        if token.casefold() not in _WEAK_TOKENS
    }

def _segment_text_tokens(segment: dict[str, Any]) -> set[str]:
    return _tokens(
        " ".join(
            str(segment.get(key) or "")
            for key in ("title", "summary", "evidence")
        )
    )

def _has_meaningful_segment_support(segment: dict[str, Any]) -> bool:
    if str(segment.get("evidence") or "").strip():
        return True
    return any(
        isinstance(source, dict)
        and str(source.get("source_type") or "")
        in {
            "chat_or_text_evidence",
            "subtitle",
            "subtitle_cue",
            "srt_quote_cross_validation",
        }
        for source in (segment.get("sources") or [])
    )

def _clip_rows(
    viewer_clip_evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows_by_uid: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, int] = {}

    def reject(code: str) -> None:
        diagnostics[code] = diagnostics.get(code, 0) + 1

    for raw in viewer_clip_evidence:
        if not isinstance(raw, dict):
            reject("CLIP_ROW_INVALID")
            continue
        uid = str(raw.get("clip_uid") or "").strip()
        url = str(raw.get("clip_url") or "").strip()
        offset = _safe_sec(raw.get("start_sec"))
        if not _SAFE_CLIP_UID_RE.fullmatch(uid):
            reject("CLIP_UID_INVALID")
            continue
        if not _SAFE_CLIP_URL_RE.fullmatch(url):
            reject("CLIP_URL_INVALID")
            continue
        if offset is None:
            reject("CLIP_OFFSET_INVALID")
            continue
        if raw.get("public_safe") is not True or bool(raw.get("raw_content_included")):
            reject("CLIP_NOT_PUBLIC_SAFE")
            continue
        if uid in rows_by_uid:
            reject("DUPLICATE_CLIP_UID")
            continue
        engagement = (
            raw.get("clip_engagement")
            if isinstance(raw.get("clip_engagement"), dict)
            else {}
        )
        rows_by_uid[uid] = {
            **raw,
            "clip_uid": uid,
            "clip_url": url,
            "start_sec": offset,
            "clip_title": str(raw.get("clip_title") or "").strip()[:180],
            "clip_engagement": {
                key: int(
                    _safe_nonnegative_float(
                        engagement.get(key)
                    )
                )
                for key in ("like_count", "play_count", "read_count")
            },
            "semantic_support_score": min(
                1.0, _safe_nonnegative_float(raw.get("semantic_support_score"))
            ),
            "semantic_supporting_evidence": [
                {
                    "evidence_id": str(ref.get("evidence_id") or ""),
                    "source_type": str(ref.get("source_type") or ""),
                    "start_sec": _safe_sec(ref.get("start_sec")),
                    "end_sec": _safe_sec(ref.get("end_sec")),
                }
                for ref in (raw.get("semantic_supporting_evidence") or [])
                if isinstance(ref, dict)
                and str(ref.get("evidence_id") or "")
                and str(ref.get("source_type") or "") in {"subtitle", "chat"}
                and _safe_sec(ref.get("start_sec")) is not None
            ],
        }
    return list(rows_by_uid.values()), diagnostics

def _segment_exact_uids(segment: dict[str, Any]) -> set[str]:
    uids: set[str] = set()
    for source in segment.get("sources") or []:
        if not isinstance(source, dict):
            continue
        uid = str(source.get("clip_uid") or "").strip()
        role = str(source.get("role") or "")
        source_type = str(source.get("source_type") or "")
        if (
            _SAFE_CLIP_UID_RE.fullmatch(uid)
            and source_type in {"viewer_clip", "viewer_clip_anchor"}
            and role
            not in {"proximity_signal", "temporal_candidate", "supporting_signal_only"}
        ):
            uids.add(uid)
    return uids

def _segment_anchor_uids(segment: dict[str, Any], clip_by_uid: dict[str, dict[str, Any]]) -> set[str]:
    segment_start = _safe_sec(segment.get("start_sec"))
    if segment_start is None:
        return set()
    injected = bool(segment.get("is_injected")) or (
        "injected_viewer_clip_anchor" in (segment.get("risk_flags") or [])
    )
    segment_names_clip = "클립" in " ".join(
        str(segment.get(key) or "") for key in ("title", "summary", "evidence")
    )
    matched: set[str] = set()
    for source in segment.get("sources") or []:
        if not isinstance(source, dict):
            continue
        if str(source.get("source_type") or "") != "viewer_clip_anchor":
            continue
        uid = str(source.get("clip_uid") or "").strip()
        offset = _safe_sec(source.get("offset_sec"))
        role = str(source.get("role") or "")
        if uid in clip_by_uid and offset == int(clip_by_uid[uid]["start_sec"]):
            if (
                injected
                or role == "deterministic_injection"
                or offset == segment_start
                or (role == "proximity_signal" and segment_names_clip)
            ):
                matched.add(uid)
    if injected and not matched:
        for uid, clip in clip_by_uid.items():
            if int(clip["start_sec"]) == segment_start:
                matched.add(uid)
    return matched

def _link(
    *,
    segment: dict[str, Any],
    clip: dict[str, Any],
    match_type: str,
    temporal_distance_sec: int,
    supporting_evidence_ids: list[str],
    confidence: float,
    accepted: bool,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": LINK_SCHEMA_VERSION,
        "segment_id": str(segment.get("segment_id") or ""),
        "clip_uid": str(clip.get("clip_uid") or ""),
        "clip_url": str(clip.get("clip_url") or ""),
        "clip_title": str(clip.get("clip_title") or "")[:180],
        "match_type": match_type,
        "clip_offset_sec": int(clip["start_sec"]),
        "temporal_distance_sec": int(temporal_distance_sec),
        "supporting_evidence_ids": list(dict.fromkeys(supporting_evidence_ids)),
        "confidence": round(float(confidence), 6),
        "public_safe": True,
        "raw_content_included": False,
        "manager_review_required": not accepted,
        "accepted": bool(accepted),
        "risk_flags": list(risk_flags or []),
    }

def _same_scene(left: dict[str, Any], right: dict[str, Any], *, window_sec: int) -> bool:
    left_start = int(left.get("clip_offset_sec", left.get("start_sec", 0)) or 0)
    right_start = int(right.get("clip_offset_sec", right.get("start_sec", 0)) or 0)
    if abs(left_start - right_start) > window_sec:
        return False
    if abs(left_start - right_start) <= min(15, window_sec):
        return True
    if _tokens(left.get("clip_title")) & _tokens(right.get("clip_title")):
        return True
    left_support = {
        str(ref.get("evidence_id") or "")
        for ref in left.get("semantic_supporting_evidence") or []
        if isinstance(ref, dict)
    }
    right_support = {
        str(ref.get("evidence_id") or "")
        for ref in right.get("semantic_supporting_evidence") or []
        if isinstance(ref, dict)
    }
    return bool(left_support & right_support)

def _engagement_total(clip: dict[str, Any]) -> int:
    engagement = clip.get("clip_engagement") or {}
    return sum(int(engagement.get(key) or 0) for key in ("like_count", "play_count", "read_count"))

def _nomination(
    clip: dict[str, Any],
    *,
    selected: bool,
    reason: str,
    exclusion_reason: str = "",
) -> dict[str, Any]:
    refs = list(clip.get("semantic_supporting_evidence") or [])
    source_types = sorted({str(ref.get("source_type") or "") for ref in refs})
    confidence = float(clip.get("semantic_support_score") or 0.0)
    title = str(clip.get("clip_title") or "").strip()[:180]
    return {
        "schema_version": NOMINATION_SCHEMA_VERSION,
        "segment_id": f"viewer-clip-nomination-{clip['clip_uid']}",
        "clip_uid": str(clip["clip_uid"]),
        "clip_url": str(clip["clip_url"]),
        "same_vod_verified": True,
        "proposed_start_sec": int(clip["start_sec"]),
        "proposed_end_sec": clip.get("end_sec"),
        "proposed_title": title,
        "proposed_description": (
            f"{'·'.join(source_types)} 근거와 같은 시각의 시청자 인기 클립이 확인된 장면"
        ),
        "supporting_evidence": refs,
        "supporting_evidence_ids": [str(ref["evidence_id"]) for ref in refs],
        "confidence": round(confidence, 6),
        "selection_reason": reason if selected else "",
        "support_match_type": "semantic_source_match",
        "exclusion_reason": exclusion_reason if not selected else "",
        "new_card": True,
        "selected": selected,
        "public_safe": True,
        "raw_content_included": False,
        "manager_review_required": not selected,
        "risk_flags": [] if selected else [exclusion_reason],
    }

def build_viewer_clip_timeline_links(
    *,
    segment_ledger: list[dict[str, Any]],
    viewer_clip_evidence: list[dict[str, Any]],
    supporting_refs_by_segment: dict[str, list[dict[str, Any]]] | None = None,
    safe_temporal_window_sec: int = SAFE_TEMPORAL_WINDOW_SEC,
    temporal_candidate_window_sec: int = TEMPORAL_CANDIDATE_WINDOW_SEC,
    max_missing_card_nominations: int = MAX_MISSING_CARD_NOMINATIONS,
    missing_card_scene_window_sec: int = MISSING_CARD_SCENE_WINDOW_SEC,
) -> dict[str, Any]:
    """Link each same-VOD clip to at most one timeline item.

    Exact identity and deterministic anchors always outrank temporal matching.
    Temporal acceptance additionally requires meaningful segment evidence and
    non-trivial title/scene token overlap. Other nearby rows remain manager-only
    candidates and never become viewer-clip chips.
    """

    segments = [
        row
        for row in segment_ledger
        if isinstance(row, dict)
        and str(row.get("kind") or "") == "timeline"
        and str(row.get("segment_id") or "").strip()
        and _safe_sec(row.get("start_sec")) is not None
    ]
    clips, diagnostics = _clip_rows(viewer_clip_evidence)
    clip_by_uid = {str(row["clip_uid"]): row for row in clips}
    support_map = supporting_refs_by_segment or {}
    safe_window = max(0, int(safe_temporal_window_sec))
    candidate_window = max(safe_window, int(temporal_candidate_window_sec))
    proposals: list[tuple[int, int, str, dict[str, Any]]] = []

    for segment in segments:
        segment_id = str(segment["segment_id"])
        segment_start = int(_safe_sec(segment.get("start_sec")) or 0)
        support_refs = [
            ref
            for ref in support_map.get(segment_id, [])
            if isinstance(ref, dict)
            and str(ref.get("source_type") or "") in {"subtitle", "chat"}
        ]
        support_ids = [
            str(ref.get("evidence_id") or "")
            for ref in support_refs
            if str(ref.get("evidence_id") or "")
        ]
        exact_uids = _segment_exact_uids(segment)
        anchor_uids = _segment_anchor_uids(segment, clip_by_uid)
        segment_tokens = _segment_text_tokens(segment)
        has_support = bool(support_ids) or _has_meaningful_segment_support(segment)

        for clip in clips:
            uid = str(clip["clip_uid"])
            distance = abs(segment_start - int(clip["start_sec"]))
            if uid in exact_uids:
                link = _link(
                    segment=segment,
                    clip=clip,
                    match_type="exact_identity",
                    temporal_distance_sec=distance,
                    supporting_evidence_ids=support_ids,
                    confidence=1.0,
                    accepted=True,
                )
                proposals.append((0, distance, segment_id, link))
                continue
            if uid in anchor_uids:
                link = _link(
                    segment=segment,
                    clip=clip,
                    match_type="deterministic_anchor",
                    temporal_distance_sec=distance,
                    supporting_evidence_ids=support_ids,
                    confidence=1.0,
                    accepted=True,
                )
                proposals.append((1, distance, segment_id, link))
                continue
            if distance > candidate_window:
                continue
            overlap = _tokens(clip.get("clip_title")) & segment_tokens
            accepted = has_support and (
                distance == 0
                or (distance <= safe_window and bool(overlap))
            )
            link = _link(
                segment=segment,
                clip=clip,
                match_type="temporal_supported" if accepted else "temporal_candidate",
                temporal_distance_sec=distance,
                supporting_evidence_ids=support_ids,
                confidence=0.9 if accepted else 0.35,
                accepted=accepted,
                risk_flags=[] if accepted else ["low_confidence_temporal_candidate"],
            )
            proposals.append((2 if accepted else 3, distance, segment_id, link))

    proposals.sort(key=lambda row: (row[0], row[1], row[2], row[3]["clip_uid"]))
    accepted_by_uid: dict[str, dict[str, Any]] = {}
    candidate_by_uid: dict[str, dict[str, Any]] = {}
    for _priority, _distance, _segment_id, link in proposals:
        uid = str(link["clip_uid"])
        if link["accepted"]:
            if uid not in accepted_by_uid:
                accepted_by_uid[uid] = link
            continue
        if uid not in accepted_by_uid and uid not in candidate_by_uid:
            candidate_by_uid[uid] = link
    for uid in accepted_by_uid:
        candidate_by_uid.pop(uid, None)

    accepted_links = sorted(
        accepted_by_uid.values(),
        key=lambda row: (
            str(row["segment_id"]),
            int(row["clip_offset_sec"]),
            str(row["clip_uid"]),
        ),
    )
    candidate_links = sorted(
        candidate_by_uid.values(),
        key=lambda row: (
            str(row["segment_id"]),
            int(row["temporal_distance_sec"]),
            str(row["clip_uid"]),
        ),
    )
    match_counts = {
        match_type: sum(
            1 for row in accepted_links if row["match_type"] == match_type
        )
        for match_type in (
            "exact_identity",
            "deterministic_anchor",
            "temporal_supported",
        )
    }
    linked_uids = set(accepted_by_uid)
    safe_nomination_limit = max(0, min(3, int(max_missing_card_nominations)))
    scene_window = max(0, int(missing_card_scene_window_sec))
    eligible: list[dict[str, Any]] = []
    nomination_candidates: list[dict[str, Any]] = []
    for clip in clips:
        uid = str(clip["clip_uid"])
        if uid in linked_uids:
            continue
        title_tokens = _tokens(clip.get("clip_title"))
        supporting = list(clip.get("semantic_supporting_evidence") or [])
        if not title_tokens:
            nomination_candidates.append(
                _nomination(
                    clip,
                    selected=False,
                    reason="",
                    exclusion_reason="clip_title_not_meaningful",
                )
            )
            continue
        if not supporting or float(clip.get("semantic_support_score") or 0.0) < 0.8:
            nomination_candidates.append(
                _nomination(
                    clip,
                    selected=False,
                    reason="",
                    exclusion_reason="semantic_source_support_insufficient",
                )
            )
            continue
        if any(_same_scene(clip, linked, window_sec=scene_window) for linked in accepted_links):
            nomination_candidates.append(
                _nomination(
                    clip,
                    selected=False,
                    reason="",
                    exclusion_reason="existing_timeline_scene_already_linked",
                )
            )
            continue
        eligible.append(clip)

    eligible.sort(
        key=lambda clip: (
            -float(clip.get("semantic_support_score") or 0.0),
            -len(
                {
                    str(ref.get("source_type") or "")
                    for ref in clip.get("semantic_supporting_evidence") or []
                }
            ),
            -min(4, len(_tokens(clip.get("clip_title")))),
            -_engagement_total(clip),
            int(clip["start_sec"]),
            str(clip["clip_uid"]),
        )
    )
    selected_clips: list[dict[str, Any]] = []
    for clip in eligible:
        if any(_same_scene(clip, selected, window_sec=scene_window) for selected in selected_clips):
            nomination_candidates.append(
                _nomination(
                    clip,
                    selected=False,
                    reason="",
                    exclusion_reason="duplicate_scene_nomination",
                )
            )
            continue
        if len(selected_clips) >= safe_nomination_limit:
            nomination_candidates.append(
                _nomination(
                    clip,
                    selected=False,
                    reason="",
                    exclusion_reason="per_vod_nomination_limit",
                )
            )
            continue
        selected_clips.append(clip)

    missing_card_nominations = [
        _nomination(
            clip,
            selected=True,
            reason="same_vod_semantic_source_support",
        )
        for clip in selected_clips
    ]
    nomination_candidates.extend(missing_card_nominations)
    nomination_candidates.sort(
        key=lambda row: (
            not bool(row["selected"]),
            int(row["proposed_start_sec"]),
            str(row["clip_uid"]),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "shadow_only": True,
        "admin_only": True,
        "public_output_changed": False,
        "public_safe": True,
        "raw_content_included": False,
        "summary": {
            "viewer_clip_count": len(clips),
            "accepted_link_count": len(accepted_links),
            "candidate_link_count": len(candidate_links),
            "unmatched_clip_count": len(clips) - len(linked_uids),
            "timeline_items_with_clip_count": len(
                {str(row["segment_id"]) for row in accepted_links}
            ),
            "missing_card_nomination_count": len(missing_card_nominations),
            "missing_card_candidate_count": len(nomination_candidates),
            "max_missing_card_nominations": safe_nomination_limit,
            "missing_card_scene_window_sec": scene_window,
            "match_counts": match_counts,
            "safe_temporal_window_sec": safe_window,
            "temporal_candidate_window_sec": candidate_window,
        },
        "diagnostics": {
            "raw_content_included": False,
            "code_counts": dict(sorted(diagnostics.items())),
        },
        "accepted_links": accepted_links,
        "candidate_links": candidate_links,
        "missing_card_nominations": missing_card_nominations,
        "nomination_candidates": nomination_candidates,
    }
