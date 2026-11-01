"""LLM-safe single and parallel entry points for CHZZK media tools."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from copy import deepcopy

from pathlib import Path

from typing import Any, Iterable, Mapping

from .contracts import MediaToolError, ToolRequest

from .executor import SharedSourcePool, acquire_request

def _safe_failure(request_id: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, MediaToolError):
        return {
            "status": "error",
            "request_id": request_id,
            "error": {"code": exc.code, "message": exc.public_message},
        }
    return {
        "status": "error",
        "request_id": request_id,
        "error": {"code": "media_tool_failed", "message": "the media tool failed unexpectedly"},
    }

def execute_tool_call(
    payload: Mapping[str, Any],
    output_root: str | Path,
    *,
    cookies: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and execute one LLM-produced action payload."""

    request_id = str(payload.get("request_id", ""))[:120] if isinstance(payload, Mapping) else ""
    try:
        request = ToolRequest.from_mapping(payload)
        result = acquire_request(request, Path(output_root), cookies=cookies)
        result["request_id"] = request.request_id
        return result
    except Exception as exc:
        return _safe_failure(request_id, exc)

def execute_tool_batch(
    payloads: Iterable[Mapping[str, Any]],
    output_root: str | Path,
    *,
    max_workers: int = 4,
    cookies: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Dedupe equivalent requests, run them concurrently, and preserve input order."""

    workers = max(1, min(int(max_workers), 8))
    incoming = list(payloads)
    parsed: list[ToolRequest | None] = []
    failures: dict[int, dict[str, Any]] = {}
    unique: dict[str, ToolRequest] = {}
    for index, payload in enumerate(incoming):
        request_id = str(payload.get("request_id", ""))[:120] if isinstance(payload, Mapping) else ""
        try:
            request = ToolRequest.from_mapping(payload)
        except Exception as exc:
            parsed.append(None)
            failures[index] = _safe_failure(request_id, exc)
            continue
        parsed.append(request)
        unique.setdefault(request.fingerprint, request.with_request_id(""))

    completed: dict[str, dict[str, Any]] = {}
    source_pool = SharedSourcePool()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chzzk-media") as pool:
        futures = {
            pool.submit(
                acquire_request,
                request,
                Path(output_root),
                cookies=cookies,
                source_pool=source_pool,
            ): fingerprint
            for fingerprint, request in unique.items()
        }
        for future in as_completed(futures):
            fingerprint = futures[future]
            try:
                completed[fingerprint] = future.result()
            except Exception as exc:
                completed[fingerprint] = _safe_failure("", exc)

    results: list[dict[str, Any]] = []
    for index, request in enumerate(parsed):
        if request is None:
            results.append(failures[index])
            continue
        result = deepcopy(completed[request.fingerprint])
        result["request_id"] = request.request_id
        results.append(result)
    return {
        "schema_version": 1,
        "status": "ok" if all(row.get("status") == "ok" for row in results) else "partial",
        "request_count": len(incoming),
        "unique_execution_count": len(unique),
        "results": results,
    }
