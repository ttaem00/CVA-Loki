"""Standalone metadata and engagement defaults, derived from pipeline.config. No account/provider config."""

from .runtime_root import runtime_data_root

from pipeline.loki_version_policy import normalize_pipeline as _normalize_loki_pipeline, pipeline_identity as _loki_identity, pipeline_label as _loki_label, pipelines_for as _loki_pipelines

SUMMARY_PIPELINE_LEGACY = "legacy_v1"

SUMMARY_PIPELINE_2_5 = "pipeline_2_5"

FULL_AUTHOR_PIPELINES = _loki_pipelines("full_author")

EDITORIAL_PRODUCTION_PIPELINES = _loki_pipelines("editorial", "full_author")

STRUCTURED_SUMMARY_PIPELINES = _loki_pipelines("structured", "editorial", "full_author")

LOKI_LABEL = _loki_label(SUMMARY_PIPELINE_2_5)

SUMMARY_PIPELINE_METADATA_SCHEMA_VERSION = "chzz.summary_pipeline.v3"

SUMMARY_PIPELINE_VERSIONS = _loki_pipelines()

def normalize_summary_pipeline_version(value: str) -> str:
    """Return the canonical stored version while accepting the retired alias."""

    return _normalize_loki_pipeline(value)

def summary_pipeline_manager_label(value: str) -> str:
    """Return the one manager-visible label for a summary pipeline version."""

    return _loki_label(value)

def summary_pipeline_release_identity(value: str) -> dict[str, str]:
    """Return additive product provenance for the selected summary mechanism."""

    return _loki_identity(value)

def load_config(config_path=None):
    if config_path is not None:
        raise ValueError("loki_configuration_uses_workspace_entrypoint")
    return {"summary_pipeline_version":"latest", "engagement_thresholds":{"preset":"balanced"}}
