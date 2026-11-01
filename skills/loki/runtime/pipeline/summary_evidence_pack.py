"""Raw-free retrieval packs linking summary segments to timed evidence."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "summary_evidence_pack.v1"

TIMELINE_SHADOW_SCHEMA_VERSION = "timeline_evidence_shadow.v1"

TIMELINE_REF_SCHEMA_VERSION = "timeline_evidence_ref.v1"

RERANK_POLICY = "timestamp_overlap_rerank.v1"

ATTENTION_POLICY = "timestamp_overlap_plus_local_vector_attention.v1"

_TIMELINE_SOURCE_TYPES = ("subtitle", "chat", "viewer_clip")

def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _bounds(row: dict[str, Any]) -> tuple[int | None, int | None]:
    start = row.get("start_sec")
    if start in (None, ""):
        return None, None
    start_sec = _to_int(start)
    raw_end = row.get("end_sec")
    end_sec = _to_int(raw_end, start_sec) if raw_end not in (None, "") else start_sec
    if end_sec < start_sec:
        start_sec, end_sec = end_sec, start_sec
    return start_sec, end_sec

def _score_segment_evidence(
    segment: dict[str, Any],
    evidence: dict[str, Any],
    *,
    window_sec: int,
) -> tuple[float, str]:
    seg_start, seg_end = _bounds(segment)
    ev_start, ev_end = _bounds(evidence)
    if seg_start is None or ev_start is None:
        return 0.0, "missing_time"
    seg_end = seg_start if seg_end is None else seg_end
    ev_end = ev_start if ev_end is None else ev_end
    if seg_start == seg_end and ev_start <= seg_start <= ev_end:
        span = max(1, ev_end - ev_start)
        span_penalty = min(30.0, span / 120.0)
        return max(90.0, 130.0 - span_penalty), "timestamp_contains_point"
    overlap = max(0, min(seg_end, ev_end) - max(seg_start, ev_start))
    if overlap > 0:
        denom = max(1, seg_end - seg_start, ev_end - ev_start)
        return 100.0 + (overlap / denom) * 50.0, "timestamp_overlap"
    seg_center = (seg_start + seg_end) / 2.0
    ev_center = (ev_start + ev_end) / 2.0
    distance = abs(seg_center - ev_center)
    if distance <= window_sec:
        return max(1.0, 80.0 - distance), "nearest_timed_evidence"
    return 0.0, "outside_window"

def _evidence_ref(
    evidence: dict[str, Any],
    *,
    score: float,
    rank: int,
    reason: str,
    semantic_score: float = 0.0,
) -> dict[str, Any]:
    ref = {
        "evidence_id": evidence.get("evidence_id") or "",
        "rank": rank,
        "score": round(float(score), 6),
        "match_reason": reason,
        "source_type": evidence.get("source_type"),
        "source_ref": evidence.get("source_ref"),
        "route_ref": evidence.get("route_ref"),
        "start_sec": evidence.get("start_sec"),
        "end_sec": evidence.get("end_sec"),
        "text_hash": evidence.get("text_hash"),
        "confidence": evidence.get("confidence"),
        "public_safe": bool(evidence.get("public_safe") is True),
        "raw_content_included": bool(evidence.get("raw_content_included") or False),
        "path_ref": evidence.get("path_ref"),
        "token_count": _to_int(evidence.get("token_count")),
    }
    if semantic_score > 0:
        ref["semantic_score"] = round(float(semantic_score), 6)
    return ref

def _segment_query(segment: dict[str, Any]) -> str:
    return " ".join(
        str(segment.get(key) or "").strip()
        for key in ("title", "summary", "reason", "section", "kind")
        if str(segment.get(key) or "").strip()
    )

def _timeline_evidence_ref(
    evidence: dict[str, Any],
    *,
    segment_id: str,
    score: float,
    reason: str,
) -> dict[str, Any]:
    source_type = str(evidence.get("source_type") or "")
    ref = {
        "schema_version": TIMELINE_REF_SCHEMA_VERSION,
        "evidence_id": str(evidence.get("evidence_id") or ""),
        "segment_id": segment_id,
        "source_type": source_type,
        "start_sec": evidence.get("start_sec"),
        "end_sec": evidence.get("end_sec"),
        "display_text": evidence.get("display_text"),
        "source_identity": str(evidence.get("source_identity") or ""),
        "confidence": evidence.get("confidence"),
        "match_reason": reason,
        "match_score": round(float(score), 6),
        "public_safe": bool(evidence.get("public_safe") is True),
        "raw_content_included": False,
        "manager_review_required": bool(
            evidence.get("manager_review_required")
        ),
        "risk_flags": [
            str(flag)
            for flag in (evidence.get("risk_flags") or [])
            if str(flag).strip()
        ],
    }
    if source_type == "viewer_clip":
        ref["clip_uid"] = evidence.get("clip_uid")
        ref["clip_url"] = evidence.get("clip_url")
        ref["clip_title"] = evidence.get("clip_title")
    return ref

def build_timeline_evidence_shadow_contract(
    *,
    segment_ledger: list[dict[str, Any]],
    timeline_source_evidence: dict[str, Any] | None,
    window_sec: int = 90,
    per_source_limit: int = 1,
) -> dict[str, Any]:
    """Attach real subtitle/chat/clip timestamps to timeline segments.

    Segment timestamps select a search window only. Every returned evidence
    timestamp comes from ``timeline_source_evidence`` and is never synthesized
    from the timeline card.
    """

    segments = [
        row
        for row in segment_ledger
        if isinstance(row, dict) and str(row.get("kind") or "") == "timeline"
    ]
    manifest = (
        timeline_source_evidence
        if isinstance(timeline_source_evidence, dict)
        else {}
    )
    evidence_items = [
        row
        for row in (manifest.get("items") or [])
        if isinstance(row, dict)
        and str(row.get("source_type") or "") in _TIMELINE_SOURCE_TYPES
        and row.get("public_safe") is True
        and not bool(row.get("raw_content_included"))
    ]
    safe_window_sec = max(0, _to_int(window_sec, 90))
    safe_per_source_limit = max(1, _to_int(per_source_limit, 1))
    pack_items: list[dict[str, Any]] = []

    for segment in segments:
        segment_id = str(segment.get("segment_id") or "")
        segment_start, _segment_end = _bounds(segment)
        refs: list[dict[str, Any]] = []
        risk_flags: list[str] = []
        if not segment_id:
            risk_flags.append("segment_id_missing")
        if segment_start is None:
            risk_flags.append("segment_time_missing")
        if segment_id and segment_start is not None:
            for source_type in ("subtitle", "chat"):
                scored: list[tuple[float, str, dict[str, Any]]] = []
                for evidence in evidence_items:
                    if str(evidence.get("source_type") or "") != source_type:
                        continue
                    score, reason = _score_segment_evidence(
                        segment,
                        evidence,
                        window_sec=safe_window_sec,
                    )
                    if score > 0:
                        scored.append((score, reason, evidence))
                scored.sort(
                    key=lambda row: (
                        -row[0],
                        abs(
                            float(row[2].get("start_sec") or 0)
                            - float(segment_start)
                        ),
                        str(row[2].get("evidence_id") or ""),
                    )
                )
                refs.extend(
                    _timeline_evidence_ref(
                        evidence,
                        segment_id=segment_id,
                        score=score,
                        reason=reason,
                    )
                    for score, reason, evidence in scored[:safe_per_source_limit]
                )
        refs.sort(
            key=lambda row: (
                _TIMELINE_SOURCE_TYPES.index(str(row.get("source_type") or "")),
                -float(row.get("match_score") or 0),
                str(row.get("evidence_id") or ""),
            )
        )
        if not refs:
            risk_flags.append("no_source_evidence")
        manager_review_required = bool(risk_flags) or any(
            bool(ref.get("manager_review_required")) for ref in refs
        )
        pack_items.append(
            {
                "segment_id": segment_id,
                "kind": "timeline",
                "start_sec": segment.get("start_sec"),
                "end_sec": segment.get("end_sec"),
                "evidence_refs": refs,
                "retrieval_status": (
                    "matched" if refs else "manager_review_required"
                ),
                "manager_review_required": manager_review_required,
                "risk_flags": risk_flags,
            }
        )

    viewer_link_contract: dict[str, Any] = {}
    viewer_link_failed = False
    try:
        from .viewer_clip_timeline_link import build_viewer_clip_timeline_links

        refs_by_segment = {
            str(item.get("segment_id") or ""): list(item.get("evidence_refs") or [])
            for item in pack_items
        }
        viewer_link_contract = build_viewer_clip_timeline_links(
            segment_ledger=segments,
            viewer_clip_evidence=[
                evidence
                for evidence in evidence_items
                if str(evidence.get("source_type") or "") == "viewer_clip"
            ],
            supporting_refs_by_segment=refs_by_segment,
        )
        evidence_by_uid = {
            str(evidence.get("clip_uid") or ""): evidence
            for evidence in evidence_items
            if str(evidence.get("source_type") or "") == "viewer_clip"
        }
        item_by_segment = {
            str(item.get("segment_id") or ""): item for item in pack_items
        }
        for link in viewer_link_contract.get("accepted_links") or []:
            item = item_by_segment.get(str(link.get("segment_id") or ""))
            evidence = evidence_by_uid.get(str(link.get("clip_uid") or ""))
            if item is None or evidence is None:
                continue
            ref = _timeline_evidence_ref(
                evidence,
                segment_id=str(item.get("segment_id") or ""),
                score=float(link.get("confidence") or 0) * 100,
                reason=str(link.get("match_type") or ""),
            )
            ref["viewer_clip_link"] = {
                key: link.get(key)
                for key in (
                    "match_type",
                    "clip_offset_sec",
                    "temporal_distance_sec",
                    "supporting_evidence_ids",
                    "confidence",
                    "public_safe",
                    "risk_flags",
                )
            }
            item["evidence_refs"].append(ref)
        for item in pack_items:
            item["evidence_refs"].sort(
                key=lambda row: (
                    _TIMELINE_SOURCE_TYPES.index(
                        str(row.get("source_type") or "")
                    ),
                    -float(row.get("match_score") or 0),
                    str(row.get("evidence_id") or ""),
                )
            )
            if item["evidence_refs"]:
                item["retrieval_status"] = "matched"
                if item.get("risk_flags") == ["no_source_evidence"]:
                    item["risk_flags"] = []
                    item["manager_review_required"] = False
    except Exception:
        viewer_link_contract = {}
        viewer_link_failed = True

    source_ref_counts = {
        source_type: sum(
            1
            for item in pack_items
            for ref in item.get("evidence_refs") or []
            if ref.get("source_type") == source_type
        )
        for source_type in _TIMELINE_SOURCE_TYPES
    }
    source_summary = (
        manifest.get("summary")
        if isinstance(manifest.get("summary"), dict)
        else {}
    )
    source_diagnostics = (
        manifest.get("diagnostics")
        if isinstance(manifest.get("diagnostics"), dict)
        else {}
    )
    code_counts = dict(
        source_diagnostics.get("code_counts")
        if isinstance(source_diagnostics.get("code_counts"), dict)
        else {}
    )
    if viewer_link_failed:
        code_counts["VIEWER_CLIP_LINK_BUILD_FAILED"] = 1
    evidence_less_count = sum(
        1 for item in pack_items if not item.get("evidence_refs")
    )
    if evidence_less_count:
        code_counts["TIMELINE_SEGMENT_WITHOUT_EVIDENCE"] = evidence_less_count
    accepted_counts = (
        source_summary.get("accepted_counts")
        if isinstance(source_summary.get("accepted_counts"), dict)
        else {}
    )
    return {
        "schema_version": TIMELINE_SHADOW_SCHEMA_VERSION,
        "item_schema_version": TIMELINE_REF_SCHEMA_VERSION,
        "source_manifest_schema_version": manifest.get("schema_version"),
        "shadow_only": True,
        "admin_only": True,
        "public_output_changed": False,
        "raw_content_included": False,
        "summary": {
            "timeline_item_count": len(pack_items),
            "evidence_ref_count": sum(
                len(item.get("evidence_refs") or []) for item in pack_items
            ),
            "source_ref_counts": source_ref_counts,
            "evidence_less_item_count": evidence_less_count,
            "timestamp_less_chat_count": _to_int(
                code_counts.get("CHAT_TIMESTAMP_MISSING")
            ),
            "viewer_clip_candidate_count": _to_int(
                accepted_counts.get("viewer_clip")
            ),
            "missing_card_nomination_count": _to_int(
                (viewer_link_contract.get("summary") or {}).get(
                    "missing_card_nomination_count"
                )
            ),
            "max_missing_card_nominations": _to_int(
                (viewer_link_contract.get("summary") or {}).get(
                    "max_missing_card_nominations"
                )
            ),
            "quarantined_out_of_range_count": _to_int(
                code_counts.get("SOURCE_TIMESTAMP_OUT_OF_RANGE")
            ),
            "quarantined_wrong_vod_count": _to_int(
                code_counts.get("VIEWER_CLIP_WRONG_VOD")
            ),
            "window_sec": safe_window_sec,
            "per_source_limit": safe_per_source_limit,
        },
        "diagnostics": {
            "schema_version": "timeline_evidence_shadow_diagnostics.v1",
            "raw_content_included": False,
            "code_counts": dict(sorted(code_counts.items())),
        },
        "viewer_clip_links": viewer_link_contract,
        "items": pack_items,
    }

def _semantic_scores_by_evidence_id(
    *,
    segment: dict[str, Any],
    summary_local_vector_index: dict[str, Any] | None,
    top_k: int,
) -> dict[str, float]:
    query = _segment_query(segment)
    if not query or not isinstance(summary_local_vector_index, dict):
        return {}
    try:
        from .summary_local_vector_index import query_summary_local_vector_index

        refs = query_summary_local_vector_index(
            summary_local_vector_index,
            query,
            top_k=top_k,
        )
    except Exception:
        return {}
    return {
        str(ref.get("evidence_id") or ""): float(ref.get("score") or 0.0)
        for ref in refs
        if ref.get("evidence_id") and float(ref.get("score") or 0.0) > 0
    }

def build_summary_evidence_pack(
    *,
    segment_ledger: list[dict[str, Any]],
    timed_evidence_manifest: dict[str, Any] | None,
    summary_local_vector_index: dict[str, Any] | None = None,
    top_k: int = 3,
    window_sec: int = 90,
) -> dict[str, Any]:
    """Attach the strongest timed evidence refs to each summary segment.

    This is deterministic retrieval/reranking. A timed evidence row must first
    pass the temporal gate before semantic scores can boost it. Semantic search
    is useful for ordering nearby candidates, but a far-away chunk is not
    public evidence for a timestamped scene.
    """

    segments = [row for row in segment_ledger if isinstance(row, dict)]
    manifest = timed_evidence_manifest if isinstance(timed_evidence_manifest, dict) else {}
    evidence_items = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    safe_top_k = max(1, _to_int(top_k, 3))
    safe_window_sec = max(0, _to_int(window_sec, 90))
    local_index_summary = {}
    if isinstance(summary_local_vector_index, dict):
        local_index_summary = summary_local_vector_index.get("summary") if isinstance(summary_local_vector_index.get("summary"), dict) else {}
    semantic_ready = _to_int(local_index_summary.get("vectorized_count")) > 0
    pack_items: list[dict[str, Any]] = []
    if evidence_items:
        for segment in segments:
            seg_start, _seg_end = _bounds(segment)
            if seg_start is None or str(segment.get("kind") or "") == "summary":
                continue
            semantic_scores = _semantic_scores_by_evidence_id(
                segment=segment,
                summary_local_vector_index=summary_local_vector_index,
                top_k=safe_top_k,
            )
            scored: list[tuple[float, str, dict[str, Any], float]] = []
            for evidence in evidence_items:
                score, reason = _score_segment_evidence(
                    segment,
                    evidence,
                    window_sec=safe_window_sec,
                )
                evidence_id = str(evidence.get("evidence_id") or "")
                semantic_score = semantic_scores.get(evidence_id, 0.0)
                if score > 0:
                    combined_score = score + (semantic_score * 75.0)
                    if semantic_score > 0 and score > 0:
                        reason = f"{reason}+semantic_vector"
                    scored.append((combined_score, reason, evidence, semantic_score))
            scored.sort(
                key=lambda row: (
                    -row[0],
                    _to_int(row[2].get("start_sec")),
                    str(row[2].get("evidence_id") or ""),
                )
            )
            refs = [
                _evidence_ref(
                    evidence,
                    score=score,
                    rank=rank,
                    reason=reason,
                    semantic_score=semantic_score,
                )
                for rank, (score, reason, evidence, semantic_score) in enumerate(scored[:safe_top_k], start=1)
            ]
            pack_items.append(
                {
                    "segment_id": segment.get("segment_id") or "",
                    "kind": segment.get("kind") or "",
                    "section": segment.get("section") or "",
                    "start_sec": segment.get("start_sec"),
                    "end_sec": segment.get("end_sec"),
                    "evidence_refs": refs,
                    "retrieval_status": "matched" if refs else "no_nearby_evidence",
                }
            )
    matched = sum(1 for item in pack_items if item.get("retrieval_status") == "matched")
    return {
        "schema_version": SCHEMA_VERSION,
        "retrieval_mode": "local_deterministic",
        "rerank_policy": RERANK_POLICY,
        "attention_policy": ATTENTION_POLICY,
        "semantic_index_status": "local_free_hash_ready" if semantic_ready else "pending_local_free",
        "semantic_index_ref": "summary_local_vector_index.v1" if semantic_ready else "",
        "raw_content_included": False,
        "summary": {
            "segment_count": len(pack_items),
            "matched_segment_count": matched,
            "unmatched_segment_count": max(0, len(pack_items) - matched),
            "timed_evidence_count": len(evidence_items),
            "semantic_index_item_count": _to_int(local_index_summary.get("item_count")),
            "semantic_index_vectorized_count": _to_int(local_index_summary.get("vectorized_count")),
            "semantic_matched_ref_count": sum(
                1
                for item in pack_items
                for ref in item.get("evidence_refs", [])
                if isinstance(ref, dict) and float(ref.get("semantic_score") or 0.0) > 0
            ),
            "window_sec": safe_window_sec,
            "top_k": safe_top_k,
        },
        "items": pack_items,
    }
