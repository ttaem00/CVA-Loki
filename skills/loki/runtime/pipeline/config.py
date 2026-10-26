"""Standalone metadata and engagement defaults, derived from pipeline.config. No account/provider config."""

from .runtime_root import runtime_data_root

SUMMARY_PIPELINE_LEGACY = "legacy_v1"

SUMMARY_PIPELINE_2_5 = "pipeline_2_5"

SUMMARY_PIPELINE_LOKI_301 = "loki_3_0_1"

SUMMARY_PIPELINE_AEMETH_302 = "aemeth_3_0_2"

SUMMARY_PIPELINE_LOKI_303 = "loki_3_0_3"

SUMMARY_PIPELINE_LATEST = "latest"

FULL_AUTHOR_PIPELINES = frozenset({SUMMARY_PIPELINE_AEMETH_302, SUMMARY_PIPELINE_LOKI_303})

EDITORIAL_PRODUCTION_PIPELINES = frozenset({SUMMARY_PIPELINE_LOKI_301, *FULL_AUTHOR_PIPELINES})

STRUCTURED_SUMMARY_PIPELINES = frozenset({SUMMARY_PIPELINE_2_5, *EDITORIAL_PRODUCTION_PIPELINES})

AEMETH_SUMMARY_MECHANISM_VERSION = "2.9.4.2"

LOKI_NAME = "Loki"

LOKI_VERSION = "2.9.4.2"

LOKI_LABEL = f"{LOKI_NAME} {LOKI_VERSION}"

SUMMARY_PIPELINE_METADATA_SCHEMA_VERSION = "chzz.summary_pipeline.v3"

_LEGACY_SUMMARY_PIPELINE_AEMETH_2_5 = "aemeth_2_5"

SUMMARY_PIPELINE_VERSIONS = frozenset({
    SUMMARY_PIPELINE_LEGACY,
    SUMMARY_PIPELINE_2_5,
    SUMMARY_PIPELINE_LOKI_301,
    SUMMARY_PIPELINE_AEMETH_302,
    SUMMARY_PIPELINE_LOKI_303,
})

def normalize_summary_pipeline_version(value: str) -> str:
    """Return the canonical stored version while accepting the retired alias."""

    if value == _LEGACY_SUMMARY_PIPELINE_AEMETH_2_5:
        return SUMMARY_PIPELINE_2_5
    if value == SUMMARY_PIPELINE_LATEST:
        return SUMMARY_PIPELINE_LOKI_303
    return value

def summary_pipeline_manager_label(value: str) -> str:
    """Return the one manager-visible label for a summary pipeline version."""

    normalized = normalize_summary_pipeline_version(value)
    if normalized == SUMMARY_PIPELINE_LOKI_303:
        return "Loki 3.0.3"
    if normalized == SUMMARY_PIPELINE_AEMETH_302:
        return "Loki 3.0.2"
    if normalized == SUMMARY_PIPELINE_LOKI_301:
        return "Loki 3.0.1"
    if normalized == SUMMARY_PIPELINE_2_5:
        return LOKI_LABEL
    if normalized == SUMMARY_PIPELINE_LEGACY:
        return "기존 요약"
    return "방식 기록 없음 (과거 결과)"

def summary_pipeline_release_identity(value: str) -> dict[str, str]:
    """Return additive product provenance for the selected summary mechanism."""

    normalized = normalize_summary_pipeline_version(value)
    if normalized == SUMMARY_PIPELINE_LOKI_303:
        return {"mechanism_name": LOKI_NAME, "mechanism_version": "3.0.3",
                "aemeth_version": "3.0.3", "label": "Loki 3.0.3",
                "adoption_status": "manager_selected"}
    if normalized == SUMMARY_PIPELINE_AEMETH_302:
        return {"mechanism_name": LOKI_NAME, "mechanism_version": "3.0.2",
                "aemeth_version": "3.0.2", "label": "Loki 3.0.2", "adoption_status": "manager_selected"}
    if normalized == SUMMARY_PIPELINE_LOKI_301:
        return {"mechanism_name": LOKI_NAME, "mechanism_version": "3.0.1",
                "aemeth_version": "3.0.1", "label": "Loki 3.0.1"}
    if normalized == SUMMARY_PIPELINE_2_5:
        return {
            "mechanism_name": LOKI_NAME,
            "mechanism_version": LOKI_VERSION,
            "aemeth_version": AEMETH_SUMMARY_MECHANISM_VERSION,
            "label": LOKI_LABEL,
        }
    return {"label": summary_pipeline_manager_label(normalized)}

def load_config(config_path=None):
    if config_path is not None:
        raise ValueError("loki_configuration_uses_workspace_entrypoint")
    return {"summary_pipeline_version":"latest", "engagement_thresholds":{"preset":"balanced"}}
