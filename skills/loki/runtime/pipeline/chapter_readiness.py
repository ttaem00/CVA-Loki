"""Rule-based YouTube chapter readiness checks.

This validator does not upload or publish anything. It only checks whether
parsed summary/chapter candidates are ready to be copied into YouTube chapter
text according to deterministic formatting rules.
"""

from __future__ import annotations

from collections import Counter

from typing import Any

SCHEMA_VERSION = "youtube-chapter-readiness/1"

MIN_CHAPTER_COUNT = 3

MIN_CHAPTER_DURATION_SEC = 10

def _parse_timestamp(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) == 2:
        hh = 0
        mm, ss = parts
    elif len(parts) == 3:
        hh, mm, ss = parts
    else:
        return None
    try:
        nums = [int(hh), int(mm), int(ss)]
    except ValueError:
        return None
    if nums[0] < 0 or not (0 <= nums[1] <= 59) or not (0 <= nums[2] <= 59):
        return None
    return nums[0] * 3600 + nums[1] * 60 + nums[2]

def _start_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if "~" in text:
        text = text.split("~", 1)[0].strip()
    return text.strip("[] ")

def _normalize_title(value: object) -> str:
    return " ".join(str(value or "").strip().split())

def _candidate_source_and_items(sections: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    chapter_items = sections.get("chzzk_comment_timeline") or []
    if chapter_items:
        return "chzzk_comment_timeline", list(chapter_items)
    return "timeline", list(sections.get("timeline") or [])

def _failure(
    code: str,
    message: str,
    *,
    chapter_index: int | None = None,
    timestamp: str = "",
    title: str = "",
    segment_id: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if chapter_index is not None:
        out["chapter_index"] = chapter_index
    if timestamp:
        out["timestamp"] = timestamp
    if title:
        out["title"] = title
    if segment_id:
        out["segment_id"] = segment_id
    return out

def validate_youtube_chapter_readiness(
    sections: dict[str, Any],
    *,
    duration_sec: int | float | None = None,
) -> dict[str, Any]:
    """Return structured pass/fail readiness for parsed summary sections.

    Rules:
    - first timestamp must be 00:00
    - at least 3 chapters
    - each chapter must be at least 10 seconds long
    - timestamps must be strictly ascending
    - titles must be non-empty after whitespace normalization
    - normalized duplicate titles fail

    If future segment/evidence ledger data adds ``segment_id`` to parsed chapter
    or timeline rows, the validator preserves it on chapters and failures.
    """

    source, raw_items = _candidate_source_and_items(sections or {})
    chapters: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, item in enumerate(raw_items, start=1):
        raw_tc = _start_timestamp(item.get("tc") or item.get("tc_range") or item.get("timestamp"))
        title = _normalize_title(item.get("title"))
        segment_id = str(item.get("segment_id") or "").strip()
        start_sec = _parse_timestamp(raw_tc)
        chapter: dict[str, Any] = {
            "index": index,
            "timestamp": raw_tc,
            "start_sec": start_sec,
            "title": title,
        }
        if segment_id:
            chapter["segment_id"] = segment_id
        chapters.append(chapter)

        if start_sec is None:
            failures.append(
                _failure(
                    "invalid_timestamp",
                    f"Chapter {index} has an invalid timestamp: {raw_tc or '(empty)'}.",
                    chapter_index=index,
                    timestamp=raw_tc,
                    title=title,
                    segment_id=segment_id,
                )
            )
        if not title:
            failures.append(
                _failure(
                    "empty_title",
                    f"Chapter {index} has an empty title.",
                    chapter_index=index,
                    timestamp=raw_tc,
                    segment_id=segment_id,
                )
            )

    if len(chapters) < MIN_CHAPTER_COUNT:
        failures.append(
            _failure(
                "too_few_chapters",
                f"YouTube chapters need at least {MIN_CHAPTER_COUNT} entries; found {len(chapters)}.",
            )
        )

    valid_starts = [c["start_sec"] for c in chapters if isinstance(c.get("start_sec"), int)]
    if chapters:
        first = chapters[0]
        if first.get("start_sec") != 0:
            failures.append(
                _failure(
                    "first_timestamp_not_zero",
                    "The first chapter must start at 00:00.",
                    chapter_index=1,
                    timestamp=str(first.get("timestamp") or ""),
                    title=str(first.get("title") or ""),
                    segment_id=str(first.get("segment_id") or ""),
                )
            )

    for prev, current in zip(chapters, chapters[1:]):
        prev_sec = prev.get("start_sec")
        current_sec = current.get("start_sec")
        if not isinstance(prev_sec, int) or not isinstance(current_sec, int):
            continue
        if current_sec <= prev_sec:
            failures.append(
                _failure(
                    "timestamps_not_ascending",
                    (
                        f"Chapter {current['index']} starts at {current['timestamp']}, "
                        f"which is not after chapter {prev['index']} at {prev['timestamp']}."
                    ),
                    chapter_index=int(current["index"]),
                    timestamp=str(current.get("timestamp") or ""),
                    title=str(current.get("title") or ""),
                    segment_id=str(current.get("segment_id") or ""),
                )
            )

    if len(valid_starts) == len(chapters):
        end_points = [c["start_sec"] for c in chapters[1:]]
        if duration_sec is not None:
            try:
                duration_int = int(float(duration_sec))
            except (TypeError, ValueError):
                duration_int = 0
            if duration_int > 0:
                end_points.append(duration_int)
        for chapter, end_sec in zip(chapters, end_points):
            start_sec = chapter.get("start_sec")
            if not isinstance(start_sec, int):
                continue
            chapter_duration = int(end_sec) - start_sec
            chapter["duration_sec"] = chapter_duration
            chapter["end_sec"] = int(end_sec)
            if chapter_duration < MIN_CHAPTER_DURATION_SEC:
                failures.append(
                    _failure(
                        "chapter_too_short",
                        (
                            f"Chapter {chapter['index']} is {chapter_duration}s long; "
                            f"minimum is {MIN_CHAPTER_DURATION_SEC}s."
                        ),
                        chapter_index=int(chapter["index"]),
                        timestamp=str(chapter.get("timestamp") or ""),
                        title=str(chapter.get("title") or ""),
                        segment_id=str(chapter.get("segment_id") or ""),
                    )
                )

    title_counts = Counter(c["title"].casefold() for c in chapters if c.get("title"))
    duplicate_titles = {title for title, count in title_counts.items() if count > 1}
    for chapter in chapters:
        title = chapter.get("title") or ""
        if title and title.casefold() in duplicate_titles:
            failures.append(
                _failure(
                    "duplicate_title",
                    f"Chapter title is duplicated after whitespace normalization: {title}.",
                    chapter_index=int(chapter["index"]),
                    timestamp=str(chapter.get("timestamp") or ""),
                    title=str(title),
                    segment_id=str(chapter.get("segment_id") or ""),
                )
            )

    passed = not failures
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "passed": passed,
        "status": "pass" if passed else "fail",
        "chapter_count": len(chapters),
        "rules": {
            "first_timestamp": "00:00",
            "min_chapters": MIN_CHAPTER_COUNT,
            "min_chapter_duration_sec": MIN_CHAPTER_DURATION_SEC,
            "timestamps": "strictly_ascending",
            "titles": "non_empty_unique_after_whitespace_normalization",
        },
        "chapters": chapters,
        "failures": failures,
    }
