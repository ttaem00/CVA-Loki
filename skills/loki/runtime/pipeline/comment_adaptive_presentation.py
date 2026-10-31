"""Project rewritten VOD-comment labels into weak adaptive UI hints.

The projection is manager-only shadow evidence.  A comment label may suggest
how to present a range, but it never establishes semantic, highlight, ranking,
or learning truth.
"""

from __future__ import annotations

import hashlib

import json

import math

import re

from collections import Counter

from typing import Mapping

from .comment_temporal_hints import (
    CONTEXT_RANGE_SCHEMA_VERSION,
    SCHEMA_VERSION as COMMENT_HINTS_SCHEMA_VERSION,
)

from .presentation_profiles import (
    BROADCAST_PROFILES,
    MODES,
    build_presentation_profile,
)

SCHEMA_VERSION = "comment_adaptive_presentation.v1"

_COLLAB = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"합방",
        r"합동\s*(?:방송|플레이)",
        r"콜라보\s*(?:방송|플레이|시작|합류)",
        r"다인\s*(?:방송|플레이)",
        r"multi[- ]?pov",
        r"(?:^|\s)w[./]\s*",
        r"디코\s*(?:시작|합류|방)",
    )
)

_WATCH_ALONG = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"같이\s*보기",
        r"함께\s*보기",
        r"동시\s*시청",
        r"watch[\s-]?along",
    )
)

_MATCH_WATCH = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:경기|대회|결승|준결승)\s*(?:시청|관전|중계|보기)",
        r"(?:시청|관전|중계)\s*(?:시작|종료)",
        r"(?:콘서트|시상식|쇼케이스|발표회)\s*(?:시작|시청|관람|중계)",
    )
)

_BRACKET = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"월드컵",
        r"브라켓",
        r"토너먼트\s*(?:선택|투표)?",
        r"(?<![0-9a-z])v(?:s|\.)(?![0-9a-z])",
    )
)

_GAME_PHASE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:인게임|로비|매칭|밴픽|보스전|퀘스트|리플레이)",
        r"(?:첫|다음|마지막|\d+)\s*라운드",
        r"\blevel\s*[0-9a-z]+",
        r"\bstage\s*[0-9a-z]+",
    )
)

_GAME = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?<![0-9a-z가-힣])게임(?:\s*(?:시작|종료|준비))?(?![0-9a-z가-힣])",
        r"🎮",
        r"(?:솔로|멀티)\s*플레이",
        r"(?:랭크|솔랭)\s*(?:게임|시작|종료)?",
    )
)

_TALK = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"잡담",
        r"토크",
        r"토론",
        r"(?:이야기|썰)\s*(?:시작|타임)?",
        r"(?:질답|q\s*&\s*a)",
    )
)

_CREATIVE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:그림|드로잉|코딩|작곡|편집)\s*(?:작업|시작|과정|완료)?",
        r"(?:제작|창작)\s*(?:과정|시작|완료)",
    )
)

_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")

_URL = re.compile(r"(?:https?|file|ftp)://", re.IGNORECASE)

_CONTINUATION = re.compile(
    r"^(?:└|├|┌|[-–—]\s)|"
    r"(?:진행|계속|반응|테마곡|에피소드|episode|\bep\s*[0-9.]|\blevel\s*[0-9a-z]+|\bstage\s*[0-9a-z]+)",
    re.IGNORECASE,
)

_CONTEXT_STOP = re.compile(
    r"(?:방송\s*종료|방종|게임\s*종료|같이\s*보기\s*종료|"
    r"시청\s*종료|합방\s*종료|휴식|쉬는\s*시간|브레이크|로딩\s*대기)",
    re.IGNORECASE,
)

_SECTION_HEADER = re.compile(
    r"^[🎮💬🎵🎤📺⚽🏆]|(?:새|다음)\s*(?:코너|순서)",
    re.IGNORECASE,
)

def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

def _matches(label: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(label) for pattern in patterns)

def _safe_label(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_comment_context_label")
    label = value.strip()
    if (
        not label
        or len(label) > 200
        or any(character in label for character in "\r\n\t")
        or _WINDOWS_PATH.match(label)
        or label.startswith(("/", "\\\\"))
        or _URL.search(label)
    ):
        raise ValueError("invalid_comment_context_label")
    return label

def _clock(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_comment_context_clock")
    start = value.get("start_sec")
    end = value.get("end_sec")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
    ):
        raise ValueError("invalid_comment_context_clock")
    return {"start_sec": float(start), "end_sec": float(end)}

def _classify(label: str) -> tuple[str, str, str]:
    collab = _matches(label, _COLLAB)
    watch_along = _matches(label, _WATCH_ALONG)
    match_watch = _matches(label, _MATCH_WATCH)
    bracket = _matches(label, _BRACKET)
    game_phase = _matches(label, _GAME_PHASE)
    game = game_phase or _matches(label, _GAME)
    talk = _matches(label, _TALK)
    creative = _matches(label, _CREATIVE)

    if collab:
        activity = "collaboration"
    elif watch_along:
        activity = "watch_along"
    elif match_watch:
        activity = "match_watching"
    elif bracket:
        activity = "bracket_choice"
    elif sum((game, talk, creative)) == 1:
        activity = (
            "game_play" if game else "talk" if talk else "creative_work"
        )
    else:
        activity = "unknown"

    if bracket:
        content_form = "versus_bracket"
    elif watch_along:
        content_form = "watch_along"
    elif match_watch:
        content_form = "match_watch"
    elif game_phase:
        content_form = "within_game_phase"
    elif game:
        content_form = "game_session"
    elif creative and not talk:
        content_form = "creative_step"
    elif talk and not creative:
        content_form = "topic_discussion"
    elif collab:
        content_form = "collab"
    else:
        content_form = "unknown"

    if collab:
        broadcast_profile = "collab_multi_pov"
    elif watch_along or match_watch or bracket:
        broadcast_profile = "watch_event"
    else:
        possible = [
            profile
            for matched, profile in (
                (game, "game_match"),
                (talk, "talk_story"),
                (creative, "creative_work"),
            )
            if matched
        ]
        broadcast_profile = possible[0] if len(possible) == 1 else "mixed_unknown"

    return activity, content_form, broadcast_profile

def _validated_rows(hints: Mapping[str, object]) -> list[Mapping[str, object]]:
    if not isinstance(hints, Mapping):
        raise ValueError("invalid_comment_temporal_hints")
    if hints.get("schema_version") != COMMENT_HINTS_SCHEMA_VERSION:
        raise ValueError("invalid_comment_temporal_hints")
    expected = hints.get("content_sha256")
    body = {key: value for key, value in hints.items() if key != "content_sha256"}
    if (
        not isinstance(expected, str)
        or hashlib.sha256(_canonical(body)).hexdigest() != expected
    ):
        raise ValueError("invalid_comment_temporal_hints_digest")
    rows = hints.get("context_range_candidates")
    if not isinstance(rows, list):
        raise ValueError("invalid_comment_context_ranges")
    return rows

def _project_row(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_comment_context_range")
    candidate_id = value.get("context_range_candidate_id")
    axis_states = value.get("axis_states")
    if (
        value.get("schema_version") != CONTEXT_RANGE_SCHEMA_VERSION
        or not isinstance(candidate_id, str)
        or not candidate_id.strip()
        or value.get("confidence") != "weak"
        or not isinstance(axis_states, Mapping)
        or set(axis_states) != {"activity", "topic"}
        or axis_states.get("activity") != "unknown"
        or axis_states.get("topic") != "unknown"
    ):
        raise ValueError("invalid_comment_context_range")
    label = _safe_label(value.get("label_hint"))
    activity, content_form, broadcast_profile = _classify(label)
    confidence = "unknown" if broadcast_profile == "mixed_unknown" else "low"
    return {
        "context_range_candidate_id": candidate_id,
        "clock": _clock(value.get("clock")),
        "label": label,
        "activity_hint": activity,
        "content_form_hint": content_form,
        "broadcast_profile_hint": broadcast_profile,
        "confidence_band": confidence,
    }

def _inherit_weak_context(
    row: dict[str, object],
    active: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Carry only a preceding broad header across structural child rows."""

    label = str(row["label"])
    if _CONTEXT_STOP.search(label):
        return {
            **row,
            "activity_hint": "unknown",
            "content_form_hint": "unknown",
            "broadcast_profile_hint": "mixed_unknown",
            "confidence_band": "unknown",
        }, None

    profile = str(row["broadcast_profile_hint"])
    content_form = str(row["content_form_hint"])
    if profile != "mixed_unknown":
        if (
            active
            and active.get("broadcast_profile_hint") == "collab_multi_pov"
            and profile == "game_match"
            and content_form in {"game_session", "within_game_phase"}
        ):
            row = {
                **row,
                "activity_hint": "collaboration",
                "broadcast_profile_hint": "collab_multi_pov",
            }
        return row, row

    if _SECTION_HEADER.search(label):
        return row, None
    if active is None:
        return row, None
    if not _CONTINUATION.search(label):
        return row, active

    inherited_form = str(active["content_form_hint"])
    if inherited_form == "game_session":
        inherited_form = "within_game_phase"
    row = {
        **row,
        "activity_hint": active["activity_hint"],
        "content_form_hint": inherited_form,
        "broadcast_profile_hint": active["broadcast_profile_hint"],
        "confidence_band": "low",
    }
    return row, active

def _project_rows(values: list[Mapping[str, object]]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    active: dict[str, object] | None = None
    for value in values:
        row, active = _inherit_weak_context(_project_row(value), active)
        projected.append(row)
    return projected

def _aggregate(rows: list[dict[str, object]]) -> tuple[str, str]:
    profiles = [
        str(row["broadcast_profile_hint"])
        for row in rows
        if row["broadcast_profile_hint"] != "mixed_unknown"
    ]
    if not profiles:
        return "mixed_unknown", "unknown"
    counts = Counter(profiles)
    if (
        len(counts) != 1
        or len(profiles) < 2
        or len(profiles) * 2 < len(rows)
    ):
        return "mixed_unknown", "low"
    return profiles[0], "medium"

def build_comment_adaptive_presentation(
    hints: Mapping[str, object],
    *,
    mode: str = "editor",
) -> dict[str, object]:
    """Build a deterministic, manager-only adaptive presentation projection."""

    if mode not in MODES:
        raise ValueError("invalid_presentation_mode")
    rows = _project_rows(_validated_rows(hints))
    broadcast_profile, confidence_band = _aggregate(rows)
    if broadcast_profile not in BROADCAST_PROFILES:
        raise AssertionError("closed_broadcast_profile")
    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "broadcast_profile": broadcast_profile,
        "confidence_band": confidence_band,
        "presentation_profile": build_presentation_profile(
            mode=mode,
            broadcast_profile=broadcast_profile,
            confidence_band=confidence_band,
        ),
        "context_range_hints": rows,
        "policy": {
            "manager_only": True,
            "public_payload": False,
            "comment_truth": False,
            "learning": False,
            "ranking": False,
            "default": False,
        },
    }
    result["content_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result
