"""Executable ``context_structure.v1`` identity contract.

This module is intentionally pure and stdlib-only. It defines identities,
clocks, selection references, and honest analysis states; it does not import or
change any product producer, ranker, renderer, or persistence path.
"""

from __future__ import annotations

import math

import re

from dataclasses import dataclass, field

from enum import Enum

from typing import Any, Iterable, Mapping, TypeVar

SCHEMA_VERSION = "context_structure.v1"

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

class ContextStructureContractError(ValueError):
    """Contract validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

class _TypedId(str):
    def __new__(cls, value: str) -> "_TypedId":
        if not isinstance(value, str) or not value.strip():
            raise ContextStructureContractError("invalid_id", f"{cls.__name__} must be a non-empty string")
        normalized = value.strip()
        if not _OPAQUE_ID_PATTERN.fullmatch(normalized):
            raise ContextStructureContractError(
                "invalid_id",
                f"{cls.__name__} must be an opaque public-safe token",
            )
        return str.__new__(cls, normalized)

class ContextRangeId(_TypedId):
    """Identity of a semantic context range."""

class ContextCandidateId(_TypedId):
    """Identity of one internal context candidate before projection."""

class ExactMomentId(_TypedId):
    """Identity of a bounded exact moment."""

class HighlightStoryWindowId(_TypedId):
    """Identity of a highlight Story Window."""

class LegacySelectedDraftRangeId(_TypedId):
    """Identity of the existing manager-selected draft range."""

def _validate_span(start_sec: float, end_sec: float, *, allow_zero: bool, zero_code: str) -> None:
    for field_name, value in (("start_sec", start_sec), ("end_sec", end_sec)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ContextStructureContractError("invalid_clock", f"{field_name} must be a finite number")
        if value < 0:
            raise ContextStructureContractError("invalid_clock", f"{field_name} must be non-negative")
    if end_sec < start_sec:
        raise ContextStructureContractError("invalid_clock", "end_sec must not precede start_sec")
    if not allow_zero and end_sec == start_sec:
        raise ContextStructureContractError(zero_code, "start_sec and end_sec must describe a positive duration")

@dataclass(frozen=True, slots=True)
class ContextRangeClock:
    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:
        _validate_span(self.start_sec, self.end_sec, allow_zero=False, zero_code="invalid_context_range")

@dataclass(frozen=True, slots=True)
class ExactMomentClock:
    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:
        _validate_span(
            self.start_sec,
            self.end_sec,
            allow_zero=False,
            zero_code="invalid_zero_duration_moment",
        )

@dataclass(frozen=True, slots=True)
class HighlightStoryWindowClock:
    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:
        _validate_span(self.start_sec, self.end_sec, allow_zero=False, zero_code="invalid_story_window")

@dataclass(frozen=True, slots=True)
class LegacySelectedDraftRangeClock:
    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:


        _validate_span(self.start_sec, self.end_sec, allow_zero=True, zero_code="")

class ContextAxis(str, Enum):
    ACTIVITY = "activity"
    TOPIC = "topic"

class ContextStructureState(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    ANALYZING = "analyzing"
    CANDIDATE_RANGES = "candidate_ranges"
    CONFIRMED_RANGES = "confirmed_ranges"
    NO_USEFUL_CONTEXT_RANGES = "no_useful_context_ranges"
    PARTIAL_SIGNAL_COVERAGE = "partial_signal_coverage"
    ANALYSIS_FAILED = "analysis_failed"
    DISABLED = "disabled"
    INVALID_ZERO_DURATION_MOMENT = "invalid_zero_duration_moment"

class CandidateDispositionState(str, Enum):
    MERGED_INTO = "merged_into"
    DROPPED_REASON = "dropped_reason"
    UNKNOWN = "unknown"

class ConfidenceBand(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class SemanticTitleProvenanceState(str, Enum):
    EVIDENCE_BASED = "evidence_based"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"

_GENERIC_ONLY_TITLES = frozenset(
    {
        "합방",
        "게임",
        "게임 안 단계",
        "토크",
        "같이보기",
        "맥락 구간 후보",
        "자동 구간",
    }
)

_GENERIC_TITLE_SUFFIXES = ("구간", "단계", "후보", "장면")

_FALLBACK_TITLES = {
    "insufficient_evidence": "제목 확인 필요",
    "activity_only": "활동은 확인됐지만 주제는 모름",
    "partial_evidence": "근거 일부만 있음",
}

def _require_type(value: Any, expected: type[Any], field_name: str) -> None:
    if type(value) is not expected:
        raise ContextStructureContractError(
            "identity_type_mismatch",
            f"{field_name} must be {expected.__name__}",
        )

def deterministic_fallback_title(reason: str) -> str:
    try:
        return _FALLBACK_TITLES[reason]
    except (KeyError, TypeError) as exc:
        raise ContextStructureContractError(
            "invalid_title_fallback",
            "fallback reason must use the closed deterministic vocabulary",
        ) from exc

@dataclass(frozen=True, slots=True)
class ContextRange:
    context_range_id: ContextRangeId
    clock: ContextRangeClock
    activity: str | None = None
    topic: str | None = None
    parent_context_range_id: ContextRangeId | None = None
    child_context_range_ids: tuple[ContextRangeId, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_type(self.context_range_id, ContextRangeId, "context_range_id")
        _require_type(self.clock, ContextRangeClock, "clock")
        _validate_axis_label(self.activity, "activity")
        _validate_axis_label(self.topic, "topic")
        if self.parent_context_range_id is not None:
            _require_type(
                self.parent_context_range_id,
                ContextRangeId,
                "parent_context_range_id",
            )
        if not isinstance(self.child_context_range_ids, tuple):
            raise ContextStructureContractError(
                "invalid_context_hierarchy",
                "child_context_range_ids must be a tuple",
            )
        for child_id in self.child_context_range_ids:
            _require_type(child_id, ContextRangeId, "child_context_range_ids")
        if len(set(self.child_context_range_ids)) != len(self.child_context_range_ids):
            raise ContextStructureContractError(
                "invalid_context_hierarchy",
                "child context range IDs must be unique",
            )
        if self.context_range_id == self.parent_context_range_id:
            raise ContextStructureContractError(
                "invalid_context_hierarchy",
                "a context range cannot be its own parent",
            )
        if self.context_range_id in self.child_context_range_ids:
            raise ContextStructureContractError(
                "invalid_context_hierarchy",
                "a context range cannot be its own child",
            )
        if self.parent_context_range_id is not None and self.child_context_range_ids:
            raise ContextStructureContractError(
                "invalid_context_hierarchy_depth",
                "context_structure.v1 supports one parent level and one child level",
            )

@dataclass(frozen=True, slots=True)
class CandidateDisposition:
    candidate_id: ContextCandidateId
    disposition: CandidateDispositionState
    reason: str
    merged_into_context_range_id: ContextRangeId | None = None

    def __post_init__(self) -> None:
        _require_type(self.candidate_id, ContextCandidateId, "candidate_id")
        if type(self.disposition) is not CandidateDispositionState:
            raise ContextStructureContractError(
                "invalid_candidate_disposition",
                "disposition must be a CandidateDispositionState",
            )
        if (
            type(self.reason) is not str
            or _OPAQUE_ID_PATTERN.fullmatch(self.reason) is None
        ):
            raise ContextStructureContractError(
                "invalid_candidate_reason",
                "candidate reason must be an opaque public-safe token",
            )
        if self.disposition is CandidateDispositionState.MERGED_INTO:
            if self.merged_into_context_range_id is None:
                raise ContextStructureContractError(
                    "invalid_candidate_disposition",
                    "merged_into requires merged_into_context_range_id",
                )
            _require_type(
                self.merged_into_context_range_id,
                ContextRangeId,
                "merged_into_context_range_id",
            )
        elif self.merged_into_context_range_id is not None:
            raise ContextStructureContractError(
                "invalid_candidate_disposition",
                "dropped_reason and unknown dispositions cannot claim a merged range",
            )

@dataclass(frozen=True, slots=True)
class SemanticTitle:
    text: str
    provenance_state: SemanticTitleProvenanceState
    confidence: ConfidenceBand
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_single_line_label(self.text, "semantic_title", max_length=120)
        if self.text != self.text.strip():
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "semantic title cannot have surrounding whitespace",
            )
        if _is_generic_only_title(self.text):
            raise ContextStructureContractError(
                "generic_only_title",
                "internal taxonomy cannot be the complete user-facing title",
            )
        if _looks_like_path_or_url(self.text):
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "semantic title cannot contain a URL or local path",
            )
        if type(self.provenance_state) is not SemanticTitleProvenanceState:
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "provenance_state must be a SemanticTitleProvenanceState",
            )
        if type(self.confidence) is not ConfidenceBand:
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "title confidence must be a ConfidenceBand",
            )
        if not isinstance(self.evidence_refs, tuple):
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "evidence_refs must be a tuple",
            )
        for evidence_ref in self.evidence_refs:
            if (
                type(evidence_ref) is not str
                or _OPAQUE_ID_PATTERN.fullmatch(evidence_ref) is None
            ):
                raise ContextStructureContractError(
                    "invalid_title_provenance",
                    "evidence refs must be opaque public-safe tokens",
                )
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "evidence refs must be unique",
            )
        if self.evidence_refs != tuple(sorted(self.evidence_refs)):
            raise ContextStructureContractError(
                "invalid_title_provenance",
                "evidence refs must use deterministic lexical order",
            )
        if self.provenance_state is SemanticTitleProvenanceState.EVIDENCE_BASED:
            if not self.evidence_refs or self.fallback_reason is not None:
                raise ContextStructureContractError(
                    "invalid_title_provenance",
                    "evidence-based titles require evidence refs and no fallback reason",
                )
        else:
            expected = deterministic_fallback_title(self.fallback_reason)
            if self.text != expected:
                raise ContextStructureContractError(
                    "invalid_title_fallback",
                    "fallback title must match the deterministic reason vocabulary",
                )
            if self.confidence is ConfidenceBand.HIGH:
                raise ContextStructureContractError(
                    "invalid_title_fallback",
                    "fallback titles cannot claim high confidence",
                )

@dataclass(frozen=True, slots=True)
class UserChapter:
    context_range_id: ContextRangeId
    semantic_title: SemanticTitle
    boundary_confidence: ConfidenceBand = ConfidenceBand.UNKNOWN

    def __post_init__(self) -> None:
        _require_type(self.context_range_id, ContextRangeId, "context_range_id")
        _require_type(self.semantic_title, SemanticTitle, "semantic_title")
        if type(self.boundary_confidence) is not ConfidenceBand:
            raise ContextStructureContractError(
                "invalid_boundary_confidence",
                "boundary confidence must be a ConfidenceBand",
            )

@dataclass(frozen=True, slots=True)
class ExactMoment:
    exact_moment_id: ExactMomentId
    clock: ExactMomentClock

    def __post_init__(self) -> None:
        _require_type(self.exact_moment_id, ExactMomentId, "exact_moment_id")
        _require_type(self.clock, ExactMomentClock, "clock")

@dataclass(frozen=True, slots=True)
class HighlightStoryWindow:
    highlight_story_window_id: HighlightStoryWindowId
    clock: HighlightStoryWindowClock

    def __post_init__(self) -> None:
        _require_type(
            self.highlight_story_window_id,
            HighlightStoryWindowId,
            "highlight_story_window_id",
        )
        _require_type(self.clock, HighlightStoryWindowClock, "clock")

@dataclass(frozen=True, slots=True)
class LegacySelectedDraftRange:
    legacy_selected_draft_range_id: LegacySelectedDraftRangeId
    clock: LegacySelectedDraftRangeClock

    def __post_init__(self) -> None:
        _require_type(
            self.legacy_selected_draft_range_id,
            LegacySelectedDraftRangeId,
            "legacy_selected_draft_range_id",
        )
        _require_type(self.clock, LegacySelectedDraftRangeClock, "clock")

@dataclass(frozen=True, slots=True)
class UnknownGap:
    clock: ContextRangeClock
    axes: tuple[ContextAxis, ...]

    def __post_init__(self) -> None:
        _require_type(self.clock, ContextRangeClock, "clock")
        if not self.axes:
            raise ContextStructureContractError("invalid_unknown_gap", "unknown gap needs at least one axis")
        if any(type(axis) is not ContextAxis for axis in self.axes):
            raise ContextStructureContractError("invalid_unknown_gap", "unknown gap axes must be ContextAxis values")
        if len(set(self.axes)) != len(self.axes):
            raise ContextStructureContractError("invalid_unknown_gap", "unknown gap axes must be unique")

@dataclass(frozen=True, slots=True)
class SelectionState:
    selected_context_range_id: ContextRangeId | None = None
    selected_moment_id: ExactMomentId | None = None

    def __post_init__(self) -> None:
        if self.selected_context_range_id is not None:
            _require_type(self.selected_context_range_id, ContextRangeId, "selected_context_range_id")
        if self.selected_moment_id is not None:
            _require_type(self.selected_moment_id, ExactMomentId, "selected_moment_id")

@dataclass(frozen=True, slots=True)
class ContextStructureV1:
    state: ContextStructureState
    context_ranges: tuple[ContextRange, ...] = field(default_factory=tuple)
    exact_moments: tuple[ExactMoment, ...] = field(default_factory=tuple)
    highlight_story_windows: tuple[HighlightStoryWindow, ...] = field(default_factory=tuple)
    legacy_selected_draft_ranges: tuple[LegacySelectedDraftRange, ...] = field(default_factory=tuple)
    unknown_gaps: tuple[UnknownGap, ...] = field(default_factory=tuple)
    selection: SelectionState = field(default_factory=SelectionState)
    schema_version: str = SCHEMA_VERSION
    candidate_inventory_ids: tuple[ContextCandidateId, ...] = field(default_factory=tuple)
    candidate_dispositions: tuple[CandidateDisposition, ...] = field(default_factory=tuple)
    user_chapters: tuple[UserChapter, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ContextStructureContractError("invalid_schema_version", f"schema_version must be {SCHEMA_VERSION}")
        if type(self.state) is not ContextStructureState:
            raise ContextStructureContractError("invalid_state", "state must be a ContextStructureState")
        _validate_row_types(self.context_ranges, ContextRange, "context_ranges")
        _validate_row_types(
            self.candidate_dispositions,
            CandidateDisposition,
            "candidate_dispositions",
        )
        _validate_row_types(self.user_chapters, UserChapter, "user_chapters")
        _validate_row_types(self.exact_moments, ExactMoment, "exact_moments")
        _validate_row_types(
            self.highlight_story_windows,
            HighlightStoryWindow,
            "highlight_story_windows",
        )
        _validate_row_types(
            self.legacy_selected_draft_ranges,
            LegacySelectedDraftRange,
            "legacy_selected_draft_ranges",
        )
        _validate_row_types(self.unknown_gaps, UnknownGap, "unknown_gaps")
        _require_type(self.selection, SelectionState, "selection")
        _validate_unique_ids(self.context_ranges, "context_range_id")
        if len(set(self.candidate_inventory_ids)) != len(self.candidate_inventory_ids):
            raise ContextStructureContractError(
                "duplicate_id",
                "candidate_inventory_ids values must be unique",
            )
        if any(type(value) is not ContextCandidateId for value in self.candidate_inventory_ids):
            raise ContextStructureContractError(
                "identity_type_mismatch",
                "candidate_inventory_ids must contain only ContextCandidateId values",
            )
        _validate_unique_ids(self.candidate_dispositions, "candidate_id")
        _validate_unique_ids(self.user_chapters, "context_range_id")
        _validate_unique_ids(self.exact_moments, "exact_moment_id")
        _validate_unique_ids(self.highlight_story_windows, "highlight_story_window_id")
        _validate_unique_ids(self.legacy_selected_draft_ranges, "legacy_selected_draft_range_id")
        range_ids = {row.context_range_id for row in self.context_ranges}
        _validate_candidate_targets(
            self.candidate_inventory_ids,
            self.candidate_dispositions,
            range_ids,
        )
        _validate_context_hierarchy(self.context_ranges)
        _validate_user_chapters(self.user_chapters, range_ids)
        moment_ids = {row.exact_moment_id for row in self.exact_moments}
        if self.selection.selected_context_range_id not in range_ids | {None}:
            raise ContextStructureContractError(
                "unknown_selected_context_range",
                "selected_context_range_id must reference context_ranges",
            )
        if self.selection.selected_moment_id not in moment_ids | {None}:
            raise ContextStructureContractError(
                "unknown_selected_moment",
                "selected_moment_id must reference exact_moments",
            )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "context_ranges": [
                _context_range_dict(row)
                for row in self.context_ranges
            ],
            "exact_moments": [
                {"exact_moment_id": str(row.exact_moment_id), "clock": _clock_dict(row.clock)}
                for row in self.exact_moments
            ],
            "highlight_story_windows": [
                {
                    "highlight_story_window_id": str(row.highlight_story_window_id),
                    "clock": _clock_dict(row.clock),
                }
                for row in self.highlight_story_windows
            ],
            "legacy_selected_draft_ranges": [
                {
                    "legacy_selected_draft_range_id": str(row.legacy_selected_draft_range_id),
                    "clock": _clock_dict(row.clock),
                }
                for row in self.legacy_selected_draft_ranges
            ],
            "unknown_gaps": [
                {"clock": _clock_dict(row.clock), "axes": [axis.value for axis in row.axes]}
                for row in self.unknown_gaps
            ],
            "selection": {
                "selected_context_range_id": (
                    str(self.selection.selected_context_range_id)
                    if self.selection.selected_context_range_id is not None
                    else None
                ),
                "selected_moment_id": (
                    str(self.selection.selected_moment_id)
                    if self.selection.selected_moment_id is not None
                    else None
                ),
            },
        }
        if self.candidate_inventory_ids:
            payload["candidate_inventory_ids"] = [
                str(candidate_id) for candidate_id in self.candidate_inventory_ids
            ]
        if self.candidate_dispositions:
            payload["candidate_dispositions"] = [
                {
                    "candidate_id": str(row.candidate_id),
                    "disposition": row.disposition.value,
                    "reason": row.reason,
                    "merged_into_context_range_id": (
                        str(row.merged_into_context_range_id)
                        if row.merged_into_context_range_id is not None
                        else None
                    ),
                }
                for row in self.candidate_dispositions
            ]
        if self.user_chapters:
            payload["user_chapters"] = [
                {
                    "context_range_id": str(row.context_range_id),
                    "semantic_title": {
                        "text": row.semantic_title.text,
                        "provenance_state": row.semantic_title.provenance_state.value,
                        "confidence": row.semantic_title.confidence.value,
                        "evidence_refs": list(row.semantic_title.evidence_refs),
                        "fallback_reason": row.semantic_title.fallback_reason,
                    },
                    "boundary_confidence": row.boundary_confidence.value,
                }
                for row in self.user_chapters
            ]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContextStructureV1":
        if not isinstance(payload, Mapping):
            raise ContextStructureContractError("invalid_payload", "payload must be an object")
        try:
            context_ranges = tuple(
                ContextRange(
                    context_range_id=ContextRangeId(row["context_range_id"]),
                    clock=_clock_from_dict(row["clock"], ContextRangeClock),
                    activity=row.get("activity"),
                    topic=row.get("topic"),
                    parent_context_range_id=(
                        ContextRangeId(row["parent_context_range_id"])
                        if row.get("parent_context_range_id") is not None
                        else None
                    ),
                    child_context_range_ids=tuple(
                        ContextRangeId(child_id)
                        for child_id in _id_values(row, "child_context_range_ids")
                    ),
                )
                for row in _object_rows(payload, "context_ranges")
            )
            candidate_inventory_ids = tuple(
                ContextCandidateId(candidate_id)
                for candidate_id in _id_values(payload, "candidate_inventory_ids")
            )
            dispositions = tuple(
                CandidateDisposition(
                    candidate_id=ContextCandidateId(row["candidate_id"]),
                    disposition=CandidateDispositionState(row["disposition"]),
                    reason=row["reason"],
                    merged_into_context_range_id=(
                        ContextRangeId(row["merged_into_context_range_id"])
                        if row.get("merged_into_context_range_id") is not None
                        else None
                    ),
                )
                for row in _object_rows(payload, "candidate_dispositions")
            )
            user_chapters = tuple(
                UserChapter(
                    context_range_id=ContextRangeId(row["context_range_id"]),
                    semantic_title=_semantic_title_from_dict(row["semantic_title"]),
                    boundary_confidence=ConfidenceBand(
                        row.get("boundary_confidence", ConfidenceBand.UNKNOWN.value)
                    ),
                )
                for row in _object_rows(payload, "user_chapters")
            )
            exact_moments = tuple(
                ExactMoment(
                    exact_moment_id=ExactMomentId(row["exact_moment_id"]),
                    clock=_clock_from_dict(row["clock"], ExactMomentClock),
                )
                for row in _object_rows(payload, "exact_moments")
            )
            story_windows = tuple(
                HighlightStoryWindow(
                    highlight_story_window_id=HighlightStoryWindowId(
                        row["highlight_story_window_id"]
                    ),
                    clock=_clock_from_dict(row["clock"], HighlightStoryWindowClock),
                )
                for row in _object_rows(payload, "highlight_story_windows")
            )
            legacy_ranges = tuple(
                LegacySelectedDraftRange(
                    legacy_selected_draft_range_id=LegacySelectedDraftRangeId(
                        row["legacy_selected_draft_range_id"]
                    ),
                    clock=_clock_from_dict(row["clock"], LegacySelectedDraftRangeClock),
                )
                for row in _object_rows(payload, "legacy_selected_draft_ranges")
            )
            unknown_gaps = tuple(
                UnknownGap(
                    clock=_clock_from_dict(row["clock"], ContextRangeClock),
                    axes=tuple(ContextAxis(axis) for axis in row["axes"]),
                )
                for row in _object_rows(payload, "unknown_gaps")
            )
            raw_selection = payload.get("selection", {})
            if not isinstance(raw_selection, Mapping):
                raise ContextStructureContractError("invalid_payload", "selection must be an object")
            selected_range = raw_selection.get("selected_context_range_id")
            selected_moment = raw_selection.get("selected_moment_id")
            return cls(
                schema_version=payload.get("schema_version", ""),
                state=ContextStructureState(payload["state"]),
                context_ranges=context_ranges,
                candidate_inventory_ids=candidate_inventory_ids,
                candidate_dispositions=dispositions,
                user_chapters=user_chapters,
                exact_moments=exact_moments,
                highlight_story_windows=story_windows,
                legacy_selected_draft_ranges=legacy_ranges,
                unknown_gaps=unknown_gaps,
                selection=SelectionState(
                    selected_context_range_id=ContextRangeId(selected_range) if selected_range is not None else None,
                    selected_moment_id=ExactMomentId(selected_moment) if selected_moment is not None else None,
                ),
            )
        except ContextStructureContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextStructureContractError("invalid_payload", str(exc)) from exc

T = TypeVar("T")

def _clock_dict(clock: Any) -> dict[str, float]:
    return {"start_sec": clock.start_sec, "end_sec": clock.end_sec}

def _context_range_dict(row: ContextRange) -> dict[str, Any]:
    payload = {
        "context_range_id": str(row.context_range_id),
        "clock": _clock_dict(row.clock),
        "activity": row.activity,
        "topic": row.topic,
    }
    if row.parent_context_range_id is not None or row.child_context_range_ids:
        payload["parent_context_range_id"] = (
            str(row.parent_context_range_id)
            if row.parent_context_range_id is not None
            else None
        )
        payload["child_context_range_ids"] = [
            str(child_id) for child_id in row.child_context_range_ids
        ]
    return payload

def _clock_from_dict(payload: Any, clock_type: type[T]) -> T:
    if not isinstance(payload, Mapping):
        raise ContextStructureContractError("invalid_clock", "clock must be an object")
    return clock_type(start_sec=payload["start_sec"], end_sec=payload["end_sec"])

def _object_rows(payload: Mapping[str, Any], field_name: str) -> tuple[Mapping[str, Any], ...]:
    rows = payload.get(field_name, ())
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, Mapping) for row in rows):
        raise ContextStructureContractError("invalid_payload", f"{field_name} must be a list of objects")
    return tuple(rows)

def _id_values(payload: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    values = payload.get(field_name, ())
    if not isinstance(values, (list, tuple)) or any(type(value) is not str for value in values):
        raise ContextStructureContractError(
            "invalid_payload",
            f"{field_name} must be a list of string IDs",
        )
    return tuple(values)

def _semantic_title_from_dict(payload: Any) -> SemanticTitle:
    if not isinstance(payload, Mapping):
        raise ContextStructureContractError(
            "invalid_title_provenance",
            "semantic_title must be an object",
        )
    evidence_refs = payload.get("evidence_refs", ())
    if not isinstance(evidence_refs, (list, tuple)):
        raise ContextStructureContractError(
            "invalid_title_provenance",
            "evidence_refs must be a list",
        )
    return SemanticTitle(
        text=payload["text"],
        provenance_state=SemanticTitleProvenanceState(payload["provenance_state"]),
        confidence=ConfidenceBand(payload["confidence"]),
        evidence_refs=tuple(evidence_refs),
        fallback_reason=payload.get("fallback_reason"),
    )

def _validate_unique_ids(rows: Iterable[Any], field_name: str) -> None:
    identities = [getattr(row, field_name) for row in rows]
    if len(set(identities)) != len(identities):
        raise ContextStructureContractError("duplicate_id", f"{field_name} values must be unique")

def _validate_row_types(rows: Iterable[Any], expected: type[Any], field_name: str) -> None:
    if any(type(row) is not expected for row in rows):
        raise ContextStructureContractError(
            "identity_type_mismatch",
            f"{field_name} must contain only {expected.__name__} values",
        )

def _validate_candidate_targets(
    candidate_inventory_ids: Iterable[ContextCandidateId],
    dispositions: Iterable[CandidateDisposition],
    range_ids: set[ContextRangeId],
) -> None:
    inventory_ids = tuple(candidate_inventory_ids)
    disposition_rows = tuple(dispositions)
    if {str(value) for value in inventory_ids} != {
        str(row.candidate_id) for row in disposition_rows
    }:
        raise ContextStructureContractError(
            "candidate_disposition_coverage_mismatch",
            "every inventory candidate must have exactly one disposition",
        )
    for row in disposition_rows:
        if (
            row.disposition is CandidateDispositionState.MERGED_INTO
            and row.merged_into_context_range_id not in range_ids
        ):
            raise ContextStructureContractError(
                "unknown_merged_context_range",
                "merged candidate must reference context_ranges",
            )

def _validate_context_hierarchy(
    context_ranges: Iterable[ContextRange],
) -> None:
    ranges_by_id = {row.context_range_id: row for row in context_ranges}
    for context_range in ranges_by_id.values():
        if (
            context_range.parent_context_range_id is not None
            and context_range.parent_context_range_id not in ranges_by_id
        ):
            raise ContextStructureContractError(
                "invalid_context_hierarchy",
                "context parent must reference context_ranges",
            )
        for child_id in context_range.child_context_range_ids:
            if child_id not in ranges_by_id:
                raise ContextStructureContractError(
                    "invalid_context_hierarchy",
                    "context child must reference context_ranges",
                )
            child = ranges_by_id[child_id]
            if child.parent_context_range_id != context_range.context_range_id:
                raise ContextStructureContractError(
                    "invalid_context_hierarchy",
                    "parent and child references must be reciprocal",
                )
            parent_clock = context_range.clock
            child_clock = child.clock
            if (
                child_clock.start_sec < parent_clock.start_sec
                or child_clock.end_sec > parent_clock.end_sec
            ):
                raise ContextStructureContractError(
                    "invalid_context_hierarchy",
                    "child context clock must stay inside its parent",
                )
        if context_range.parent_context_range_id is not None:
            parent = ranges_by_id[context_range.parent_context_range_id]
            if context_range.context_range_id not in parent.child_context_range_ids:
                raise ContextStructureContractError(
                    "invalid_context_hierarchy",
                    "child and parent references must be reciprocal",
                )

def _validate_user_chapters(
    chapters: Iterable[UserChapter],
    range_ids: set[ContextRangeId],
) -> None:
    for chapter in chapters:
        if chapter.context_range_id not in range_ids:
            raise ContextStructureContractError(
                "unknown_chapter_context_range",
                "user chapter must reference context_ranges",
            )

def _validate_axis_label(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _validate_single_line_label(value, field_name, max_length=120)

def _validate_single_line_label(value: Any, field_name: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ContextStructureContractError(
            "invalid_axis_value",
            f"{field_name} must be a non-empty label of at most {max_length} characters",
        )
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise ContextStructureContractError(
            "invalid_axis_value",
            f"{field_name} must be a single-line label",
        )

def _is_generic_only_title(value: str) -> bool:
    if value in _GENERIC_ONLY_TITLES:
        return True
    return any(
        value == f"{generic} {suffix}"
        for generic in _GENERIC_ONLY_TITLES
        for suffix in _GENERIC_TITLE_SUFFIXES
    )

def _looks_like_path_or_url(value: str) -> bool:
    lowered = value.casefold()
    if "://" in lowered or lowered.startswith(("file:", "www.")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value) is not None:
        return True
    if value.startswith(("\\\\", "/home/", "/users/", "/tmp/")):
        return True
    return False
