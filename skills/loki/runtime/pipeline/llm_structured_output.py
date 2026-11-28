"""Structured validation helpers for narrow LLM output contracts.

The first production caller is the CHZZK comment timeline generator.  This
module deliberately uses only the standard library so the validation pattern can
be reused without adopting generated clients or a new dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

ALLOWED_TIMELINE_MOODS = frozenset(
    {"hot", "funny", "game", "chat", "warm", "tense", "spicy", "chill", "veryhot"}
)

COMMENT_TIMELINE_ALLOWED_FIELDS = frozenset({"tc", "tc_end", "title", "summary", "mood"})

MAX_TIMELINE_TITLE_CHARS = 35

MAX_TIMELINE_SUMMARY_CHARS = 200

@dataclass(frozen=True)
class StructuredOutputFailure:
    path: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}

class StructuredOutputValidationError(ValueError):
    """Raised when an LLM output fails a typed local contract."""

    def __init__(self, label: str, failures: list[StructuredOutputFailure]):
        self.label = label
        self.failures = tuple(failures)
        super().__init__(format_structured_failures(label, failures))

    def to_failure_dicts(self) -> list[dict[str, str]]:
        return [failure.as_dict() for failure in self.failures]

def format_structured_failures(label: str, failures: list[StructuredOutputFailure]) -> str:
    if not failures:
        return f"{label}: structured output validation failed"
    parts = [f"{failure.path}: {failure.code}" for failure in failures[:4]]
    if len(failures) > 4:
        parts.append(f"+{len(failures) - 4} more")
    return f"{label}: " + "; ".join(parts)

def _failure(path: str, code: str, message: str) -> StructuredOutputFailure:
    return StructuredOutputFailure(path=path, code=code, message=message)

def _parse_hms(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hh, mm, ss = [int(part) for part in parts]
    except ValueError:
        return None
    if hh < 0 or not (0 <= mm <= 59) or not (0 <= ss <= 59):
        return None
    return hh * 3600 + mm * 60 + ss

def validate_comment_timeline_items(items: list[Any]) -> list[dict[str, str]]:
    """Validate and normalize CHZZK comment timeline LLM items.

    Required fields: ``tc`` and ``title``. Optional fields: ``tc_end``,
    ``summary``, and ``mood``. Unknown item fields fail the whole output.
    """

    failures: list[StructuredOutputFailure] = []
    normalized: list[dict[str, str]] = []
    if not isinstance(items, list):
        raise StructuredOutputValidationError(
            "comment_timeline",
            [_failure("$", "wrong_type", "Expected a list of timeline items.")],
        )
    if not items:
        failures.append(_failure("$", "too_few_items", "Timeline output must contain at least one item."))

    for index, item in enumerate(items):
        path = f"$[{index}]"
        if not isinstance(item, dict):
            failures.append(_failure(path, "wrong_type", "Timeline item must be an object."))
            continue

        unknown = sorted(set(item) - COMMENT_TIMELINE_ALLOWED_FIELDS)
        if unknown:
            failures.append(
                _failure(
                    path,
                    "unknown_field",
                    f"Timeline item has unknown fields: {', '.join(unknown)}.",
                )
            )

        row: dict[str, str] = {}
        for field in ("tc", "title"):
            if field not in item:
                failures.append(_failure(f"{path}.{field}", "missing_field", f"Missing required field: {field}."))
                continue
            value = item[field]
            if not isinstance(value, str):
                failures.append(_failure(f"{path}.{field}", "wrong_type", f"{field} must be a string."))
                continue
            value = " ".join(value.strip().split())
            if not value:
                failures.append(_failure(f"{path}.{field}", "empty_value", f"{field} must not be empty."))
                continue
            row[field] = value

        tc_sec = _parse_hms(row.get("tc", ""))
        if "tc" in row and tc_sec is None:
            failures.append(_failure(f"{path}.tc", "invalid_timestamp", "tc must be HH:MM:SS with mm/ss <= 59."))

        if "title" in row and len(row["title"]) > MAX_TIMELINE_TITLE_CHARS:
            failures.append(
                _failure(
                    f"{path}.title",
                    "too_long",
                    f"title must be <= {MAX_TIMELINE_TITLE_CHARS} characters.",
                )
            )

        if "summary" in item:
            summary = item["summary"]
            if not isinstance(summary, str):
                failures.append(_failure(f"{path}.summary", "wrong_type", "summary must be a string."))
            else:
                summary = " ".join(summary.strip().split())
                if not summary:
                    failures.append(_failure(f"{path}.summary", "empty_value", "summary must not be empty."))
                elif len(summary) > MAX_TIMELINE_SUMMARY_CHARS:
                    failures.append(
                        _failure(
                            f"{path}.summary",
                            "too_long",
                            f"summary must be <= {MAX_TIMELINE_SUMMARY_CHARS} characters.",
                        )
                    )
                else:
                    row["summary"] = summary

        if "mood" in item:
            mood = item["mood"]
            if not isinstance(mood, str):
                failures.append(_failure(f"{path}.mood", "wrong_type", "mood must be a string."))
            else:
                mood = " ".join(mood.strip().split())
                if not mood:
                    failures.append(_failure(f"{path}.mood", "empty_value", "mood must not be empty."))
                elif mood not in ALLOWED_TIMELINE_MOODS:
                    failures.append(
                        _failure(
                            f"{path}.mood",
                            "invalid_enum",
                            "mood must be one of: " + ", ".join(sorted(ALLOWED_TIMELINE_MOODS)) + ".",
                        )
                    )
                else:
                    row["mood"] = mood

        if "tc_end" in item:
            tc_end = item["tc_end"]
            if not isinstance(tc_end, str):
                failures.append(_failure(f"{path}.tc_end", "wrong_type", "tc_end must be a string."))
            else:
                tc_end = tc_end.strip()
                tc_end_sec = _parse_hms(tc_end)
                if tc_end_sec is None:
                    failures.append(
                        _failure(f"{path}.tc_end", "invalid_timestamp", "tc_end must be HH:MM:SS with mm/ss <= 59.")
                    )
                elif isinstance(tc_sec, int) and tc_end_sec < tc_sec:
                    failures.append(_failure(f"{path}.tc_end", "invalid_range", "tc_end must be after tc."))
                elif tc_end:
                    row["tc_end"] = tc_end

        if all(field in row for field in ("tc", "title")):
            normalized.append(row)

    if failures:
        raise StructuredOutputValidationError("comment_timeline", failures)
    return normalized
