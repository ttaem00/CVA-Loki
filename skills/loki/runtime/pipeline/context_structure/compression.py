"""Deterministic projection from adaptive candidates to user chapters."""

from __future__ import annotations

import hashlib

import html

import json

import math

import re

from collections.abc import Mapping, Sequence

from typing import Any

from .contracts import (
    CandidateDisposition,
    CandidateDispositionState,
    ConfidenceBand,
    ContextAxis,
    ContextCandidateId,
    ContextRange,
    ContextRangeClock,
    ContextRangeId,
    ContextStructureContractError,
    ContextStructureState,
    ContextStructureV1,
    SemanticTitle,
    SemanticTitleProvenanceState,
    UnknownGap,
    UserChapter,
    deterministic_fallback_title,
)

RULE_VERSION = "deterministic_context_compression.v2"

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_GENERIC = {
    "합방",
    "게임",
    "게임 안 단계",
    "토크",
    "같이보기",
    "진행",
    "맥락 구간 후보",
    "자동 구간",
    "챕터 후보",
    "unknown",
}

_STRUCTURAL_TOPIC = re.compile(
    r"(?:"
    r"같이\s*보기|앨범|트랙리스트|비하인드|ASMR|"
    r"게임|보스|공략|퀘스트|마을|도시|난이도|테스트|접속|"
    r"텔레포트|등장|만남|합류|사망|승리|패배|시작|종료|"
    r"결승|준결승|라운드|스테이지|stage|phase|"
    r"합방|콜라보|노래|곡|토크|마무리|엔딩"
    r")",
    re.IGNORECASE,
)

_GENERIC_BOUNDARY_TOPIC = re.compile(
    r"^(?:등장|오프닝|시작|토크|잡담|마무리|엔딩)$",
    re.IGNORECASE,
)

def _stable_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:20]}"

def _clock(row: Mapping[str, Any]) -> ContextRangeClock:
    return ContextRangeClock(
        start_sec=float(row["start_sec"]),
        end_sec=float(row["end_sec"]),
    )

def _candidate_id(row: Mapping[str, Any], field: str) -> ContextCandidateId:
    try:
        return ContextCandidateId(str(row[field]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ContextStructureContractError(
            "invalid_compression_candidate",
            f"{field} must be an opaque candidate ID",
        ) from exc

def _clean_label(value: Any) -> str | None:
    if type(value) is not str:
        return None
    label = " ".join(html.unescape(value).strip().split())
    label = label.strip("`*_~ ")
    label = re.sub(r"^[<>]+|[<>]+$", "", label).strip()
    label = label.lstrip("🎮💬🎬🎤📺✨⭐️└├│ ")
    label = re.sub(r"\s+진행$", "", label).strip()
    label = re.sub(r"^[<>]+|[<>]+$", "", label).strip()
    return label or None

def _safe_label(value: Any) -> str | None:
    label = _clean_label(value)
    if (
        not label
        or len(label) > 120
        or label.casefold() in _GENERIC
        or "_" in label
        or "://" in label
        or re.match(r"^[A-Za-z]:[\\/]", label)
    ):
        return None
    return label

def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _safe_label(row.get("activity_state")) or "",
        _safe_label(row.get("topic_state")) or "",
    )

def _evidence_refs(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values = {
        str(value)
        for row in rows
        for value in (row.get("source_event_ids") or ())
        if type(value) is str and _TOKEN.fullmatch(value)
    }
    return tuple(sorted(values))

def _confidence(rows: Sequence[Mapping[str, Any]]) -> ConfidenceBand:
    values = {str(row.get("confidence_band") or "unknown") for row in rows}
    for value in ("unknown", "low", "medium", "high"):
        if value in values:
            return ConfidenceBand(value)
    return ConfidenceBand.UNKNOWN

def _title(rows: Sequence[Mapping[str, Any]]) -> SemanticTitle:
    activity, topic = _semantic_key(rows[0])
    evidence_refs = _evidence_refs(rows)
    if topic and evidence_refs:
        text = f"{topic} · {activity}" if activity and activity != topic else topic
        return SemanticTitle(
            text=text[:120],
            provenance_state=SemanticTitleProvenanceState.EVIDENCE_BASED,
            confidence=_confidence(rows),
            evidence_refs=evidence_refs,
        )
    fallback_reason = "activity_only" if activity else "insufficient_evidence"
    return SemanticTitle(
        text=deterministic_fallback_title(fallback_reason),
        provenance_state=SemanticTitleProvenanceState.DETERMINISTIC_FALLBACK,
        confidence=ConfidenceBand.LOW if activity else ConfidenceBand.UNKNOWN,
        fallback_reason=fallback_reason,
    )

def _should_surface_child_chapter(
    rows: Sequence[Mapping[str, Any]],
) -> bool:
    """Keep one-off child seeds internal until semantic support is strong."""

    return len(rows) >= 2 or _confidence(rows) is ConfidenceBand.HIGH

def _run_label(rows: Sequence[Mapping[str, Any]]) -> str:
    return _safe_label(rows[0].get("topic_state")) or ""

def _run_midpoint(rows: Sequence[Mapping[str, Any]]) -> float:
    return (
        min(float(row["start_sec"]) for row in rows)
        + max(float(row["end_sec"]) for row in rows)
    ) / 2

def _landmark_score(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int, int, str]:
    """Rank evidence-backed story landmarks without source-specific phrases."""

    label = _run_label(rows)
    if not label:
        return (-100, 0, 0, "")
    families = {
        str(family)
        for row in rows
        for family in (row.get("support_modality_families") or ())
        if family
    }
    structural = 1 if _STRUCTURAL_TOPIC.search(label) else 0
    descriptive = 1 if len(label) >= 5 or any(mark in label for mark in ("/", ":", "-", "·")) else 0
    return (
        structural * 6 + descriptive * 2 + min(3, len(families)),
        structural,
        len(label),
        label,
    )

def _surface_child_run_indexes(
    source_parent: Mapping[str, Any] | None,
    semantic_runs: Sequence[Sequence[Mapping[str, Any]]],
) -> set[int]:
    """Expose useful subchapters inside long sections without a timestamp dump."""

    selected = {
        index
        for index, run in enumerate(semantic_runs)
        if _should_surface_child_chapter(run)
    }
    if len(semantic_runs) < 4:
        return selected
    start_sec = min(float(row["start_sec"]) for run in semantic_runs for row in run)
    end_sec = max(float(row["end_sec"]) for run in semantic_runs for row in run)
    duration_sec = end_sec - start_sec
    projection_cap = 8
    if duration_sec < 30 * 60 and len(selected) <= projection_cap:
        return selected



    budget = min(8, max(2, math.ceil(duration_sec / (90 * 60)) + 1))
    window_size = duration_sec / budget
    window_selected: set[int] = set()
    for window in range(budget):
        lower = start_sec + window * window_size
        upper = end_sec if window == budget - 1 else lower + window_size
        candidates = [
            (index, run)
            for index, run in enumerate(semantic_runs)
            if lower <= _run_midpoint(run) <= upper
            and _landmark_score(run)[1] == 1
        ]
        if candidates:
            selected_index = max(
                candidates,
                key=lambda item: (_landmark_score(item[1]), -item[0]),
            )[0]
            selected.add(selected_index)
            window_selected.add(selected_index)

    if len(selected) <= projection_cap:
        return selected




    priority = set(window_selected)
    kept = set(
        sorted(
            priority,
            key=lambda index: (_run_midpoint(semantic_runs[index]), index),
        )[:projection_cap]
    )
    ranked_remaining = sorted(
        (index for index in selected if index not in kept),
        key=lambda index: (
            int(_should_surface_child_chapter(semantic_runs[index])),
            _landmark_score(semantic_runs[index]),
            -index,
        ),
        reverse=True,
    )
    kept.update(
        ranked_remaining[: max(0, projection_cap - len(kept))]
    )
    return kept

def _runs(
    rows: Sequence[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row["start_sec"]),
            float(row["end_sec"]),
            str(row["context_range_id"]),
        ),
    )
    groups: list[list[Mapping[str, Any]]] = []
    for row in ordered:
        if (
            groups
            and _semantic_key(groups[-1][-1]) == _semantic_key(row)
            and float(row["start_sec"]) <= float(groups[-1][-1]["end_sec"])
        ):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups

def _clock_clusters(
    rows: Sequence[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row["start_sec"]),
            float(row["end_sec"]),
            str(row["context_range_id"]),
        ),
    )
    clusters: list[list[Mapping[str, Any]]] = []
    for row in ordered:
        if (
            clusters
            and float(row["start_sec"])
            <= max(float(item["end_sec"]) for item in clusters[-1])
        ):
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return clusters

def _range_id(role: str, rows: Sequence[Mapping[str, Any]]) -> ContextRangeId:
    return ContextRangeId(
        _stable_id(
            f"compressed-{role}",
            sorted(str(row["context_range_id"]) for row in rows),
        )
    )

def _join_korean_and(left: str, right: str) -> str:
    last = left[-1]
    has_final_consonant = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    particle = "과" if has_final_consonant else "와"
    return f"{left}{particle} {right}"

def _group_title(
    source_parent: Mapping[str, Any] | None,
    semantic_runs: Sequence[Sequence[Mapping[str, Any]]],
) -> SemanticTitle:
    if source_parent is not None:
        parent_title = _title([source_parent])
        if (
            parent_title.provenance_state
            is SemanticTitleProvenanceState.EVIDENCE_BASED
        ):
            return parent_title
    if len(semantic_runs) == 1:
        return _title(semantic_runs[0])
    topics = [
        _safe_label(run[0].get("topic_state")) or ""
        for run in semantic_runs
        if _safe_label(run[0].get("topic_state"))
    ]
    labels = list(dict.fromkeys(topics))
    if not labels:
        activities = [
            _semantic_key(run[0])[0]
            for run in semantic_runs
            if _semantic_key(run[0])[0]
        ]
        labels = list(dict.fromkeys(activities))
    all_rows = [row for run in semantic_runs for row in run]
    evidence_refs = _evidence_refs(all_rows)
    profile = str((source_parent or {}).get("broadcast_profile") or "")
    parent_topic = _safe_label((source_parent or {}).get("topic_state"))
    first_topic = _clean_label(semantic_runs[0][0].get("topic_state")) or ""
    weak_comment_topics_only = all(
        "comment_seed_is_weak" in set(row.get("risk_flags") or ())
        for row in all_rows
    )
    if (
        profile == "mixed_unknown"
        and not parent_topic
        and _GENERIC_BOUNDARY_TOPIC.fullmatch(first_topic)
        and weak_comment_topics_only
    ):





        return _title([source_parent or semantic_runs[0][0]])
    if len(labels) == 1 and evidence_refs:
        return SemanticTitle(
            text=labels[0][:120],
            provenance_state=SemanticTitleProvenanceState.EVIDENCE_BASED,
            confidence=_confidence(all_rows),
            evidence_refs=evidence_refs,
        )
    if len(labels) >= 2 and evidence_refs:
        structural_labels = [
            label for label in labels[1:] if _STRUCTURAL_TOPIC.search(label)
        ]
        if profile == "game_match":
            detail = structural_labels[-1] if structural_labels else labels[-1]
            text = f"{labels[0]} — {detail}"
        elif profile in {"watch_event", "collab_multi_pov"}:
            detail = structural_labels[-1] if structural_labels else labels[-1]
            text = _join_korean_and(labels[0], detail)
        elif _GENERIC_BOUNDARY_TOPIC.fullmatch(labels[0]):
            text = labels[-1]
        else:
            text = _join_korean_and(labels[0], labels[-1])
        return SemanticTitle(
            text=text[:120],
            provenance_state=SemanticTitleProvenanceState.EVIDENCE_BASED,
            confidence=_confidence(all_rows),
            evidence_refs=evidence_refs,
        )
    return _title(semantic_runs[0])

def compress_adaptive_context_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a validated ``context_structure.v1`` projection.

    The input's legacy ranges remain the candidate inventory. No count cap,
    ranking, renderer state, or source-specific rule participates.
    """

    if not isinstance(payload, Mapping):
        raise ContextStructureContractError(
            "invalid_compression_payload",
            "adaptive payload must be an object",
        )
    schema_version = payload.get("schema_version")
    if schema_version not in (None, "adaptive_context_ranges.v1"):
        raise ContextStructureContractError(
            "invalid_compression_payload",
            "compression input must use adaptive_context_ranges.v1",
        )
    raw_ranges = payload.get("ranges", ())
    raw_gaps = payload.get("unknown_gaps", ())
    if (
        not isinstance(raw_ranges, (list, tuple))
        or not isinstance(raw_gaps, (list, tuple))
        or any(not isinstance(row, Mapping) for row in (*raw_ranges, *raw_gaps))
    ):
        raise ContextStructureContractError(
            "invalid_compression_payload",
            "ranges and unknown_gaps must be object lists",
        )

    ranges = list(raw_ranges)
    gaps = list(raw_gaps)
    range_candidates = [
        _candidate_id(row, "context_range_id") for row in ranges
    ]
    gap_candidates = [_candidate_id(row, "gap_id") for row in gaps]
    inventory = tuple(range_candidates + gap_candidates)
    if len(set(inventory)) != len(inventory):
        raise ContextStructureContractError(
            "duplicate_id",
            "adaptive range and gap candidate IDs must be globally unique",
        )

    ranges_by_id = {
        str(row["context_range_id"]): row
        for row in ranges
    }
    legacy_parents = sorted(
        (row for row in ranges if row.get("child_context_range_ids")),
        key=lambda row: (
            float(row["start_sec"]),
            float(row["end_sec"]),
            str(row["context_range_id"]),
        ),
    )
    claimed_children: set[str] = set()
    context_ranges: list[ContextRange] = []
    chapters: list[UserChapter] = []
    chapter_titles: set[str] = set()
    dispositions: list[CandidateDisposition] = []
    unknown_gaps: list[UnknownGap] = []

    def add_chapter(
        context_range_id: ContextRangeId,
        title: SemanticTitle,
        boundary_confidence: ConfidenceBand,
        start_sec: float,
    ) -> None:
        if title.text in chapter_titles:
            if (
                title.provenance_state
                is SemanticTitleProvenanceState.DETERMINISTIC_FALLBACK
            ):
                return
            suffix = f" · {int(start_sec // 60)}분 지점"
            title = SemanticTitle(
                text=f"{title.text[: 120 - len(suffix)]}{suffix}",
                provenance_state=title.provenance_state,
                confidence=title.confidence,
                evidence_refs=title.evidence_refs,
            )
            if title.text in chapter_titles:
                return
        chapter_titles.add(title.text)
        chapters.append(
            UserChapter(
                context_range_id=context_range_id,
                semantic_title=title,
                boundary_confidence=boundary_confidence,
            )
        )

    def add_group(
        source_parent: Mapping[str, Any] | None,
        leaf_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        if not leaf_rows:
            return
        accepted: list[Mapping[str, Any]] = []
        conflicts: list[Mapping[str, Any]] = []
        for row in sorted(
            leaf_rows,
            key=lambda item: (
                float(item["start_sec"]),
                float(item["end_sec"]),
                str(item["context_range_id"]),
            ),
        ):
            overlapping = [
                item
                for item in accepted
                if float(row["start_sec"]) < float(item["end_sec"])
                and float(row["end_sec"]) > float(item["start_sec"])
            ]
            if any(
                _semantic_key(row) != _semantic_key(item)
                for item in overlapping
            ):
                conflicts.append(row)
            else:
                accepted.append(row)
        for row in conflicts:
            claimed_children.add(str(row["context_range_id"]))
            unknown_gaps.append(
                UnknownGap(
                    clock=_clock(row),
                    axes=(ContextAxis.ACTIVITY, ContextAxis.TOPIC),
                )
            )
            dispositions.append(
                CandidateDisposition(
                    candidate_id=_candidate_id(
                        row, "context_range_id"
                    ),
                    disposition=CandidateDispositionState.UNKNOWN,
                    reason="conflicting_signals",
                )
            )
        if not accepted:
            return
        top_id = _range_id("parent", accepted)
        semantic_runs = _runs(accepted)
        child_ids = (
            tuple(_range_id("child", group) for group in semantic_runs)
            if len(semantic_runs) > 1
            else ()
        )
        top_clock = ContextRangeClock(
            min(float(row["start_sec"]) for row in accepted),
            max(float(row["end_sec"]) for row in accepted),
        )
        existing_top_overlap = any(
            row.parent_context_range_id is None
            and top_clock.start_sec < row.clock.end_sec
            and top_clock.end_sec > row.clock.start_sec
            for row in context_ranges
        )
        if existing_top_overlap:
            for row in accepted:
                claimed_children.add(str(row["context_range_id"]))
                unknown_gaps.append(
                    UnknownGap(
                        clock=_clock(row),
                        axes=(ContextAxis.ACTIVITY, ContextAxis.TOPIC),
                    )
                )
                dispositions.append(
                    CandidateDisposition(
                        candidate_id=_candidate_id(
                            row, "context_range_id"
                        ),
                        disposition=CandidateDispositionState.UNKNOWN,
                        reason="conflicting_signals",
                    )
                )
            if source_parent is not None:
                dispositions.append(
                    CandidateDisposition(
                        candidate_id=_candidate_id(
                            source_parent, "context_range_id"
                        ),
                        disposition=CandidateDispositionState.UNKNOWN,
                        reason="conflicting_signals",
                    )
                )
            return
        top_activity, top_topic = _semantic_key(
            source_parent or semantic_runs[0][0]
        )
        top_title = _group_title(source_parent, semantic_runs)
        if not top_topic and (
            top_title.provenance_state
            is SemanticTitleProvenanceState.EVIDENCE_BASED
        ):
            top_topic = top_title.text
        context_ranges.append(
            ContextRange(
                context_range_id=top_id,
                clock=top_clock,
                activity=top_activity or None,
                topic=top_topic or None,
                child_context_range_ids=child_ids,
            )
        )
        add_chapter(
            top_id,
            top_title,
            _confidence(accepted),
            top_clock.start_sec,
        )
        if source_parent is not None:
            dispositions.append(
                CandidateDisposition(
                    candidate_id=_candidate_id(
                        source_parent, "context_range_id"
                    ),
                    disposition=CandidateDispositionState.MERGED_INTO,
                    reason="legacy_parent_projection",
                    merged_into_context_range_id=top_id,
                )
            )
        surfaced_child_indexes = _surface_child_run_indexes(
            source_parent,
            semantic_runs,
        )
        for index, run in enumerate(semantic_runs):
            target_id = child_ids[index] if child_ids else top_id
            surfaced_child = False
            if child_ids:
                activity, topic = _semantic_key(run[0])
                context_ranges.append(
                    ContextRange(
                        context_range_id=target_id,
                        clock=ContextRangeClock(
                            min(float(row["start_sec"]) for row in run),
                            max(float(row["end_sec"]) for row in run),
                        ),
                        activity=activity or None,
                        topic=topic or None,
                        parent_context_range_id=top_id,
                    )
                )
                if index in surfaced_child_indexes:
                    surfaced_child = True
                    add_chapter(
                        target_id,
                        _title(run),
                        _confidence(run),
                        min(float(row["start_sec"]) for row in run),
                    )
            lineage_target_id = (
                target_id if not child_ids or surfaced_child else top_id
            )
            for row in run:
                claimed_children.add(str(row["context_range_id"]))
                dispositions.append(
                    CandidateDisposition(
                        candidate_id=_candidate_id(
                            row, "context_range_id"
                        ),
                        disposition=CandidateDispositionState.MERGED_INTO,
                        reason="semantic_continuity",
                        merged_into_context_range_id=lineage_target_id,
                    )
                )

    for parent in legacy_parents:
        children = [
            ranges_by_id[str(child_id)]
            for child_id in parent.get("child_context_range_ids") or ()
            if str(child_id) in ranges_by_id
        ]
        add_group(parent, children)

    unclaimed = [
        row
        for row in ranges
        if not row.get("child_context_range_ids")
        and str(row["context_range_id"]) not in claimed_children
    ]
    for group in _clock_clusters(unclaimed):
        add_group(None, group)

    for row in sorted(
        gaps,
        key=lambda item: (
            float(item["start_sec"]),
            float(item["end_sec"]),
            str(item["gap_id"]),
        ),
    ):
        raw_axes = row.get("axes") or ("activity", "topic")
        try:
            axes = tuple(ContextAxis(value) for value in raw_axes)
        except (TypeError, ValueError) as exc:
            raise ContextStructureContractError(
                "invalid_unknown_gap",
                "unknown gap axes must use activity/topic",
            ) from exc
        unknown_gaps.append(
            UnknownGap(
                clock=_clock(row),
                axes=axes,
            )
        )
        dispositions.append(
            CandidateDisposition(
                candidate_id=_candidate_id(row, "gap_id"),
                disposition=CandidateDispositionState.UNKNOWN,
                reason=str(row.get("reason") or "insufficient_evidence"),
            )
        )

    dispositions.sort(key=lambda row: str(row.candidate_id))
    empty_state = {
        "disabled": ContextStructureState.DISABLED,
        "not_analyzed": ContextStructureState.NOT_ANALYZED,
        "analysis_failed": ContextStructureState.ANALYSIS_FAILED,
        "no_useful_context_ranges": ContextStructureState.NO_USEFUL_CONTEXT_RANGES,
    }.get(
        str(payload.get("analysis_state") or ""),
        ContextStructureState.NO_USEFUL_CONTEXT_RANGES,
    )
    contract = ContextStructureV1(
        state=(
            ContextStructureState.PARTIAL_SIGNAL_COVERAGE
            if context_ranges
            else empty_state
        ),
        context_ranges=tuple(context_ranges),
        unknown_gaps=tuple(unknown_gaps),
        candidate_inventory_ids=tuple(
            sorted(inventory, key=str)
        ),
        candidate_dispositions=tuple(dispositions),
        user_chapters=tuple(chapters),
    )
    result = contract.to_dict()
    top_levels = sorted(
        (
            row
            for row in context_ranges
            if row.parent_context_range_id is None
        ),
        key=lambda row: (
            row.clock.start_sec,
            row.clock.end_sec,
            str(row.context_range_id),
        ),
    )
    top_level_overlap_count = sum(
        current.clock.start_sec < previous.clock.end_sec
        for previous, current in zip(top_levels, top_levels[1:])
    )
    title_values = [row.semantic_title.text for row in chapters]
    duplicate_user_title_count = len(title_values) - len(set(title_values))
    projected_range_ids = {
        row.context_range_id for row in chapters
    }
    result["compression_rule_version"] = RULE_VERSION
    result["compression_summary"] = {
        "original_candidate_count": len(inventory),
        "disposition_count": len(dispositions),
        "top_level_chapter_count": sum(
            row.parent_context_range_id is None
            for row in context_ranges
        ),
        "child_chapter_count": sum(
            row.parent_context_range_id is not None
            for row in context_ranges
        ),
        "projected_child_chapter_count": sum(
            row.parent_context_range_id is not None
            and row.context_range_id in projected_range_ids
            for row in context_ranges
        ),
        "user_chapter_count": len(chapters),
        "unknown_candidate_count": sum(
            row.disposition is CandidateDispositionState.UNKNOWN
            for row in dispositions
        ),
        "top_level_overlap_count": top_level_overlap_count,
        "duplicate_user_title_count": duplicate_user_title_count,
        "coverage_complete": True,
    }
    result["compression_digest"] = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return result
