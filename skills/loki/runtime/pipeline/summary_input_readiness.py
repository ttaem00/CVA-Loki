"""Deterministic summary-input readiness metadata.

This module describes whether the inputs that feed the VOD summary path were
available before report generation.  It intentionally records counts, statuses,
and retry hints only; it must not include raw transcript, chat, community, or
private config content.
"""

from __future__ import annotations

from collections import Counter

from copy import deepcopy

from typing import Any

DIAGNOSTICS_SCHEMA_VERSION = "summary_input_diagnostics.v1"

def _segment_ledger_detail(segment_ledger: list | None) -> tuple[bool, dict[str, Any]]:
    if not isinstance(segment_ledger, list) or not segment_ledger:
        return False, {}
    kinds = Counter(str(item.get("kind") or "unknown") for item in segment_ledger if isinstance(item, dict))
    return True, {
        "segment_count": len(segment_ledger),
        "kind_counts": dict(sorted(kinds.items())),
    }

def _overall_status(items: list[dict[str, Any]]) -> str:
    if any(item["required"] and item["status"] != "present" for item in items):
        return "blocked_required_missing"
    if any(item["status"] not in {"present", "not_configured"} for item in items):
        return "ready_with_gaps"
    return "ready"

def finalize_summary_input_readiness(
    readiness: dict[str, Any] | None,
    *,
    segment_ledger: list | None,
) -> dict[str, Any] | None:
    """Return readiness with final segment-ledger availability added."""

    if not isinstance(readiness, dict):
        return None
    finalized = deepcopy(readiness)
    items = finalized.get("items")
    if not isinstance(items, list):
        return finalized

    ledger_present, ledger_detail = _segment_ledger_detail(segment_ledger)
    for item in items:
        if isinstance(item, dict) and item.get("key") == "segment_ledger":
            item["status"] = "present" if ledger_present else "missing_optional"
            item["reason"] = (
                "렌더링된 리포트의 segment ledger가 있습니다."
                if ledger_present
                else "최종 Markdown에서 segment ledger를 만들 수 없었습니다."
            )
            item["retry_condition"] = "summary Markdown parser/metadata generation을 확인한 뒤 다시 렌더링합니다."
            item["detail"] = ledger_detail if ledger_present else {"segment_count": 0}
            break
    finalized["status"] = _overall_status([item for item in items if isinstance(item, dict)])
    finalized["quality_claim"] = "not_evaluated"
    return finalized

def _diagnostic_message(item: dict[str, Any]) -> str:
    key = str(item.get("key") or "")
    status = str(item.get("status") or "")
    name = str(item.get("name") or key or "입력")
    if key == "chat" and status == "partial":
        return "이번 요약은 채팅 일부만 반영됨"
    if key == "chat" and status == "missing_optional":
        return "이번 요약은 채팅 없이 자막 중심으로 생성됨"
    if key == "audio_peaks" and status == "missing_optional":
        return "오디오 반응 신호 없음"
    if key == "timeline_comments" and status == "missing_optional":
        return "시청자 시간표 댓글 신호 없음"
    if key == "user_clips" and status == "missing_optional":
        return "시청자 클립 근거 없음"
    if key == "community" and status == "missing_optional":
        return "커뮤니티 반응 후보 없음"
    if key == "community" and status == "partial":
        return "커뮤니티 반응 캐시가 오래됐을 수 있음"
    if key == "community" and status == "not_configured":
        return "커뮤니티 반응 수집 안 함"
    if status == "missing_required":
        return f"{name} 없음"
    if status == "partial":
        return f"{name} 일부만 반영됨"
    if status == "pending":
        return f"{name} 아직 확인 중"
    if status == "missing_optional":
        return f"{name} 없음"
    return str(item.get("reason") or name)

def _diagnostic_detail_label(item: dict[str, Any]) -> str:
    key = str(item.get("key") or "")
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    if key == "chat":
        latest = str(detail.get("latest_message_hms") or "")
        expected = str(detail.get("expected_duration_hms") or "")
        if latest and expected:
            return f"채팅 범위 {latest} / VOD {expected}"
        count = detail.get("message_count")
        if count is not None:
            return f"채팅 {int(count or 0)}개"
    if key == "audio_peaks":
        return f"피크 {int(detail.get('peak_count') or 0)}개"
    if key == "timeline_comments":
        return f"시간표 줄 {int(detail.get('timed_line_count') or 0)}개"
    if key == "user_clips":
        return f"클립 {int(detail.get('total') or 0)}개"
    if key == "community":
        label = f"후보 {int(detail.get('post_count') or 0)}개"
        source = str(detail.get("source") or "")
        cache_status = str(detail.get("cache_status") or "")
        if source and source != "none":
            label += f" / {source}"
        if cache_status and cache_status != "not_applicable":
            label += f" / {cache_status}"
        return label
    if key == "srt":
        filename = str(detail.get("filename") or "")
        return filename
    return ""

def build_summary_input_diagnostics(readiness: dict[str, Any] | None) -> dict[str, Any] | None:
    """Derive manager-facing, no-raw-content diagnostics from readiness metadata."""

    if not isinstance(readiness, dict):
        return None
    items = [item for item in (readiness.get("items") or []) if isinstance(item, dict)]
    visible_statuses = {"missing_required", "missing_optional", "partial", "pending"}
    visible_items: list[dict[str, Any]] = []
    counts = Counter(str(item.get("status") or "unknown") for item in items)
    for item in items:
        status = str(item.get("status") or "")
        key = str(item.get("key") or "")
        if status not in visible_statuses and not (key == "community" and status == "not_configured"):
            continue
        severity = "error" if status == "missing_required" else "warning"
        if status in {"missing_optional", "pending", "not_configured"}:
            severity = "info"
        visible_items.append(
            {
                "key": key,
                "name": str(item.get("name") or item.get("key") or ""),
                "status": status,
                "severity": severity,
                "message": _diagnostic_message(item),
                "detail_label": _diagnostic_detail_label(item),
                "retry_condition": str(item.get("retry_condition") or ""),
            }
        )

    required_missing = counts.get("missing_required", 0)
    partial = counts.get("partial", 0)
    optional_missing = counts.get("missing_optional", 0)
    pending = counts.get("pending", 0)
    if required_missing:
        manager_label = "요약 입력 확인 필요"
        manager_message = "필수 입력이 없어 최종 요약 생성 전에 재실행이 필요합니다."
    elif partial or optional_missing or pending:
        manager_label = "요약 입력 일부 부족"
        manager_message = "요약은 볼 수 있지만 일부 보조 신호가 빠졌거나 부분 반영되었습니다."
    else:
        manager_label = "요약 입력 준비됨"
        manager_message = "필수 입력과 보조 입력 상태에서 바로 막히는 항목은 없습니다."

    return {
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "status": str(readiness.get("status") or ""),
        "manager_label": manager_label,
        "manager_message": manager_message,
        "quality_claim": "not_evaluated",
        "summary": {
            "present_count": counts.get("present", 0),
            "required_missing_count": required_missing,
            "optional_missing_count": optional_missing,
            "partial_count": partial,
            "pending_count": pending,
            "not_configured_count": counts.get("not_configured", 0),
        },
        "items": visible_items,
    }
