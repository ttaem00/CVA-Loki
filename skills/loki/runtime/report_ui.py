"""Read-only template binding for an already validated local review context."""
from __future__ import annotations

import json

from pipeline.models import VODInfo
from scripts.admin.report_reader import _REQUEST, _IMAGE_TYPES, project_saved_report_context


def project_report(context, report_id, *, expected_pin=None):
    """Consume the store's validated current revision without writing any files."""
    metadata = context["meta"]
    vod = VODInfo(video_no=str(metadata["video_no"]), title=str(metadata.get("title", "")),
        channel_id=str(metadata.get("channel_id", "")), channel_name=str(metadata.get("channel", "")),
        duration=int(metadata["duration"]), publish_date=str(metadata.get("publish_date", "")),
        source_platform=metadata.get("source_platform") or "chzzk",
        source_id=str(metadata.get("source_id") or ""), source_url=str(metadata.get("source_url") or ""),
        thumbnail_url=str(metadata.get("thumbnail_url") or ""), category=str(metadata.get("category") or ""))
    return project_saved_report_context(dict(context, vod_info=vod), report_id, expected_pin=expected_pin)


def read_report_view(store, request_path):
    """Serve only the selected JSON/image closure of the current saved revision."""
    match = _REQUEST.fullmatch(request_path)
    if match is None:
        raise FileNotFoundError("loki_reader_asset_not_found")
    report_id, pin, asset = match.groups()
    projection = store.report_presentation(report_id, expected_pin=pin)
    if projection is None:
        raise FileNotFoundError("loki_reader_unsupported_report")
    if asset == "loki-page.json":
        return (json.dumps(projection["packet"], ensure_ascii=False, separators=(",", ":"),
                           allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")
    raw = projection["images"].get(asset)
    if raw is None:
        raise FileNotFoundError("loki_reader_image_not_selected")
    return raw, _IMAGE_TYPES[asset.rsplit(".", 1)[1]]
