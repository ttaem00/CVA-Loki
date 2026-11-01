"""Source-bound preserved pixels for the existing T4 item owner.

The catalog is a derived locator, not an OCR summary or a new semantic owner.
It never downloads media or chooses an editorial boundary.
"""

from copy import deepcopy

import hashlib

import math

import base64

from .evidence_world.contracts import native_path

PROFILE = "preserved_frames_v1"

def owned_images(attachments, root):
    from .llm_gateway import _normalize_multimodal_attachments
    owner = native_path(root).resolve(strict=True)
    for item in attachments:
        path = native_path(item["path"] if isinstance(item, dict) else item).resolve(strict=True)
        if not path.is_relative_to(owner):
            raise ValueError("editorial_frame_outside_owned_root")
    images = _normalize_multimodal_attachments(attachments)
    if len(images) > 3 or sum(i["bytes"] for i in images) > 2_000_000:
        raise ValueError("editorial_frame_transfer_limit")
    for supplied, actual in zip(attachments, images):
        if isinstance(supplied, dict) and supplied.get("sha256") != actual["sha256"]:
            raise ValueError("editorial_frame_bytes_changed")
    return images

def frame_evidence(frames):
    return {ref: dict(evidence_id=ref, source_type="frame", role="supporting_visual",
                      time_sec=f["source_sec"], source_revision=f["image"]["sha256"],
                      source_text="attached original pixels; semantic claims belong to the author")
            for ref,f in frames.items()}

def validate_frame_references(proposals, frames):
    def inside(span, frame):
        interval = frame.get("source_interval_sec")
        if interval is not None:
            return (isinstance(interval, (list, tuple)) and len(interval) == 2
                and all(type(value) in (int, float) and math.isfinite(value) for value in interval)
                and span["start_sec"] <= interval[0] < interval[1] <= span["end_sec"]
                and interval[0] <= frame["source_sec"] < interval[1])
        return span["start_sec"] <= frame["source_sec"] < span["end_sec"]

    for proposal in proposals:
        for span in proposal.get("source_spans", []):
            for ref in span.get("evidence_refs", []):
                if ref.startswith("frame:") and (ref not in frames or not inside(span, frames[ref])):
                    raise ValueError("editorial_frame_outside_selected_span")
        image = proposal.get("image")
        if image is not None:
            ref = image.get("frame_ref") if isinstance(image, dict) else None
            if ref not in frames or not any(inside(span, frames[ref]) for span in proposal.get("source_spans", [])):
                raise ValueError("editorial_frame_outside_selected_span")

def selected_frame_image(selection, *, frames, source_spans):
    """Preserve the author's selected, delivered pixels in the same report JSON."""
    if selection is None:
        return None
    fields = {"frame_ref", "alt", "caption"}
    normalized_fields = fields | {"data_url", "sha256", "source_sec"}
    if (not isinstance(selection, dict)
            or (set(selection) != fields and not normalized_fields <= set(selection)
                <= normalized_fields | {"time_binding", "source_interval_sec"})
            or not isinstance(selection.get("frame_ref"), str)
            or not isinstance(selection.get("alt"), str) or not selection["alt"].strip()
            or not isinstance(selection.get("caption"), str)):
        raise ValueError("editorial_selected_image_shape")
    validate_frame_references([dict(image=selection, source_spans=source_spans)], frames)
    frame = frames[selection["frame_ref"]]
    supplied = frame["image"]


    image = owned_images([supplied], native_path(supplied["path"]).resolve().parent)[0]
    data = native_path(image["path"]).read_bytes()
    if (image["bytes"] != supplied["bytes"] or len(data) != image["bytes"]
            or hashlib.sha256(data).hexdigest() != image["sha256"]):
        raise ValueError("editorial_frame_bytes_changed")
    result = dict(frame_ref=selection["frame_ref"], alt=selection["alt"], caption=selection["caption"],
        source_sec=frame["source_sec"], sha256=image["sha256"],
        data_url="data:" + image["mimeType"] + ";base64," + base64.b64encode(data).decode("ascii"))
    for field in ("time_binding", "source_interval_sec"):
        if field in frame:
            result[field] = deepcopy(frame[field])
    if normalized_fields <= set(selection) and selection != result:
        raise ValueError("editorial_selected_image_changed")
    return result
