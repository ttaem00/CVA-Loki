"""Optional lazy adapter for `ruptures` boundary suggestions.

The caller always owns the deterministic fallback. This adapter only returns a
compact receipt and optional bucket indexes when the local dependency is
available and the input stays within a small bounded budget.
"""

from __future__ import annotations

import importlib

import math

from typing import Any

SCHEMA_VERSION = "ruptures_boundary_receipt.v1"

PROVIDER_NAME = "ruptures"

PROVIDER_VERSION = "lazy"

_MAX_ROWS = 1200

_MAX_COLUMNS = 16

def _unavailable(status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER_NAME,
        "provider_version": PROVIDER_VERSION,
        "status": status,
        "reason": reason,
        "boundary_bucket_indexes": [],
        "provenance": "deterministic_fallback",
    }

def _numeric_matrix(feature_matrix: Any) -> list[list[float]]:
    if not isinstance(feature_matrix, list) or not feature_matrix:
        raise ValueError("feature_matrix must be a non-empty list")
    rows: list[list[float]] = []
    width = None
    for row in feature_matrix:
        if not isinstance(row, list) or not row:
            raise ValueError("feature_matrix rows must be non-empty lists")
        numeric_row: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("feature_matrix must contain finite numbers")
            numeric_row.append(float(value))
        if width is None:
            width = len(numeric_row)
        elif len(numeric_row) != width:
            raise ValueError("feature_matrix rows must have the same width")
        rows.append(numeric_row)
    return rows

def detect_boundary_buckets(
    feature_matrix: Any,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """Return optional boundary bucket indexes without changing fallback output."""

    if not enabled:
        return _unavailable("disabled", "provider_disabled")

    try:
        matrix = _numeric_matrix(feature_matrix)
    except ValueError:
        return _unavailable("unavailable", "malformed_feature_matrix")

    if len(matrix) > _MAX_ROWS or len(matrix[0]) > _MAX_COLUMNS:
        return _unavailable("unavailable", "budget_exceeded")

    try:
        ruptures = importlib.import_module("ruptures")
        numpy = importlib.import_module("numpy")
    except Exception:
        return _unavailable("unavailable", "missing_dependency")

    try:
        requested_boundaries = min(24, max(1, len(matrix) // 60))
        signal = numpy.asarray(matrix, dtype=float)
        model = ruptures.Binseg(model="l2", min_size=2, jump=1).fit(signal)
        predicted = model.predict(n_bkps=requested_boundaries)
        indexes = sorted(
            {
                int(value)
                for value in predicted
                if isinstance(value, int) and 0 < int(value) < len(matrix)
            }
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": PROVIDER_NAME,
            "provider_version": getattr(ruptures, "__version__", PROVIDER_VERSION),
            "status": "available",
            "reason": "",
            "boundary_bucket_indexes": indexes,
            "provenance": "optional_provider",
            "row_count": len(matrix),
            "column_count": len(matrix[0]),
            "algorithm": "binseg_l2",
            "requested_boundary_count": requested_boundaries,
        }
    except Exception:
        return _unavailable("unavailable", "provider_failure")
