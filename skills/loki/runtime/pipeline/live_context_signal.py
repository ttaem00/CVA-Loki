"""Raw-free CHZZK live context signal contract helpers.

`live_context_signal.v1` rows preserve volatile public live metadata such as
title changes and viewer-count spikes as supporting timeline evidence. They are
not summary truth and they do not read browser storage, cookies, credentials, or
raw chat/transcript text.
"""

from __future__ import annotations

import hashlib

import json

import re

from datetime import datetime, timezone

from pathlib import Path

from typing import Any, Iterable

SCHEMA_VERSION = "live_context_signal.v1"

PRIVACY_CLASS = "public_metadata"

SUPPORTED_SOURCES = {
    "extension",
    "browser_probe",
    "documented_chzzk_openapi_metadata",
    "fixture",
    "manual_import",
    "unknown",
}

_SPACE_RE = re.compile(r"\s+")

def sanitize_public_title(value: Any, *, limit: int = 80) -> str:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    if not text:
        return ""
    return text[: limit - 1].rstrip() + "..." if len(text) > limit else text

def stable_text_hash(value: Any) -> str:
    text = sanitize_public_title(value, limit=200)
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

def parse_iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None

def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "").replace(" ", "")))
    except (TypeError, ValueError):
        return None

def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None

def _elapsed_from_observed_and_start(observed_at: datetime | None, live_start_at: datetime | None) -> int | None:
    if observed_at is None or live_start_at is None:
        return None
    elapsed = int(round((observed_at - live_start_at).total_seconds()))
    return elapsed if elapsed >= 0 else None

def _bucket_viewer_count(count: int | None) -> str:
    if count is None:
        return "unavailable"
    if count < 100:
        return "lt_100"
    if count < 1_000:
        return "100_999"
    if count < 5_000:
        return "1k_5k"
    if count < 10_000:
        return "5k_10k"
    if count < 50_000:
        return "10k_50k"
    return "50k_plus"

def _event_label(event_type: str, current_title: str, previous_title: str, viewer_count: int | None) -> str:
    if event_type == "live_title_change":
        if current_title and previous_title:
            return f"방송 제목 변경: {previous_title} -> {current_title}"
        if current_title:
            return f"방송 제목 변경: {current_title}"
        return "방송 제목 변경"
    if event_type == "viewer_count_spike":
        return f"시청자 급증: {viewer_count:,}명" if viewer_count is not None else "시청자 급증"
    if event_type == "viewer_count_sample":
        return f"시청자 수: {viewer_count:,}명" if viewer_count is not None else "시청자 수 샘플"
    if event_type == "live_start":
        return "라이브 시작"
    if event_type == "metadata_unavailable":
        return "라이브 맥락 수집 안 됨"
    return "라이브 맥락"

def normalize_live_context_signal(row: dict[str, Any], *, default_source: str = "unknown") -> dict[str, Any]:
    observed_at = parse_iso_datetime(row.get("observed_at") or row.get("createdAt") or row.get("timestamp"))
    live_start_at = parse_iso_datetime(row.get("live_start_at") or row.get("liveStartAt") or row.get("live_start_ms"))
    elapsed_sec = _safe_int(row.get("elapsed_sec"))
    if elapsed_sec is None:
        elapsed_sec = _elapsed_from_observed_and_start(observed_at, live_start_at)

    current_title = sanitize_public_title(
        row.get("title_current_sanitized")
        or row.get("title_current")
        or row.get("currentTitle")
        or row.get("title")
    )
    previous_title = sanitize_public_title(
        row.get("title_previous_sanitized")
        or row.get("title_previous")
        or row.get("previousTitle")
    )
    viewer_count = _safe_int(
        _first_value(
            row,
            "viewer_count",
            "viewerCount",
            "viewer_count_current",
            "viewerCountCurrent",
            "currentViewerCount",
            "current_viewer_count",
        )
    )
    event_type = str(row.get("event_type") or row.get("type") or "").strip()
    if event_type in {"title-change", "title_changed", "live_title_changed"}:
        event_type = "live_title_change"
    elif event_type in {"viewer-spike", "viewer_count_peak", "viewer_count_spike"}:
        event_type = "viewer_count_spike"
    elif event_type in {"viewer-count", "viewer_count", "viewer_count_sample"}:
        event_type = "viewer_count_sample"
    elif event_type in {"live-start", "live_started"}:
        event_type = "live_start"
    if not event_type:
        event_type = "viewer_count_sample" if viewer_count is not None else "metadata_unavailable"

    source = str(row.get("source") or default_source or "unknown").strip() or "unknown"
    if source not in SUPPORTED_SOURCES:
        source = "unknown"
    fetch_status = str(row.get("fetch_status") or row.get("status") or "ok").strip() or "ok"
    risk_flags = [str(flag) for flag in (row.get("risk_flags") or []) if flag]
    if elapsed_sec is None:
        risk_flags.append("live_context_missing_elapsed")
    if fetch_status != "ok":
        risk_flags.append("live_context_fetch_not_ok")
    if event_type in {"live_title_change", "viewer_count_spike", "viewer_count_sample", "metadata_unavailable"}:
        risk_flags.append("live_context_supporting_signal_not_truth")

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "event_id": "",
        "observed_at": _iso(observed_at),
        "channel_id": str(row.get("channel_id") or row.get("channelId") or "").strip(),
        "live_id": str(row.get("live_id") or row.get("liveId") or "").strip(),
        "stream_url": str(row.get("stream_url") or row.get("streamUrl") or row.get("url") or "").strip(),
        "live_start_at": _iso(live_start_at),
        "elapsed_sec": elapsed_sec,
        "title_current_sanitized": current_title,
        "title_previous_sanitized": previous_title,
        "title_current_hash": str(row.get("title_current_hash") or stable_text_hash(current_title)),
        "title_previous_hash": str(row.get("title_previous_hash") or stable_text_hash(previous_title)),
        "viewer_count": viewer_count,
        "viewer_count_bucket": str(
            _first_value(row, "viewer_count_bucket", "viewerCountBucket", "viewerBucket")
            or _bucket_viewer_count(viewer_count)
        ),
        "fetch_status": fetch_status,
        "source": source,
        "privacy_class": PRIVACY_CLASS,
        "confidence": str(row.get("confidence") or ("medium" if elapsed_sec is not None else "low")),
        "label": _event_label(event_type, current_title, previous_title, viewer_count),
        "risk_flags": sorted(set(risk_flags)),
    }
    seed = json.dumps(
        {
            "schema": SCHEMA_VERSION,
            "type": normalized["event_type"],
            "observed_at": normalized["observed_at"],
            "elapsed_sec": normalized["elapsed_sec"],
            "title_hash": normalized["title_current_hash"],
            "viewer_count": normalized["viewer_count"],
            "url": normalized["stream_url"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    normalized["event_id"] = str(row.get("event_id") or "livectx_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12])
    return normalized

def normalize_live_context_signals(rows: Iterable[dict[str, Any]], *, default_source: str = "unknown") -> list[dict[str, Any]]:
    normalized = [
        normalize_live_context_signal(row, default_source=default_source)
        for row in rows
        if isinstance(row, dict)
    ]
    return sorted(
        normalized,
        key=lambda item: (
            item.get("elapsed_sec") is None,
            float(item.get("elapsed_sec") or 0),
            str(item.get("observed_at") or ""),
            str(item.get("event_id") or ""),
        ),
    )

def load_live_context_signals(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    if not source_path.is_file():
        return []
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [
            normalize_live_context_signal(
                {
                    "event_type": "metadata_unavailable",
                    "fetch_status": "sidecar_unreadable",
                    "source": "unknown",
                    "confidence": "low",
                    "risk_flags": ["live_context_sidecar_unreadable"],
                }
            )
        ]
    rows: list[dict[str, Any]] = []
    try:
        if source_path.suffix.lower() == ".jsonl":
            malformed_count = 0
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    malformed_count += 1
                    continue
                if isinstance(value, dict):
                    rows.append(value)
            if malformed_count:
                rows.append(
                    {
                        "event_type": "metadata_unavailable",
                        "fetch_status": "malformed_sidecar",
                        "source": "unknown",
                        "confidence": "low",
                        "risk_flags": ["live_context_malformed_sidecar"],
                    }
                )
            return normalize_live_context_signals(rows)
        value = json.loads(text)
    except json.JSONDecodeError:
        return [
            normalize_live_context_signal(
                {
                    "event_type": "metadata_unavailable",
                    "fetch_status": "malformed_sidecar",
                    "source": "unknown",
                    "confidence": "low",
                    "risk_flags": ["live_context_malformed_sidecar"],
                }
            )
        ]
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            rows = [row for row in value["items"] if isinstance(row, dict)]
        elif isinstance(value.get("events"), list):
            rows = [row for row in value["events"] if isinstance(row, dict)]
        else:
            rows = [value]
    elif isinstance(value, list):
        rows = [row for row in value if isinstance(row, dict)]
    return normalize_live_context_signals(rows)
