from __future__ import annotations

class LLMGatewayError(RuntimeError):
    pass

class SanityGateError(LLMGatewayError):
    pass

import hashlib

import warnings

from pathlib import Path

from typing import Any, Optional

_MULTIMODAL_MAX_IMAGE_COUNT = 8

_MULTIMODAL_MAX_IMAGE_BYTES = 10 * 1024 * 1024

_MULTIMODAL_MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024

_MULTIMODAL_MAX_IMAGE_DIMENSION = 16_384

_MULTIMODAL_MAX_IMAGE_PIXELS = 40_000_000

_MULTIMODAL_MAX_ANIMATION_FRAMES = 32

_MULTIMODAL_MAX_DECODED_FRAME_PIXELS = 80_000_000

_MULTIMODAL_IMAGE_MIME_TYPES = {
    ".png": "image/png", ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

def _detect_multimodal_image_mime(header: bytes) -> Optional[str]:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _validate_decodable_multimodal_image(path: Path, expected_mime_type: str) -> None:
    """Decode every bounded frame so a matching header alone is never trusted."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise ValueError("multimodal image decoder is unavailable") from exc

    expected_format = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/gif": "GIF",
        "image/webp": "WEBP",
    }[expected_mime_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if str(image.format or "").upper() != expected_format:
                    raise ValueError("multimodal attachment decoded format does not match its image extension")
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                if frame_count < 1 or frame_count > _MULTIMODAL_MAX_ANIMATION_FRAMES:
                    raise ValueError(
                        f"multimodal attachment supports at most {_MULTIMODAL_MAX_ANIMATION_FRAMES} animation frames"
                    )
                decoded_pixels = 0
                for frame_index in range(frame_count):
                    image.seek(frame_index)
                    width, height = image.size
                    if (
                        width < 1
                        or height < 1
                        or width > _MULTIMODAL_MAX_IMAGE_DIMENSION
                        or height > _MULTIMODAL_MAX_IMAGE_DIMENSION
                    ):
                        raise ValueError(
                            f"multimodal attachment dimensions must be between 1 and {_MULTIMODAL_MAX_IMAGE_DIMENSION} pixels"
                        )
                    frame_pixels = width * height
                    if frame_pixels > _MULTIMODAL_MAX_IMAGE_PIXELS:
                        raise ValueError(
                            f"multimodal attachment frame must contain at most {_MULTIMODAL_MAX_IMAGE_PIXELS} pixels"
                        )
                    decoded_pixels += frame_pixels
                    if decoded_pixels > _MULTIMODAL_MAX_DECODED_FRAME_PIXELS:
                        raise ValueError(
                            "multimodal attachment decoded animation exceeds the total pixel budget"
                        )
                    image.load()
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, EOFError) as exc:
        raise ValueError("multimodal attachment must be a fully decodable supported image") from exc

def _normalize_multimodal_attachments(attachments: Optional[list[object]]) -> list[dict[str, Any]]:
    values = list(attachments or [])
    if len(values) > _MULTIMODAL_MAX_IMAGE_COUNT:
        raise ValueError(f"multimodal attachments support at most {_MULTIMODAL_MAX_IMAGE_COUNT} images")
    normalized: list[dict[str, Any]] = []
    total_bytes = 0
    for raw in values:
        item = raw if isinstance(raw, dict) else {"path": raw}
        path_value = item.get("path")
        if not isinstance(path_value, (str, Path)):
            raise ValueError("multimodal attachment requires a local path")
        path = Path(path_value).expanduser().resolve(strict=True)
        expected_mime_type = _MULTIMODAL_IMAGE_MIME_TYPES.get(path.suffix.lower())
        if not path.is_file() or not expected_mime_type:
            raise ValueError("multimodal attachment must be an existing supported image")
        supplied_mime_type = str(item.get("mimeType") or item.get("mime_type") or "")
        if supplied_mime_type and supplied_mime_type != expected_mime_type:
            raise ValueError("multimodal attachment mimeType does not match its image extension")
        size = path.stat().st_size
        if size <= 0 or size > _MULTIMODAL_MAX_IMAGE_BYTES:
            raise ValueError("multimodal attachment image size must be between 1 byte and 10 MiB")
        total_bytes += size
        if total_bytes > _MULTIMODAL_MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("multimodal attachments must total at most 20 MiB")
        with path.open("rb") as stream:
            detected_mime_type = _detect_multimodal_image_mime(stream.read(16))
        if detected_mime_type != expected_mime_type:
            raise ValueError("multimodal attachment image magic does not match its extension")
        _validate_decodable_multimodal_image(path, expected_mime_type)
        normalized.append({
            "path": str(path),
            "mimeType": expected_mime_type,
            "bytes": size,
            "sha256": _file_sha256(path),
        })
    return normalized
