"""Private anchor packing for viewer timeline comments.

Viewer comments are useful as private navigation hints, but source text and
engagement metadata must not be copied into public report surfaces.  This module
normalizes raw timed comment lines into rewritten anchors and clusters near-time
duplicates. Legacy prompt helpers may sample for their own display budget;
Pipeline 2.5's structured context keeps every meaningful rewritten anchor.
"""

from __future__ import annotations

import hashlib

import json

import math

import re

from dataclasses import dataclass, replace

from typing import Any, Iterable

from .utils import sec_to_hms

CHAPTER_TIMETABLE = "chapter_timetable"

HIGHLIGHT_HINT = "highlight_hint"

DEFAULT_CLUSTER_WINDOW_SEC = 75

TIMELINE_COMMENT_CONTEXT_SCHEMA = "timeline_comment_context.v1"

_TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s*)")

_MENU_ONLY_RE = re.compile(r"^(작성자|메뉴|답글\s*쓰기|댓글\s*쓰기|더보기)$")

_HIGHLIGHT_HINT_RE = re.compile(
    r"(ㅋㅋ|ㅎㅎ|웃김|웃음|개웃|폭소|레전드|하이라이트|클립|명장면|밈|드립|미친|"
    r"highlight|clip|meme|joke|funny|laugh|range)",
    re.IGNORECASE,
)

_LIFECYCLE_WORD_RE = re.compile(
    r"(시작|종료|엔딩|마무리|준비|공개|설명|안내|진행|입장|클리어|도전|보기|둘러보기|"
    r"밴픽|라운드|경기|게임|콘텐츠|컨텐츠|후열|카페|스타|배팅)"
)

_PRIVACY_FLAGS = (
    "raw_comment_excluded",
    "source_metadata_excluded",
    "engagement_counts_excluded",
    "public_rewrite_required",
)

@dataclass(frozen=True)
class TimelineAnchor:
    """A private, rewritten timed anchor derived from viewer comments."""

    tc: str
    sec: int
    label: str
    lane: str = CHAPTER_TIMETABLE
    internal_weight: float = 0.0
    comment_score: float = 0.0
    like_weight: float = 0.0
    aggregate_weight: float = 0.0
    max_weight: float = 0.0
    cluster_size: int = 1
    source_key: str = ""
    privacy_flags: tuple[str, ...] = _PRIVACY_FLAGS

def timecode_to_seconds(value: str) -> int:
    parts = [int(part) for part in str(value or "").strip().split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"invalid timecode: {value!r}")

def classify_comment_lane(
    text: str,
    *,
    timecodes: list[str],
) -> str:
    """Classify a selected viewer comment into the private signal lane."""

    tc_count = len(timecodes)
    if tc_count <= 0:
        return HIGHLIGHT_HINT
    if tc_count <= 4 and _HIGHLIGHT_HINT_RE.search(str(text or "")):
        return HIGHLIGHT_HINT
    if tc_count <= 2:
        timed_lines = [
            line
            for line in str(text or "").splitlines()
            if _TIMECODE_RE.search(line)
        ]
        if any(len(_TIMECODE_RE.findall(line)) >= 2 for line in timed_lines):
            return HIGHLIGHT_HINT
    return CHAPTER_TIMETABLE

def like_weight_from_count(value: Any) -> float:
    try:
        count = max(0, int(value))
    except (TypeError, ValueError):
        return 0.0
    return min(120.0, math.log1p(count) * 18.0)

def rewrite_viewer_anchor_label(label: str) -> str:
    """Rewrite viewer-authored labels into private/public-safe candidates."""

    original = " ".join(str(label or "").split())
    original = original.strip(" .,:;:：-–—~·|")
    if not original:
        return "챕터 후보"
    if _HIGHLIGHT_HINT_RE.search(original):
        return "웃음 반응 후보"

    text = original
    replacements = [
        ("컨텐츠", "콘텐츠"),
        ("카페 시찰", "카페 둘러보기"),
        ("시찰", "둘러보기"),
        ("오늘 스타는 여기까지", "스타크래프트 마무리"),
        ("여기까지", "마무리"),
        ("디스코드 입장", "디스코드 준비"),
        ("입장", "준비"),
        ("설명", "안내"),
        ("후열 게임", "후열 게임 진행"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    text = re.sub(r"[~!！?？ㅋㅎ]+$", "", text).strip()
    text = re.sub(r"\b(진행|구간|안내|마무리|둘러보기)(?:\s+\1)+$", r"\1", text)

    if text == original:
        if text.endswith("시작 구간"):
            pass
        elif text.endswith("시작"):
            text = f"{text} 구간"
        elif text.endswith(("진행", "둘러보기", "안내", "마무리", "공개", "도전", "클리어", "엔딩")):
            pass
        else:
            text = f"{text} 진행"

    if len(text) > 35:
        text = text[:35].rstrip(" .,:;:：-–—~·|")
    return text or "챕터 후보"

def _clean_timed_line_label(line: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", str(line or ""))
    text = _TIMECODE_RE.sub(" ", text)
    text = _LEADING_LIST_MARKER_RE.sub("", text)
    text = re.sub(r"[`*_#>\[\]()]+", " ", text)
    text = re.sub(r"\blane\s*=\s*(chapter_timetable|highlight_hint)\b", " ", text, flags=re.I)
    text = re.sub(r"^[\s:：\-–—~·|]+|[\s:：\-–—~·|]+$", "", text)
    return " ".join(text.split())

def extract_anchors_from_comment_text(
    text: str,
    *,
    lane: str = CHAPTER_TIMETABLE,
    comment_score: float = 0.0,
    like_count: Any = None,
    reply_count: Any = None,
    source_key: str = "",
    max_line_chars: int = 220,
) -> list[TimelineAnchor]:
    """Extract rewritten private anchors from a selected comment body."""

    body = str(text or "")
    like_weight = like_weight_from_count(like_count)
    reply_weight = 0.0
    try:
        reply_weight = min(30.0, math.log1p(max(0, int(reply_count))) * 5.0)
    except (TypeError, ValueError):
        reply_weight = 0.0

    anchors: list[TimelineAnchor] = []
    for raw_line in body.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line or _MENU_ONLY_RE.fullmatch(line):
            continue
        matches = list(_TIMECODE_RE.finditer(line))
        if not matches:
            continue
        if len(line) > max_line_chars:
            line = line[:max_line_chars].rstrip()
        raw_label = _clean_timed_line_label(line)
        label = rewrite_viewer_anchor_label(raw_label)
        for match in matches:
            try:
                sec = timecode_to_seconds(match.group(1))
            except ValueError:
                continue
            tc = sec_to_hms(sec)
            internal_weight = float(comment_score or 0.0) + like_weight + reply_weight
            anchors.append(
                TimelineAnchor(
                    tc=tc,
                    sec=sec,
                    label=label,
                    lane=lane if lane in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT} else CHAPTER_TIMETABLE,
                    internal_weight=round(internal_weight, 3),
                    comment_score=round(float(comment_score or 0.0), 3),
                    like_weight=round(like_weight, 3),
                    aggregate_weight=round(internal_weight, 3),
                    max_weight=round(internal_weight, 3),
                    source_key=str(source_key or ""),
                )
            )
    return anchors

def _label_quality(anchor: TimelineAnchor, preferred_lane: str) -> tuple[int, int, int, int, float]:
    label = anchor.label or ""
    generic = 1 if label in {"챕터 후보", "장면 후보", "웃음 반응 후보"} else 0
    lifecycle = 0 if _LIFECYCLE_WORD_RE.search(label) else 1
    lane_penalty = 0 if anchor.lane == preferred_lane else 1
    start_penalty = 1 if label.endswith(("시작", "시작 구간")) else 0
    length_penalty = abs(len(label) - 10)
    return (lane_penalty, generic, start_penalty, lifecycle + length_penalty, -float(anchor.internal_weight or 0.0))

def _best_cluster_anchor(cluster: list[TimelineAnchor]) -> TimelineAnchor:
    preferred_lane = (
        CHAPTER_TIMETABLE
        if any(anchor.lane == CHAPTER_TIMETABLE for anchor in cluster)
        else HIGHLIGHT_HINT
    )
    best = sorted(cluster, key=lambda anchor: _label_quality(anchor, preferred_lane))[0]
    aggregate_weight = sum(float(anchor.internal_weight or 0.0) for anchor in cluster)
    max_weight = max(float(anchor.internal_weight or 0.0) for anchor in cluster)
    flags = tuple(sorted({flag for anchor in cluster for flag in anchor.privacy_flags} | {"clustered_near_time"}))
    return replace(
        best,
        lane=preferred_lane,
        aggregate_weight=round(aggregate_weight, 3),
        max_weight=round(max_weight, 3),
        cluster_size=len(cluster),
        privacy_flags=flags,
    )

def cluster_timeline_anchors(
    anchors: Iterable[TimelineAnchor],
    *,
    window_sec: int = DEFAULT_CLUSTER_WINDOW_SEC,
) -> list[TimelineAnchor]:
    """Cluster near-duplicate anchors across selected comments/replies."""

    rows = sorted(list(anchors), key=lambda anchor: (anchor.sec, -anchor.internal_weight, anchor.label))
    if not rows:
        return []
    clusters: list[list[TimelineAnchor]] = []
    current: list[TimelineAnchor] = []
    cluster_start = rows[0].sec
    for anchor in rows:
        if current and (
            anchor.sec - cluster_start > max(1, int(window_sec))
            or not _can_join_anchor_cluster(current, anchor)
        ):
            clusters.append(current)
            current = []
            cluster_start = anchor.sec
        current.append(anchor)
    if current:
        clusters.append(current)
    packed = [_best_cluster_anchor(cluster) for cluster in clusters]
    packed.sort(key=lambda anchor: (anchor.sec, anchor.label))
    return packed

def build_timeline_comment_context(
    anchors: Iterable[TimelineAnchor | dict[str, Any]],
    *,
    source_status: str = "",
    selected_comment_count: int = 0,
    source_kind: str = "chzzk",
    duration_sec: int | None = None,
) -> dict[str, Any]:
    """Build the complete raw-free timetable context used by Pipeline 2.5.

    This contract deliberately has no item-count cap.  A long, meaningful
    timetable is distributed across the already-existing chronological chunk
    calls instead of being sampled down.  Only invalid/out-of-range rows and
    semantic near-duplicates are removed.
    """

    normalized: list[TimelineAnchor] = []
    invalid_count = 0
    for raw in anchors:
        if isinstance(raw, TimelineAnchor):
            lane = str(raw.lane or CHAPTER_TIMETABLE)
            if lane not in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT}:
                lane = CHAPTER_TIMETABLE
            anchor = replace(
                raw,
                label=rewrite_viewer_anchor_label(str(raw.label or "")),
                lane=lane,
            )
        elif isinstance(raw, dict):
            try:
                sec = int(raw.get("sec", raw.get("time_sec", -1)))
            except (TypeError, ValueError):
                invalid_count += 1
                continue
            label = rewrite_viewer_anchor_label(str(raw.get("label") or ""))
            lane = str(raw.get("lane") or CHAPTER_TIMETABLE)
            if lane not in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT}:
                lane = CHAPTER_TIMETABLE
            anchor = TimelineAnchor(
                tc=sec_to_hms(max(0, sec)),
                sec=sec,
                label=label,
                lane=lane,
                source_key=str(raw.get("source_key") or ""),
            )
        elif hasattr(raw, "sec"):
            try:
                sec = int(getattr(raw, "sec"))
            except (TypeError, ValueError):
                invalid_count += 1
                continue
            lane = str(getattr(raw, "lane", CHAPTER_TIMETABLE) or CHAPTER_TIMETABLE)
            if lane not in {CHAPTER_TIMETABLE, HIGHLIGHT_HINT}:
                lane = CHAPTER_TIMETABLE
            anchor = TimelineAnchor(
                tc=sec_to_hms(max(0, sec)),
                sec=sec,
                label=rewrite_viewer_anchor_label(
                    str(getattr(raw, "label", "") or "")
                ),
                lane=lane,
                source_key=str(getattr(raw, "source_key", "") or ""),
            )
        else:
            invalid_count += 1
            continue
        if anchor.sec < 0 or (duration_sec is not None and anchor.sec > int(duration_sec)):
            invalid_count += 1
            continue
        if not str(anchor.label or "").strip():
            invalid_count += 1
            continue
        normalized.append(anchor)

    clustered = cluster_timeline_anchors(normalized)
    rows = [
        {
            "time_sec": int(anchor.sec),
            "timecode": sec_to_hms(int(anchor.sec)),
            "label": str(anchor.label),
            "lane": str(anchor.lane),
        }
        for anchor in clustered
    ]
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    chapter_count = sum(row["lane"] == CHAPTER_TIMETABLE for row in rows)
    highlight_count = sum(row["lane"] == HIGHLIGHT_HINT for row in rows)
    return {
        "schema_version": TIMELINE_COMMENT_CONTEXT_SCHEMA,
        "source_kind": str(source_kind or "unknown"),
        "source_status": str(source_status or "unknown"),
        "selected_comment_count": max(0, int(selected_comment_count or 0)),
        "source_anchor_count": len(normalized),
        "used_anchor_count": len(rows),
        "chapter_anchor_count": chapter_count,
        "highlight_anchor_count": highlight_count,
        "invalid_anchor_count": invalid_count,
        "deduplicated_anchor_count": max(0, len(normalized) - len(rows)),
        "anchor_sha256": hashlib.sha256(encoded).hexdigest(),
        "privacy": {
            "raw_comment_included": False,
            "author_included": False,
            "engagement_counts_included": False,
            "comment_id_included": False,
            "labels_rewritten": True,
        },
        "anchors": rows,
    }

def _can_join_anchor_cluster(cluster: list[TimelineAnchor], anchor: TimelineAnchor) -> bool:
    """Avoid collapsing close sequential anchors from the same timetable."""

    anchor_source = anchor.source_key or ""
    for existing in cluster:
        existing_source = existing.source_key or ""
        if existing_source and anchor_source and existing_source != anchor_source:
            return True
        if _normalized_label(existing.label) == _normalized_label(anchor.label):
            return True
    return False

def _normalized_label(label: str) -> str:
    text = re.sub(r"\s+", "", str(label or "")).casefold()
    text = re.sub(r"(시작구간|시작|진행|구간)$", "", text)
    return text
