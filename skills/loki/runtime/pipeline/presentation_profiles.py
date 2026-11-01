"""Pure, presentation-only projections for context-aware VOD review."""

from __future__ import annotations

import re

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "presentation_profile.v1"

RULE_VERSION = "presentation_profile_rules.v1"

MODES = ("viewer", "editor")

BROADCAST_PROFILES = (
    "game_match",
    "talk_story",
    "collab_multi_pov",
    "watch_event",
    "creative_work",
    "mixed_unknown",
)

CONFIDENCE_BANDS = ("high", "medium", "low", "unknown")

_SELECTION_SLOT_KEYS = (
    "context_range",
    "exact_moment",
    "story_window",
    "legacy_draft",
)

_NEUTRAL_ORDER = (
    "context_range",
    "exact_moment",
    "story_window",
    "interest",
    "edit_intent",
    "legacy_draft",
)

_PROFILE_ORDERS = {
    "game_match": (
        "exact_moment",
        "story_window",
        "context_range",
        "interest",
        "edit_intent",
        "legacy_draft",
    ),
    "talk_story": (
        "context_range",
        "story_window",
        "exact_moment",
        "interest",
        "edit_intent",
        "legacy_draft",
    ),
    "collab_multi_pov": (
        "story_window",
        "context_range",
        "exact_moment",
        "interest",
        "edit_intent",
        "legacy_draft",
    ),
    "watch_event": (
        "exact_moment",
        "interest",
        "story_window",
        "context_range",
        "edit_intent",
        "legacy_draft",
    ),
    "creative_work": (
        "context_range",
        "edit_intent",
        "story_window",
        "exact_moment",
        "interest",
        "legacy_draft",
    ),
    "mixed_unknown": _NEUTRAL_ORDER,
}

_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,159}$")

_URL_SCHEME_RE = re.compile(r"^(?:https?|ftp|file|mailto):", re.IGNORECASE)

_FORBIDDEN_KEYS = {
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "local_path",
    "media_path",
    "password",
    "private_note",
    "raw",
    "raw_audio",
    "raw_chat",
    "raw_frame",
    "raw_ocr",
    "raw_report",
    "raw_transcript",
    "secret",
    "token",
    "transcript_text",
}

class PresentationProfileError(ValueError):
    """Raised when projection input violates the bounded presentation contract."""

def build_presentation_profile(
    *,
    mode: str,
    broadcast_profile: str,
    confidence_band: str = "unknown",
    segment_role_ref: str | None = None,
    state_ref: str | None = None,
    selection_refs: Mapping[str, str | None] | None = None,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Return deterministic hints without interpreting C0-owned opaque refs."""

    _require_member("mode", mode, MODES)
    _require_member("broadcast_profile", broadcast_profile, BROADCAST_PROFILES)
    _require_member("confidence_band", confidence_band, CONFIDENCE_BANDS)
    safe_segment_role_ref = _opaque_ref(segment_role_ref, "segment_role_ref")
    safe_state_ref = _opaque_ref(state_ref, "state_ref")
    safe_selection_refs = _selection_ref_map(selection_refs)
    safe_evidence_refs = _opaque_ref_list(evidence_refs, "evidence_refs")

    neutral_reason = _fallback_reason(broadcast_profile, confidence_band)
    order = _NEUTRAL_ORDER if neutral_reason else _PROFILE_ORDERS[broadcast_profile]
    profile_id = f"neutral.{mode}" if neutral_reason else f"{broadcast_profile}.{mode}"

    if mode == "viewer":
        current_work_actions: tuple[str, ...] = ()
        visible_tools = (
            "context_chapters",
            "exact_seek",
            "story_window",
            "heatmap",
            "viewer_clips",
            "chat_density",
        )
        details = ("interest", "edit_intent", "legacy_draft")
    else:
        current_work_actions = ("approve", "reject", "mark_unknown")
        visible_tools = (
            "context_chapters",
            "exact_seek",
            "story_window",
            "heatmap",
            "viewer_clips",
            "chat_density",
            "evidence",
            "boundary_alternatives",
            *current_work_actions,
            "trim",
            "save_reopen",
        )
        details = ("evidence", "boundary_alternatives", "legacy_draft")

    projection = {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "profile_id": profile_id,
        "mode": mode,
        "broadcast_profile": broadcast_profile,
        "confidence_band": confidence_band,
        "fallback_reason": neutral_reason,
        "hints": {
            "order": list(order),
            "emphasis": list(order[:3]),
            "visible_tools": list(visible_tools),
            "current_work_actions": {
                "optional": list(current_work_actions),
                "corpus_prerequisite": False,
                "learning_prerequisite": False,
            },
            "disclosure": {
                "initial": list(order[:3]),
                "details": list(details),
                "segment_role_ref": safe_segment_role_ref,
                "state_ref": safe_state_ref,
                "selection_refs": safe_selection_refs,
                "evidence_refs": safe_evidence_refs,
            },
        },
        "authority": {
            "presentation_only": True,
            "may_change_order": True,
            "may_change_emphasis": True,
            "may_change_disclosure": True,
            "may_change_truth": False,
            "may_change_rank": False,
            "may_validate_c0_vocabulary": False,
            "may_merge_semantic_axes": False,
        },
        "privacy": {
            "presentation_safe": True,
            "includes_source_content": False,
            "includes_local_paths": False,
        },
    }
    _assert_presentation_safe(projection)
    return projection

def _require_member(name: str, value: str, allowed: Sequence[str]) -> None:
    if value not in allowed:
        raise PresentationProfileError(
            f"{name} must be one of: {', '.join(allowed)}"
        )

def _opaque_ref(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PresentationProfileError(f"{name} must be an opaque string or null")
    ref = value
    if not _OPAQUE_REF_RE.fullmatch(ref) or _URL_SCHEME_RE.match(ref):
        raise PresentationProfileError(
            f"{name} must be a compact public-safe reference string"
        )
    _assert_presentation_safe(ref, f"$.{name}")
    return ref

def _selection_ref_map(
    values: Mapping[str, str | None] | None,
) -> dict[str, str | None]:
    if values is None:
        values = {key: None for key in _SELECTION_SLOT_KEYS}
    if not isinstance(values, Mapping):
        raise PresentationProfileError("selection_refs must be an object")
    if set(values) != set(_SELECTION_SLOT_KEYS):
        raise PresentationProfileError(
            "selection_refs must contain exactly the four presentation slots"
        )
    return {
        key: _opaque_ref(values[key], f"selection_refs.{key}")
        for key in _SELECTION_SLOT_KEYS
    }

def _opaque_ref_list(values: Sequence[str], name: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise PresentationProfileError(f"{name} must be a sequence of opaque refs")
    refs: list[str] = []
    for index, value in enumerate(values):
        ref = _opaque_ref(value, f"{name}[{index}]")
        assert ref is not None
        if ref not in refs:
            refs.append(ref)
    return refs

def _fallback_reason(broadcast_profile: str, confidence_band: str) -> str | None:
    if confidence_band in {"low", "unknown"}:
        return f"{confidence_band}_confidence"
    if broadcast_profile == "mixed_unknown":
        return "mixed_unknown_profile"
    return None

def _assert_presentation_safe(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in _FORBIDDEN_KEYS or normalized_key.endswith("_path"):
                raise PresentationProfileError(
                    f"private or source-content field is not presentation-safe: {path}.{key}"
                )
            _assert_presentation_safe(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _assert_presentation_safe(child, f"{path}[{index}]")
    elif isinstance(value, str):
        text = value.strip()
        if (
            _WINDOWS_ABSOLUTE_PATH_RE.match(text)
            or text.startswith(("/", "\\\\", "file://"))
        ):
            raise PresentationProfileError(
                f"local path is not presentation-safe: {path}"
            )
