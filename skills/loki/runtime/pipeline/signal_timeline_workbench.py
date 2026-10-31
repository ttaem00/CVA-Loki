"""Admin-only Signal Timeline Workbench helpers.

The workbench is a local inspection payload. It reads existing report metadata
and optional work sidecars, then builds compact candidate lanes for the report
admin UI. It does not mutate report HTML, prompt inputs, or public metadata.
"""

from __future__ import annotations

import hashlib

import json

import math

import re

from bisect import bisect_left, bisect_right

from pathlib import Path

from typing import Any

from .asr_segments import AsrSegmentsError, parse_srt_segments

from .chat_emoticons import (
    CHAT_MODE_REACTION_HEAVY,
    EMOTICON_TOKEN_RE,
    aggregate_emoticon_windows,
    classify_emoticon_windows,
)

from .comment_temporal_hints import (
    CONTEXT_RANGE_SCHEMA_VERSION as COMMENT_CONTEXT_RANGE_SCHEMA_VERSION,
    EXACT_MOMENT_SEED_SCHEMA_VERSION as COMMENT_MOMENT_SEED_SCHEMA_VERSION,
    SCHEMA_VERSION as COMMENT_TEMPORAL_HINTS_SCHEMA_VERSION,
    SIDECAR_FILENAME as COMMENT_TEMPORAL_HINTS_FILENAME,
    STORY_WINDOW_SCHEMA_VERSION as COMMENT_STORY_WINDOW_SCHEMA_VERSION,
)

from .comment_adaptive_presentation import build_comment_adaptive_presentation

from .context_structure.adaptive_context_ranges import build_adaptive_context_ranges

from .context_structure.contracts import (
    ContextStructureContractError,
    ContextStructureV1,
)

from .live_context_signal import SCHEMA_VERSION as LIVE_CONTEXT_SCHEMA_VERSION

from .live_context_signal import load_live_context_signals, normalize_live_context_signals

from .visual_scene_signal import (
    ALLOWED_TEXT_CLASSES as VISUAL_SCENE_ALLOWED_TEXT_CLASSES,
    SCHEMA_VERSION as VISUAL_SCENE_SCHEMA_VERSION,
)

from .visual_scene_signal import load_visual_scene_signals, normalize_visual_scene_signals

from .scene_signal_contract import canonical_lane_key, lane_empty_reasons, lane_labels, overview_lane_keys, validate_lane_payload

SCHEMA_VERSION = "signal_timeline_workbench.v1"

DRAFT_SCHEMA_VERSION = "signal_timeline_selected_ranges.v1"

_MAX_LIVE_CONTEXT_SAMPLE_EVENTS = 24

_MAX_VISUAL_SCENE_EVENTS = 36

_MAX_MANAGER_COMMENT_HINT_EVENTS = 6000

_MAX_MANAGER_COMMENT_POINT_EVENTS = 12

_MAX_MANAGER_COMMENT_STORY_EVENTS = 12

_MAX_MANAGER_CONTEXT_SHADOW_EVENTS = 2400

_MANAGER_COMMENT_HINT_LANE_KEYS = (
    "comment_context_range",
    "comment_moment_seed",
    "comment_story_window",
)

_MANAGER_CONTEXT_SHADOW_LANE_KEYS = (
    "context_activity_shadow",
    "context_topic_shadow",
    "context_unknown_gap_shadow",
)

_CONTEXT_TRANSITION_SHADOW_FILENAME = "context_transition_shadow.json"

_CONTEXT_TRANSITION_SHADOW_SCHEMA_VERSION = "context_transition_shadow.v1"

_CHAT_DENSITY_SIGNAL_FILENAME = "chat_density_signal.json"

_CHAT_DENSITY_SIGNAL_SCHEMA_VERSION = "chat_density_signal.v1"

_ADAPTIVE_AUDIO_WAVEFORM_ACTIVE_MIN = 0.65

_LANE_LABELS = lane_labels()

_OVERVIEW_LANE_KEYS = overview_lane_keys()

_REACTION_SIGNAL_ALIASES = {
    "reaction_surprise",
    "reaction_question",
    "question",
    "qna",
    "question_cluster",
}

_ROLE_LABELS = {
    "candidate": "검토 후보",
    "supporting": "보조 신호",
    "confirmed": "요약에 반영됨",
}

_DRAFT_ACTION_LABELS = {
    "pin": "중요 장면으로 표시",
    "ignore": "이번 편집에서 제외",
    "add": "요약에 추가 검토",
    "retime": "시각 조정 필요",
    "note": "메모만 남김",
}

def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str) and ":" in value:
            parts = [float(part.strip()) for part in value.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return float(value)
    except (TypeError, ValueError):
        return default

def _clip_text(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."

def _audio_peak_editor_label(labels: list[Any]) -> str:
    normalized = {str(label).lower() for label in labels if label}
    if "chat_audio_overlap" in normalized:
        return "소리와 채팅이 함께 튄 구간"
    if "voice_reaction_candidate" in normalized:
        return "목소리 리액션이 커진 구간"
    if "clipping_or_limiter_diagnostic" in normalized:
        return "음량이 갑자기 거칠어진 구간"
    if "audio_energy_peak" in normalized:
        return "소리가 크게 튄 구간"
    return "소리 변화가 큰 구간"

def _sec_to_tc(sec: Any) -> str:
    n = _safe_int(sec, 0) or 0
    h = n // 3600
    m = (n % 3600) // 60
    s = n % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _event_id(lane: str, start_sec: Any, end_sec: Any, label: str, segment_id: str = "") -> str:
    payload = "|".join(
        [
            SCHEMA_VERSION,
            lane,
            "" if start_sec is None else str(start_sec),
            "" if end_sec is None else str(end_sec),
            segment_id,
            label,
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"stw_{lane}_{digest}"

def _canonical_event_lane(lane: Any) -> str:
    return canonical_lane_key(lane)

def _event(
    lane: str,
    *,
    start_sec: Any,
    end_sec: Any = None,
    label: str,
    role: str = "candidate",
    confidence: str = "medium",
    segment_id: str = "",
    evidence: list[dict[str, Any]] | None = None,
    risk_flags: list[str] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lane = _canonical_event_lane(lane)
    start = _safe_float(start_sec)
    end = _safe_float(end_sec)
    if start is not None and end is not None and end < start:
        start, end = end, start
    return {
        "event_id": _event_id(lane, start, end, label, segment_id),
        "lane": lane,
        "lane_label": _LANE_LABELS.get(lane, lane),
        "start_sec": start,
        "end_sec": end,
        "timecode": _sec_to_tc(start) if start is not None else "",
        "label": _clip_text(label, 120),
        "role": role,
        "role_label": _ROLE_LABELS.get(role, role),
        "confidence": confidence,
        "segment_id": segment_id,
        "source_refs": source_refs or [],
        "evidence": evidence or [],
        "risk_flags": risk_flags or [],
    }

def _stable_source_identity_key(prefix: str, *parts: Any) -> str:
    payload = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"

def _viewer_clip_source_identity(src: dict[str, Any], row: dict[str, Any], start: Any) -> dict[str, Any]:
    clip_uid = str(src.get("clip_uid") or src.get("uid") or "").strip()
    video_no = str(src.get("video_no") or src.get("source_video_no") or row.get("video_no") or "").strip()
    offset = src.get("offset_sec", start)
    title = src.get("title") or row.get("title") or ""
    if clip_uid:
        return {
            "source_identity_key": _stable_source_identity_key("viewer_clip", video_no, clip_uid),
            "source_identity_kind": "public_viewer_clip_uid",
            "source_identity_confidence": "high",
            "engagement_weighted": True,
        }
    return {
        "source_identity_key": _stable_source_identity_key("viewer_clip_anchor", video_no, offset, title),
        "source_identity_kind": "deterministic_viewer_clip_anchor",
        "source_identity_confidence": "low",
        "engagement_weighted": False,
    }

def _metadata_events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    ledger = meta.get("segment_ledger") if isinstance(meta, dict) else []
    if not isinstance(ledger, list):
        ledger = []
    has_legacy_comment_chapter = any(
        isinstance(row, dict) and str(row.get("kind") or "") == "chapter"
        for row in ledger
    )

    for row in ledger:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind == "summary":
            continue
        start = row.get("start_sec")
        end = row.get("end_sec")
        title = row.get("title") or row.get("timecode") or kind or "segment"
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        evidence = [
            {
                "type": "segment",
                "label": kind or "segment",
                "text": _clip_text(row.get("summary") or row.get("reason") or row.get("timecode")),
            }
        ]
        for src in sources[:5]:
            if isinstance(src, dict):
                evidence.append(
                    {
                        "type": str(src.get("source_type") or "source"),
                        "label": str(src.get("section") or src.get("role") or ""),
                        "text": _clip_text(src.get("text") or src.get("title") or src.get("timecode") or src),
                    }
                )






        segment_lane = {
            "timeline": "existing_segments",
            "highlight": "highlight",
        }.get(kind)
        if segment_lane:
            events.append(
                _event(
                    segment_lane,
                    start_sec=start,
                    end_sec=end,
                    label=title,
                    role="confirmed",
                    confidence="high",
                    segment_id=str(row.get("segment_id") or ""),
                    evidence=evidence,
                    risk_flags=[str(v) for v in (row.get("risk_flags") or [])],
                    source_refs=[{"type": "metadata.segment_ledger", "kind": kind}],
                )
            )
        if kind == "chapter":



            comment_evidence = [
                {
                    "type": "comment_replay",
                    "label": "치지직 댓글 타임라인",
                    "text": _clip_text(row.get("title") or row.get("timecode")),
                }
            ]
            comment_evidence.extend(
                {
                    "type": str(src.get("source_type") or "comment_replay"),
                    "label": str(src.get("section") or src.get("role") or "원문"),
                    "text": _clip_text(src.get("text") or src.get("title") or src.get("timecode") or src),
                }
                for src in sources
                if isinstance(src, dict)
            )
            events.append(
                _event(
                    "comment_replay",
                    start_sec=start,
                    end_sec=end,
                    label=title,
                    role="supporting",
                    confidence="medium",
                    segment_id=str(row.get("segment_id") or ""),
                    evidence=comment_evidence,
                    risk_flags=["comment_chapter_is_navigation_not_fact"],
                    source_refs=[{"type": "metadata.segment_ledger", "kind": "chapter"}],
                )
            )
        for src in sources:
            if not isinstance(src, dict) or src.get("source_type") != "viewer_clip_anchor":
                continue
            offset = src.get("offset_sec", start)
            identity = _viewer_clip_source_identity(src, row, start)
            event = _event(
                "viewer_clip",
                start_sec=offset,
                label=src.get("title") or title or "viewer clip",
                role="supporting",
                confidence="medium",
                segment_id=str(row.get("segment_id") or ""),
                evidence=[
                    {
                        "type": "viewer_clip",
                        "label": str(src.get("clip_uid") or "viewer_clip_anchor"),
                        "text": _clip_text(src.get("title") or src),
                    }
                ],
                risk_flags=[
                    "viewer_interest_signal_not_ground_truth",
                    *([] if identity["engagement_weighted"] else ["viewer_clip_anchor_not_engagement_weighted"]),
                ],
                source_refs=[
                    {
                        "type": "metadata.segment_ledger.sources",
                        "source_type": "viewer_clip_anchor",
                        "source_identity_key": identity["source_identity_key"],
                        "source_identity_kind": identity["source_identity_kind"],
                        "source_identity_confidence": identity["source_identity_confidence"],
                        "engagement_weighted": identity["engagement_weighted"],
                    }
                ],
            )
            event.update(identity)
            events.append(event)





    comment_context = meta.get("timeline_comment_context") if isinstance(meta, dict) else None
    if not has_legacy_comment_chapter and isinstance(comment_context, dict):
        context_hash = str(comment_context.get("anchor_sha256") or "")
        for index, row in enumerate(comment_context.get("anchors") or []):
            if not isinstance(row, dict):
                continue
            sec = _safe_float(row.get("time_sec"))
            label = str(row.get("label") or "").strip()
            lane = str(row.get("lane") or "chapter_timetable")
            if sec is None or not label:
                continue
            events.append(
                _event(
                    "comment_replay",
                    start_sec=sec,
                    label=label,
                    role="supporting",
                    confidence="medium" if lane == "chapter_timetable" else "low",
                    segment_id=f"comment-context-{index}",
                    evidence=[{
                        "type": "comment_replay",
                        "label": "정제된 댓글 시간표 후보",
                        "text": "자막·채팅과 함께 확인해야 하는 탐색 후보",
                    }],
                    risk_flags=[
                        "private_rewritten_timetable_anchor",
                        "comment_anchor_requires_stt_or_chat_corroboration",
                    ],
                    source_refs=[{
                        "type": "metadata.timeline_comment_context",
                        "schema_version": str(
                            comment_context.get("schema_version") or ""
                        ),
                        "anchor_sha256": context_hash,
                        "lane": lane,
                    }],
                )
            )

    for peak in meta.get("audio_reaction_peaks") or []:
        if not isinstance(peak, dict):
            continue
        center = peak.get("center_sec", peak.get("sec"))
        labels = peak.get("labels") if isinstance(peak.get("labels"), list) else []
        events.append(
            _event(
                "audio_peak",
                start_sec=center,
                label=_audio_peak_editor_label(labels),
                role="supporting",
                confidence="medium" if labels else "low",
                evidence=[
                    {
                        "type": "audio_peak",
                        "label": "보조 신호",
                        "text": _clip_text(peak.get("caution") or peak.get("decision_rule") or labels),
                    }
                ],
                risk_flags=["audio_peak_supporting_signal"],
                source_refs=[{"type": "metadata.audio_reaction_peaks"}],
            )
        )

    return events

def _chat_message_sec(item: Any) -> float | None:
    if not isinstance(item, dict):
        return None
    for key in ("sec", "time_sec", "offset_sec"):
        value = _safe_float(item.get(key))
        if value is not None:
            return value
    ms = _safe_float(item.get("ms"))
    if ms is not None:
        return ms / 1000.0
    return None

def _chat_message_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("msg", "message", "text", "content"):
        if item.get(key):
            text = EMOTICON_TOKEN_RE.sub("", str(item.get(key) or ""))
            return _clip_text(text, 80)
    return ""

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

_LENIENT_SRT_TIME_RE = re.compile(
    r"(?P<h1>\d{2,}):(?P<m1>\d{2}):(?P<s1>\d{2})[,\.](?P<ms1>\d{3})"
    r"\s*-->\s*"
    r"(?P<h2>\d{2,}):(?P<m2>\d{2}):(?P<s2>\d{2})[,\.](?P<ms2>\d{3})"
)

def _srt_match_to_seconds(match: re.Match[str], suffix: str) -> float:
    millis = (
        int(match.group(f"h{suffix}")) * 3_600_000
        + int(match.group(f"m{suffix}")) * 60_000
        + int(match.group(f"s{suffix}")) * 1_000
        + int(match.group(f"ms{suffix}"))
    )
    return round(millis / 1000.0, 3)

def _parse_srt_segments_lenient(srt_text: str) -> list[dict[str, Any]]:
    """Best-effort subtitle extraction for the admin editor cockpit.

    The strict ASR sidecar parser is intentionally fail-fast for benchmark and
    reproducibility checks. The admin cockpit has a different job: show the
    manager any usable nearby subtitle context while quietly skipping malformed
    SRT blocks.
    """

    normalized = srt_text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in re.split(r"\n[ \t]*\n", normalized.strip()) if block.strip()]
    rows: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n")]
        timestamp_idx = next((idx for idx, line in enumerate(lines) if _LENIENT_SRT_TIME_RE.search(line)), -1)
        if timestamp_idx < 0:
            continue
        match = _LENIENT_SRT_TIME_RE.search(lines[timestamp_idx])
        if not match:
            continue
        start_sec = _srt_match_to_seconds(match, "1")
        end_sec = _srt_match_to_seconds(match, "2")
        if end_sec <= start_sec:
            continue
        text = " ".join(line for line in lines[timestamp_idx + 1 :] if line).strip()
        if not text:
            continue
        rows.append(
            {
                "index": len(rows) + 1,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "text": _clip_text(text, 260),
                "source": "srt_lenient_fallback",
            }
        )
    return rows

def _load_chat_messages(video_no: str, work_dir: Path | None) -> list[dict[str, Any]]:
    if not video_no or work_dir is None:
        return []
    path = work_dir / str(video_no) / f"{video_no}_chat.log.json"
    data = _load_json(path)
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

def _load_manager_chat_density_buckets(
    video_no: str,
    work_dir: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = {
        "status": "missing",
        "manager_only": True,
        "public_payload": False,
        "raw_chat_text_included": False,
        "raw_author_included": False,
        "bucket_count": 0,
    }
    if not video_no or work_dir is None:
        return [], summary
    payload = _load_json(
        work_dir / str(video_no) / _CHAT_DENSITY_SIGNAL_FILENAME
    )
    if payload is None:
        return [], summary
    policy = payload.get("policy") if isinstance(payload, dict) else None
    duration = _safe_float(
        payload.get("source_duration_sec") if isinstance(payload, dict) else None
    )
    rows = payload.get("buckets") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _CHAT_DENSITY_SIGNAL_SCHEMA_VERSION
        or payload.get("source_asset_id") != f"chzzk-{video_no}"
        or payload.get("source_clock_ref") != f"chzzk-vod-{video_no}"
        or duration is None
        or not math.isfinite(duration)
        or duration <= 0
        or not isinstance(policy, dict)
        or policy.get("raw_chat_text_included") is not False
        or policy.get("raw_author_included") is not False
        or policy.get("public_payload") is not False
        or policy.get("ranking_or_default_changed") is not False
        or not isinstance(rows, list)
        or len(rows) > 2400
    ):
        return [], {**summary, "status": "unavailable"}
    allowed_keys = {
        "start_sec",
        "end_sec",
        "count",
        "laughter_count",
        "surprise_count",
    }
    buckets: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - allowed_keys:
            return [], {**summary, "status": "unavailable"}
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        count = _safe_int(row.get("count"))
        laughter = _safe_int(row.get("laughter_count"), 0)
        surprise = _safe_int(row.get("surprise_count"), 0)
        if (
            start is None
            or end is None
            or count is None
            or laughter is None
            or surprise is None
            or not all(math.isfinite(value) for value in (start, end))
            or start < 0
            or end <= start
            or end > duration
            or count < 0
            or laughter < 0
            or surprise < 0
        ):
            return [], {**summary, "status": "unavailable"}
        buckets.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "count": count,
                "laughter_count": laughter,
                "surprise_count": surprise,
                "samples": [],
            }
        )
    return buckets, {
        **summary,
        "status": "partial_signal_coverage" if buckets else "no_useful_range",
        "bucket_count": len(buckets),
    }

def _merge_chat_buckets(
    raw_buckets: list[dict[str, Any]],
    density_buckets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {
        (float(row["start_sec"]), float(row["end_sec"])): row
        for row in density_buckets
    }
    for row in raw_buckets:
        merged[(float(row["start_sec"]), float(row["end_sec"]))] = row
    return [merged[key] for key in sorted(merged)]

def _adaptive_input_clock(
    row: dict[str, Any],
    *,
    start_fields: tuple[str, ...] = ("start_sec",),
    end_fields: tuple[str, ...] = ("end_sec",),
) -> tuple[float, float | None] | None:
    start: float | None = None
    for field in start_fields:
        start = _safe_float(row.get(field))
        if start is not None:
            break
    if start is None or not math.isfinite(start) or start < 0:
        return None
    end: float | None = None
    for field in end_fields:
        end = _safe_float(row.get(field))
        if end is not None:
            break
    if end is not None and (
        not math.isfinite(end)
        or end < start
    ):
        return None
    return round(start, 3), round(end, 3) if end is not None else None

def _adaptive_input_event_id(family: str, *identity: Any) -> str:
    digest = hashlib.sha1(
        json.dumps(
            [family, *(str(value) for value in identity)],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"adaptive-input-{family}-{digest}"

def _adaptive_subtitle_buckets(
    subtitles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for index, row in enumerate(subtitles):
        clock = _adaptive_input_clock(row)
        if clock is None:
            continue
        start, end = clock
        buckets.append(
            {
                "event_id": _adaptive_input_event_id(
                    "subtitle",
                    row.get("index", index),
                    start,
                    end,
                ),
                "start_sec": start,
                "end_sec": end,
            }
        )
    return buckets

def _adaptive_chat_buckets(
    chat_buckets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for row in chat_buckets:
        clock = _adaptive_input_clock(row)
        count = _safe_int(row.get("count"))
        if clock is None or count is None or count <= 0:
            continue
        start, end = clock
        buckets.append(
            {
                "start_sec": start,
                "end_sec": end,
                "count": count,
            }
        )
    return buckets

def _adaptive_audio_events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    waveform = meta.get("audio_waveform")
    samples = (
        waveform.get("samples")
        if isinstance(waveform, dict)
        and waveform.get("schema_version") == "audio_waveform.v1"
        and isinstance(waveform.get("samples"), list)
        else []
    )
    for index, row in enumerate(samples):
        if not isinstance(row, dict):
            continue
        clock = _adaptive_input_clock(row)
        value = _safe_float(row.get("value"))
        if (
            clock is None
            or value is None
            or not math.isfinite(value)
            or value < _ADAPTIVE_AUDIO_WAVEFORM_ACTIVE_MIN
        ):
            continue
        start, end = clock
        events.append(
            {
                "event_id": _adaptive_input_event_id(
                    "audio-waveform",
                    index,
                    start,
                    end,
                ),
                "lane": "audio_peak",
                "kind": "audio_waveform",
                "start_sec": start,
                "end_sec": end,
            }
        )
    peaks = meta.get("audio_reaction_peaks")
    for index, row in enumerate(peaks if isinstance(peaks, list) else []):
        if not isinstance(row, dict):
            continue
        clock = _adaptive_input_clock(
            row,
            start_fields=("start_sec", "center_sec", "sec"),
        )
        if clock is None:
            continue
        start, end = clock
        events.append(
            {
                "event_id": _adaptive_input_event_id(
                    "audio-peak",
                    index,
                    start,
                    end,
                ),
                "lane": "audio_peak",
                "kind": "audio_peak",
                "start_sec": start,
                "end_sec": end,
            }
        )
    return events

def _adaptive_visual_events(
    visual_scene_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, row in enumerate(visual_scene_rows):
        if (
            str(row.get("status") or "ok") != "ok"
            or row.get("public_safe") is not True
            or row.get("raw_frame_included") is not False
            or row.get("raw_ocr_text_included") is not False
        ):
            continue
        clock = _adaptive_input_clock(row)
        if clock is None:
            continue
        start, end = clock
        signal_type = str(row.get("signal_type") or "")
        event_id = _adaptive_input_event_id(
            "visual",
            row.get("event_id", index),
            signal_type,
            start,
            end,
        )
        if signal_type == "ocr_text_class":
            text_class = str(row.get("sanitized_text_class") or "")
            events.append(
                {
                    "event_id": event_id,
                    "lane": "ocr_signal",
                    "kind": "ocr_signal",
                    "start_sec": start,
                    "end_sec": end,
                    **(
                        {"sanitized_text_class": text_class}
                        if text_class in VISUAL_SCENE_ALLOWED_TEXT_CLASSES
                        and text_class
                        else {}
                    ),
                }
            )
            continue
        events.append(
            {
                "event_id": event_id,
                "lane": "visual_scene",
                "kind": "visual_scene",
                "signal_type": signal_type,
                "start_sec": start,
                "end_sec": end,
            }
        )
    return events

def _adaptive_summary_events(meta: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = meta.get("segment_ledger")
    events: list[dict[str, Any]] = []
    for index, row in enumerate(ledger if isinstance(ledger, list) else []):
        if not isinstance(row, dict):
            continue
        clock = _adaptive_input_clock(row)
        if clock is None:
            continue
        start, end = clock
        kind = str(row.get("kind") or "")


        if kind not in {"summary", "timeline", "highlight"}:
            continue
        events.append(
            {
                "event_id": _adaptive_input_event_id(
                    "summary",
                    row.get("segment_id", index),
                    kind,
                    start,
                    end,
                ),
                "lane": "highlight" if kind == "highlight" else "existing_segments",
                "kind": "timed_summary",
                "start_sec": start,
                "end_sec": end,
            }
        )
    return events

def _adaptive_multisignal_events(
    meta: dict[str, Any],
    *,
    visual_scene_rows: list[dict[str, Any]],
    live_context_events: list[dict[str, Any]],
    context_shadow_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        *_adaptive_audio_events(meta),
        *_adaptive_visual_events(visual_scene_rows),
        *_adaptive_summary_events(meta),
        *live_context_events,
        *context_shadow_events,
    ]

def _spread_sample(rows: list[dict[str, Any]], limit: int, time_key: str) -> list[dict[str, Any]]:
    if limit <= 0 or not rows:
        return []
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row.get(time_key) or 0),
            str(row.get("event_id") or row.get("candidate_id") or ""),
        ),
    )
    if len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[len(ordered) // 2]]
    return [
        ordered[round(index * (len(ordered) - 1) / (limit - 1))]
        for index in range(limit)
    ]

def _comment_hint_id(value: Any, prefix: str) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(rf"{re.escape(prefix)}[0-9a-f]{{64}}", text) else ""

def _comment_hint_clock(row: dict[str, Any], duration_sec: float) -> tuple[float, float] | None:
    clock = row.get("clock") if isinstance(row.get("clock"), dict) else {}
    start = _safe_float(clock.get("start_sec"))
    end = _safe_float(clock.get("end_sec"))
    if (
        start is None
        or end is None
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
        or end > duration_sec
    ):
        return None
    return start, end

def _comment_hint_source_ref(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"comment-anchor:\d+", text) else ""

def _load_manager_comment_hint_events(
    video_no: str,
    work_dir: Path | None,
    *,
    max_events: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = {
        "status": "missing",
        "manager_only": True,
        "public_payload": False,
        "raw_comment_text_included": False,
        "raw_author_included": False,
        "engagement_counts_included": False,
        "event_count": 0,
    }
    if not video_no or work_dir is None:
        return [], summary
    sidecar_path = work_dir / str(video_no) / COMMENT_TEMPORAL_HINTS_FILENAME
    if not sidecar_path.exists():
        return [], summary
    payload = _load_json(sidecar_path)
    if payload is None:
        return [], {**summary, "status": "unavailable"}
    if not isinstance(payload, dict) or payload.get("schema_version") != COMMENT_TEMPORAL_HINTS_SCHEMA_VERSION:
        return [], {**summary, "status": "unavailable"}
    digest = str(payload.get("content_sha256") or "")
    digest_body = {key: value for key, value in payload.items() if key != "content_sha256"}
    try:
        actual_digest = hashlib.sha256(
            json.dumps(
                digest_body,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return [], {**summary, "status": "unavailable"}
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    duration = _safe_float(payload.get("source_duration_sec"))
    analysis_state = str(payload.get("analysis_state") or "")
    if (
        digest != actual_digest
        or payload.get("source_asset_id") != f"chzzk-{video_no}"
        or payload.get("source_clock_ref") != f"chzzk-vod-{video_no}"
        or duration is None
        or not math.isfinite(duration)
        or duration <= 0
        or analysis_state not in {
            "no_useful_range",
            "not_analyzed",
            "partial_signals",
            "failed",
            "disabled",
        }
        or policy.get("raw_comment_text_included") is not False
        or policy.get("public_payload") is not False
        or policy.get("comment_is_source_time_truth") is not False
        or policy.get("comment_is_highlight_truth") is not False
        or policy.get("comment_is_learning_label") is not False
        or policy.get("manager_manual_intervals_required") is not False
        or policy.get("ranking_or_default_changed") is not False
    ):
        return [], {**summary, "status": "unavailable"}
    try:
        adaptive_presentation = build_comment_adaptive_presentation(
            payload,
            mode="editor",
        )
    except (TypeError, ValueError):
        return [], {**summary, "status": "unavailable"}
    adaptive_rows = {
        str(row.get("context_range_candidate_id") or ""): row
        for row in adaptive_presentation.get("context_range_hints") or []
        if isinstance(row, dict)
        and row.get("context_range_candidate_id")
    }
    adaptive_summary = {
        key: adaptive_presentation[key]
        for key in (
            "schema_version",
            "mode",
            "broadcast_profile",
            "confidence_band",
            "presentation_profile",
            "policy",
        )
    }
    if analysis_state != "partial_signals":
        return [], {
            **summary,
            "status": analysis_state,
            "analysis_state": analysis_state,
            "adaptive_presentation": adaptive_summary,
        }

    total_budget = min(_MAX_MANAGER_COMMENT_HINT_EVENTS, max(0, max_events // 2))
    if max_events >= len(_MANAGER_COMMENT_HINT_LANE_KEYS):
        total_budget = max(len(_MANAGER_COMMENT_HINT_LANE_KEYS), total_budget)
    if total_budget >= 3:
        point_limit = min(
            _MAX_MANAGER_COMMENT_POINT_EVENTS,
            max(1, total_budget // 6),
        )
        story_limit = min(
            _MAX_MANAGER_COMMENT_STORY_EVENTS,
            max(1, total_budget // 6),
        )
        lane_limits = {
            "comment_context_range": total_budget - point_limit - story_limit,
            "comment_moment_seed": point_limit,
            "comment_story_window": story_limit,
        }
    else:
        lane_limits = {
            "comment_context_range": min(1, total_budget),
            "comment_moment_seed": 1 if total_budget >= 2 else 0,
            "comment_story_window": 0,
        }
    common_risks = [
        "comment_hint_supporting_signal_only",
        "comment_hint_not_source_time_truth",
        "comment_hint_not_highlight_truth",
    ]
    events_by_lane: dict[str, list[dict[str, Any]]] = {
        lane: [] for lane in _MANAGER_COMMENT_HINT_LANE_KEYS
    }
    valid_context_ids: set[str] = set()
    valid_moment_ids: set[str] = set()

    for row in payload.get("context_range_candidates") or []:
        if not isinstance(row, dict) or row.get("schema_version") != COMMENT_CONTEXT_RANGE_SCHEMA_VERSION:
            continue
        candidate_id = _comment_hint_id(row.get("context_range_candidate_id"), "comment-context-")
        clock = _comment_hint_clock(row, duration)
        source_ref = _comment_hint_source_ref(row.get("source_anchor_ref"))
        if (
            not candidate_id
            or clock is None
            or not source_ref
            or row.get("confidence") != "weak"
            or row.get("axis_states") != {"activity": "unknown", "topic": "unknown"}
        ):
            continue
        adaptive = adaptive_rows.get(candidate_id)
        if not isinstance(adaptive, dict):
            continue
        event = _event(
            "comment_context_range",
            start_sec=clock[0],
            end_sec=clock[1],
            label=str(adaptive.get("label") or ""),
            role="supporting",
            confidence="low",
            evidence=[{"type": "comment_hint", "label": "약한 맥락 범위", "text": "다른 방송 신호로 확인해야 합니다."}],
            risk_flags=common_risks,
            source_refs=[{"type": "comment_temporal_hint", "candidate_id": candidate_id, "source_anchor_ref": source_ref}],
        )
        event.update(
            {
                "event_id": candidate_id,
                "context_range_candidate_id": candidate_id,
                "analysis_state": str(payload.get("analysis_state") or ""),
                "axis_states": {"activity": "unknown", "topic": "unknown"},
                "activity_hint": str(adaptive.get("activity_hint") or "unknown"),
                "topic_hint": str(adaptive.get("label") or ""),
                "content_form_hint": str(
                    adaptive.get("content_form_hint") or "unknown"
                ),
                "broadcast_profile_hint": str(
                    adaptive.get("broadcast_profile_hint") or "mixed_unknown"
                ),
                "confidence_band": str(
                    adaptive.get("confidence_band") or "unknown"
                ),
            }
        )
        events_by_lane["comment_context_range"].append(event)
        valid_context_ids.add(candidate_id)

    for row in payload.get("exact_moment_seeds") or []:
        if not isinstance(row, dict) or row.get("schema_version") != COMMENT_MOMENT_SEED_SCHEMA_VERSION:
            continue
        candidate_id = _comment_hint_id(row.get("exact_moment_seed_id"), "comment-moment-seed-")
        at_sec = _safe_float(row.get("at_sec"))
        source_ref = _comment_hint_source_ref(row.get("source_anchor_ref"))
        state = str(row.get("state") or "")
        if (
            not candidate_id
            or at_sec is None
            or not math.isfinite(at_sec)
            or at_sec < 0
            or at_sec >= duration
            or not source_ref
            or state != "point_hint_needs_positive_duration_refinement"
            or row.get("confidence") != "weak"
        ):
            continue
        event = _event(
            "comment_moment_seed",
            start_sec=at_sec,
            label="댓글이 가리킨 시각 씨앗",
            role="supporting",
            confidence="low",
            evidence=[{"type": "comment_hint", "label": "시각 점", "text": "아직 정확한 사건 구간이 아니며 양의 길이 보정이 필요합니다."}],
            risk_flags=[*common_risks, "point_hint_is_not_exact_moment"],
            source_refs=[{"type": "comment_temporal_hint", "candidate_id": candidate_id, "source_anchor_ref": source_ref}],
        )
        event.update(
            {
                "event_id": candidate_id,
                "exact_moment_seed_id": candidate_id,
                "state": state,
            }
        )
        events_by_lane["comment_moment_seed"].append(event)
        valid_moment_ids.add(candidate_id)

    for row in payload.get("highlight_story_window_candidates") or []:
        if not isinstance(row, dict) or row.get("schema_version") != COMMENT_STORY_WINDOW_SCHEMA_VERSION:
            continue
        candidate_id = _comment_hint_id(
            row.get("highlight_story_window_candidate_id"),
            "comment-story-seed-",
        )
        clock = _comment_hint_clock(row, duration)
        state = str(row.get("state") or "")
        moment_ref = _comment_hint_id(row.get("exact_moment_seed_ref"), "comment-moment-seed-")
        raw_context_refs = row.get("context_range_candidate_refs")
        context_refs = [
            ref
            for ref in (
                _comment_hint_id(value, "comment-context-")
                for value in (raw_context_refs or [])
            )
            if ref
        ]
        if (
            not candidate_id
            or clock is None
            or state != "candidate_bounds_from_neighbor_anchors"
            or row.get("confidence") != "weak"
            or moment_ref not in valid_moment_ids
            or not isinstance(raw_context_refs, list)
            or len(context_refs) != len(raw_context_refs)
            or any(ref not in valid_context_ids for ref in context_refs)
            or row.get("role_hints")
            != {
                "setup": "unknown",
                "event": "point_hint",
                "reaction": "unknown",
                "payoff": "unknown",
            }
        ):
            continue
        event = _event(
            "comment_story_window",
            start_sec=clock[0],
            end_sec=clock[1],
            label="댓글 주변 Story Window 후보",
            role="supporting",
            confidence="low",
            evidence=[{"type": "comment_hint", "label": "약한 Story Window", "text": "앞상황·사건·반응·마무리는 아직 확인 전입니다."}],
            risk_flags=common_risks,
            source_refs=[
                {
                    "type": "comment_temporal_hint",
                    "candidate_id": candidate_id,
                    "exact_moment_seed_ref": moment_ref,
                    "context_range_candidate_refs": context_refs,
                }
            ],
        )
        event.update(
            {
                "event_id": candidate_id,
                "highlight_story_window_candidate_id": candidate_id,
                "state": state,
                "exact_moment_seed_ref": moment_ref,
                "context_range_candidate_refs": context_refs,
                "role_hints": {
                    "setup": "unknown",
                    "event": "point_hint",
                    "reaction": "unknown",
                    "payoff": "unknown",
                },
            }
        )
        adaptive = adaptive_rows.get(context_refs[0]) if context_refs else None
        if isinstance(adaptive, dict):
            event.update(
                {
                    "activity_hint": str(
                        adaptive.get("activity_hint") or "unknown"
                    ),
                    "content_form_hint": str(
                        adaptive.get("content_form_hint") or "unknown"
                    ),
                    "broadcast_profile_hint": str(
                        adaptive.get("broadcast_profile_hint") or "mixed_unknown"
                    ),
                    "confidence_band": str(
                        adaptive.get("confidence_band") or "unknown"
                    ),
                }
            )
        events_by_lane["comment_story_window"].append(event)

    source_lane_event_counts = {
        lane: len(events_by_lane[lane])
        for lane in _MANAGER_COMMENT_HINT_LANE_KEYS
    }
    selected: list[dict[str, Any]] = []
    for lane in _MANAGER_COMMENT_HINT_LANE_KEYS:
        selected.extend(
            _spread_sample(events_by_lane[lane], lane_limits[lane], "start_sec")
        )
    lane_event_counts = {
        lane: len([event for event in selected if event.get("lane") == lane])
        for lane in _MANAGER_COMMENT_HINT_LANE_KEYS
    }
    hidden_lane_event_counts = {
        lane: max(0, source_lane_event_counts[lane] - lane_event_counts[lane])
        for lane in _MANAGER_COMMENT_HINT_LANE_KEYS
    }
    return selected, {
        **summary,
        "status": "present" if selected else analysis_state,
        "analysis_state": analysis_state,
        "event_count": len(selected),
        "max_event_count": total_budget,
        "source_event_count": sum(source_lane_event_counts.values()),
        "hidden_event_count": sum(hidden_lane_event_counts.values()),
        "source_lane_event_counts": source_lane_event_counts,
        "lane_event_counts": lane_event_counts,
        "hidden_lane_event_counts": hidden_lane_event_counts,
        "adaptive_presentation": adaptive_summary,
    }

def _context_shadow_ref(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return (
        text
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", text)
        else ""
    )

def _context_shadow_label(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        re.match(r"^[A-Za-z]:[\\/]", value)
        or value.startswith(("/", "\\\\"))
        or re.search(r"(?:https?|file|ftp)://", value, re.IGNORECASE)
    ):
        raise ValueError("unsafe_context_shadow_label")
    return value

def _context_shadow_clock_within_duration(
    structure: ContextStructureV1,
    duration_sec: float,
) -> bool:
    clocks = [
        *(row.clock for row in structure.context_ranges),
        *(row.clock for row in structure.exact_moments),
        *(row.clock for row in structure.highlight_story_windows),
        *(row.clock for row in structure.legacy_selected_draft_ranges),
        *(row.clock for row in structure.unknown_gaps),
    ]
    return all(clock.end_sec <= duration_sec for clock in clocks)

def _context_shadow_range_links(
    payload: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    links: dict[tuple[str, str], dict[str, Any]] = {}
    rows = payload.get("range_evidence_links")
    if not isinstance(rows, list):
        return links
    for row in rows:
        if not isinstance(row, dict):
            continue
        context_range_id = _context_shadow_ref(row.get("context_range_id"))
        axis = str(row.get("axis") or "")
        raw_refs = row.get("evidence_refs")
        if (
            not context_range_id
            or axis not in {"activity", "topic"}
            or not isinstance(raw_refs, list)
        ):
            continue
        evidence_refs = [
            ref
            for ref in (_context_shadow_ref(value) for value in raw_refs)
            if ref
        ]
        links[(context_range_id, axis)] = {
            "fusion_family_ref": _context_shadow_ref(row.get("fusion_family_ref")),
            "evidence_refs": evidence_refs,
        }
    return links

def _context_shadow_gap_links(
    payload: dict[str, Any],
) -> dict[tuple[str, float, float], dict[str, Any]]:
    links: dict[tuple[str, float, float], dict[str, Any]] = {}
    rows = payload.get("gap_evidence")
    if not isinstance(rows, list):
        return links
    for row in rows:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis") or "")
        clock = row.get("clock") if isinstance(row.get("clock"), dict) else {}
        start = _safe_float(clock.get("start_sec"))
        end = _safe_float(clock.get("end_sec"))
        gap_ref = _context_shadow_ref(row.get("gap_ref"))
        claims = row.get("claims")
        if (
            axis not in {"activity", "topic"}
            or start is None
            or end is None
            or not gap_ref
            or not isinstance(claims, list)
        ):
            continue
        evidence_refs = sorted(
            {
                ref
                for claim in claims
                if isinstance(claim, dict)
                for ref in (
                    _context_shadow_ref(value)
                    for value in (
                        claim.get("evidence_refs")
                        if isinstance(claim.get("evidence_refs"), list)
                        else []
                    )
                )
                if ref
            }
        )
        links[(axis, start, end)] = {
            "gap_ref": gap_ref,
            "evidence_refs": evidence_refs,
        }
    return links

def _load_manager_context_shadow_events(
    video_no: str,
    work_dir: Path | None,
    *,
    max_events: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = {
        "status": "not_analyzed",
        "manager_only": True,
        "public_payload": False,
        "event_count": 0,
        "activity_range_count": 0,
        "topic_range_count": 0,
        "unknown_gap_count": 0,
    }
    if not video_no or work_dir is None:
        return [], summary
    sidecar_path = work_dir / str(video_no) / _CONTEXT_TRANSITION_SHADOW_FILENAME
    if not sidecar_path.exists():
        return [], summary
    payload = _load_json(sidecar_path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        != _CONTEXT_TRANSITION_SHADOW_SCHEMA_VERSION
        or payload.get("source_asset_id") != f"chzzk-{video_no}"
        or payload.get("source_clock_ref") != f"chzzk-vod-{video_no}"
    ):
        return [], {**summary, "status": "unavailable"}
    raw_duration = payload.get("source_duration_sec")
    duration = (
        float(raw_duration)
        if not isinstance(raw_duration, bool)
        and isinstance(raw_duration, (int, float))
        else None
    )
    if (
        duration is None
        or not math.isfinite(duration)
        or duration <= 0
        or not isinstance(payload.get("context_structure"), dict)
    ):
        return [], {**summary, "status": "unavailable"}
    try:
        structure = ContextStructureV1.from_dict(payload["context_structure"])
    except ContextStructureContractError:
        return [], {**summary, "status": "unavailable"}
    if not _context_shadow_clock_within_duration(structure, duration):
        return [], {**summary, "status": "unavailable"}
    try:
        range_labels = {
            str(row.context_range_id): (
                _context_shadow_label(row.activity),
                _context_shadow_label(row.topic),
            )
            for row in structure.context_ranges
        }
    except ValueError:
        return [], {**summary, "status": "unavailable"}

    range_links = _context_shadow_range_links(payload)
    gap_links = _context_shadow_gap_links(payload)
    state = structure.state.value
    common_risks = [
        "context_shadow_manager_only",
        "context_shadow_not_confirmed",
        "context_shadow_not_highlight_truth",
    ]
    events_by_lane: dict[str, list[dict[str, Any]]] = {
        lane: [] for lane in _MANAGER_CONTEXT_SHADOW_LANE_KEYS
    }
    for row in structure.context_ranges:
        context_range_id = str(row.context_range_id)
        activity, topic = range_labels[context_range_id]
        for axis, label, lane in (
            ("activity", activity, "context_activity_shadow"),
            ("topic", topic, "context_topic_shadow"),
        ):
            if label is None:
                continue
            link = range_links.get((context_range_id, axis), {})
            evidence_link_refs = list(link.get("evidence_refs") or [])
            source_ref = {
                "type": "context_transition_shadow",
                "context_range_id": context_range_id,
                "axis": axis,
                "evidence_refs": evidence_link_refs,
            }
            fusion_family_ref = str(link.get("fusion_family_ref") or "")
            if fusion_family_ref:
                source_ref["fusion_family_ref"] = fusion_family_ref
            event = _event(
                lane,
                start_sec=row.clock.start_sec,
                end_sec=row.clock.end_sec,
                label=label,
                role="supporting",
                confidence="low",
                evidence=[
                    {
                        "type": "context_shadow",
                        "label": "활동 축" if axis == "activity" else "주제 축",
                        "text": "자동 shadow 분석 후보이며 관리자 확정 전입니다.",
                    }
                ],
                risk_flags=common_risks,
                source_refs=[source_ref],
            )
            event.update(
                {
                    "context_range_id": context_range_id,
                    "axis": axis,
                    "state": state,
                    "evidence_link_refs": evidence_link_refs,
                }
            )
            events_by_lane[lane].append(event)

    for row in structure.unknown_gaps:
        for axis_value in row.axes:
            axis = axis_value.value
            link = gap_links.get(
                (axis, row.clock.start_sec, row.clock.end_sec),
                {},
            )
            evidence_link_refs = list(link.get("evidence_refs") or [])
            gap_ref = str(link.get("gap_ref") or "")
            source_ref = {
                "type": "context_transition_shadow",
                "axis": axis,
                "evidence_refs": evidence_link_refs,
            }
            if gap_ref:
                source_ref["gap_ref"] = gap_ref
            event = _event(
                "context_unknown_gap_shadow",
                start_sec=row.clock.start_sec,
                end_sec=row.clock.end_sec,
                label=(
                    "활동 맥락 미확인"
                    if axis == "activity"
                    else "주제 맥락 미확인"
                ),
                role="supporting",
                confidence="low",
                evidence=[
                    {
                        "type": "context_shadow",
                        "label": "미확인 구간",
                        "text": "신호가 부족해 이 축의 맥락을 정하지 않았습니다.",
                    }
                ],
                risk_flags=[*common_risks, "context_unknown_gap"],
                source_refs=[source_ref],
            )
            event.update(
                {
                    "axis": axis,
                    "state": state,
                    "gap_ref": gap_ref,
                    "evidence_link_refs": evidence_link_refs,
                }
            )
            events_by_lane["context_unknown_gap_shadow"].append(event)

    total_budget = min(
        _MAX_MANAGER_CONTEXT_SHADOW_EVENTS,
        max(0, max_events // 5),
    )
    if max_events >= len(_MANAGER_CONTEXT_SHADOW_LANE_KEYS):
        total_budget = max(
            len(_MANAGER_CONTEXT_SHADOW_LANE_KEYS),
            total_budget,
        )
    base_limit, extra = divmod(
        total_budget,
        len(_MANAGER_CONTEXT_SHADOW_LANE_KEYS),
    )
    selected: list[dict[str, Any]] = []
    for index, lane in enumerate(_MANAGER_CONTEXT_SHADOW_LANE_KEYS):
        limit = base_limit + (1 if index < extra else 0)
        selected.extend(_spread_sample(events_by_lane[lane], limit, "start_sec"))
    lane_counts = {
        lane: len([event for event in selected if event.get("lane") == lane])
        for lane in _MANAGER_CONTEXT_SHADOW_LANE_KEYS
    }
    return selected, {
        **summary,
        "status": "present" if selected else state,
        "analysis_state": state,
        "event_count": len(selected),
        "max_event_count": total_budget,
        "activity_range_count": lane_counts["context_activity_shadow"],
        "topic_range_count": lane_counts["context_topic_shadow"],
        "unknown_gap_count": lane_counts["context_unknown_gap_shadow"],
    }

def _load_live_context_rows(meta: dict[str, Any], video_no: str, work_dir: Path | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    meta_rows = meta.get("live_context_signals") if isinstance(meta, dict) else None
    if isinstance(meta_rows, list):
        rows.extend(row for row in meta_rows if isinstance(row, dict))
    elif isinstance(meta_rows, dict):
        items = meta_rows.get("items")
        if isinstance(items, list):
            rows.extend(row for row in items if isinstance(row, dict))
    if video_no and work_dir is not None:
        for name in ("live_context_signal.jsonl", "live_context_signal.json"):
            path = work_dir / str(video_no) / name
            if path.is_file():
                rows.extend(load_live_context_signals(path))
    return normalize_live_context_signals(rows)

def _load_visual_scene_rows(
    meta: dict[str, Any],
    video_no: str,
    work_dir: Path | None,
    *,
    duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    meta_rows = meta.get("visual_scene_signals") if isinstance(meta, dict) else None
    if isinstance(meta_rows, list):
        rows.extend(row for row in meta_rows if isinstance(row, dict))
    elif isinstance(meta_rows, dict):
        found_items = False
        for key in ("signals", "items", "rows", "events"):
            items = meta_rows.get(key)
            if isinstance(items, list):
                rows.extend(row for row in items if isinstance(row, dict))
                found_items = True
                break
        if not found_items:
            rows.append(meta_rows)
    if video_no and work_dir is not None:
        for name in ("visual_scene_signal.jsonl", "visual_scene_signal.json"):
            path = work_dir / str(video_no) / name
            if path.is_file():
                rows.extend(load_visual_scene_signals(path))
    return normalize_visual_scene_signals(rows, video_no=video_no, duration_sec=duration_sec)

def _live_context_diagnostic(code: str, message: str, *, count: int = 1) -> dict[str, Any]:
    return {"code": code, "message": message, "count": count}

def _near_live_context_content(
    elapsed: float,
    *,
    base_events: list[dict[str, Any]],
    subtitles: list[dict[str, Any]],
    chat_buckets: list[dict[str, Any]],
    radius_sec: int = 300,
) -> list[str]:
    nearby: set[str] = set()

    def near(row: dict[str, Any]) -> bool:
        start = _safe_float(row.get("start_sec"), row.get("sec"))
        end = _safe_float(row.get("end_sec"), start)
        if start is None:
            return False
        if end is None:
            end = start
        return (start - radius_sec) <= elapsed <= (end + radius_sec)

    for event in base_events:
        lane = str(event.get("lane") or "")
        if lane in {"existing_segments", "highlight", "audio_peak", "viewer_clip", "comment_replay"} and near(event):
            nearby.add(lane)
    if any(near(row) for row in subtitles):
        nearby.add("subtitle_density")
    if any(near(row) and (_safe_int(row.get("count"), 0) or 0) > 0 for row in chat_buckets):
        nearby.add("chat_density")
    return sorted(nearby)

def _viewer_peak_reason_sets(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    samples = [
        row for row in rows
        if str(row.get("event_type") or "") in {"viewer_count_sample", "viewer_count_spike"}
        and str(row.get("fetch_status") or "") not in {
            "outside_vod_duration",
            "malformed_sidecar",
            "sidecar_unreadable",
        }
        and _safe_float(row.get("elapsed_sec")) is not None
        and _safe_int(row.get("viewer_count")) is not None
    ]
    if not samples:
        return {}
    samples.sort(key=lambda row: (_safe_float(row.get("elapsed_sec"), 0.0) or 0.0, str(row.get("event_id") or "")))
    max_count = max(_safe_int(row.get("viewer_count"), 0) or 0 for row in samples)
    reasons_by_id: dict[str, set[str]] = {}

    def add(row: dict[str, Any], reason: str) -> None:
        event_id = str(row.get("event_id") or "")
        if event_id:
            reasons_by_id.setdefault(event_id, set()).add(reason)

    sample_points = [
        (
            _safe_float(row.get("elapsed_sec"), 0.0) or 0.0,
            _safe_int(row.get("viewer_count"), 0) or 0,
            row,
        )
        for row in samples
    ]
    for index, (elapsed, count, row) in enumerate(sample_points):
        if count == max_count:
            add(row, "stream_max")
        previous: list[int] = []
        for previous_elapsed, previous_count, _previous_row in reversed(sample_points[:index]):
            delta = elapsed - previous_elapsed
            if delta > 600:
                break
            if delta >= 300:
                previous.append(previous_count)
        if previous:
            baseline = max(previous)
            if count - baseline >= max(100, int(baseline * 0.2)):
                add(row, "rolling_jump")
        nearby_counts: list[int] = []
        for nearby_elapsed, nearby_count, _nearby_row in sample_points[max(0, index - 8):index]:
            if elapsed - nearby_elapsed <= 300:
                nearby_counts.append(nearby_count)
        for nearby_elapsed, nearby_count, _nearby_row in sample_points[index + 1:index + 9]:
            if nearby_elapsed - elapsed > 300:
                break
            nearby_counts.append(nearby_count)
        if count >= int(max_count * 0.9) and any(value >= int(count * 0.85) for value in nearby_counts):
            add(row, "sustained_peak")

    selected: dict[str, set[str]] = {}
    for row in sorted(samples, key=lambda item: -(_safe_int(item.get("viewer_count"), 0) or 0)):
        event_id = str(row.get("event_id") or "")
        if event_id not in reasons_by_id:
            continue
        reasons = reasons_by_id[event_id]
        if "rolling_jump" not in reasons or not ({"stream_max", "sustained_peak"} & reasons):
            continue
        elapsed = _safe_float(row.get("elapsed_sec"), 0.0) or 0.0
        if any(
            abs(elapsed - (_safe_float(chosen.get("elapsed_sec"), 0.0) or 0.0)) <= 300
            for chosen in samples
            if str(chosen.get("event_id") or "") in selected
        ):
            reasons_by_id[event_id].add("cooldown_deduped_peak")
            continue
        selected[event_id] = reasons_by_id[event_id]
    return selected

def _annotate_live_context_default_influence(
    rows: list[dict[str, Any]],
    *,
    base_events: list[dict[str, Any]],
    subtitles: list[dict[str, Any]],
    chat_buckets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    peak_reasons = _viewer_peak_reason_sets(rows)
    annotated: list[dict[str, Any]] = []
    influence_count = 0
    viewer_peak_candidate_count = 0
    title_candidate_count = 0
    uncertain_count = 0
    for raw_row in rows:
        row = dict(raw_row)
        elapsed = _safe_float(row.get("elapsed_sec"))
        event_type = str(row.get("event_type") or "")
        fetch_status = str(row.get("fetch_status") or "")
        if event_type == "metadata_unavailable" or fetch_status in {
            "outside_vod_duration",
            "malformed_sidecar",
            "sidecar_unreadable",
        }:
            row["default_influence_candidate"] = False
            row["default_influence_reasons"] = [fetch_status or "metadata_unavailable"]
            annotated.append(row)
            continue
        if elapsed is None:
            row["default_influence_candidate"] = False
            row["default_influence_reason"] = "time_mapping_uncertain"
            uncertain_count += 1
            annotated.append(row)
            continue
        if event_type == "live_title_change":
            row["default_influence_candidate"] = True
            row["default_influence_weight"] = "strong_split_candidate"
            row["default_influence_reasons"] = ["title_change", "elapsed_time_mapped"]
            title_candidate_count += 1
            influence_count += 1
        elif event_type in {"viewer_count_sample", "viewer_count_spike"}:
            reasons = sorted(peak_reasons.get(str(row.get("event_id") or ""), set()))
            nearby = _near_live_context_content(
                elapsed,
                base_events=base_events,
                subtitles=subtitles,
                chat_buckets=chat_buckets,
            )
            if reasons and nearby:
                row["event_type"] = "viewer_count_spike"
                row["label"] = f"시청자 급증: {_safe_int(row.get('viewer_count'), 0) or 0:,}명"
                row["default_influence_candidate"] = True
                row["default_influence_weight"] = "attention_candidate_needs_content_evidence"
                row["default_influence_reasons"] = [*reasons, "nearby_content_evidence"]
                row["nearby_content_evidence"] = nearby
                viewer_peak_candidate_count += 1
                influence_count += 1
            else:
                row["default_influence_candidate"] = False
                row["default_influence_reasons"] = reasons or ["not_ranked_peak"]
                if not nearby:
                    row["default_influence_reasons"] = [*row["default_influence_reasons"], "nearby_content_evidence_missing"]
        annotated.append(row)
    return annotated, {
        "default_influence_candidate_count": influence_count,
        "title_split_candidate_count": title_candidate_count,
        "viewer_peak_candidate_count": viewer_peak_candidate_count,
        "time_mapping_uncertain_count": uncertain_count,
    }

def _prepare_live_context_rows(
    rows: list[dict[str, Any]],
    *,
    duration_sec: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prepared_all: list[dict[str, Any]] = []
    prepared_display: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    sample_seen: set[int] = set()
    sample_input_count = 0
    sample_used_count = 0
    sample_deduped_count = 0
    sample_capped_count = 0
    quarantined_count = 0
    unavailable_count = 0

    for raw_row in rows:
        row = dict(raw_row)
        event_type = str(row.get("event_type") or "")
        fetch_status = str(row.get("fetch_status") or "")
        risk_flags = [str(flag) for flag in (row.get("risk_flags") or []) if flag]
        elapsed = _safe_float(row.get("elapsed_sec"))

        if event_type == "metadata_unavailable" or fetch_status in {"malformed_sidecar", "sidecar_unreadable"}:
            unavailable_count += 1
            row["risk_flags"] = sorted(set([*risk_flags, "live_context_unavailable_visible"]))
            prepared_all.append(row)
            prepared_display.append(row)
            continue

        if elapsed is not None and (elapsed < 0 or (duration_sec > 0 and elapsed > duration_sec)):
            quarantined_count += 1
            row["fetch_status"] = "outside_vod_duration"
            row["risk_flags"] = sorted(set([*risk_flags, "live_context_outside_vod_duration"]))
            prepared_all.append(row)
            prepared_display.append(row)
            continue

        if event_type == "viewer_count_sample":
            sample_input_count += 1
            bucket = int((elapsed or 0) // 60) if elapsed is not None else -1
            if bucket in sample_seen:
                sample_deduped_count += 1
                prepared_all.append(row)
                continue
            if sample_used_count >= _MAX_LIVE_CONTEXT_SAMPLE_EVENTS:
                sample_capped_count += 1
                prepared_all.append(row)
                continue
            sample_seen.add(bucket)
            sample_used_count += 1

        prepared_all.append(row)
        prepared_display.append(row)

    if unavailable_count:
        diagnostics.append(
            _live_context_diagnostic(
                "unavailable_sidecar",
                "라이브 맥락 파일을 읽을 수 없어 수집 안 됨으로 표시했습니다.",
                count=unavailable_count,
            )
        )
    if quarantined_count:
        diagnostics.append(
            _live_context_diagnostic(
                "outside_vod_duration",
                "라이브 맥락 시간이 VOD 길이 밖이라 요약 후보에서 제외했습니다.",
                count=quarantined_count,
            )
        )
    if sample_deduped_count:
        diagnostics.append(
            _live_context_diagnostic(
                "sample_deduped",
                "같은 1분 구간의 시청자 수 샘플은 하나만 남겼습니다.",
                count=sample_deduped_count,
            )
        )
    if sample_capped_count:
        diagnostics.append(
            _live_context_diagnostic(
                "sample_capped",
                "시청자 수 샘플이 많아 다른 근거를 밀어내지 않도록 일부만 표시했습니다.",
                count=sample_capped_count,
            )
        )

    return prepared_display, prepared_all, {
        "input_count": len(rows),
        "prepared_count": len(prepared_display),
        "sample_input_count": sample_input_count,
        "sample_used_count": sample_used_count,
        "sample_deduped_count": sample_deduped_count,
        "sample_capped_count": sample_capped_count,
        "quarantined_count": quarantined_count,
        "unavailable_count": unavailable_count,
        "diagnostics": diagnostics,
    }

def _live_context_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        elapsed = _safe_float(row.get("elapsed_sec"))
        event_type = str(row.get("event_type") or "")
        fetch_status = str(row.get("fetch_status") or "")
        if event_type == "metadata_unavailable" or fetch_status in {
            "malformed_sidecar",
            "sidecar_unreadable",
            "outside_vod_duration",
        }:
            continue
        if elapsed is None:
            continue
        label = str(row.get("label") or "라이브 맥락")
        evidence_bits: list[str] = []
        if row.get("viewer_count_bucket") and row.get("viewer_count_bucket") != "unavailable":
            evidence_bits.append(f"시청자 구간 {row.get('viewer_count_bucket')}")
        if row.get("title_current_sanitized"):
            evidence_bits.append(f"제목 {row.get('title_current_sanitized')}")
        if row.get("default_influence_candidate"):
            evidence_bits.append("요약 반영 후보")
        nearby = row.get("nearby_content_evidence") if isinstance(row.get("nearby_content_evidence"), list) else []
        if nearby:
            evidence_bits.append("근처 근거 " + ", ".join(str(item) for item in nearby[:4]))
        if row.get("fetch_status") and row.get("fetch_status") != "ok":
            evidence_bits.append(f"수집 상태 {row.get('fetch_status')}")
        events.append(
            _event(
                "live_context",
                start_sec=elapsed,
                label=label,
                role="candidate" if row.get("default_influence_candidate") else "supporting",
                confidence=row.get("confidence") or "medium",
                evidence=[
                    {
                        "type": "live_context_signal",
                        "label": "방송 제목/시청자 변화",
                        "text": _clip_text(" · ".join(evidence_bits) or "라이브 공개 메타데이터 변화", 220),
                    }
                ],
                risk_flags=[
                    *(row.get("risk_flags") or []),
                    "live_context_supporting_signal_not_truth",
                    *([] if row.get("default_influence_candidate") else ["live_context_not_default_influence_candidate"]),
                ],
                source_refs=[
                    {
                        "type": "live_context_signal.v1",
                        "schema_version": LIVE_CONTEXT_SCHEMA_VERSION,
                        "event_type": event_type,
                        "source": row.get("source") or "",
                        "privacy_class": row.get("privacy_class") or "public_metadata",
                        "default_influence_candidate": bool(row.get("default_influence_candidate")),
                        "default_influence_weight": row.get("default_influence_weight") or "",
                        "default_influence_reasons": row.get("default_influence_reasons") or [],
                        "nearby_content_evidence": nearby,
                    }
                ],
            )
        )
    return events

def _visual_scene_event_rows(rows: list[dict[str, Any]], *, max_rows: int = _MAX_VISUAL_SCENE_EVENTS) -> list[dict[str, Any]]:
    if max_rows <= 0:
        return []
    priority = {
        "game_state_marker": 0,
        "menu_loading_marker": 1,
        "scene_boundary": 2,
        "ocr_text_class": 3,
        "visual_similarity_change": 4,
    }
    eligible = [
        row for row in rows
        if str(row.get("status") or "ok") == "ok"
        and _safe_float(row.get("start_sec")) is not None
    ]
    eligible.sort(
        key=lambda row: (
            priority.get(str(row.get("signal_type") or ""), 9),
            _sort_time_or_missing(row.get("start_sec")),
            str(row.get("event_id") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    seen_buckets: set[tuple[str, int]] = set()
    for row in eligible:
        start = _safe_float(row.get("start_sec"), 0.0) or 0.0
        bucket = (str(row.get("signal_type") or ""), int(start // 2))
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        selected.append(row)
        if len(selected) >= max_rows:
            break
    selected.sort(key=lambda row: (_sort_time_or_missing(row.get("start_sec")), str(row.get("signal_type") or "")))
    return selected

def _sort_time_or_missing(value: Any, missing: float = 999999999.0) -> float:
    number = _safe_float(value)
    return missing if number is None else number

def _visual_scene_events(rows: list[dict[str, Any]], *, max_rows: int = _MAX_VISUAL_SCENE_EVENTS) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in _visual_scene_event_rows(rows, max_rows=max_rows):
        start = _safe_float(row.get("start_sec"))
        if start is None:
            continue
        signal_type = str(row.get("signal_type") or "visual_similarity_change")
        evidence_bits = [str(row.get("label") or "화면 신호 후보")]
        if row.get("sanitized_text_class"):
            evidence_bits.append(f"OCR 유형 {row.get('sanitized_text_class')}")
        if row.get("marker"):
            evidence_bits.append(f"화면 상태 {row.get('marker')}")
        if row.get("text_hash"):
            evidence_bits.append("OCR 해시 있음")
        events.append(
            _event(
                "visual_scene",
                start_sec=start,
                end_sec=row.get("end_sec"),
                label=row.get("label") or "화면 전환/게임 시작 후보",
                role="supporting",
                confidence=row.get("confidence") or "medium",
                segment_id=str(row.get("event_id") or ""),
                evidence=[
                    {
                        "type": "visual_scene_signal",
                        "label": "화면 전환/게임 시작 후보",
                        "text": _clip_text(" · ".join(evidence_bits), 220),
                    }
                ],
                risk_flags=[
                    *(row.get("risk_flags") or []),
                    "visual_scene_supporting_signal_not_truth",
                ],
                source_refs=[
                    {
                        "type": "visual_scene_signal.v1",
                        "schema_version": VISUAL_SCENE_SCHEMA_VERSION,
                        "signal_type": signal_type,
                        "source": row.get("source") or "",
                        "privacy_class": row.get("privacy_class") or "public_safe",
                        "public_safe": bool(row.get("public_safe", True)),
                        "raw_frame_included": False,
                        "raw_ocr_text_included": False,
                        "sanitized_text_class": row.get("sanitized_text_class") or "",
                        "text_hash": row.get("text_hash") or "",
                        "evidence_refs": row.get("evidence_refs") or [],
                    }
                ],
            )
        )
    return events

def _chat_sample_index(chats: list[dict[str, Any]]) -> tuple[list[float], list[tuple[float, str]]]:
    """Normalize once per build without mutating or caching source messages."""
    rows: list[tuple[float, str]] = []
    for item in chats:
        sec = _chat_message_sec(item)
        if sec is None or math.isnan(sec):
            continue
        text = _chat_message_text(item)
        if text:
            rows.append((sec, text))
    rows.sort(key=lambda row: row[0])
    return [row[0] for row in rows], rows

def _chat_samples(
    chats: list[dict[str, Any]], center_sec: Any, radius_sec: int = 20, *,
    sample_index: tuple[list[float], list[tuple[float, str]]] | None = None,
) -> list[str]:
    center = _safe_float(center_sec)
    if center is None:
        return []
    times, rows = sample_index if sample_index is not None else _chat_sample_index(chats)
    if math.isfinite(center) and math.isfinite(radius_sec):


        left = bisect_left(times, math.nextafter(center - radius_sec, -math.inf))
        right = bisect_right(times, math.nextafter(center + radius_sec, math.inf))
        rows = rows[left:right]
    samples: list[tuple[float, str]] = []
    for sec, text in rows:
        delta = abs(sec - center)
        if delta <= radius_sec:
            samples.append((delta, text))
    samples.sort(key=lambda row: (row[0], row[1]))
    return [text for _, text in samples[:3]]

def _chat_events(meta: dict[str, Any], chats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    sample_index = None
    for row in meta.get("highlights") or []:
        if not isinstance(row, dict):
            continue
        sec = row.get("sec")
        source_signals = {str(v).lower() for v in (row.get("source_signals") or [])}
        labels = source_signals or {str(v).lower() for v in (row.get("labels") or [])}



        if source_signals and not (
            "chat" in source_signals
            or source_signals & _REACTION_SIGNAL_ALIASES
            or any(label.startswith(("chat_", "reaction_")) or "laugh" in label for label in source_signals)
        ):
            continue
        if sample_index is None and _safe_float(sec) is not None:
            sample_index = _chat_sample_index(chats)
        sample_texts = _chat_samples(chats, sec, sample_index=sample_index)
        sample_blob = " / ".join(sample_texts)
        reaction_families = {str(v).lower() for v in (row.get("reaction_families") or [])}
        has_reaction_signal = bool(
            labels & _REACTION_SIGNAL_ALIASES
            or reaction_families & {"surprise", "question"}
            or any(label.startswith("reaction_") for label in labels)
        )
        if (
            row.get("laughter_burst")
            or any("laugh" in label or "laughter" in label for label in labels)
            or has_reaction_signal
        ):
            lane = "chat_laughter"
        else:
            lane = "chat_surprise"
        events.append(
            _event(
                lane,
                start_sec=sec,
                label=sample_blob or f"chat peak count={row.get('count', '')}",
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "chat",
                        "label": "채팅 반응 묶음",
                        "text": sample_blob,
                    }
                ],
                risk_flags=["chat_peak_supporting_signal"],
                source_refs=[{"type": "metadata.highlights"}],
            )
        )
    return events

def _chat_context_index(chats: list[dict[str, Any]], *, bucket_sec: int = 30, max_buckets: int = 2400) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    for item in chats:
        sec = _chat_message_sec(item)
        text = _chat_message_text(item)
        if sec is None:
            continue
        bucket_start = int(sec // bucket_sec) * bucket_sec
        bucket = buckets.setdefault(
            bucket_start,
            {
                "start_sec": bucket_start,
                "end_sec": bucket_start + bucket_sec,
                "count": 0,
                "laughter_count": 0,
                "surprise_count": 0,
                "samples": [],
            },
        )
        bucket["count"] += 1
        lowered = text.lower()
        if "ㅋ" in text or "웃" in text or "개웃" in lowered:
            bucket["laughter_count"] += 1
        if any(token in text for token in ("뭐야", "헐", "와", "미친", "대박", "?")):
            bucket["surprise_count"] += 1
        if text and len(bucket["samples"]) < 4:
            bucket["samples"].append({"sec": round(sec, 3), "text": text})
    rows = sorted(buckets.values(), key=lambda row: row["start_sec"])
    return rows[:max_buckets]

def _chat_bucket_reaction_events(
    chat_buckets: list[dict[str, Any]],
    *,
    window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    """Promote time-balanced chat markers for long VOD admin review.

    The main chat peak list is intentionally selective. The scene finder has a
    different job: keep later reviewable moments visible even when the strongest
    global peaks all happened early.
    """

    if not chat_buckets:
        return []
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in chat_buckets:
        start = _safe_float(row.get("start_sec"))
        if start is None:
            continue
        grouped.setdefault(int(start // window_sec), []).append(row)

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        selected_reaction_start: float | None = None
        best_reaction = max(
            rows,
            key=lambda row: (
                int(row.get("laughter_count") or 0),
                int(row.get("surprise_count") or 0),
                int(row.get("count") or 0),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        laughter_count = int(best_reaction.get("laughter_count") or 0)
        surprise_count = int(best_reaction.get("surprise_count") or 0)
        if laughter_count >= 3 or surprise_count >= 2:
            selected_reaction_start = _safe_float(best_reaction.get("start_sec"))
            samples = [
                str(sample.get("text") or "")
                for sample in (best_reaction.get("samples") or [])
                if isinstance(sample, dict) and sample.get("text")
            ]
            reaction_label = []
            if laughter_count:
                reaction_label.append(f"웃음 {laughter_count}개")
            if surprise_count:
                reaction_label.append(f"놀람/질문 {surprise_count}개")
            label = " / ".join(samples[:3]) or ", ".join(reaction_label) or "웃음/놀람 채팅"
            events.append(
                _event(
                    "chat_laughter",
                    start_sec=best_reaction.get("start_sec"),
                    end_sec=best_reaction.get("end_sec"),
                    label=label,
                    role="supporting",
                    confidence="medium",
                    evidence=[
                        {
                            "type": "chat_bucket",
                            "label": "시간대 대표 웃음/놀람",
                            "text": _clip_text(label),
                        }
                    ],
                    risk_flags=["chat_bucket_supporting_signal"],
                    source_refs=[{"type": "work.chat_bucket_context", "selection": "balanced_laughter_surprise"}],
                )
            )

        density_rows = rows
        if selected_reaction_start is not None:
            density_rows = [
                row for row in rows
                if _safe_float(row.get("start_sec")) != selected_reaction_start
            ]
        if not density_rows:
            continue
        best_density = max(
            density_rows,
            key=lambda row: (
                int(row.get("count") or 0),
                int(row.get("surprise_count") or 0),
                int(row.get("laughter_count") or 0),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        count = int(best_density.get("count") or 0)
        surprise_count = int(best_density.get("surprise_count") or 0)
        if count >= 20:
            samples = [
                str(sample.get("text") or "")
                for sample in (best_density.get("samples") or [])
                if isinstance(sample, dict) and sample.get("text")
            ]
            label = " / ".join(samples[:3]) or f"채팅 {count}개"
            events.append(
                _event(
                    "chat_surprise",
                    start_sec=best_density.get("start_sec"),
                    end_sec=best_density.get("end_sec"),
                    label=label,
                    role="supporting",
                    confidence="medium",
                    evidence=[
                        {
                            "type": "chat_bucket",
                            "label": "시간대 대표 채팅 급증",
                            "text": _clip_text(f"{count}개" + (f", 놀람/질문 {surprise_count}개" if surprise_count else "")),
                        }
                    ],
                    risk_flags=["chat_bucket_supporting_signal"],
                    source_refs=[{"type": "work.chat_bucket_context", "selection": "balanced_density"}],
                )
            )
    return events

def _emoticon_reaction_events(
    chats: list[dict[str, Any]],
    *,
    window_sec: int = 30,
    balance_window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    """Expose aggregate emoticon spikes without copying raw chat text.

    Emoticon codes are opaque reaction tokens. This lane is only a default
    editor-finder signal, not a semantic label such as laugh/hype/surprise.
    """

    if not chats:
        return []
    windows = aggregate_emoticon_windows(chats, window_sec=window_sec, include_empty_windows=False)
    modes = classify_emoticon_windows(windows)
    grouped: dict[int, list[tuple[Any, Any]]] = {}
    for window, mode in zip(windows, modes):
        if window.emote_count <= 0:
            continue
        if mode.chat_mode != CHAT_MODE_REACTION_HEAVY and mode.reaction_strength < 0.35:
            continue
        grouped.setdefault(int(window.start_sec // balance_window_sec), []).append((window, mode))

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        window, mode = max(
            rows,
            key=lambda item: (
                float(item[1].reaction_strength),
                int(item[0].emote_count),
                int(item[0].emote_message_count),
                -int(item[0].start_sec),
            ),
        )
        label = f"감정표현 반응 {window.emote_count}개 · 채팅 {window.message_count}개"
        events.append(
            _event(
                "emoticon_reaction",
                start_sec=window.start_sec,
                end_sec=window.end_sec,
                label=label,
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "emoticon_aggregate",
                        "label": "집계 감정표현 반응",
                        "text": _clip_text(
                            f"반응 강도 {mode.reaction_strength:.2f}, "
                            f"감정표현 메시지 {window.emote_message_count}개, "
                            f"서로 다른 감정표현 {window.unique_emote_count}종"
                        ),
                    }
                ],
                risk_flags=["emoticon_aggregate_supporting_signal"],
                source_refs=[
                    {
                        "type": "work.chat_emoticon_aggregate",
                        "privacy_class": window.privacy_class,
                    }
                ],
            )
        )
    return events

def _chat_coverage_summary(chats: list[dict[str, Any]], duration_sec: float) -> dict[str, Any]:
    seconds = [
        sec
        for sec in (_chat_message_sec(item) for item in chats)
        if sec is not None
    ]
    if not seconds:
        return {"status": "missing", "message_count": 0}
    latest = max(seconds)
    first = min(seconds)
    gap = max(0.0, float(duration_sec or 0.0) - latest)
    ratio = min(1.0, latest / duration_sec) if duration_sec > 0 else 0.0
    status = "partial" if duration_sec > 0 and gap >= 600 and ratio < 0.85 else "present"
    return {
        "status": status,
        "message_count": len(seconds),
        "first_message_sec": round(first, 3),
        "latest_message_sec": round(latest, 3),
        "coverage_ratio": round(ratio, 4),
        "coverage_gap_sec": round(gap, 3),
    }

def _load_asr_segments(video_no: str, work_dir: Path | None) -> list[dict[str, Any]]:
    if not video_no or work_dir is None:
        return []
    per_vod = work_dir / str(video_no)
    json_candidates = [
        per_vod / "asr_segments.json",
        per_vod / f"{video_no}_asr_segments.json",
    ]
    for path in json_candidates:
        data = _load_json(path)
        if isinstance(data, dict) and data.get("schema_version") == "asr_segments.v1":
            segments = data.get("segments")
            if isinstance(segments, list):
                return [row for row in segments if isinstance(row, dict)]
    try:
        srts = sorted(
            per_vod.glob(f"{video_no}_*.srt"),
            key=lambda p: p.stat().st_size if p.exists() else 0,
            reverse=True,
        )
    except OSError:
        srts = []
    if not srts:
        return []
    srt_text = ""
    try:
        srt_text = srts[0].read_text(encoding="utf-8", errors="replace")
        return parse_srt_segments(srt_text)
    except OSError:
        return []
    except AsrSegmentsError:
        return _parse_srt_segments_lenient(srt_text)

def _subtitle_context_index(segments: list[dict[str, Any]], *, max_segments: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in segments[:max_segments]:
        if not isinstance(row, dict):
            continue
        start = _safe_float(row.get("start_sec"))
        end = _safe_float(row.get("end_sec"))
        text = _clip_text(row.get("text"), 220)
        if start is None or not text:
            continue
        rows.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end if end is not None else start, 3),
                "text": text,
                "index": _safe_int(row.get("index"), 0) or 0,
            }
        )
    return rows

def _subtitle_events_from_segments(segments: list[dict[str, Any]], *, video_no: str = "") -> list[dict[str, Any]]:
    if not segments:
        return []
    ranked = sorted(
        segments,
        key=lambda row: (
            -len(str(row.get("text") or "")),
            _safe_float(row.get("start_sec"), 0.0) or 0.0,
        ),
    )[:16]
    ranked.sort(key=lambda row: _safe_float(row.get("start_sec"), 0.0) or 0.0)
    events = [
        _event(
            "subtitle_density",
            start_sec=row.get("start_sec"),
            end_sec=row.get("end_sec"),
            label=row.get("text") or f"subtitle {row.get('index', '')}",
            role="supporting",
            confidence="medium",
            evidence=[
                {
                    "type": "subtitle",
                    "label": f"asr segment {row.get('index', '')}",
                    "text": _clip_text(row.get("text")),
                }
            ],
            risk_flags=["subtitle_context_not_final_claim"],
            source_refs=[{"type": "work.asr_segments_or_srt", "video_no": video_no}],
        )
        for row in ranked
    ]
    events.extend(_subtitle_balanced_events_from_segments(segments, video_no=video_no))
    return events

def _subtitle_balanced_events_from_segments(
    segments: list[dict[str, Any]],
    *,
    video_no: str = "",
    window_sec: int = 1800,
    max_windows: int = 24,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in segments:
        if not isinstance(row, dict):
            continue
        start = _safe_float(row.get("start_sec"))
        text = str(row.get("text") or "").strip()
        if start is None or len(text) < 24:
            continue
        grouped.setdefault(int(start // window_sec), []).append(row)

    events: list[dict[str, Any]] = []
    for _, rows in sorted(grouped.items())[:max_windows]:
        best = max(
            rows,
            key=lambda row: (
                len(str(row.get("text") or "")),
                -(_safe_float(row.get("start_sec"), 0.0) or 0.0),
            ),
        )
        events.append(
            _event(
                "subtitle_density",
                start_sec=best.get("start_sec"),
                end_sec=best.get("end_sec"),
                label=best.get("text") or f"subtitle {best.get('index', '')}",
                role="supporting",
                confidence="medium",
                evidence=[
                    {
                        "type": "subtitle",
                        "label": f"시간대 대표 자막 {best.get('index', '')}",
                        "text": _clip_text(best.get("text")),
                    }
                ],
                risk_flags=["subtitle_context_not_final_claim"],
                source_refs=[{"type": "work.asr_segments_or_srt", "video_no": video_no, "selection": "balanced_window"}],
            )
        )
    return events

def _estimate_duration_sec(meta: dict[str, Any], events: list[dict[str, Any]], subtitles: list[dict[str, Any]], chat_buckets: list[dict[str, Any]]) -> float:
    candidates: list[float] = []
    for key in ("duration_sec", "vod_duration_sec", "duration"):
        value = _safe_float(meta.get(key))
        if value is not None:
            candidates.append(value)
    for event in events:
        for key in ("end_sec", "start_sec"):
            value = _safe_float(event.get(key))
            if value is not None:
                candidates.append(value)
    for row in subtitles:
        value = _safe_float(row.get("end_sec"), row.get("start_sec"))
        if value is not None:
            candidates.append(value)
    for row in chat_buckets:
        value = _safe_float(row.get("end_sec"), row.get("start_sec"))
        if value is not None:
            candidates.append(value)
    return round(max(candidates) if candidates else 0.0, 3)

def _explicit_duration_sec(meta: dict[str, Any]) -> float | None:
    for key in ("duration_sec", "vod_duration_sec", "duration"):
        value = _safe_float(meta.get(key))
        if value is not None and math.isfinite(value) and value > 0:
            return round(value, 3)
    return None

_LANE_EMPTY_REASONS = lane_empty_reasons()

def _lane_summary(
    events: list[dict[str, Any]],
    *,
    has_audio_waveform: bool = False,
    has_chat_buckets: bool = False,
    include_manager_comment_hints: bool = False,
    include_manager_context_shadow: bool = False,
) -> list[dict[str, Any]]:
    lanes: list[dict[str, Any]] = []
    for key, label in _LANE_LABELS.items():
        if key in _MANAGER_COMMENT_HINT_LANE_KEYS and not include_manager_comment_hints:
            continue
        if (
            key in _MANAGER_CONTEXT_SHADOW_LANE_KEYS
            and not include_manager_context_shadow
        ):
            continue
        lane_events = [event for event in events if event.get("lane") == key]
        count = len(lane_events)
        if key == "audio_waveform" and has_audio_waveform:
            count = max(count, 1)
        if key == "chat_volume" and has_chat_buckets:
            count = max(count, 1)
        display_role = "overview" if key in _OVERVIEW_LANE_KEYS else "scene"
        if count:
            lane = {"key": key, "label": label, "status": "present", "event_count": count, "display_role": display_role}
            if key == "viewer_clip":
                identity_keys = {
                    str(event.get("source_identity_key") or "")
                    for event in lane_events
                    if event.get("source_identity_key")
                }
                engagement_keys = {
                    str(event.get("source_identity_key") or "")
                    for event in lane_events
                    if event.get("source_identity_key") and event.get("engagement_weighted") is not False
                }
                lane["distinct_source_count"] = len(identity_keys)
                lane["distinct_engagement_source_count"] = len(engagement_keys)
            lanes.append(lane)
        else:
            lanes.append({
                "key": key,
                "label": label,
                "status": "empty",
                "event_count": 0,
                "display_role": display_role,
                "empty_reason": _LANE_EMPTY_REASONS.get(key, "No signal data is available for this lane."),
            })
    return lanes

def _sort_events(events: list[dict[str, Any]], max_events: int) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for raw_event in events:
        event = dict(raw_event)
        lane = _canonical_event_lane(event.get("lane"))
        if lane != event.get("lane"):
            event["lane"] = lane
            event["lane_label"] = _LANE_LABELS.get(lane, lane)
        event_id = str(event.get("event_id") or "")
        if event_id and event_id not in deduped:
            deduped[event_id] = event
    rows = sorted(
        deduped.values(),
        key=lambda event: (
            _sort_time_or_missing(event.get("start_sec")),
            str(event.get("lane") or ""),
            str(event.get("label") or ""),
        ),
    )
    return rows[:max_events]

def validate_signal_timeline_lane_contract(workbench: dict[str, Any]) -> dict[str, Any]:
    """Return compact diagnostics for the scene-finder lane contract."""
    lanes = [row for row in (workbench.get("lanes") or []) if isinstance(row, dict)]
    events = [row for row in (workbench.get("events") or []) if isinstance(row, dict)]
    return validate_lane_payload(lanes, events)

def draft_sidecar_path(draft_dir: str | Path, base: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base or "").strip()) or "unknown"
    return Path(draft_dir) / f"{safe}_selected_ranges_draft.json"

def default_selected_ranges_draft(base: str = "") -> dict[str, Any]:
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "base": str(base or ""),
        "selected_ranges": [],
    }

def load_selected_ranges_draft(draft_dir: str | Path, base: str) -> dict[str, Any]:
    path = draft_sidecar_path(draft_dir, base)
    data = _load_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != DRAFT_SCHEMA_VERSION:
        return default_selected_ranges_draft(base)
    ranges = data.get("selected_ranges")
    if not isinstance(ranges, list):
        data["selected_ranges"] = []
    return data

def build_signal_timeline_workbench(
    meta: dict[str, Any],
    *,
    base: str = "",
    work_dir: str | Path | None = None,
    draft_dir: str | Path | None = None,
    max_events: int = 180,
    include_manager_comment_hints: bool = False,
    include_manager_context_shadow: bool = False,
) -> dict[str, Any]:
    video_no = str(meta.get("video_no") or "").strip() if isinstance(meta, dict) else ""
    work_path = Path(work_dir) if work_dir is not None else None
    chats = _load_chat_messages(video_no, work_path)
    live_context_rows = _load_live_context_rows(meta if isinstance(meta, dict) else {}, video_no, work_path)
    asr_segments = _load_asr_segments(video_no, work_path)
    subtitles = _subtitle_context_index(asr_segments)
    raw_chat_buckets = _chat_context_index(chats)
    density_buckets, density_summary = (
        _load_manager_chat_density_buckets(video_no, work_path)
        if include_manager_comment_hints or include_manager_context_shadow
        else (
            [],
            {
                "status": "disabled",
                "manager_only": True,
                "public_payload": False,
                "raw_chat_text_included": False,
                "raw_author_included": False,
                "bucket_count": 0,
            },
        )
    )
    chat_buckets = _merge_chat_buckets(raw_chat_buckets, density_buckets)
    metadata_events = _metadata_events(meta if isinstance(meta, dict) else {})
    private_timetable_events = [
        row
        for row in metadata_events
        if "private_rewritten_timetable_anchor" in {
            str(value) for value in (row.get("risk_flags") or [])
        }
    ]
    private_timetable_row_ids = {id(row) for row in private_timetable_events}
    base_events = [
        *[
            row
            for row in metadata_events
            if id(row) not in private_timetable_row_ids
        ],
        *_chat_events(meta if isinstance(meta, dict) else {}, chats),
        *_chat_bucket_reaction_events(chat_buckets),
        *_emoticon_reaction_events(chats),
        *_subtitle_events_from_segments(asr_segments, video_no=video_no),
    ]
    duration_meta = meta if isinstance(meta, dict) else {}
    explicit_duration_sec = _explicit_duration_sec(duration_meta)
    duration_before_live_sec = explicit_duration_sec
    if duration_before_live_sec is None:
        duration_before_live_sec = _estimate_duration_sec(duration_meta, base_events, subtitles, chat_buckets)
    visual_scene_rows = _load_visual_scene_rows(
        duration_meta,
        video_no,
        work_path,
        duration_sec=explicit_duration_sec,
    )
    live_context_rows, live_context_scoring_rows, live_context_limits = _prepare_live_context_rows(
        live_context_rows,
        duration_sec=duration_before_live_sec,
    )
    scored_live_context_rows, live_context_influence = _annotate_live_context_default_influence(
        live_context_scoring_rows or live_context_rows,
        base_events=base_events,
        subtitles=subtitles,
        chat_buckets=chat_buckets,
    )
    scored_live_context_by_id = {
        str(row.get("event_id") or ""): row
        for row in scored_live_context_rows
        if row.get("event_id")
    }
    live_context_rows = [
        scored_live_context_by_id.get(str(row.get("event_id") or ""), row)
        for row in live_context_rows
    ]
    displayed_live_context_ids = {
        str(row.get("event_id") or "")
        for row in live_context_rows
        if row.get("event_id")
    }
    ranked_hidden_rows = [
        row for row in scored_live_context_rows
        if row.get("default_influence_candidate")
        and row.get("event_id")
        and str(row.get("event_id") or "") not in displayed_live_context_ids
    ]
    live_context_rows = [*live_context_rows, *ranked_hidden_rows]
    live_context_diagnostics = list(live_context_limits["diagnostics"])
    if live_context_influence["time_mapping_uncertain_count"]:
        live_context_diagnostics.append(
            _live_context_diagnostic(
                "time_mapping_uncertain",
                "시간 연결 불확실: 라이브 시각을 VOD 경과 시간으로 바꾸지 못한 행은 요약 후보에서 제외했습니다.",
                count=live_context_influence["time_mapping_uncertain_count"],
            )
        )
    live_context_events = _live_context_events(live_context_rows)
    adaptive_comment_hint_events: list[dict[str, Any]] = []
    comment_hint_events: list[dict[str, Any]] = []
    comment_hint_summary: dict[str, Any] = {}
    if include_manager_comment_hints:
        adaptive_comment_max_events = _MAX_MANAGER_COMMENT_HINT_EVENTS * 2
        (
            adaptive_comment_hint_events,
            adaptive_comment_hint_summary,
        ) = _load_manager_comment_hint_events(
            video_no,
            work_path,
            max_events=adaptive_comment_max_events,
        )
        if max_events == adaptive_comment_max_events:
            comment_hint_events = adaptive_comment_hint_events
            comment_hint_summary = adaptive_comment_hint_summary
        else:
            (
                comment_hint_events,
                comment_hint_summary,
            ) = _load_manager_comment_hint_events(
                video_no,
                work_path,
                max_events=max_events,
            )
    adaptive_context_shadow_events: list[dict[str, Any]] = []
    context_shadow_events: list[dict[str, Any]] = []
    context_shadow_summary: dict[str, Any] = {}
    if include_manager_context_shadow:
        adaptive_context_max_events = _MAX_MANAGER_CONTEXT_SHADOW_EVENTS * 5
        (
            adaptive_context_shadow_events,
            adaptive_context_shadow_summary,
        ) = _load_manager_context_shadow_events(
            video_no,
            work_path,
            max_events=adaptive_context_max_events,
        )
        if max_events == adaptive_context_max_events:
            context_shadow_events = adaptive_context_shadow_events
            context_shadow_summary = adaptive_context_shadow_summary
        else:
            (
                context_shadow_events,
                context_shadow_summary,
            ) = _load_manager_context_shadow_events(
                video_no,
                work_path,
                max_events=max_events,
            )
    adaptive_subtitles = _adaptive_subtitle_buckets(subtitles)
    adaptive_chat = _adaptive_chat_buckets(chat_buckets)
    adaptive_workbench_events = _adaptive_multisignal_events(
        duration_meta,
        visual_scene_rows=visual_scene_rows,
        live_context_events=live_context_events,
        context_shadow_events=adaptive_context_shadow_events,
    )
    duration_sec = (
        explicit_duration_sec
        if explicit_duration_sec is not None
        else _estimate_duration_sec(
            duration_meta,
            [
                *adaptive_workbench_events,
                *adaptive_comment_hint_events,
            ],
            adaptive_subtitles,
            adaptive_chat,
        )
    )
    visual_scene_max_events = min(
        _MAX_VISUAL_SCENE_EVENTS,
        max(1, max_events // 5),
    )
    core_events = _sort_events([*base_events, *live_context_events], max_events=max_events)
    visual_scene_slots = max(0, max_events - len(core_events))
    visual_scene_events = _visual_scene_events(
        visual_scene_rows,
        max_rows=min(visual_scene_max_events, visual_scene_slots),
    )
    events = _sort_events([*core_events, *visual_scene_events], max_events=max_events)
    manager_only_events = [*comment_hint_events, *context_shadow_events]
    if manager_only_events:
        retained = _spread_sample(
            events,
            max(0, max_events - len(manager_only_events)),
            "start_sec",
        )
        events = _sort_events(
            [*retained, *manager_only_events],
            max_events=max_events,
        )
    if private_timetable_events:



        events = _sort_events(
            [*events, *private_timetable_events],
            max_events=len(events) + len(private_timetable_events),
        )
    adaptive_context_ranges: dict[str, Any] = {}
    if include_manager_comment_hints or include_manager_context_shadow:
        try:
            adaptive_context_ranges = build_adaptive_context_ranges(
                source_asset_id=f"chzzk-{video_no}" if video_no else "chzzk-unknown",
                source_clock_ref=f"chzzk-vod-{video_no}" if video_no else "chzzk-vod-unknown",
                source_duration_sec=duration_sec,
                workbench_events=adaptive_workbench_events,
                subtitle_buckets=adaptive_subtitles,
                chat_buckets=adaptive_chat,
                comment_context_events=[
                    row
                    for row in adaptive_comment_hint_events
                    if str(row.get("lane") or "") == "comment_context_range"
                ],
                provider_enabled=bool(
                    (meta if isinstance(meta, dict) else {}).get(
                        "adaptive_context_ruptures_enabled"
                    )
                ),
            )
        except ValueError as exc:
            adaptive_context_ranges = {
                "schema_version": "adaptive_context_ranges.v1",
                "rule_version": "adaptive_context_ranges_rules.v2",
                "source_asset_id": f"chzzk-{video_no}" if video_no else "chzzk-unknown",
                "source_clock_ref": f"chzzk-vod-{video_no}" if video_no else "chzzk-vod-unknown",
                "source_duration_sec": duration_sec,
                "bucket_sec": 30,
                "analysis_state": (
                    "analysis_failed"
                    if str(exc) != "invalid_zero_duration"
                    else "invalid_zero_duration_moment"
                ),
                "policy": {
                    "manager_only": True,
                    "public_payload": False,
                    "ranking_or_default_changed": False,
                    "manager_manual_intervals_required": False,
                },
                "provider_receipt": {
                    "schema_version": "ruptures_boundary_receipt.v1",
                    "provider": "ruptures",
                    "provider_version": "lazy",
                    "status": "unavailable",
                    "reason": "builder_failure",
                    "boundary_bucket_indexes": [],
                    "provenance": "deterministic_fallback",
                },
                "ranges": [],
                "unknown_gaps": [],
                "summary": {
                    "range_count": 0,
                    "parent_range_count": 0,
                    "child_range_count": 0,
                    "unknown_gap_count": 0,
                },
            }
    chat_coverage = _chat_coverage_summary(chats, duration_sec)
    audio_waveform = duration_meta.get("audio_waveform")
    draft = (
        load_selected_ranges_draft(draft_dir, base)
        if draft_dir is not None and base
        else default_selected_ranges_draft(base)
    )
    lanes = _lane_summary(
        events,
        has_audio_waveform=isinstance(audio_waveform, dict) and bool(audio_waveform.get("samples")),
        has_chat_buckets=bool(chat_buckets),
        include_manager_comment_hints=include_manager_comment_hints,
        include_manager_context_shadow=include_manager_context_shadow,
    )
    active_lane_labels = {
        key: label
        for key, label in _LANE_LABELS.items()
        if (
            (include_manager_comment_hints or key not in _MANAGER_COMMENT_HINT_LANE_KEYS)
            and (
                include_manager_context_shadow
                or key not in _MANAGER_CONTEXT_SHADOW_LANE_KEYS
            )
        )
    }
    workbench = {
        "schema_version": SCHEMA_VERSION,
        "video_no": video_no,
        "base": str(base or ""),
        "duration_sec": duration_sec,
        **({"audio_waveform": audio_waveform} if isinstance(audio_waveform, dict) else {}),
        "lanes": lanes,
        "events": events,
        "context_index": {
            "subtitles": subtitles,
            "chat_buckets": chat_buckets,
            **(
                {"chat_density_signal": density_summary}
                if include_manager_comment_hints or include_manager_context_shadow
                else {}
            ),
            "limits": {
                "subtitle_count": len(subtitles),
                "chat_bucket_count": len(chat_buckets),
                "live_context_count": len(live_context_rows),
                "live_context_event_count": len(live_context_events),
                "visual_scene_count": len(visual_scene_rows),
                "visual_scene_event_count": len([event for event in events if event.get("lane") == "visual_scene"]),
                **live_context_limits,
            },
        },
        "selected_ranges_draft": draft,
        **(
            {"adaptive_context_ranges": adaptive_context_ranges}
            if adaptive_context_ranges
            else {}
        ),
        "summary": {
            "lane_count": len(lanes),
            "event_count": len(events),
            "duration_sec": duration_sec,
            "subtitle_context_count": len(subtitles),
            "chat_bucket_count": len(chat_buckets),
            "chat_coverage": chat_coverage,
            **(
                {"chat_density_signal": density_summary}
                if include_manager_comment_hints or include_manager_context_shadow
                else {}
            ),
            "live_context": {
                "status": (
                    "present"
                    if live_context_events
                    else "unavailable"
                    if live_context_rows
                    else "missing"
                ),
                "count": len(live_context_rows),
                "event_count": len(live_context_events),
                "schema_version": LIVE_CONTEXT_SCHEMA_VERSION,
                "missing_is_fail_open": True,
                "unavailable_count": live_context_limits["unavailable_count"],
                "quarantined_count": live_context_limits["quarantined_count"],
                "sample_input_count": live_context_limits["sample_input_count"],
                "sample_used_count": live_context_limits["sample_used_count"],
                "sample_deduped_count": live_context_limits["sample_deduped_count"],
                "sample_capped_count": live_context_limits["sample_capped_count"],
                **live_context_influence,
                "diagnostics": live_context_diagnostics,
            },
            "visual_scene": {
                "status": (
                    "present"
                    if any(event.get("lane") == "visual_scene" for event in events)
                    else "unavailable"
                    if visual_scene_rows
                    else "missing"
                ),
                "count": len(visual_scene_rows),
                "event_count": len([event for event in events if event.get("lane") == "visual_scene"]),
                "schema_version": VISUAL_SCENE_SCHEMA_VERSION,
                "missing_is_fail_open": True,
                "candidate_only": True,
                "raw_frame_included": False,
                "raw_ocr_text_included": False,
                "quarantined_count": len([
                    row for row in visual_scene_rows
                    if str(row.get("status") or "") in {"outside_vod_duration", "time_unmapped"}
                ]),
                "unavailable_count": len([
                    row for row in visual_scene_rows
                    if str(row.get("status") or "") == "sidecar_unreadable"
                ]),
            },
            **({"manager_comment_hints": comment_hint_summary} if include_manager_comment_hints else {}),
            **(
                {"manager_context_shadow": context_shadow_summary}
                if include_manager_context_shadow
                else {}
            ),
            **(
                {
                    "adaptive_context_ranges": {
                        "analysis_state": str(adaptive_context_ranges.get("analysis_state") or "not_analyzed"),
                        "range_count": int(adaptive_context_ranges.get("summary", {}).get("range_count") or 0),
                        "parent_range_count": int(adaptive_context_ranges.get("summary", {}).get("parent_range_count") or 0),
                        "child_range_count": int(adaptive_context_ranges.get("summary", {}).get("child_range_count") or 0),
                        "unknown_gap_count": int(adaptive_context_ranges.get("summary", {}).get("unknown_gap_count") or 0),
                    }
                }
                if adaptive_context_ranges
                else {}
            ),
            "selected_range_count": len(draft.get("selected_ranges") or []),
            "available_lane_keys": [lane["key"] for lane in lanes],
            "role_note": "검토 후보/보조 신호/요약에 반영됨은 관리자 검토용 표시이며 방송 사실 확정이 아닙니다.",
        },
        "labels": {
            "lanes": active_lane_labels,
            "roles": _ROLE_LABELS,
            "actions": _DRAFT_ACTION_LABELS,
        },
    }
    workbench["summary"]["lane_contract"] = validate_signal_timeline_lane_contract(workbench)
    return workbench
