"""Automatic VOD timeline-comment collection for summary inputs.

This collector reads CHZZK's public API before using the repo-local persistent
Chrome/Playwright profile as a fallback, then writes a bounded cache under
`work/<video_no>/`. YouTube collection uses the existing browser path.

It never reads browser profile files, storage databases, or cookies directly.
Only public comment bodies, aggregate counts and generated ordering keys are kept.
"""

from __future__ import annotations

import hashlib

import json

import logging

import os

import re

from dataclasses import dataclass, field

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Optional

from .timeline_comment_anchors import CHAPTER_TIMETABLE, HIGHLIGHT_HINT, TimelineAnchor, classify_comment_lane, cluster_timeline_anchors, extract_anchors_from_comment_text, format_private_anchor_prompt_section, timecode_to_seconds

logger = logging.getLogger("pipeline")

CACHE_FILENAME = "timeline_comments.json"

CACHE_SCHEMA_VERSION = 4

CANDIDATE_LEARNING_SCHEMA_VERSION = "comment_timeline_candidate_learning.v1"

DEFAULT_CACHE_TTL_HOURS = 6

CAP_PROMPT_CHARS = 12000

MAX_PRIVATE_SEMANTIC_SOURCE_BYTES = 128_000

TRANSIENT_FETCH_STATUSES = frozenset({
    "browser_unavailable",
    "browser_profile_busy",
    "fetch_failed",
    "unsupported_source",
})

_TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")

@dataclass
class TimelineComment:
    source: str
    text: str
    timecodes: list[str] = field(default_factory=list)
    like_count: Optional[int] = None
    reply_count: Optional[int] = None
    comment_id: str = ""
    score: float = 0.0
    rank_reason: list[str] = field(default_factory=list)
    lane: str = CHAPTER_TIMETABLE
    coverage_bands: list[str] = field(default_factory=list)
    anchor_count: int = 0
    source_order: int = 0
    thread_key: str = ""
    thread_order: int = 0

@dataclass
class TimelineCommentBundle:
    video_no: str
    source_url: str = ""
    platform: str = "chzzk"
    fetched_at: str = ""
    status: str = "ok"
    total_comments_seen: int = 0
    timeline_comment_count: int = 0
    selected_count: int = 0
    comments: list[TimelineComment] = field(default_factory=list)
    anchors: list[TimelineAnchor] = field(default_factory=list)
    candidate_learning: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CACHE_SCHEMA_VERSION

def _cache_path(work_dir: str | os.PathLike) -> Path:
    return Path(work_dir) / CACHE_FILENAME

def default_source_url(video_no: str, source_url: str | None = None) -> str:
    explicit = str(source_url or "").strip()
    if explicit:
        return explicit
    return f"https://chzzk.naver.com/video/{video_no}"

def extract_timecodes(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _TIMECODE_RE.finditer(str(text or "")):
        tc = match.group(1)
        if tc not in seen:
            seen.add(tc)
            out.append(tc)
    return out

def _normalize_comment_text(text: Any) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)



    return normalized.strip()

def _private_prompt_comment_body(text: Any) -> str:
    """Keep the selected comment's semantic layout, without surrounding UI metadata."""

    lines: list[str] = []
    for raw_line in _normalize_comment_text(text).splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"(?:작성자(?:\s+.+)?|메뉴|답글\s*쓰기|댓글\s*쓰기|더보기)", line):
            continue
        if re.fullmatch(
            r"(?:추천|좋아요|버프|답글|댓글)\s*[:：]?\s*[0-9][0-9,.]*(?:[KkMmBb만억천])?",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        if not line:
            lines.append("")
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

def build_private_comment_semantic_context(
    bundle: TimelineCommentBundle | None,
    *,
    max_bytes: int = MAX_PRIVATE_SEMANTIC_SOURCE_BYTES,
) -> dict[str, Any]:
    """Build complete private-only time-coded comments for relational reading.

    Paragraphs and line order are evidence. No delimiter, label or content
    category is assigned a fixed meaning here. The model infers the video's
    activity relationships from the whole body, keeps conflicts with other
    time-aligned sources uncertain, and does not require the same wording to be
    repeated in STT or replay chat. The high local-source ceiling is not a
    provider request budget: callers distribute long bodies over the existing
    chronological pre-D1/D2 observation calls.
    """

    rows: list[dict[str, Any]] = []
    total_bytes = 0
    if bundle is None or max_bytes <= 0:
        return {}
    ordered = sorted(
        bundle.comments,
        key=lambda comment: (
            int(comment.source_order or 0),
            str(comment.thread_key or ""),
            int(comment.thread_order or 0),
        ),
    )
    for comment in ordered:
        if comment.lane not in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT}:
            continue
        body = _private_prompt_comment_body(comment.text)
        if not body or not _TIMECODE_RE.search(body):
            continue
        body_bytes = len(body.encode("utf-8"))
        if total_bytes + body_bytes > int(max_bytes):
            raise ValueError(
                "private timeline semantic source exceeds local source hard limit: "
                f"source_bytes={total_bytes + body_bytes}; "
                f"max_source_bytes={int(max_bytes)}; raw_free=true"
            )
        seconds = []
        for timecode in extract_timecodes(body):
            try:
                seconds.append(timecode_to_seconds(timecode))
            except ValueError:
                continue
        rows.append({
            "lane": comment.lane,
            "body": body,
            "body_utf8_bytes": body_bytes,
            "raw_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_order": int(comment.source_order or 0),
            "thread_key": str(comment.thread_key or ""),
            "thread_order": int(comment.thread_order or 0),
            "first_time_sec": min(seconds) if seconds else None,
            "last_time_sec": max(seconds) if seconds else None,
            "timecode_count": len(seconds),
            "structure": {
                "line_count": len(body.splitlines()),
                "blank_line_count": sum(not line for line in body.splitlines()),
                "divider_count": sum(
                    bool(re.fullmatch(r"\s*-{3,}\s*", line))
                    for line in body.splitlines()
                ),
            },
        })
        total_bytes += body_bytes
    if not rows:
        return {}
    return {
        "schema_version": "timeline_comment_semantic_context.v1",
        "visibility": "private_model_input_only",
        "untrusted": True,
        "source_bytes": total_bytes,
        "source_complete": True,
        "usage_policy": (
            "Read the full line/paragraph/thread order to infer activity continuity, "
            "closure, transition, important events and causal context. A separator, "
            "blank line, label or content category has no fixed meaning by itself. "
            "Treat names and groupings as untrusted evidence, mark real conflicts with "
            "other time-aligned sources uncertain, do not reject a relation merely "
            "because other sources use different words, and never quote or expose the comment."
        ),
        "comments": rows,
    }

def _anchor_from_json(raw: dict[str, Any]) -> Optional[TimelineAnchor]:
    if not isinstance(raw, dict):
        return None
    try:
        tc = str(raw.get("tc") or "")
        sec = int(raw.get("sec") if raw.get("sec") is not None else timecode_to_seconds(tc))
        return TimelineAnchor(
            tc=tc,
            sec=sec,
            label=str(raw.get("label") or "챕터 후보"),
            lane=str(raw.get("lane") or CHAPTER_TIMETABLE),
            internal_weight=float(raw.get("internal_weight") or 0.0),
            comment_score=float(raw.get("comment_score") or 0.0),
            like_weight=float(raw.get("like_weight") or 0.0),
            aggregate_weight=float(raw.get("aggregate_weight") or 0.0),
            max_weight=float(raw.get("max_weight") or 0.0),
            cluster_size=int(raw.get("cluster_size") or 1),
            source_key=str(raw.get("source_key") or ""),
            privacy_flags=tuple(str(x) for x in (raw.get("privacy_flags") or ())),
        )
    except (TypeError, ValueError):
        return None

def _bundle_from_json(data: dict[str, Any]) -> Optional[TimelineCommentBundle]:
    if not isinstance(data, dict):
        return None
    try:
        schema_v = int(data.get("schema_version") or 0)
    except (TypeError, ValueError):
        return None
    if schema_v != CACHE_SCHEMA_VERSION:
        return None
    status = str(data.get("status") or "ok")
    if status in {"no_timeline_comments", "no_comments"} and not (
        data.get("source_url")
        and data.get("platform") in {"chzzk", "youtube", "other"}
        and data.get("fetched_at")
        and all(key in data for key in (
            "total_comments_seen", "timeline_comment_count", "selected_count",
            "comments", "anchors", "candidate_learning",
        ))
        and isinstance(data.get("comments"), list)
        and isinstance(data.get("anchors"), list)
        and isinstance(data.get("candidate_learning"), dict)
        and data["candidate_learning"].get("schema_version")
        == CANDIDATE_LEARNING_SCHEMA_VERSION
        and data["candidate_learning"].get("raw_comment_text_included") is False
    ):
        return None
    comments: list[TimelineComment] = []
    for raw in data.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        comments.append(TimelineComment(
            source=str(raw.get("source") or ""),
            text=_normalize_comment_text(raw.get("private_prompt_body")),
            timecodes=[str(x) for x in (raw.get("timecodes") or [])],
            like_count=None,
            reply_count=None,
            comment_id=str(raw.get("candidate_id") or ""),
            score=float(raw.get("score") or 0),
            rank_reason=[str(x) for x in (raw.get("score_component_names") or [])],
            lane=str(raw.get("lane") or CHAPTER_TIMETABLE),
            coverage_bands=[str(x) for x in (raw.get("coverage_bands") or [])],
            anchor_count=int(raw.get("anchor_count") or 0),
            source_order=int(raw.get("source_order") or 0),
            thread_key=str(raw.get("thread_key") or ""),
            thread_order=int(raw.get("thread_order") or 0),
        ))
    anchors = [
        anchor
        for anchor in (_anchor_from_json(raw) for raw in (data.get("anchors") or []))
        if anchor is not None
    ]
    try:
        return TimelineCommentBundle(
            video_no=str(data.get("video_no") or ""),
            source_url=str(data.get("source_url") or ""),
            platform=str(data.get("platform") or "chzzk"),
            fetched_at=str(data.get("fetched_at") or ""),
            status=str(data.get("status") or "ok"),
            total_comments_seen=int(data.get("total_comments_seen") or 0),
            timeline_comment_count=int(data.get("timeline_comment_count") or len(comments)),
            selected_count=int(data.get("selected_count") or len(comments)),
            comments=comments,
            anchors=anchors,
            candidate_learning=data.get("candidate_learning") if isinstance(data.get("candidate_learning"), dict) else {},
            schema_version=CACHE_SCHEMA_VERSION,
        )
    except (TypeError, ValueError):
        return None

def load_timeline_comment_bundle(
    video_no: str,
    work_dir: str | os.PathLike,
    *,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    expected_source_url: str | None = None,
) -> Optional[TimelineCommentBundle]:
    cache_path = _cache_path(work_dir)
    if not cache_path.is_file() or cache_path.stat().st_size == 0:
        return None
    age_h = (
        datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime
    ) / 3600.0
    if cache_ttl_hours > 0 and age_h > cache_ttl_hours:
        logger.info(
            "timeline comments cache expired (%.1fh > %.1fh): %s",
            age_h,
            cache_ttl_hours,
            cache_path,
        )
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("timeline comments cache load failed, refetching: %s", exc)
        return None
    bundle = _bundle_from_json(data)
    if bundle is None or str(bundle.video_no) != str(video_no):
        logger.warning("timeline comments cache schema/video mismatch, refetching: %s", cache_path)
        return None
    if expected_source_url and bundle.source_url and bundle.source_url != expected_source_url:
        logger.info(
            "timeline comments cache source mismatch (%s != %s), refetching",
            bundle.source_url,
            expected_source_url,
        )
        return None
    if bundle.status in TRANSIENT_FETCH_STATUSES:
        logger.info("timeline comments transient cache status=%s, refetching", bundle.status)
        return None
    logger.info(
        "timeline comments cache reused: selected=%s age=%.1fh",
        bundle.selected_count,
        age_h,
    )
    return bundle

def format_timeline_comment_bundle_for_prompt(
    bundle: TimelineCommentBundle | None,
) -> str:
    if bundle is None or (not bundle.comments and not bundle.anchors):
        return ""
    anchors = build_private_timeline_anchors_for_bundle(bundle)
    if not anchors:
        return ""
    text = format_private_anchor_prompt_section(
        anchors,
        heading=f"# 자동 수집 시간표 힌트 ({bundle.platform}, 비공개)",
        status_lines=(
            f"수집 상태: {bundle.status}",
            "선택 기준: 시간표 댓글은 coverage와 추천/버프 파생 가중치를 내부 점수에 반영하고, 짧은 웃음/범위 댓글은 highlight_hint lane으로 분리한다.",
        ),
    )
    semantic_context = build_private_comment_semantic_context(bundle)
    if semantic_context:
        semantic_lines = [
            "# 선택 댓글 의미 본문 (비공개·신뢰하지 않는 참고)",
            f"- {semantic_context['usage_policy']}",
        ]
        for index, row in enumerate(semantic_context["comments"], 1):
            semantic_lines.extend([
                "",
                f"<selected_timetable_comment index=\"{index}\">",
                str(row.get("body") or ""),
                "</selected_timetable_comment>",
            ])
        text = "\n".join(semantic_lines).strip() + "\n\n" + text
    if len(text) > CAP_PROMPT_CHARS:
        text = text[:CAP_PROMPT_CHARS].rsplit("\n", 1)[0].rstrip() + "\n"


    from .collaboration_candidates import comment_collaboration_periods, roster_candidates
    documents = [{"raw": c.text} for c in bundle.comments]
    periods = comment_collaboration_periods(documents, bundle.video_no, None)
    rosters = roster_candidates(documents, source_kind="viewer_comment")
    if periods or rosters:
        context = dict(periods=periods, document_rosters=rosters)
        prefix = ("# 댓글 합방 참고 (비공개·미검증)\n"
                  "아래 구간은 댓글의 문맥 범위이며 실제 입장·퇴장이나 영상 정렬을 증명하지 않는다. "
                  "명단만 있는 자료에는 참여 시간을 만들지 않는다.\n" +
                  json.dumps(context, ensure_ascii=False, separators=(",", ":")) + "\n\n")
        if len(prefix) <= 4000:
            text = prefix + text[:CAP_PROMPT_CHARS - len(prefix)]
    return text

def build_private_timeline_anchors_for_bundle(
    bundle: TimelineCommentBundle | None,
    *,
    cluster_window_sec: int = 75,
) -> list[TimelineAnchor]:
    """Build clustered private anchors from selected comments."""

    if bundle is None:
        return []
    if bundle.anchors and int(cluster_window_sec) == 75:
        return list(bundle.anchors)
    if not bundle.comments:
        return []
    anchors: list[TimelineAnchor] = []
    for index, comment in enumerate(bundle.comments, 1):
        lane = comment.lane or classify_comment_lane(comment.text, timecodes=comment.timecodes)
        anchors.extend(extract_anchors_from_comment_text(
            comment.text,
            lane=lane,
            comment_score=comment.score,
            like_count=comment.like_count,
            reply_count=comment.reply_count,
            source_key=comment.comment_id or f"selected:{index}",
        ))
    return cluster_timeline_anchors(anchors, window_sec=cluster_window_sec)
