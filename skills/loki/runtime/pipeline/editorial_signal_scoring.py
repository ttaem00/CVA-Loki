"""Conservative observed-signal support for exploration, not editorial value.

This provisional heuristic is neither a trained model nor a confidence estimate.
Adapters own same-VOD/native-clock binding, collection coverage, and consistent
nonnegative feature units. V1 uses fixed scale 1.0 and x / (1 + x), so a candidate
does not change score when unrelated candidates or page boundaries change. Unit
changes require a policy change; these are not universal cross-VOD grades.

Missing features retain None and their weight is not redistributed. Coverage
reports observed weight separately; zero support is not evidence of low editing
value. Callers retain original order and decide whether to display the result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import math

from types import MappingProxyType

from typing import Any

MODEL_VERSION = "editorial-signal-support.fixed-saturation-v1"

DEFAULT_WEIGHTS = MappingProxyType({
    "chat_density": 0.20,
    "laughter": 0.15,
    "question": 0.10,
    "surprise": 0.05,
    "clips": 0.25,
    "comments": 0.15,
    "viewers": 0.10,
})

_FEATURES = tuple(DEFAULT_WEIGHTS)

_CHAT_FEATURES = ("chat_density", "laughter", "question", "surprise")

def _number(value: Any, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"invalid_{field}_number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"invalid_{field}_number") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"invalid_{field}_number")
    return number

def _weights(value: Mapping[str, float] | None) -> tuple[dict[str, float], float]:
    source = DEFAULT_WEIGHTS if value is None else value
    if not isinstance(source, Mapping) or set(source) != set(_FEATURES):
        raise ValueError("invalid_weight_features")
    weights = {name: _number(source[name], "weight") for name in _FEATURES}
    if any(weight > 1 for weight in weights.values()):
        raise ValueError("invalid_weight_range")
    total = math.fsum(weights.values())
    if total == 0:
        raise ValueError("invalid_weight_total")
    if math.fsum(weights[name] for name in _CHAT_FEATURES) > total * 0.5:
        raise ValueError("chat_weight_budget_exceeded")
    return weights, total

def score_candidates(
    candidates: Iterable[Mapping[str, Any]],
    weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Return stable support traces in input order without modifying candidates.

    Omitted feature keys mean None. Custom weights name all seven features,
    contain finite values in [0, 1], have a positive total, and reserve at most
    half that total for correlated chat features. Contributions are score points
    against the fixed total weight, not percentages of observed features.
    An observed feature with zero weight remains observed but adds no support.
    """
    selected_weights, total = _weights(weights)
    if isinstance(candidates, (str, bytes, Mapping)):
        raise ValueError("invalid_candidates")
    try:
        rows = iter(candidates)
    except TypeError as error:
        raise ValueError("invalid_candidates") from error
    result, seen = [], set()
    for candidate in rows:
        if (not isinstance(candidate, Mapping)
                or set(candidate) != {"id", "start_sec", "end_sec", "features"}):
            raise ValueError("invalid_candidate_shape")
        identifier = candidate["id"]
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError("invalid_candidate_id")
        seen.add(identifier)
        start = _number(candidate["start_sec"], "time")
        end = _number(candidate["end_sec"], "time")
        if end < start:
            raise ValueError("invalid_time_order")
        features = candidate["features"]
        if not isinstance(features, Mapping) or set(features) - set(_FEATURES):
            raise ValueError("invalid_feature_names")
        raw, normalized, contributions, missing = {}, {}, {}, []
        for name in _FEATURES:
            value = features.get(name)
            raw[name] = value
            if value is None:
                normalized[name] = contributions[name] = None
                missing.append(name)
            else:
                strength = _number(value, "feature")
                normalized[name] = strength / (1.0 + strength)
                contributions[name] = normalized[name] * (selected_weights[name] / total) * 100
        observed = [name for name in _FEATURES if raw[name] is not None]
        result.append(dict(
            id=identifier, start_sec=candidate["start_sec"], end_sec=candidate["end_sec"],
            score=(min(100.0, math.fsum(contributions[name] for name in observed))
                   if observed else None),
            coverage=min(1.0, math.fsum(selected_weights[name] for name in observed) / total),
            raw_features=raw, normalized=normalized, contributions=contributions,
            missing_features=missing, model_version=MODEL_VERSION,
        ))
    return result
