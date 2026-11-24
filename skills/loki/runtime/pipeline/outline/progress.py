"""Validated review projections kept independently of downstream investigation."""

from __future__ import annotations

def author_budget_stopped(reason):
    return isinstance(reason, str) and (
        reason.startswith("production_author_budget_exhausted:")
        or reason == "production_author_budget_usage_exceeded")
