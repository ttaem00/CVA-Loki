"""Read-only Loki presentation of an existing validated report family."""

from copy import deepcopy

from html import escape

import re

from urllib.parse import quote

from pipeline.collaboration_stage import read_report_collaboration

from pipeline.report_artifact_family import _viewer_payload

from pipeline.summary.parse import _parse_summary_sections

from pipeline.summary.render import _viewer_outline_projection_rows

from publish.builder.loki_template import entry_html, prepare_report, supports_report

from publish.builder.report_sanitizers import (
    _project_public_editorial_scene_images_payload,
    _sanitize_public_bundle_viewer_editor_payload,
    _strip_admin_only_timeline_comment_events,
)

from scripts.admin.manager_outline import _accepted_qg1_packet

PREFIX = "/api/report-view/"

_PIN = r"[A-Za-z0-9_-]{1,128}\.[a-f0-9]{64}(?:\.[a-f0-9]{64})?"

_ASSET = r"(?:loki-page\.json|images/[a-f0-9]{64}\.(?:png|jpg|webp|gif|avif))"

_REQUEST = re.compile(rf"{PREFIX}([^/]+)/({_PIN})/({_ASSET})\Z")

_IMAGE_TYPES = {"png":"image/png", "jpg":"image/jpeg", "webp":"image/webp",
                "gif":"image/gif", "avif":"image/avif"}

def project_saved_report_context(ctx: dict, base: str, expected_pin: str | None = None) -> dict | None:
    """Bind a source-owner-validated immutable report context to the common reader."""
    metadata = ctx["meta"]
    family = metadata["report_artifact_family"]
    pin = f"{family['generation_id']}.{family['metadata_payload_sha256']}"
    collaboration = read_report_collaboration(ctx.get("metadata_path"), metadata)
    if collaboration is not None:
        pin = f"{pin}.{collaboration[2]}"
    if not re.fullmatch(_PIN, pin):
        raise ValueError("loki_reader_family_identity_invalid")
    if expected_pin is not None and expected_pin != pin:
        raise FileNotFoundError("loki_reader_report_generation_changed")
    viewer = deepcopy(_viewer_payload(ctx["html"]))
    if not supports_report(viewer):
        return None
    video = str(ctx["vod_info"].video_no)
    if str(viewer.get("video_no")) != video:
        raise ValueError("loki_reader_video_mismatch")
    packet = metadata.get("editorial_review_packet")
    outline = metadata.get("structured_outline")
    if isinstance(packet, dict):
        if not isinstance(outline, dict):
            raise ValueError("loki_reader_outline_missing")
        accepted = _accepted_qg1_packet(packet, expected_broadcast_map=outline)
        projected = _viewer_outline_projection_rows(outline, _parse_summary_sections(ctx["md"]), accepted)
        canonical = {row["id"]:row for row in projected if row.get("kind") == "editorial_highlight"}
        authored = {row["highlight_id"]:row for row in accepted["editorial_highlights"]}
        rows, seen = [], set()
        for original in viewer["outline_projection"]:
            row = deepcopy(original)
            if row.get("kind") == "editorial_highlight":
                identifier = row.get("id")
                if identifier not in canonical or identifier in seen:
                    raise ValueError("loki_reader_highlight_identity_changed")
                source = authored[identifier]
                if "source_spans" in row:
                    geometry = lambda spans: [(s["start_sec"],s["end_sec"]) for s in spans]
                    if geometry(row["source_spans"]) != geometry(source["source_spans"]):
                        raise ValueError("loki_reader_highlight_geometry_changed")
                row = {**canonical[identifier], **row}
                if not row.get("image") and source.get("image") is not None:
                    row["image"] = deepcopy(source["image"])
                seen.add(identifier)
            rows.append(row)
        for identifier, row in canonical.items():
            if identifier not in seen:
                row = deepcopy(row)
                if authored[identifier].get("image") is not None:
                    row["image"] = deepcopy(authored[identifier]["image"])
                rows.append(row)
        viewer["outline_projection"] = rows
    if collaboration is not None:
        viewer["collaboration_periods"], viewer["collaboration_candidates"], _ = collaboration
    viewer = _project_public_editorial_scene_images_payload(
        _sanitize_public_bundle_viewer_editor_payload(_strip_admin_only_timeline_comment_events(viewer)))
    vod = ctx["vod_info"]
    record = dict(video_no=video, title=vod.title, streamer_name=vod.channel_name,
                  published_at=vod.publish_date, platform_category=vod.category)
    prepared = prepare_report(ctx["html"], viewer, record)
    if prepared is None:
        return None
    page, images = prepared
    url = f"{PREFIX}{quote(base, safe='')}/{pin}/loki-page.json"
    html = entry_html().replace('data-default-report="./loki-page.json"',
                                f'data-default-report="{escape(url, quote=True)}"', 1)
    return dict(packet=page, images=images, pin=pin, entry_html=html)
