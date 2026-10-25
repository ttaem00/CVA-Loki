"""Exact source text for the owner; full locator/provenance stays in read artifacts.

This changes serialization, not source selection, active memory or edit meaning.
No source text is summarized, truncated, normalized or silently retired here.
"""

from copy import deepcopy

from .contracts import EvidenceError, digest

MEANING_FIELDS = ("record_id", "ordinal", "family", "raw_text", "time_index", "raw_time",
                  "raw_char_start", "raw_char_end", "raw_char_total", "fragment_complete",
                  "clock_status", "uncertainties", "playable", "role", "text_status")

PROVENANCE_FIELDS = {"asset_id", "native_locator", "legacy_pointer", "raw_payload_sha256",
                     "source_id", "video_no", "transform"}

def compact_read(page):
    if page.get("schema_version") != "ew.read_result.v1":
        raise EvidenceError("unsupported_owner_read_schema")
    records = page["records"]
    for record in records:
        if (set(record) != set(MEANING_FIELDS) | PROVENANCE_FIELDS or
                record["source_id"] != page["source_id"] or record["family"] not in {"stt", "chat", "audio_features", "clip", "live_metadata"}):
            raise EvidenceError("unsupported_owner_record_shape")


    defaults = {k: deepcopy(records[0][k]) for k in MEANING_FIELDS if records and
                all(record[k] == records[0][k] for record in records)}
    columns = [k for k in MEANING_FIELDS if k not in defaults]
    assets = {}
    for record in records:
        asset = {k: record[k] for k in ("asset_id", "family", "source_id", "video_no", "transform")}
        if record["asset_id"] in assets and assets[record["asset_id"]] != asset:
            raise EvidenceError("owner_read_asset_conflict")
        assets[record["asset_id"]] = asset
    result = {k: deepcopy(v) for k, v in page.items() if k not in {"records", "schema_version"}}
    result.update(schema_version="ew.owner_read.v1", full_read_sha256=digest(page),
                  assets=list(assets.values()), record_defaults=defaults, record_columns=columns,
                  record_rows=[[deepcopy(record[k]) for k in columns] for record in records])
    if any(r["family"] in {"audio_features", "clip"} for r in records):
        result["representation_policy"] = (
            "STT/chat retain exact original characters. audio_features/clip raw_text is a serialization "
            "of selected producer metadata fields, not original media, heard speech or verified matches. "
            "Its full source row and hash remain in the immutable read. Check transform and uncertainties.")
    if any(r["family"] == "live_metadata" for r in records):
        result["live_metadata_policy"] = (
            "Source-bound observed viewer counts, titles and categories retain native times and "
            "uncertainties. Net change is not individual arrivals or scene causality. "
            "These records do not authorize STT citations or VOD playback/cut clocks.")
    return result
