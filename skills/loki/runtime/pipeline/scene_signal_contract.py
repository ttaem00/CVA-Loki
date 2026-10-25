"""Versioned scene-finder signal contract.

The contract is a checked-in JSON artifact so producer, processor, and UI code
cannot silently disagree about lane keys, labels, roles, or obsolete aliases.
"""

from __future__ import annotations

import json

from functools import lru_cache

from pathlib import Path

from typing import Any, Mapping

SCHEMA_VERSION = "scene_signal_contract.v1"

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "scene_signal_contract.v1.json"

class SceneSignalContractError(ValueError):
    """Raised when the scene signal contract or payload violates the contract."""

@lru_cache(maxsize=1)
def load_scene_signal_contract(path: str | Path | None = None) -> dict[str, Any]:
    contract_path = Path(path) if path is not None else CONTRACT_PATH
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SceneSignalContractError("scene signal contract must be a JSON object")
    validate_scene_signal_contract(payload)
    return payload

def validate_scene_signal_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise SceneSignalContractError("schema_version must be scene_signal_contract.v1")
    lanes = contract.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise SceneSignalContractError("lanes must be a non-empty list")
    display_roles = set(_string_list(contract.get("display_roles"), "display_roles"))
    event_roles = set(_string_list(contract.get("event_roles"), "event_roles"))
    privacy_classes = set(_string_list(contract.get("privacy_classes"), "privacy_classes"))
    seen: set[str] = set()
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise SceneSignalContractError("lane entries must be objects")
        key = _non_empty_string(lane.get("key"), "lane.key")
        if key in seen:
            raise SceneSignalContractError(f"duplicate lane key: {key}")
        seen.add(key)
        for field in ("label", "viewer_label", "purpose", "edit_candidate_role", "color", "empty_reason"):
            _non_empty_string(lane.get(field), f"{key}.{field}")
        if lane.get("display_role") not in display_roles:
            raise SceneSignalContractError(f"{key}.display_role is not allowed")
        if lane.get("default_event_role") not in event_roles:
            raise SceneSignalContractError(f"{key}.default_event_role is not allowed")
        if lane.get("privacy_class") not in privacy_classes:
            raise SceneSignalContractError(f"{key}.privacy_class is not allowed")
    aliases = contract.get("obsolete_aliases")
    if not isinstance(aliases, list):
        raise SceneSignalContractError("obsolete_aliases must be a list")
    for alias in aliases:
        if not isinstance(alias, Mapping):
            raise SceneSignalContractError("obsolete_aliases entries must be objects")
        key = _non_empty_string(alias.get("key"), "obsolete_alias.key")
        canonical = _non_empty_string(alias.get("canonical_lane"), f"{key}.canonical_lane")
        if key in seen:
            raise SceneSignalContractError(f"obsolete alias conflicts with active lane: {key}")
        if canonical not in seen:
            raise SceneSignalContractError(f"obsolete alias has unknown canonical_lane: {canonical}")

def lane_definitions() -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in load_scene_signal_contract()["lanes"])

def lane_keys() -> tuple[str, ...]:
    return tuple(str(row["key"]) for row in lane_definitions())

def lane_labels() -> dict[str, str]:
    return {str(row["key"]): str(row["label"]) for row in lane_definitions()}

def lane_viewer_labels() -> dict[str, str]:
    return {str(row["key"]): str(row["viewer_label"]) for row in lane_definitions()}

def lane_colors() -> dict[str, str]:
    return {str(row["key"]): str(row["color"]) for row in lane_definitions()}

def lane_purposes() -> dict[str, str]:
    return {str(row["key"]): str(row["purpose"]) for row in lane_definitions()}

def lane_empty_reasons() -> dict[str, str]:
    return {str(row["key"]): str(row["empty_reason"]) for row in lane_definitions()}

def overview_lane_keys() -> frozenset[str]:
    return frozenset(str(row["key"]) for row in lane_definitions() if row.get("display_role") == "overview")

def scene_lane_keys() -> frozenset[str]:
    return frozenset(str(row["key"]) for row in lane_definitions() if row.get("display_role") == "scene")

def manager_only_lane_keys(*, include_obsolete_aliases: bool = True) -> frozenset[str]:
    """Return lanes that may be used internally but must not reach public artifacts."""

    hidden = {
        str(row["key"])
        for row in lane_definitions()
        if row.get("privacy_class") != "public_safe"
    }
    if include_obsolete_aliases:
        hidden.update(
            alias
            for alias, canonical in obsolete_lane_aliases().items()
            if canonical in hidden
        )
    return frozenset(hidden)

def obsolete_lane_aliases() -> dict[str, str]:
    aliases = load_scene_signal_contract()["obsolete_aliases"]
    return {str(row["key"]): str(row["canonical_lane"]) for row in aliases}

def canonical_lane_key(lane: Any) -> str:
    key = str(lane or "").strip()
    return obsolete_lane_aliases().get(key, key)

def validate_lane_payload(lanes: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    lane_key_set = {str(row.get("key") or "") for row in lanes if isinstance(row, dict)}
    event_lane_set = {str(row.get("lane") or "") for row in events if isinstance(row, dict)}
    allowed = set(lane_keys())
    obsolete = set(obsolete_lane_aliases())
    overview = overview_lane_keys()
    scene = scene_lane_keys()
    overview_wrong = sorted(
        key
        for key in overview
        if any(str(row.get("key") or "") == key and str(row.get("display_role") or "") != "overview" for row in lanes)
    )
    scene_wrong = sorted(
        key
        for key in scene
        if any(str(row.get("key") or "") == key and str(row.get("display_role") or "") != "scene" for row in lanes)
    )
    findings = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "obsolete_lane_keys": sorted((lane_key_set | event_lane_set) & obsolete),
        "unknown_lane_keys": sorted((lane_key_set | event_lane_set) - allowed - obsolete - {""}),
        "overview_role_mismatch": overview_wrong,
        "scene_role_mismatch": scene_wrong,
    }
    if any(findings[key] for key in findings if key not in {"schema_version", "status"}):
        findings["status"] = "FAIL"
    return findings

def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SceneSignalContractError(f"{field_name} must be a non-empty list")
    strings = []
    for item in value:
        strings.append(_non_empty_string(item, field_name))
    return strings

def _non_empty_string(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SceneSignalContractError(f"{field_name} is required")
    return text
