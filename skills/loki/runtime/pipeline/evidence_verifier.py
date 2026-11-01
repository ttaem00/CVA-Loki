"""Small structured verifier for summary evidence payloads.

This module intentionally does not call an LLM and does not introduce provider
tool-calling abstractions.  It validates already-produced segment/evidence,
chapter-readiness, and manager-edit-diff shaped data.
"""

from __future__ import annotations

from typing import Any

from .timeline_alignment import hms_to_sec

SCHEMA_VERSION = "evidence_verifier.v1"

INPUT_SCHEMA_VERSION = "evidence_verifier_input.v1"

CHAPTER_SCHEMA_VERSION = "chapter_readiness.v1"

MISSING_SEGMENT_ID = "MISSING_SEGMENT_ID"

MISSING_SOURCES = "MISSING_SOURCES"

RISKY_SOURCE_ONLY_CLAIM = "RISKY_SOURCE_ONLY_CLAIM"

CHAPTER_READINESS_FAIL = "CHAPTER_READINESS_FAIL"

UNMATCHED_MANAGER_EDIT_DIFF = "UNMATCHED_MANAGER_EDIT_DIFF"

SUMMARY_EVIDENCE_PACK_MISSING = "SUMMARY_EVIDENCE_PACK_MISSING"

SEGMENT_EVIDENCE_PACK_MISSING = "SEGMENT_EVIDENCE_PACK_MISSING"

EVIDENCE_TIMESTAMP_OUT_OF_BOUNDS = "EVIDENCE_TIMESTAMP_OUT_OF_BOUNDS"

EVIDENCE_RAW_CONTENT_INCLUDED = "EVIDENCE_RAW_CONTENT_INCLUDED"

EVIDENCE_PRIVATE_PATH_REF = "EVIDENCE_PRIVATE_PATH_REF"

TIMELINE_EVIDENCE_SHADOW_INVALID = "TIMELINE_EVIDENCE_SHADOW_INVALID"

TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING = "TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING"

TIMELINE_EVIDENCE_SOURCE_TYPE_INVALID = "TIMELINE_EVIDENCE_SOURCE_TYPE_INVALID"

TIMELINE_EVIDENCE_REVIEW_STATE_MISSING = "TIMELINE_EVIDENCE_REVIEW_STATE_MISSING"

TIMELINE_EVIDENCE_CLIP_IDENTITY_INVALID = "TIMELINE_EVIDENCE_CLIP_IDENTITY_INVALID"

_TIMELINE_SOURCE_TYPES = {"subtitle", "chat", "viewer_clip"}

_RISKY_SOURCE_MODALITIES = {
    "audio",
    "audio_peak",
    "audio_reaction",
    "chat",
    "chat_peak",
    "community",
    "community_prior",
    "laughter_burst",
}

def build_verifier_input_from_summary_sections(
    sections: dict[str, Any],
    *,
    manager_edit_diffs: list[dict[str, Any]] | None = None,
    chapter_readiness: dict[str, Any] | None = None,
    summary_evidence_pack: dict[str, Any] | None = None,
    timeline_evidence_shadow: dict[str, Any] | None = None,
    duration_sec: int | None = None,
) -> dict[str, Any]:
    """Build the verifier input shape from `_parse_summary_sections()` output.

    Existing parser output does not yet carry segment IDs or structured
    `sources[]`; the verifier should report that as structured failure instead
    of silently treating visible Markdown evidence as schema-complete evidence.
    """
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(sections.get("timeline") or []):
        segments.append(_segment_from_item(item, kind="timeline", index=index))
    for index, item in enumerate(sections.get("highlights") or []):
        segments.append(_segment_from_item(item, kind="highlight", index=index))
    for index, item in enumerate(sections.get("chzzk_comment_timeline") or []):
        segments.append(_segment_from_item(item, kind="chapter", index=index))

    chapters = [
        {
            "tc": item.get("tc", ""),
            "tc_end": item.get("tc_end", ""),
            "title": item.get("title", ""),
        }
        for item in sections.get("chzzk_comment_timeline") or []
        if isinstance(item, dict)
    ]

    payload: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "segments": segments,
        "manager_edit_diffs": manager_edit_diffs or [],
    }
    if chapter_readiness is not None:
        payload["chapter_readiness"] = chapter_readiness
    elif chapters:
        payload["chapter_readiness"] = validate_chapter_readiness(chapters)
    if summary_evidence_pack is not None:
        payload["summary_evidence_pack"] = summary_evidence_pack
    if timeline_evidence_shadow is not None:
        payload["timeline_evidence_shadow"] = timeline_evidence_shadow
    if duration_sec is not None:
        payload["duration_sec"] = duration_sec
    return payload

def verify_summary_sections(
    sections: dict[str, Any],
    *,
    manager_edit_diffs: list[dict[str, Any]] | None = None,
    chapter_readiness: dict[str, Any] | None = None,
    summary_evidence_pack: dict[str, Any] | None = None,
    timeline_evidence_shadow: dict[str, Any] | None = None,
    duration_sec: int | None = None,
) -> dict[str, Any]:
    """Verify parser output and return the structured verifier result."""
    return verify_evidence_payload(
        build_verifier_input_from_summary_sections(
            sections,
            manager_edit_diffs=manager_edit_diffs,
            chapter_readiness=chapter_readiness,
            summary_evidence_pack=summary_evidence_pack,
            timeline_evidence_shadow=timeline_evidence_shadow,
            duration_sec=duration_sec,
        )
    )

def verify_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a structured evidence payload.

    Required failure codes:
    - `MISSING_SEGMENT_ID`
    - `MISSING_SOURCES`
    - `RISKY_SOURCE_ONLY_CLAIM`
    - `CHAPTER_READINESS_FAIL`
    - `UNMATCHED_MANAGER_EDIT_DIFF`
    """
    findings: list[dict[str, Any]] = []
    segments = payload.get("segments") or []
    if not isinstance(segments, list):
        segments = []

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        ref = _segment_ref(segment, index)
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            findings.append(_finding(
                MISSING_SEGMENT_ID,
                "error",
                "segment has no stable segment_id",
                segment_ref=ref,
                source_fields=["segments[].segment_id"],
            ))

        sources = segment.get("sources")
        normalized_sources = _normalize_sources(sources)
        if not normalized_sources:
            findings.append(_finding(
                MISSING_SOURCES,
                "error",
                "segment has no structured sources[] evidence",
                segment_ref=ref,
                segment_id=segment_id if isinstance(segment_id, str) else "",
                source_fields=["segments[].sources"],
            ))
        elif _has_fact_claim(segment) and _is_risky_source_only(normalized_sources):
            findings.append(_finding(
                RISKY_SOURCE_ONLY_CLAIM,
                "error",
                "segment claim is backed only by community/chat/audio prior signals",
                segment_ref=ref,
                segment_id=segment_id if isinstance(segment_id, str) else "",
                source_fields=["segments[].sources", "segments[].claim_text"],
            ))

    chapter_readiness = payload.get("chapter_readiness")
    if isinstance(chapter_readiness, dict) and _chapter_readiness_failed(chapter_readiness):
        chapter_severity = _chapter_readiness_finding_severity(chapter_readiness)
        findings.append(_finding(
            CHAPTER_READINESS_FAIL,
            chapter_severity,
            (
                "chapter readiness validator reported export warning"
                if chapter_severity == "warning"
                else "chapter readiness validator reported fail"
            ),
            source_fields=["chapter_readiness"],
            detail={
                "status": chapter_readiness.get("status"),
                "failure_reasons": chapter_readiness.get("failure_reasons") or [],
                "failures": chapter_readiness.get("failures") or [],
            },
        ))

    manager_edit_diffs = payload.get("manager_edit_diffs") or []
    if isinstance(manager_edit_diffs, list):
        for index, diff in enumerate(manager_edit_diffs):
            if not isinstance(diff, dict):
                continue
            matched = bool(diff.get("matched_segment_id") or diff.get("segment_id"))
            if not matched:
                findings.append(_finding(
                    UNMATCHED_MANAGER_EDIT_DIFF,
                    "error",
                    "manager edit diff is not linked to a segment_id",
                    source_fields=["manager_edit_diffs[].segment_id"],
                    detail={
                        "diff_index": index,
                        "action_type": diff.get("action_type", ""),
                        "timecode": diff.get("timecode", ""),
                    },
                ))

    _verify_summary_evidence_pack(
        payload.get("summary_evidence_pack"),
        findings,
        duration_sec=_safe_int_or_none(payload.get("duration_sec")),
    )
    _verify_timeline_evidence_shadow(
        payload.get("timeline_evidence_shadow"),
        findings,
        duration_sec=_safe_int_or_none(payload.get("duration_sec")),
    )

    error_count = sum(1 for item in findings if item.get("severity") == "error")
    warning_count = sum(1 for item in findings if item.get("severity") == "warning")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "fail" if error_count else "pass",
        "summary": {
            "checked_segments": len(segments),
            "failure_count": error_count,
            "warning_count": warning_count,
        },
        "findings": findings,
    }

def _verify_summary_evidence_pack(
    summary_evidence_pack: Any,
    findings: list[dict[str, Any]],
    *,
    duration_sec: int | None,
) -> None:
    if summary_evidence_pack is None:
        return
    if not isinstance(summary_evidence_pack, dict):
        findings.append(_finding(
            SUMMARY_EVIDENCE_PACK_MISSING,
            "error",
            "summary evidence pack is missing or not an object",
            source_fields=["summary_evidence_pack"],
        ))
        return
    items = summary_evidence_pack.get("items")
    if not isinstance(items, list):
        findings.append(_finding(
            SUMMARY_EVIDENCE_PACK_MISSING,
            "error",
            "summary evidence pack has no items[]",
            source_fields=["summary_evidence_pack.items"],
        ))
        return

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            findings.append(_finding(
                SEGMENT_EVIDENCE_PACK_MISSING,
                "error",
                "summary segment has no retrieved timed evidence refs",
                segment_id=segment_id,
                segment_ref={
                    "kind": item.get("kind", ""),
                    "index": index,
                    "timecode": "",
                    "title": "",
                },
                source_fields=["summary_evidence_pack.items[].evidence_refs"],
            ))
            continue
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            ref_fields = {
                "segment_id": segment_id,
                "ref_index": ref_index,
                "evidence_id": ref.get("evidence_id") or "",
            }
            if bool(ref.get("raw_content_included")):
                findings.append(_finding(
                    EVIDENCE_RAW_CONTENT_INCLUDED,
                    "error",
                    "retrieved evidence ref includes raw content",
                    segment_id=segment_id,
                    source_fields=["summary_evidence_pack.items[].evidence_refs[].raw_content_included"],
                    detail=ref_fields,
                ))
            for path_key in ("path_ref", "source_ref"):
                path_ref = str(ref.get(path_key) or "")
                if _looks_like_private_path(path_ref):
                    findings.append(_finding(
                        EVIDENCE_PRIVATE_PATH_REF,
                        "error",
                        "retrieved evidence ref serializes a private absolute path",
                        segment_id=segment_id,
                        source_fields=[f"summary_evidence_pack.items[].evidence_refs[].{path_key}"],
                        detail={**ref_fields, "path_key": path_key},
                    ))
            if duration_sec is not None:
                for side in ("start_sec", "end_sec"):
                    sec = _safe_int_or_none(ref.get(side))
                    if sec is None:
                        continue
                    if sec < 0 or sec > duration_sec:
                        findings.append(_finding(
                            EVIDENCE_TIMESTAMP_OUT_OF_BOUNDS,
                            "error",
                            "retrieved evidence timestamp is outside VOD duration",
                            segment_id=segment_id,
                            source_fields=[f"summary_evidence_pack.items[].evidence_refs[].{side}"],
                            detail={**ref_fields, "side": side, "value_sec": sec, "duration_sec": duration_sec},
                        ))

def _verify_timeline_evidence_shadow(
    contract: Any,
    findings: list[dict[str, Any]],
    *,
    duration_sec: int | None,
) -> None:
    if contract is None:
        return
    if not isinstance(contract, dict) or not isinstance(contract.get("items"), list):
        findings.append(_finding(
            TIMELINE_EVIDENCE_SHADOW_INVALID,
            "error",
            "timeline evidence shadow contract is missing items[]",
            source_fields=["timeline_evidence_shadow.items"],
        ))
        return
    if bool(contract.get("raw_content_included")):
        findings.append(_finding(
            EVIDENCE_RAW_CONTENT_INCLUDED,
            "error",
            "timeline evidence shadow contract includes raw content",
            source_fields=["timeline_evidence_shadow.raw_content_included"],
        ))

    for item_index, item in enumerate(contract.get("items") or []):
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list):
            findings.append(_finding(
                TIMELINE_EVIDENCE_SHADOW_INVALID,
                "error",
                "timeline evidence shadow item has no evidence_refs[]",
                segment_id=segment_id,
                source_fields=["timeline_evidence_shadow.items[].evidence_refs"],
            ))
            continue
        if not refs and item.get("manager_review_required") is not True:
            findings.append(_finding(
                TIMELINE_EVIDENCE_REVIEW_STATE_MISSING,
                "error",
                "timeline item without evidence must require manager review",
                segment_id=segment_id,
                source_fields=[
                    "timeline_evidence_shadow.items[].manager_review_required"
                ],
                detail={"item_index": item_index},
            ))
        if not isinstance(item.get("risk_flags"), list):
            findings.append(_finding(
                TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING,
                "error",
                "timeline evidence shadow item has no risk_flags list",
                segment_id=segment_id,
                source_fields=["timeline_evidence_shadow.items[].risk_flags"],
            ))

        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            required_keys = (
                "evidence_id",
                "segment_id",
                "source_type",
                "start_sec",
                "end_sec",
                "display_text",
                "source_identity",
                "confidence",
                "match_reason",
                "public_safe",
                "raw_content_included",
                "manager_review_required",
                "risk_flags",
            )
            missing = [key for key in required_keys if key not in ref]
            if missing:
                findings.append(_finding(
                    TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING,
                    "error",
                    "timeline evidence ref is missing required fields",
                    segment_id=segment_id,
                    source_fields=[
                        f"timeline_evidence_shadow.items[].evidence_refs[].{key}"
                        for key in missing
                    ],
                    detail={"ref_index": ref_index, "missing": missing},
                ))
                continue
            source_type = str(ref.get("source_type") or "")
            if source_type not in _TIMELINE_SOURCE_TYPES:
                findings.append(_finding(
                    TIMELINE_EVIDENCE_SOURCE_TYPE_INVALID,
                    "error",
                    "timeline evidence ref source_type is not allowed",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].source_type"
                    ],
                    detail={"ref_index": ref_index, "source_type": source_type},
                ))
            if not str(ref.get("evidence_id") or "").strip() or not str(
                ref.get("source_identity") or ""
            ).strip():
                findings.append(_finding(
                    TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING,
                    "error",
                    "timeline evidence ref has no stable public-safe identity",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].evidence_id",
                        "timeline_evidence_shadow.items[].evidence_refs[].source_identity",
                    ],
                    detail={"ref_index": ref_index},
                ))
            if str(ref.get("segment_id") or "") != segment_id:
                findings.append(_finding(
                    TIMELINE_EVIDENCE_SHADOW_INVALID,
                    "error",
                    "timeline evidence ref points to a different segment_id",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].segment_id"
                    ],
                    detail={"ref_index": ref_index},
                ))
            if ref.get("public_safe") is not True or bool(
                ref.get("raw_content_included")
            ):
                findings.append(_finding(
                    EVIDENCE_RAW_CONTENT_INCLUDED,
                    "error",
                    "timeline evidence ref is not public-safe and raw-free",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].public_safe",
                        "timeline_evidence_shadow.items[].evidence_refs[].raw_content_included",
                    ],
                    detail={"ref_index": ref_index},
                ))
            if not isinstance(ref.get("manager_review_required"), bool) or not isinstance(
                ref.get("risk_flags"), list
            ):
                findings.append(_finding(
                    TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING,
                    "error",
                    "timeline evidence ref review state is not structured",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].manager_review_required",
                        "timeline_evidence_shadow.items[].evidence_refs[].risk_flags",
                    ],
                    detail={"ref_index": ref_index},
                ))
            start_sec = _safe_int_or_none(ref.get("start_sec"))
            end_sec = _safe_int_or_none(ref.get("end_sec"))
            if start_sec is None:
                findings.append(_finding(
                    TIMELINE_EVIDENCE_REQUIRED_FIELD_MISSING,
                    "error",
                    "timeline evidence ref has no actual source timestamp",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].start_sec"
                    ],
                    detail={"ref_index": ref_index},
                ))
            for side, sec in (("start_sec", start_sec), ("end_sec", end_sec)):
                if sec is not None and (
                    sec < 0 or (duration_sec is not None and sec > duration_sec)
                ):
                    findings.append(_finding(
                        EVIDENCE_TIMESTAMP_OUT_OF_BOUNDS,
                        "error",
                        "timeline evidence ref timestamp is outside VOD duration",
                        segment_id=segment_id,
                        source_fields=[
                            f"timeline_evidence_shadow.items[].evidence_refs[].{side}"
                        ],
                        detail={
                            "ref_index": ref_index,
                            "side": side,
                            "value_sec": sec,
                            "duration_sec": duration_sec,
                        },
                    ))
            clip_uid = str(ref.get("clip_uid") or "")
            clip_url = str(ref.get("clip_url") or "")
            if source_type == "viewer_clip":
                expected_url = (
                    f"https://chzzk.naver.com/clips/{clip_uid}"
                    if clip_uid
                    else ""
                )
                if not clip_uid or clip_url != expected_url:
                    findings.append(_finding(
                        TIMELINE_EVIDENCE_CLIP_IDENTITY_INVALID,
                        "error",
                        "viewer clip ref has mismatched clip_uid/clip_url",
                        segment_id=segment_id,
                        source_fields=[
                            "timeline_evidence_shadow.items[].evidence_refs[].clip_uid",
                            "timeline_evidence_shadow.items[].evidence_refs[].clip_url",
                        ],
                        detail={"ref_index": ref_index},
                    ))
            elif "clip_uid" in ref or "clip_url" in ref:
                findings.append(_finding(
                    TIMELINE_EVIDENCE_CLIP_IDENTITY_INVALID,
                    "error",
                    "non-clip evidence ref contains clip identity fields",
                    segment_id=segment_id,
                    source_fields=[
                        "timeline_evidence_shadow.items[].evidence_refs[].clip_uid",
                        "timeline_evidence_shadow.items[].evidence_refs[].clip_url",
                    ],
                    detail={"ref_index": ref_index},
                ))
            for key in ("display_text", "source_identity", "clip_url"):
                value = str(ref.get(key) or "")
                if _looks_like_private_path(value):
                    findings.append(_finding(
                        EVIDENCE_PRIVATE_PATH_REF,
                        "error",
                        "timeline evidence ref serializes a private absolute path",
                        segment_id=segment_id,
                        source_fields=[
                            f"timeline_evidence_shadow.items[].evidence_refs[].{key}"
                        ],
                        detail={"ref_index": ref_index, "path_key": key},
                    ))

def _safe_int_or_none(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None

def _looks_like_private_path(value: str) -> bool:
    if not value:
        return False
    text = value.strip()
    if "\\" in text:
        return True
    if len(text) >= 3 and text[1:3] == ":\\":
        return True
    if len(text) >= 2 and text[1] == ":":
        return True
    if text.startswith("/") or text.startswith("//"):
        return True
    return False

def validate_chapter_readiness(
    chapters: list[dict[str, Any]],
    *,
    min_chapters: int = 3,
    min_duration_sec: int = 10,
) -> dict[str, Any]:
    """Validate chapter-export readiness with deterministic platform rules."""
    normalized: list[dict[str, Any]] = []
    failure_reasons: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        title = str(chapter.get("title") or "").strip()
        start = _chapter_time_to_sec(chapter, "start")
        end = _chapter_time_to_sec(chapter, "end")
        normalized.append({"index": index, "start_sec": start, "end_sec": end, "title": title})
        if not title:
            failure_reasons.append({
                "code": "CHAPTER_TITLE_EMPTY",
                "chapter_index": index,
                "message": "chapter title is empty",
            })
        title_key = title.casefold()
        if title_key and title_key in seen_titles:
            failure_reasons.append({
                "code": "CHAPTER_TITLE_DUPLICATE",
                "chapter_index": index,
                "message": "chapter title is duplicated",
            })
        if title_key:
            seen_titles.add(title_key)

    normalized.sort(key=lambda item: (item["start_sec"] is None, item["start_sec"] or 0, item["index"]))

    if len(normalized) < min_chapters:
        failure_reasons.append({
            "code": "CHAPTER_COUNT_TOO_LOW",
            "message": f"chapter count is below {min_chapters}",
        })
    if normalized and normalized[0]["start_sec"] != 0:
        failure_reasons.append({
            "code": "CHAPTER_START_NOT_ZERO",
            "chapter_index": normalized[0]["index"],
            "message": "first chapter does not start at 00:00",
        })

    previous_start: int | None = None
    for position, chapter in enumerate(normalized):
        start = chapter["start_sec"]
        if start is None:
            failure_reasons.append({
                "code": "CHAPTER_START_INVALID",
                "chapter_index": chapter["index"],
                "message": "chapter start time is invalid",
            })
            continue
        if previous_start is not None and start <= previous_start:
            failure_reasons.append({
                "code": "CHAPTER_ORDER_INVALID",
                "chapter_index": chapter["index"],
                "message": "chapter start time is not strictly increasing",
            })
        previous_start = start

        next_start = None
        if position + 1 < len(normalized):
            next_start = normalized[position + 1]["start_sec"]
        end = chapter["end_sec"] if chapter["end_sec"] is not None else next_start
        if end is not None and end - start < min_duration_sec:
            failure_reasons.append({
                "code": "CHAPTER_DURATION_TOO_SHORT",
                "chapter_index": chapter["index"],
                "message": f"chapter duration is below {min_duration_sec} seconds",
            })

    return {
        "schema_version": CHAPTER_SCHEMA_VERSION,
        "status": "fail" if failure_reasons else "pass",
        "pass": not failure_reasons,
        "chapters_checked": len(normalized),
        "failure_reasons": failure_reasons,
    }

def _segment_from_item(item: dict[str, Any], *, kind: str, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"kind": kind, "index": index}
    timecode = item.get("tc") or item.get("tc_range") or item.get("timecode") or ""
    text_parts = [
        str(item.get("title") or "").strip(),
        str(item.get("summary") or "").strip(),
        str(item.get("reason") or "").strip(),
    ]
    segment = {
        "kind": kind,
        "index": index,
        "timecode": str(timecode),
        "title": item.get("title", ""),
        "claim_text": " ".join(part for part in text_parts if part),
        "evidence_text": item.get("evidence", ""),
    }
    for key in ("segment_id", "sources", "risk_flags"):
        if key in item:
            segment[key] = item[key]
    return segment

def _finding(
    code: str,
    severity: str,
    message: str,
    *,
    segment_ref: dict[str, Any] | None = None,
    segment_id: str = "",
    source_fields: list[str] | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if segment_id:
        finding["segment_id"] = segment_id
    if segment_ref is not None:
        finding["segment_ref"] = segment_ref
    if source_fields:
        finding["source_fields"] = source_fields
    if detail:
        finding["detail"] = detail
    return finding

def _segment_ref(segment: dict[str, Any], fallback_index: int) -> dict[str, Any]:
    return {
        "kind": segment.get("kind", ""),
        "index": segment.get("index", fallback_index),
        "timecode": segment.get("timecode", ""),
        "title": segment.get("title", ""),
    }

def _normalize_sources(sources: Any) -> list[dict[str, Any]]:
    if not isinstance(sources, list):
        return []
    normalized: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, str):
            modality = source.strip().casefold()
            if modality:
                normalized.append({"modality": modality, "prior_only": False})
        elif isinstance(source, dict):
            modality = str(
                source.get("modality")
                or source.get("source_type")
                or source.get("type")
                or source.get("source")
                or ""
            ).strip().casefold()
            if not modality:
                continue
            fact_status = str(source.get("fact_status") or "").casefold()
            prior_only = bool(source.get("is_prior_only")) or fact_status in {"prior", "topic_prior"}
            normalized.append({"modality": modality, "prior_only": prior_only})
    return normalized

def _has_fact_claim(segment: dict[str, Any]) -> bool:
    return bool(str(segment.get("claim_text") or segment.get("title") or "").strip())

def _is_risky_source_only(sources: list[dict[str, Any]]) -> bool:
    if not sources:
        return False
    modalities = {str(source.get("modality") or "").casefold() for source in sources}
    modalities.discard("")
    if not modalities:
        return False
    all_prior = all(bool(source.get("prior_only")) for source in sources)
    return modalities.issubset(_RISKY_SOURCE_MODALITIES) or all_prior

def _chapter_readiness_failed(chapter_readiness: dict[str, Any]) -> bool:
    if "pass" in chapter_readiness:
        return not bool(chapter_readiness.get("pass"))
    return str(chapter_readiness.get("status") or "").casefold() in {"fail", "failed", "error"}

def _chapter_readiness_finding_severity(chapter_readiness: dict[str, Any]) -> str:
    """Missing 00:00 is an export-readiness warning, not evidence failure."""
    reasons = chapter_readiness.get("failure_reasons")
    if not isinstance(reasons, list):
        reasons = chapter_readiness.get("failures")
    if not isinstance(reasons, list) or not reasons:
        return "error"
    codes = {
        str(reason.get("code") or "").strip()
        for reason in reasons
        if isinstance(reason, dict)
    }
    codes.discard("")
    if codes and codes.issubset({"CHAPTER_START_NOT_ZERO", "first_timestamp_not_zero"}):
        return "warning"
    return "error"

def _chapter_time_to_sec(chapter: dict[str, Any], side: str) -> int | None:
    keys = ("start_sec", "start") if side == "start" else ("end_sec", "end", "tc_end")
    if side == "start":
        keys = ("start_sec", "start", "tc", "timecode")
    for key in keys:
        if key not in chapter:
            continue
        value = chapter.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if "~" in text and side == "start":
            text = text.split("~", 1)[0].strip()
        elif "~" in text and side == "end":
            text = text.split("~", 1)[1].strip()
        text = text.strip("[] ")
        try:
            return int(hms_to_sec(text))
        except (TypeError, ValueError):
            continue
    return None
