"""Manager-outline edit transaction owner.

Owns Report Admin registration, projection, QG1 validation, viewer projection,
revision persistence, save/sync, and restore. It deliberately does not own the
read-only normal-report renderer or report discovery lifecycle.
"""

import copy

import hashlib

import json

import re

from pipeline.editorial_highlight_pipeline import broadcast_map_identity_hash

from pipeline.manager_outline import parse_manager_outline_markdown, project_manager_outline_markdown, validate_structured_manager_outline

from pipeline.summary.render import _strip_editorial_review_markdown, _validate_editorial_review_projection

def _validate_editorial_packet_edit(current: dict, proposed: dict, *, duration_sec=None) -> dict:
    """Allow bounded Point/Story edits while preserving Highlight authority.

    A manager may add or remove a one-to-one Point/Story pair.  A Point that
    is already used by a Highlight cannot be removed, and retained rows keep
    their identity, evidence lineage, and relationships.  Copy, the Point's
    single timestamp, and the one canonical Highlight geometry remain the
    only editable values.
    """

    _validate_editorial_review_projection(current)

    if proposed.get("_evidence_dispositions_deferred") is True:


        proposed = dict(proposed)
        proposed.pop("_evidence_dispositions_deferred")
        originals = {row["highlight_id"]: row for row in current.get("editorial_highlights") or []}
        hydrated = []
        for row in proposed.get("editorial_highlights") or []:
            row = dict(row)
            original = originals.get(row.get("highlight_id"), {})
            if "evidence_dispositions" in original and "evidence_dispositions" not in row:
                row["evidence_dispositions"] = original["evidence_dispositions"]
            hydrated.append(row)
        proposed["editorial_highlights"] = hydrated

    editable_top_level = {"broadcast_map", "stories", "editorial_highlights", "broadcast_map_sha256", "collaboration_periods"}
    if "collaboration_periods" in proposed:
        from pipeline.chatgpt_export.contract.collaboration_periods import validate_collaboration_periods
        try:
            validate_collaboration_periods(proposed["collaboration_periods"], duration_sec if duration_sec is not None else current.get("duration_sec"))
        except ValueError as exc:
            raise ValueError("합방 구간을 확인하세요. 시작보다 끝이 늦어야 하며 방송 길이 안에서 구간이 겹치지 않아야 합니다. 이름은 빈 값이나 중복 없이 입력하세요. 한 멤버에 여러 VOD를 연결할 수 있으며, 같은 VOD를 다른 멤버에 연결하거나 한 멤버의 채널이 충돌하면 안 됩니다.") from exc
        previous_periods = {row["id"]: row for row in current.get("collaboration_periods", [])}
        if any(row != previous_periods.get(row["id"]) and row["source_kind"] != "manager_declared"
               for row in proposed["collaboration_periods"]):
            raise ValueError("관리자 편집으로 추가한 합방 멤버는 manager_declared 출처로 기록해야 합니다.")
    composed = current.get("projection_mode") == "composed_review_draft"
    if composed:
        editable_top_level.update({"review_revision_sha256", "manager_edited"})
    if {
        key: value for key, value in current.items() if key not in editable_top_level
    } != {
        key: value for key, value in proposed.items() if key not in editable_top_level
    }:
        raise ValueError("승인된 편집 packet의 출처와 상태는 바꿀 수 없습니다.")
    if composed:



        proposed = copy.deepcopy(proposed)
        for key, value in current.items():
            if key not in editable_top_level:
                proposed[key] = copy.deepcopy(value)

    def rows_by(rows: object, key: str) -> dict[str, dict]:
        return {
            str(row.get(key) or ""): row
            for row in rows or []
            if isinstance(row, dict)
        }

    current_points = rows_by((current.get("broadcast_map") or {}).get("points"), "id")
    proposed_points = rows_by((proposed.get("broadcast_map") or {}).get("points"), "id")
    current_stories = rows_by(current.get("stories"), "story_ref")
    proposed_stories = rows_by(proposed.get("stories"), "story_ref")

    referenced_point_ids = {
        str(point_ref or "")
        for highlight in current.get("editorial_highlights") or []
        if isinstance(highlight, dict)
        for point_ref in highlight.get("point_refs") or []
    }
    removed_point_ids = set(current_points) - set(proposed_points)
    if removed_point_ids & referenced_point_ids:
        raise ValueError("Highlight가 사용하는 Point는 연결된 Highlight를 먼저 정리해야 삭제할 수 있습니다.")
    if not composed:
        _validate_editorial_review_projection(proposed)

    def without(row: dict, *editable: str) -> dict:
        frozen = copy.deepcopy(row)
        for field in editable:
            frozen.pop(field, None)
        return frozen

    for point_id in set(current_points) & set(proposed_points):
        if without(current_points[point_id], "title", "timestamp", "content") != without(
            proposed_points[point_id], "title", "timestamp", "content"
        ):
            raise ValueError("승인된 Point의 ID, 근거와 Story 연결은 바꿀 수 없습니다.")

    for story_ref in set(current_stories) & set(proposed_stories):
        if without(current_stories[story_ref], "title", "why_notable") != without(
            proposed_stories[story_ref], "title", "why_notable"
        ):
            raise ValueError("승인된 Story의 근거와 역할 연결은 바꿀 수 없습니다.")

    new_point_ids = set(proposed_points) - set(current_points)
    new_story_refs = set(proposed_stories) - set(current_stories)
    expected_new_story_refs = {
        str(proposed_points[point_id].get("story_ref") or "")
        for point_id in new_point_ids
    }
    if new_story_refs != expected_new_story_refs:
        raise ValueError("새 Point는 1:1 Story 근거와 함께 추가해야 합니다.")
    for story_ref in new_story_refs:
        story = proposed_stories[story_ref]
        if story.get("roles") or story.get("evidence_refs"):
            raise ValueError("관리자가 추가한 Point에는 확인되지 않은 자동 근거를 붙일 수 없습니다.")

    current_highlights = rows_by(current.get("editorial_highlights"), "highlight_id")
    proposed_highlights = rows_by(proposed.get("editorial_highlights"), "highlight_id")
    if set(current_highlights) != set(proposed_highlights):
        raise ValueError("승인된 Highlight의 추가·삭제는 이 편집 화면에서 할 수 없습니다.")
    for highlight_id, current_highlight in current_highlights.items():
        proposed_highlight = proposed_highlights[highlight_id]
        from pipeline.highlight_story_window import project_editorial_highlight_durations
        project_editorial_highlight_durations(proposed_highlight, original=current_highlight)
        frozen_current = without(current_highlight, "title", "story_summary", "selection_reason",
                                 "total_source_duration_sec", "excluded_gap_duration_sec")
        frozen_proposed = without(proposed_highlight, "title", "story_summary", "selection_reason",
                                  "total_source_duration_sec", "excluded_gap_duration_sec")
        for frozen in (frozen_current, frozen_proposed):
            if "estimated_edited_duration" in frozen:
                frozen["estimated_edited_duration"].pop("min_sec", None)
                frozen["estimated_edited_duration"].pop("max_sec", None)
            for span in frozen.get("source_spans") or []:
                span.pop("start_sec", None)
                span.pop("end_sec", None)
                span.pop("duration_sec", None)
        if frozen_current != frozen_proposed:
            raise ValueError("승인된 Highlight의 ID, 근거와 연결은 바꿀 수 없습니다.")
    if composed:




        data = proposed.get("broadcast_map") or {}
        proposed = _rebind_editorial_packet_broadcast_map(proposed, {
            "ranges": list(data.get("d1") or []) + list(data.get("d2") or []),
            "points": list(data.get("points") or []),
        })
        _validate_editorial_review_projection(proposed)
    return copy.deepcopy(proposed)

def _rebind_editorial_packet_broadcast_map(
    packet: dict,
    expected_broadcast_map: dict | None = None,
) -> dict:
    """Bind a validated packet to the exact Map revision being saved."""

    rebound = copy.deepcopy(packet)
    if isinstance(expected_broadcast_map, dict):
        ranges = list(expected_broadcast_map.get("ranges") or [])

        def clock_seconds(value: object) -> int:
            parts = str(value or "").strip().split(":")
            if len(parts) != 3:
                return 0
            try:
                hour, minute, second = (int(part) for part in parts)
            except ValueError:
                return 0
            return max(0, hour * 3600 + minute * 60 + second)

        timed_ranges = [
            (
                row,
                clock_seconds(row.get("start")),
                clock_seconds(row.get("end")),
            )
            for row in ranges
            if isinstance(row, dict) and str(row.get("id") or "")
        ]
        timed_range_by_id = {
            str(row.get("id")): (row, range_start, range_end)
            for row, range_start, range_end in timed_ranges
        }

        def refs_for_interval(level: str, start_sec: float, end_sec: float) -> list[str]:
            effective_end = max(float(end_sec), float(start_sec) + 0.001)
            return [
                str(row.get("id"))
                for row, range_start, range_end in timed_ranges
                if row.get("level") == level
                and range_start < effective_end
                and float(start_sec) < range_end
            ]

        def ref_for_point(level: str, prior_ref: object, point_sec: int) -> str:
            prior_ref_text = str(prior_ref or "")
            prior_range = timed_range_by_id.get(prior_ref_text)
            if prior_range is not None:
                row, range_start, range_end = prior_range


                if (
                    row.get("level") == level
                    and range_start <= point_sec <= range_end
                ):
                    return prior_ref_text
            matching_refs = refs_for_interval(level, point_sec, point_sec)
            return matching_refs[0] if matching_refs else ""
        prior_points = {
            str(row.get("id") or ""): row
            for row in (rebound.get("broadcast_map") or {}).get("points") or []
            if str(row.get("id") or "")
        }
        prior_stories = {
            str(row.get("story_ref") or ""): row
            for row in rebound.get("stories") or []
            if str(row.get("story_ref") or "")
        }
        points: list[dict] = []
        stories: list[dict] = []
        for projected_point in expected_broadcast_map.get("points") or []:
            point = copy.deepcopy(
                prior_points.get(str(projected_point.get("id") or ""), {})
            )
            point.update(copy.deepcopy(projected_point))

            point.pop("story_why_notable", None)
            point_second = clock_seconds(point.get("timestamp"))
            if str(point.get("d1_ref") or ""):
                d1_ref = ref_for_point("D1", point.get("d1_ref"), point_second)
                if d1_ref:
                    point["d1_ref"] = d1_ref
                else:
                    point.pop("d1_ref", None)
            if str(point.get("d2_ref") or ""):
                d2_ref = ref_for_point("D2", point.get("d2_ref"), point_second)
                if d2_ref:
                    point["d2_ref"] = d2_ref
                else:
                    point.pop("d2_ref", None)
            story_ref = str(point.get("story_ref") or "")
            if not story_ref:
                seed = json.dumps(
                    {
                        "id": point.get("id"),
                        "timestamp": point.get("timestamp"),
                        "title": point.get("title"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                story_ref = "story-manager-" + hashlib.sha256(
                    seed.encode("utf-8")
                ).hexdigest()[:24]
                point["story_ref"] = story_ref
                point.setdefault("evidence_refs", [])
            story = copy.deepcopy(prior_stories.get(story_ref) or {
                "story_ref": story_ref,
                "title": str(point.get("title") or "Point"),
                "why_notable": str(
                    point.get("content") or point.get("title") or "관리자가 확인한 Point"
                ),
                "roles": {},
                "evidence_refs": [],
            })



            prior_point = prior_points.get(str(point.get("id") or ""), {})
            for point_field, story_field in (("title", "title"), ("content", "why_notable")):
                if (point_field in prior_point and point.get(point_field) != prior_point.get(point_field)
                        and story.get(story_field) == prior_point.get(point_field)):
                    story[story_field] = point[point_field]
            points.append(point)
            stories.append(story)
        rebound["stories"] = stories
        rebound["broadcast_map"] = {
            "d1": copy.deepcopy([
                row for row in ranges if row.get("level") == "D1"
            ]),
            "d2": copy.deepcopy([
                row for row in ranges if row.get("level") == "D2"
            ]),
            "points": points,
        }
        for highlight in rebound.get("editorial_highlights") or []:
            if not isinstance(highlight, dict):
                continue
            highlight_d1_refs: list[str] = []
            highlight_d2_refs: list[str] = []
            for span in highlight.get("source_spans") or []:
                if not isinstance(span, dict):
                    continue
                start_sec = float(span.get("start_sec") or 0)
                end_sec = float(span.get("end_sec") or start_sec)
                span_d1_refs = refs_for_interval("D1", start_sec, end_sec)
                span_d2_refs = refs_for_interval("D2", start_sec, end_sec)
                if span.get("d1_refs"):
                    span["d1_refs"] = span_d1_refs
                if span.get("d2_refs"):
                    span["d2_refs"] = span_d2_refs
                for ref in span_d1_refs:
                    if ref not in highlight_d1_refs:
                        highlight_d1_refs.append(ref)
                for ref in span_d2_refs:
                    if ref not in highlight_d2_refs:
                        highlight_d2_refs.append(ref)
            if highlight.get("d1_refs"):
                highlight["d1_refs"] = highlight_d1_refs
            if highlight.get("d2_refs"):
                highlight["d2_refs"] = highlight_d2_refs
    from pipeline.highlight_story_window import project_editorial_highlight_durations
    rebound["editorial_highlights"] = [
        project_editorial_highlight_durations(highlight)
        for highlight in rebound.get("editorial_highlights") or []
    ]
    broadcast_map = rebound.get("broadcast_map") or {}
    rebound["broadcast_map_sha256"] = broadcast_map_identity_hash(
        {
            "ranges": list(broadcast_map.get("d1") or [])
            + list(broadcast_map.get("d2") or []),
            "points": list(broadcast_map.get("points") or []),
        }
    )
    if rebound.get("projection_mode") == "composed_review_draft":
        from pipeline.editorial_review_import import seal_review_revision
        rebound = seal_review_revision(rebound)
    return rebound

def _merge_broadcast_map_sections_into_report_markdown(
    report_markdown: str,
    outline: dict,
    *,
    duration_sec: int | None,
    evidence_by_id: dict | None = None,
) -> str:
    """Update only the full report's BroadcastMap-owned Markdown sections."""

    projected = project_manager_outline_markdown(
        outline,
        duration_sec=duration_sec,
        evidence_by_id=evidence_by_id,
    )

    def section(text: str, heading: str) -> str:
        match = re.search(
            rf"^## {re.escape(heading)}\s*$\n.*?(?=^## |\Z)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise ValueError(f"normal report {heading} section missing")
        return match.group(0).rstrip() + "\n"

    merged = str(report_markdown or "")
    if not merged.strip():
        return projected
    for heading in ("방송 흐름", "주요 장면"):
        replacement = section(projected, heading)
        pattern = re.compile(
            rf"^## {re.escape(heading)}\s*$\n.*?(?=^## |\Z)",
            flags=re.MULTILINE | re.DOTALL,
        )
        if pattern.search(merged) is None:
            raise ValueError(f"normal report {heading} section missing")
        merged = pattern.sub(lambda _: replacement + "\n", merged, count=1)
    return merged.rstrip() + "\n"

def _editable_broadcast_map_markdown(markdown: str) -> str:
    """Read only the Map-owned sections of a full accepted report.

    The accepted Highlight tail is owned by its packet, and intentionally
    replaces the legacy review-window section in the full Markdown view.
    """
    text = _strip_editorial_review_markdown(markdown)
    sections = []
    for heading in ("방송 흐름", "주요 장면", "검토 범위"):
        matches = list(re.finditer(
            rf"^## {re.escape(heading)}\s*$\n.*?(?=^## |\Z)",
            text, flags=re.MULTILINE | re.DOTALL,
        ))
        if len(matches) != 1:
            if heading == "검토 범위" and not matches:
                sections.append("## 검토 범위\n")
                continue
            raise ValueError(f"'{heading}' 영역이 없거나 중복되었습니다.")
        sections.append(matches[0].group(0).rstrip() + "\n")
    return "\n".join(sections)

def _merge_virtual_markdown(current: dict, proposed: dict, markdown: str, duration: int) -> dict:
    """Merge two views of one draft, rejecting overlapping inconsistent edits."""
    parsed = parse_manager_outline_markdown(markdown, previous_outline=proposed, duration_sec=duration)
    merged = copy.deepcopy(proposed)
    fields_by_collection = {
        "ranges": ("level", "parent_id", "start", "end", "title", "content"),
        "points": ("timestamp", "title", "content"),
        "candidates": ("start", "end", "title", "content"),
    }
    for collection, fields in fields_by_collection.items():
        prior = {row["id"]: row for row in current.get(collection) or []}
        markdown_rows = {row["id"]: row for row in parsed.get(collection) or []}
        for row in merged.get(collection) or []:
            before = prior.get(row["id"], row)
            written = markdown_rows[row["id"]]
            for field in fields:
                old, structured, text = before.get(field), row.get(field), written.get(field)
                if structured != old and text != old and structured != text:
                    raise ValueError("Markdown과 선택 항목 편집에서 같은 값을 다르게 고쳤습니다. 한쪽 값을 맞춘 뒤 다시 저장해 주세요.")
                row[field] = structured if structured != old else text
    validate_structured_manager_outline(merged, duration_sec=duration)
    return merged

def _merge_virtual_markdown(current: dict, proposed: dict, markdown: str, duration: int) -> dict:
    """Merge two views of one draft, rejecting overlapping inconsistent edits."""
    parsed = parse_manager_outline_markdown(markdown, previous_outline=proposed, duration_sec=duration)
    merged = copy.deepcopy(proposed)
    fields_by_collection = {
        "ranges": ("level", "parent_id", "start", "end", "title", "content"),
        "points": ("timestamp", "title", "content"),
        "candidates": ("start", "end", "title", "content"),
    }
    for collection, fields in fields_by_collection.items():
        prior = {row["id"]: row for row in current.get(collection) or []}
        markdown_rows = {row["id"]: row for row in parsed.get(collection) or []}
        for row in merged.get(collection) or []:
            before = prior.get(row["id"], row)
            written = markdown_rows[row["id"]]
            for field in fields:
                old, structured, text = before.get(field), row.get(field), written.get(field)
                if structured != old and text != old and structured != text:
                    raise ValueError("Markdown과 선택 항목 편집에서 같은 값을 다르게 고쳤습니다. 한쪽 값을 맞춘 뒤 다시 저장해 주세요.")
                row[field] = structured if structured != old else text
    validate_structured_manager_outline(merged, duration_sec=duration)
    return merged

def manager_outline_edit_revision(report: dict) -> str:
    """Hash the existing editable report state, without creating new storage."""
    authority = report.get("manager_outline_authority") or {}
    family = report.get("summary_pipeline_integrity") or {}
    state = {
        "base": report.get("base"),
        "video_no": report.get("video_no"),
        "revision_id": authority.get("revision_id"),
        "generation_id": family.get("generation_id"),
        "structured_outline": report.get("structured_outline"),
        "qg1_review_packet": report.get("qg1_review_packet"),
        "manager_markdown": report.get("manager_markdown"),
        "md": report.get("md"),
        "source_clock": report.get("source_clock"),
    }
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
