"""One public version registry for fresh selections and preserved job identities."""

from functools import lru_cache

import json

from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[1] / "vendor/chat-analyze-engine/config/loki_versions.json"

@lru_cache(maxsize=1)
def load_loki_version_policy() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    rows = policy.get("pipelines", {})
    current = policy.get("current_pipeline")
    if (policy.get("schema_version") != "loki.version-policy.v1"
            or not isinstance(policy.get("product_name"), str) or not policy["product_name"]
            or not isinstance(rows, dict) or current not in rows
            or rows[current].get("kind") != "full_author"
            or policy.get("new_choices") != ["latest", current]
            or not isinstance(policy.get("aliases"), dict)
            or any(target not in rows for target in policy["aliases"].values())):
        raise ValueError("loki_version_policy_invalid")
    for row in rows.values():
        if (not isinstance(row, dict) or row.get("kind") not in {"legacy", "structured", "editorial", "full_author"}
                or row["kind"] == "legacy" and not isinstance(row.get("label"), str)
                or row["kind"] != "legacy" and not isinstance(row.get("version"), str)):
            raise ValueError("loki_version_policy_invalid")
    return policy

def pipelines_for(*kinds: str) -> frozenset[str]:
    return frozenset(name for name, row in load_loki_version_policy()["pipelines"].items()
                     if not kinds or row["kind"] in kinds)

def normalize_pipeline(value: str) -> str:
    policy = load_loki_version_policy()
    if value == "latest":
        return policy["current_pipeline"]
    return policy["aliases"].get(value, value) if isinstance(value, str) else value

def select_new_pipeline(value: str) -> str:
    if not isinstance(value, str) or value not in load_loki_version_policy()["new_choices"]:
        raise ValueError("loki_new_pipeline_not_selectable")
    return normalize_pipeline(value)

def pipeline_identity(value: str) -> dict[str, str]:
    policy = load_loki_version_policy()
    normalized = normalize_pipeline(value)
    row = policy["pipelines"].get(normalized) if isinstance(normalized, str) else None
    if row is None:
        return {"label": "방식 기록 없음 (과거 결과)"}
    if row["kind"] == "legacy":
        return {"label": row["label"]}
    version = row["version"]
    result = dict(mechanism_name=policy["product_name"], mechanism_version=version,
                  aemeth_version=version, label=f"{policy['product_name']} {version}")
    if "adoption_status" in row:
        result["adoption_status"] = row["adoption_status"]
    return result

def pipeline_label(value: str) -> str:
    return pipeline_identity(value)["label"]
