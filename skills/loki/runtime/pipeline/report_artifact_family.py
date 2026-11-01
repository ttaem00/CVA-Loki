"""Complete, verifiable report artifact-family transactions."""

from __future__ import annotations

import copy

import hashlib

import html

import json

import os

import re

import shutil

import tempfile

import time

from contextlib import contextmanager

from datetime import datetime, timezone

from pathlib import Path

from typing import Any

from uuid import uuid4

SCHEMA_VERSION = "report_artifact_family.v1"

class ReportArtifactFamilyError(RuntimeError):
    """Raised when a report family is partial, mixed, or unverifiable."""

@contextmanager
def _report_family_lock(
    output_dir: str | Path,
    *,
    video_no: object,
    timeout_sec: float = 15.0,
):
    """Serialize one VOD's lineage resolution and family promotion."""

    video_key = str(video_no or "").strip()
    if not video_key:
        raise ValueError("video_no is required for report family locking")
    lock_key = hashlib.sha256(video_key.encode("utf-8")).hexdigest()[:20]
    lock_path = Path(output_dir) / f".report-family-{lock_key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + timeout_sec
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out locking report family for VOD {video_key}"
                        )
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            acquired = True
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

def _metadata_payload_hash(metadata: dict[str, Any]) -> str:


    payload = dict(metadata)
    family = payload.get("report_artifact_family")
    if isinstance(family, dict):
        family = dict(family)
        payload["report_artifact_family"] = family
        family.pop("metadata_payload_sha256", None)
    return _sha256_bytes(_canonical_json_bytes(payload))

def _viewer_payload(html_text: str) -> dict[str, Any] | None:
    match = re.search(
        r'<script(?=[^>]*\bid=["\']viewerEditorToolsData["\'])[^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None

def _editorial_packet_sha256(metadata: dict[str, Any]) -> str:
    packet = metadata.get("editorial_review_packet")
    return _sha256_bytes(_canonical_json_bytes(packet)) if isinstance(packet, dict) else ""

def _viewer_asset_version(html_text: str) -> str:
    match = re.search(
        r'data-viewer-editor-render-version=["\']([^"\']+)["\']',
        html_text,
        flags=re.IGNORECASE,
    )
    return str(match.group(1) if match else "").strip()

def _summary_pipeline_identity_from_html(html_text: str) -> dict[str, str] | None:
    marker = re.search(
        r'<div\b(?=[^>]*\bid=["\']summaryPipelineIdentity["\'])[^>]*>',
        html_text,
        flags=re.IGNORECASE,
    )
    if not marker:
        return None
    attrs = {
        name.lower(): html.unescape(value)
        for name, _quote, value in re.findall(
            r'\b(data-[a-z0-9-]+)\s*=\s*(["\'])(.*?)\2',
            marker.group(0),
            flags=re.IGNORECASE | re.DOTALL,
        )
    }
    return {
        "actual_version": str(attrs.get("data-summary-pipeline") or "").strip(),
        "mechanism_name": str(attrs.get("data-summary-mechanism") or "").strip(),
        "mechanism_version": str(
            attrs.get("data-summary-mechanism-version") or ""
        ).strip(),
        "aemeth_version": str(attrs.get("data-aemeth-version") or "").strip(),
    }

def _validate_summary_pipeline_identity(
    metadata: dict[str, Any],
    html_text: str,
) -> None:
    metadata_identity = metadata.get("summary_pipeline")
    html_identity = _summary_pipeline_identity_from_html(html_text)
    exact_identity_expected = bool(
        html_identity
        or (
            isinstance(metadata_identity, dict)
            and (
                str(metadata_identity.get("schema_version") or "")
                == "chzz.summary_pipeline.v3"
                or any(
                    metadata_identity.get(key)
                    for key in (
                        "mechanism_name",
                        "mechanism_version",
                        "aemeth_version",
                    )
                )
            )
        )
    )
    if not exact_identity_expected:
        return
    if not isinstance(metadata_identity, dict) or not html_identity:
        raise ReportArtifactFamilyError(
            "summary pipeline identity is missing from metadata or HTML"
        )
    for key in (
        "actual_version",
        "mechanism_name",
        "mechanism_version",
        "aemeth_version",
    ):
        metadata_value = str(metadata_identity.get(key) or "").strip()
        html_value = str(html_identity.get(key) or "").strip()
        if metadata_value != html_value:
            raise ReportArtifactFamilyError(
                "summary pipeline identity mismatch: "
                f"{key} metadata={metadata_value!r} html={html_value!r}"
            )

def next_generation_lineage(
    output_dir: str | Path,
    *,
    video_no: object,
) -> tuple[int, str]:
    """Return the next explicit per-VOD sequence and known parent generation."""

    highest = 0
    parent = ""
    for path in Path(output_dir).glob("*_metadata.json"):
        base_name = path.name[: -len("_metadata.json")]
        if not base_name:
            continue
        try:
            validated = validate_report_artifact_family(
                path.with_name(f"{base_name}.md"),
                path.with_name(f"{base_name}.html"),
                path,
            )
        except (OSError, UnicodeError, ReportArtifactFamilyError):
            continue
        metadata = validated["metadata"]
        if str(metadata.get("video_no") or "") != str(video_no or ""):
            continue
        sequence = int(validated["generation_sequence"])
        generation_id = str(validated["generation_id"])
        if sequence > highest and generation_id:
            highest = sequence
            parent = generation_id
    return highest + 1, parent

def _validate_viewer_contract(metadata: dict[str, Any], html_text: str) -> dict[str, Any]:
    viewer_payload = _viewer_payload(html_text)
    if not viewer_payload:
        if str(metadata.get("source_platform") or metadata.get("platform") or "").lower() == "youtube":
            return {}
        raise ReportArtifactFamilyError("viewerEditorToolsData is missing or empty")
    expected_outline = metadata.get("structured_outline")
    if isinstance(expected_outline, dict):
        projection = viewer_payload.get("outline_projection")
        expected_rows = [
            row
            for key in ("ranges", "points", "candidates")
            for row in (expected_outline.get(key) or [])
            if isinstance(row, dict)
        ]
        projection_rows = projection if isinstance(projection, list) else []
        expected_ids = {
            str(row.get("id") or "").strip()
            for row in expected_rows
            if str(row.get("id") or "").strip()
        }
        projected_ids = {
            str(row.get("id") or "").strip()
            for row in projection_rows
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        missing_expected_rows = (
            not expected_ids.issubset(projected_ids)
            if expected_ids
            else len(projection_rows) < len(expected_rows)
        )
        if expected_rows and missing_expected_rows:
            raise ReportArtifactFamilyError("viewer outline_projection is incomplete")
    return viewer_payload

def prepare_report_artifact_family(
    *,
    metadata: dict[str, Any],
    markdown_text: str,
    html_text: str,
    markdown_name: str,
    html_name: str,
    metadata_name: str,
    candidate_alias_name: str = "",
    generation_sequence: int,
    parent_generation_id: str = "",
    authority_revision_id: str = "",
) -> tuple[dict[str, Any], str]:
    generation_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid4().hex[:12]
    )
    _validate_summary_pipeline_identity(metadata, html_text)
    viewer_payload = _validate_viewer_contract(metadata, html_text)
    family = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": generation_id,
        "generation_sequence": int(generation_sequence),
        "parent_generation_id": str(parent_generation_id or ""),
        "authority_revision_id": str(authority_revision_id or ""),
        "artifacts": {
            "markdown": {
                "name": markdown_name,
                "sha256": _sha256_bytes(markdown_text.encode("utf-8")),
            },
            "html": {
                "name": html_name,
                "sha256": _sha256_bytes(html_text.encode("utf-8")),
            },
            "metadata": {"name": metadata_name},
        },
        "viewer_payload_sha256": _sha256_bytes(_canonical_json_bytes(viewer_payload)),
        "editorial_packet_sha256": _editorial_packet_sha256(metadata),
        "viewer_asset_version": _viewer_asset_version(html_text),
        "metadata_payload_sha256": "",
    }
    if candidate_alias_name:
        family["artifacts"]["candidate_alias"] = {
            "name": candidate_alias_name,
            "sha256": family["artifacts"]["html"]["sha256"],
        }
    prepared = copy.deepcopy(metadata)
    prepared["report_artifact_family"] = family
    family["metadata_payload_sha256"] = _metadata_payload_hash(prepared)
    return prepared, generation_id

def validate_report_artifact_family(
    markdown_path: str | Path,
    html_path: str | Path,
    metadata_path: str | Path,
    *,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    md_path = Path(markdown_path)
    html_path = Path(html_path)
    meta_path = Path(metadata_path)
    if not (md_path.is_file() and html_path.is_file() and meta_path.is_file()):
        raise ReportArtifactFamilyError("report family is incomplete")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportArtifactFamilyError("report metadata is unreadable") from exc
    if not isinstance(metadata, dict):
        raise ReportArtifactFamilyError("report metadata is not an object")
    family = metadata.get("report_artifact_family")
    if not isinstance(family, dict):
        if allow_legacy:
            return {"legacy": True, "metadata": metadata}
        raise ReportArtifactFamilyError("report family metadata is absent")
    if family.get("schema_version") != SCHEMA_VERSION:
        raise ReportArtifactFamilyError("unknown report family schema")
    generation_id = str(family.get("generation_id") or "")
    try:
        sequence = int(family.get("generation_sequence") or 0)
    except (TypeError, ValueError) as exc:
        raise ReportArtifactFamilyError("invalid generation sequence") from exc
    if not generation_id or sequence <= 0:
        raise ReportArtifactFamilyError("report generation lineage is incomplete")
    artifacts = family.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReportArtifactFamilyError("report artifact hashes are absent")
    for label, path in (("markdown", md_path), ("html", html_path), ("metadata", meta_path)):
        row = artifacts.get(label)
        if not isinstance(row, dict) or row.get("name") != path.name:
            raise ReportArtifactFamilyError(f"{label} artifact name mismatch")
        if label != "metadata" and row.get("sha256") != _sha256_bytes(path.read_bytes()):
            raise ReportArtifactFamilyError(f"{label} artifact hash mismatch")
    alias_row = artifacts.get("candidate_alias")
    if alias_row is not None:
        if not isinstance(alias_row, dict):
            raise ReportArtifactFamilyError("candidate alias artifact metadata is invalid")
        alias_name = str(alias_row.get("name") or "")
        if not alias_name or Path(alias_name).name != alias_name:
            raise ReportArtifactFamilyError("candidate alias artifact name is invalid")
        alias_path = meta_path.parent / alias_name
        if not alias_path.is_file():
            raise ReportArtifactFamilyError("candidate alias artifact is incomplete")
        alias_hash = _sha256_bytes(alias_path.read_bytes())
        if alias_row.get("sha256") != alias_hash:
            raise ReportArtifactFamilyError("candidate alias artifact hash mismatch")
        html_row = artifacts.get("html")
        if not isinstance(html_row, dict) or alias_hash != html_row.get("sha256"):
            raise ReportArtifactFamilyError("candidate alias differs from family HTML")
    if family.get("metadata_payload_sha256") != _metadata_payload_hash(metadata):
        raise ReportArtifactFamilyError("metadata payload hash mismatch")
    html_text = html_path.read_text(encoding="utf-8")
    _validate_summary_pipeline_identity(metadata, html_text)
    viewer_payload = _validate_viewer_contract(metadata, html_text)
    if family.get("viewer_payload_sha256") != _sha256_bytes(_canonical_json_bytes(viewer_payload)):
        raise ReportArtifactFamilyError("viewer payload generation hash mismatch")
    if (
        "editorial_packet_sha256" in family
        and family.get("editorial_packet_sha256") != _editorial_packet_sha256(metadata)
    ):
        raise ReportArtifactFamilyError("editorial packet generation hash mismatch")
    if (
        "viewer_asset_version" in family
        and family.get("viewer_asset_version") != _viewer_asset_version(html_text)
    ):
        raise ReportArtifactFamilyError("viewer asset generation version mismatch")
    return {
        "legacy": False,
        "metadata": metadata,
        "generation_id": generation_id,
        "generation_sequence": sequence,
        "parent_generation_id": str(family.get("parent_generation_id") or ""),
        "authority_revision_id": str(family.get("authority_revision_id") or ""),
    }

def _write_staged(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

def commit_report_artifact_family(
    *,
    markdown_path: str | Path,
    html_path: str | Path,
    metadata_path: str | Path,
    candidate_alias_path: str | Path | None = None,
    markdown_text: str,
    html_text: str,
    metadata: dict[str, Any],
    generation_sequence: int,
    parent_generation_id: str = "",
    authority_revision_id: str = "",
) -> str:
    """Stage and promote a family; restore every previous byte on caught error."""

    markdown_final = Path(markdown_path)
    html_final = Path(html_path)
    metadata_final = Path(metadata_path)
    alias_final = Path(candidate_alias_path) if candidate_alias_path else None
    if alias_final is not None and alias_final == html_final:
        alias_final = None
    if alias_final is not None and alias_final.parent != metadata_final.parent:
        raise ValueError("candidate alias must be a sibling of report metadata")
    content_finals = [markdown_final, html_final]
    if alias_final is not None:
        content_finals.append(alias_final)
    finals = [*content_finals, metadata_final]
    if len(set(finals)) != len(finals):
        raise ValueError("report artifact family paths must be unique")
    prepared, generation_id = prepare_report_artifact_family(
        metadata=metadata,
        markdown_text=markdown_text,
        html_text=html_text,
        markdown_name=markdown_final.name,
        html_name=html_final.name,
        metadata_name=metadata_final.name,
        candidate_alias_name=alias_final.name if alias_final is not None else "",
        generation_sequence=generation_sequence,
        parent_generation_id=parent_generation_id,
        authority_revision_id=authority_revision_id,
    )
    content_payloads = [
        markdown_text.encode("utf-8"),
        html_text.encode("utf-8"),
    ]
    if alias_final is not None:
        content_payloads.append(html_text.encode("utf-8"))
    payloads = [
        *content_payloads,
        (json.dumps(prepared, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    ]
    metadata_final.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(
        tempfile.mkdtemp(



            prefix=".rf-",
            dir=metadata_final.parent,
        )
    )
    staged = [stage_dir / path.name for path in finals]
    previous = {path: path.read_bytes() if path.is_file() else None for path in finals}
    try:
        for path, payload in zip(staged, payloads):
            _write_staged(path, payload)
        validate_report_artifact_family(staged[0], staged[1], staged[-1])

        for source, target in zip(staged, finals):
            os.replace(source, target)
        validate_report_artifact_family(finals[0], finals[1], finals[-1])
    except Exception:
        for index, path in enumerate(finals):
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
                continue
            recovery = stage_dir / f".rollback-{index}"
            _write_staged(recovery, old)
            os.replace(recovery, path)
        raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return generation_id

def commit_next_report_artifact_family(
    *,
    video_no: object,
    markdown_path: str | Path,
    html_path: str | Path,
    metadata_path: str | Path,
    candidate_alias_path: str | Path | None = None,
    markdown_text: str,
    html_text: str,
    metadata: dict[str, Any],
    authority_revision_id: str = "",
) -> str:
    """Resolve and commit the next VOD generation under one process lock."""

    output_dir = Path(metadata_path).parent
    with _report_family_lock(output_dir, video_no=video_no):
        generation_sequence, parent_generation_id = next_generation_lineage(
            output_dir,
            video_no=video_no,
        )
        return commit_report_artifact_family(
            markdown_path=markdown_path,
            html_path=html_path,
            metadata_path=metadata_path,
            candidate_alias_path=candidate_alias_path,
            markdown_text=markdown_text,
            html_text=html_text,
            metadata=metadata,
            generation_sequence=generation_sequence,
            parent_generation_id=parent_generation_id,
            authority_revision_id=authority_revision_id,
        )
