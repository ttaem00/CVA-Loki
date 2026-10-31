"""Local manual review revisions for an existing source-bound Loki report."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from pipeline.evidence_world.index import build_lock
from pipeline.evidence_world.contracts import digest, native_path
from pipeline.manager_outline import validate_structured_manager_outline
from pipeline.models import VODInfo
from pipeline.outline.schema import hms_to_seconds
from pipeline.report_artifact_family import commit_report_artifact_family, validate_report_artifact_family
from pipeline.editorial_visual_input import validate_frame_references
from pipeline.summary.report_html import ReportHtmlRenderRequest, render_summary_report_html
from pipeline.summary.render import _merge_editorial_review_markdown
from scripts.admin.manager_outline import (
    ManagerEditValidationRejected,
    _editable_broadcast_map_markdown, _merge_virtual_markdown,
    _merge_broadcast_map_sections_into_report_markdown, _rebind_editorial_packet_broadcast_map,
    _validate_editorial_packet_edit, manager_outline_edit_revision,
)

try:
    from .loki import configuration, contained
except ImportError:
    from loki import configuration, contained

MAX_BYTES = 50_000_000
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
VOD_IDENTITY_FIELDS = frozenset(VODInfo.__dataclass_fields__) | {"channel", "platform", "source_clock"}


class ReviewConflict(ValueError):
    status = 409
    status_code = 409


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _original_identity_defaults(metadata, original):
    restored = deepcopy(metadata)
    for key in VOD_IDENTITY_FIELDS:
        value = restored.get(key)
        if key in original and (value is None or isinstance(value, str) and not value.strip()):
            restored[key] = deepcopy(original[key])
    return restored


def _check_images(packet, duration):
    ranges = [row for level in ("d1", "d2") for row in packet.get("broadcast_map", {}).get(level, [])]
    for row in [*packet.get("editorial_highlights", []), *ranges]:
        image = row.get("image")
        if image is None:
            continue
        second = image.get("source_sec")
        if (type(second) not in (int, float) or not math.isfinite(second)
                or not 0 <= second < duration):
            raise ValueError("review_image_outside_selected_span")
        spans = row["source_spans"] if "source_spans" in row else [dict(
            start_sec=hms_to_seconds(row["start"]), end_sec=hms_to_seconds(row["end"]))]
        frame = {key: image[key] for key in ("source_sec", "source_interval_sec") if key in image}
        try:
            validate_frame_references([dict(image=image, source_spans=spans)], {image["frame_ref"]: frame})
        except ValueError as error:
            raise ValueError("review_image_outside_selected_span") from error
        data = image.get("data_url", "")
        if not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", data):
            raise ValueError("review_image_must_be_preserved_raster")
        if _hash(base64.b64decode(data.split(",", 1)[1], validate=True)) != image.get("sha256"):
            raise ValueError("review_image_bytes_changed")


class ReviewStore:
    def __init__(self, workspace, config_path=None):
        self.root = Path(workspace).resolve(strict=True)
        root, self.work, _ = configuration(self._path(config_path or self.root / "loki.json"))
        if root != self.root:
            raise ValueError("review_workspace_mismatch")

    def _path(self, path):
        value = str(path)
        if os.name == "nt" and value.startswith("\\\\?\\"):
            value = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value[4:]
        if value.startswith(("\\\\", "//")) or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", value):
            raise ValueError("review_external_path_forbidden")
        lexical = Path(os.path.abspath(value if Path(value).is_absolute() else self.root / value))
        if not lexical.is_relative_to(self.root):
            raise ValueError("review_path_outside_workspace")
        return contained(self.root, lexical)

    def _bytes(self, path):
        path = native_path(self._path(path))
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("review_artifact_too_large")
        return path.read_bytes()

    def _read(self, path):
        return json.loads(self._bytes(path).decode("utf-8-sig"))

    def _write(self, path, value, *, replace=False):
        path = native_path(self._path(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = _json_bytes(value)
        if not replace:
            with path.open("xb") as stream:
                stream.write(raw)
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pending", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _jobs(self):
        for vod in sorted(native_path(self.work).iterdir()):
            vod = self._path(vod)
            if not native_path(vod).is_dir():
                continue
            folder = self._path(vod / "current-conversation")
            if not native_path(folder).is_dir():
                continue
            for job in sorted(native_path(folder).iterdir()):
                job = self._path(job)
                if (native_path(job).is_dir() and native_path(self._path(job / "submission.json")).is_file()
                        and native_path(self._path(job / "submission-result.json")).is_file()):
                    yield _hash(job.relative_to(self.root).as_posix().encode())[:20], job

    def _job(self, report_id):
        if not isinstance(report_id, str) or not re.fullmatch(r"[a-f0-9]{20}", report_id):
            raise ValueError("review_report_id_invalid")
        for identity, job in self._jobs():
            if identity == report_id:
                return job
        raise FileNotFoundError("review_report_not_found")

    def _source(self, job):
        terminal = self._read(job / "submission-result.json")
        if terminal.get("status") != "new_normal_report_created":
            raise ValueError("review_report_not_completed")
        paths = [self._path(terminal["paths"][key]) for key in ("markdown", "html", "metadata")]
        if any(path.parent != job / "report" for path in paths):
            raise ValueError("review_source_report_outside_job")
        raws = [self._bytes(path) for path in paths]
        meta = validate_report_artifact_family(*map(native_path, paths))["metadata"]
        return dict(md=raws[0].decode("utf-8"), html=raws[1].decode("utf-8"), meta=meta,
            created_at=datetime.fromtimestamp(native_path(paths[0]).stat().st_mtime, timezone.utc).isoformat(),
            base=paths[0].stem, source_sha=_hash(b"\0".join(raws)), revision_id="original", parent=None)

    def _context(self, job):
        source = self._source(job)
        pointer = self._path(job / "review/current.json")
        if not native_path(pointer).exists():
            return source
        saved = self._read(pointer)
        revision = saved.get("revision_id")
        if not isinstance(revision, str) or not TOKEN.fullmatch(revision):
            raise ValueError("review_revision_invalid")
        folder = self._path(job / "review/revisions" / revision)
        state = self._read(folder / "state.json")
        if state.get("source_sha") != source["source_sha"] or _hash(_json_bytes(state)) != saved.get("state_sha"):
            raise ValueError("review_source_or_revision_changed")
        paths = [folder / (source["base"] + suffix) for suffix in (".md", ".html", "_metadata.json")]
        for path in paths:
            self._bytes(path)
        meta = _original_identity_defaults(validate_report_artifact_family(*map(native_path, paths))["metadata"], source["meta"])
        return dict(state, md=self._bytes(paths[0]).decode(), html=self._bytes(paths[1]).decode(), meta=meta)

    def _view(self, report_id, context):
        meta = context["meta"]
        view = dict(id=report_id, base=context["base"], video_no=str(meta["video_no"]),
            title=str(meta.get("title", "")), md=context["md"], manager_markdown=context["md"],
            structured_outline=deepcopy(meta["structured_outline"]),
            qg1_review_packet=deepcopy(meta["editorial_review_packet"]),
            source_clock=deepcopy(meta.get("source_clock")),
            manager_outline_authority=dict(revision_id=context["revision_id"]),
            summary_pipeline_integrity=dict(generation_id=meta["report_artifact_family"]["generation_id"]))
        story_refs = {point["id"]: point["story_ref"]
            for point in view["qg1_review_packet"]["broadcast_map"]["points"]}
        for point in view["structured_outline"]["points"]:
            if not point.get("story_ref") and point["id"] in story_refs:
                point["story_ref"] = story_refs[point["id"]]
        view["manager_edit_revision"] = manager_outline_edit_revision(view)
        return view

    def list_reports(self):
        return [{key: value for key, value in self.load(identity).items()
                 if key in {"id", "base", "video_no", "title", "manager_edit_revision"}}
                for identity, _ in self._jobs()]

    def load(self, report_id):
        return self._view(report_id, self._context(self._job(report_id)))

    def report_html(self, report_id):
        return self._context(self._job(report_id))["html"]

    def _expect(self, report_id, context, expected):
        if expected != self._view(report_id, context)["manager_edit_revision"]:
            raise ReviewConflict("다른 수정본이 먼저 저장되었습니다. 입력을 유지하고 최신 저장본을 다시 열어 주세요.")

    def _draft_content(self, context, payload):
        current = context["meta"]
        duration = int(current["duration"])
        markdown = payload.get("manager_markdown", context["md"])
        if not isinstance(markdown, str) or len(markdown.encode()) > MAX_BYTES:
            raise ValueError("review_markdown_invalid")
        outline = deepcopy(payload.get("outline", current["structured_outline"]))
        validate_structured_manager_outline(outline, duration_sec=duration)
        image_map = lambda value: {row["id"]: row["image"] for row in value["ranges"] if row.get("image") is not None}
        if image_map(outline) != image_map(current["structured_outline"]):
            raise ValueError("review_selected_range_image_changed")
        packet = _validate_editorial_packet_edit(current["editorial_review_packet"],
            deepcopy(payload.get("editorial_review_packet", current["editorial_review_packet"])), duration_sec=duration)
        if markdown != context["md"]:
            outline = _merge_virtual_markdown(current["structured_outline"], outline,
                _editable_broadcast_map_markdown(markdown), duration)
        packet = _rebind_editorial_packet_broadcast_map(packet, outline)
        _check_images(packet, duration)
        markdown = _merge_broadcast_map_sections_into_report_markdown(markdown, outline, duration_sec=duration)
        markdown = _merge_editorial_review_markdown(markdown, packet, video_no=str(current["video_no"]))
        return markdown, outline, packet

    def _draft(self, context, payload):
        current = context["meta"]
        duration = int(current["duration"])
        try:
            markdown, outline, packet = self._draft_content(context, payload)
        except ReviewConflict:
            raise
        except ValueError as error:
            raise ManagerEditValidationRejected(str(error)) from error
        vod = VODInfo(video_no=str(current["video_no"]), title=str(current.get("title", "")),
            channel_id=str(current.get("channel_id", "")), channel_name=str(current.get("channel", "")),
            duration=duration, publish_date=str(current.get("publish_date", "")),
            thumbnail_url=str(current.get("thumbnail_url") or ""), category=str(current.get("category") or ""))
        rendered = render_summary_report_html(ReportHtmlRenderRequest(report_markdown=markdown, vod_info=vod,
            output_dir="", base=context["base"], highlights=[], chats=[], community_posts=[], public_url_base="",
            user_clips=None, promoted_anchors_dicts=[], show_community_reaction_summary=True,
            show_community_reaction_cafe=False, audio_reaction_metadata=None,
            youtube_chapter_readiness=deepcopy(current.get("youtube_chapter_readiness", {})), srt_path="",
            safe_timeline_comment_context={}, comment_timeline_supplement_learning=deepcopy(
                current.get("comment_timeline_supplement_learning", {})), outline_source_path="",
            structured_outline=outline, editorial_review_packet=packet, summary_pipeline=current["summary_pipeline"],
            report_projection_source=deepcopy(current.get("report_projection"))))
        meta = deepcopy(current)
        meta.update({key: value for key, value in rendered.metadata_sec.items() if key not in VOD_IDENTITY_FIELDS})
        meta.update(structured_outline=outline, manager_markdown=markdown, editorial_review_packet=packet,
                    manager_edited=True)
        return dict(md=markdown, html=rendered.html_content, meta=meta)

    def preview(self, report_id, payload):
        job = self._job(report_id)
        review = self._path(job / "review")
        self._path(review / "build.lock")
        with build_lock(native_path(review)):
            context = self._context(job)
            self._expect(report_id, context, payload.get("expected_edit_revision"))
            draft = self._draft(context, payload)
            preview = uuid.uuid4().hex
            record = dict(draft, source_sha=context["source_sha"], base=context["base"],
                parent=context["revision_id"], expected_edit_revision=payload["expected_edit_revision"])
            self._write(review / "previews" / (preview + ".json"), dict(payload=record, sha256=_hash(_json_bytes(record))))
            projected = self._view(report_id, dict(context, **draft))
            projected["manager_edit_revision"] = payload["expected_edit_revision"]
            return dict(preview_id=preview, revision=payload["expected_edit_revision"],
                        expected_edit_revision=payload["expected_edit_revision"], report=projected)

    def _preview(self, job, preview_id):
        if not isinstance(preview_id, str) or not TOKEN.fullmatch(preview_id):
            raise ValueError("review_preview_id_invalid")
        record = self._read(job / "review/previews" / (preview_id + ".json"))
        if _hash(_json_bytes(record["payload"])) != record.get("sha256"):
            raise ValueError("review_preview_changed")
        return record["payload"]

    def preview_html(self, report_id, preview_id):
        return self._preview(self._job(report_id), preview_id)["html"]

    def save(self, report_id, payload):
        if set(payload) != {"preview_id", "expected_edit_revision"}:
            raise ValueError("review_save_requires_exact_preview")
        job = self._job(report_id)
        review = self._path(job / "review")
        self._path(review / "build.lock")
        with build_lock(native_path(review)):
            context = self._context(job)
            self._expect(report_id, context, payload["expected_edit_revision"])
            draft = self._preview(job, payload["preview_id"])
            if draft["expected_edit_revision"] != payload["expected_edit_revision"] or draft["source_sha"] != context["source_sha"]:
                raise ReviewConflict("review_preview_is_stale")
            revision = uuid.uuid4().hex
            folder = self._path(review / "revisions" / revision)
            paths = [folder / (context["base"] + suffix) for suffix in (".md", ".html", "_metadata.json")]
            for path in paths:
                self._path(path)
            old = context["meta"]["report_artifact_family"]
            commit_report_artifact_family(markdown_path=native_path(paths[0]), html_path=native_path(paths[1]),
                metadata_path=native_path(paths[2]),
                markdown_text=draft["md"], html_text=draft["html"], metadata=deepcopy(draft["meta"]),
                generation_sequence=old["generation_sequence"] + 1, parent_generation_id=old["generation_id"],
                authority_revision_id=revision)
            state = dict(source_sha=context["source_sha"], base=context["base"], revision_id=revision,
                parent=context["revision_id"], created_at=datetime.now(timezone.utc).isoformat())
            self._write(folder / "state.json", state)
            self._write(review / "current.json", dict(revision_id=revision, state_sha=_hash(_json_bytes(state))), replace=True)
        return self.load(report_id)

    def revisions(self, report_id):
        job = self._job(report_id)
        context = self._context(job)
        rows, revision = [], context["revision_id"]
        while revision != "original":
            if not TOKEN.fullmatch(revision) or len(rows) >= 1000:
                raise ValueError("review_history_invalid")
            state = self._read(job / "review/revisions" / revision / "state.json")
            rows.append(dict(revision_id=revision, created_at=state["created_at"]))
            revision = state["parent"]
        for row in rows:
            row["title"] = "수동 저장본 " + row["created_at"]
        return dict(revisions=rows + [dict(revision_id="original", created_at=None, title="원본 보고서")])

    def preview_revision(self, report_id, payload):
        job = self._job(report_id)
        revision = payload.get("revision_id")
        if revision == "original":
            context = self._source(job)
        elif any(row["revision_id"] == revision for row in self.revisions(report_id)["revisions"]):
            state = self._read(job / "review/revisions" / revision / "state.json")
            folder = self._path(job / "review/revisions" / revision)
            paths = [folder / (state["base"] + suffix) for suffix in (".md", ".html", "_metadata.json")]
            for path in paths:
                self._bytes(path)
            meta = validate_report_artifact_family(*map(native_path, paths))["metadata"]
            context = dict(state, md=self._bytes(folder / (state["base"] + ".md")).decode(),
                           meta=meta)
        else:
            raise ValueError("review_revision_not_found")
        return self.preview(report_id, dict(manager_markdown=context["md"], outline=context["meta"]["structured_outline"],
            editorial_review_packet=context["meta"]["editorial_review_packet"],
            expected_edit_revision=payload.get("expected_edit_revision")))

    def originals(self, report_id):
        job = self._job(report_id)
        prepared = self._read(job / "prepared.json")
        asset = prepared["source"]["asset"]
        path = self._path(asset["source_locator"]["path"])
        if not path.is_relative_to(self.work) or path.suffix.lower() != ".srt":
            raise ValueError("review_original_source_invalid")
        raw = self._bytes(path)
        if _hash(raw) != asset["bytes_sha256"]:
            raise ValueError("review_original_source_changed")
        return dict(sources=[dict(name=path.name, text=raw.decode("utf-8-sig"))])

    def workspace_id(self, base):
        if not isinstance(base, str) or not base or len(base) > 256:
            raise ValueError("review_base_invalid")
        matches = [identity for identity, job in self._jobs() if self._source(job)["base"] == base]
        if len(matches) != 1:
            raise ValueError("review_base_not_unique")
        return matches[0]

    def _history_context(self, job, revision):
        source = self._source(job)
        if revision == "original":
            return source
        if not isinstance(revision, str) or not TOKEN.fullmatch(revision):
            raise ValueError("review_revision_invalid")
        folder = self._path(job / "review/revisions" / revision)
        state = self._read(folder / "state.json")
        if state.get("source_sha") != source["source_sha"] or state.get("base") != source["base"]:
            raise ValueError("review_source_or_revision_changed")
        paths = [self._path(folder / (source["base"] + suffix)) for suffix in (".md", ".html", "_metadata.json")]
        for path in paths:
            self._bytes(path)
        meta = _original_identity_defaults(validate_report_artifact_family(*map(native_path, paths))["metadata"], source["meta"])
        return dict(state, md=self._bytes(paths[0]).decode(), html=self._bytes(paths[1]).decode(), meta=meta)

    def workspace_report(self, base, *, revision_only=False):
        identity = self.workspace_id(base)
        report = self.load(identity)
        if revision_only:
            return {key: report[key] for key in ("base", "video_no", "manager_edit_revision")}
        job = self._job(identity)
        history = []
        for row in self.revisions(identity)["revisions"]:
            previous = self._history_context(job, row["revision_id"])
            history.append(dict(row, outline=self._view(identity, previous)["structured_outline"],
                qg1_review_packet=deepcopy(previous["meta"]["editorial_review_packet"]), md=previous["md"]))
        context = self._context(job)
        meta = context["meta"]
        return dict(report, kind="vod", revision_id=context["revision_id"],
            channel=meta.get("channel", ""), channel_id=meta.get("channel_id", ""),
            duration=meta["duration"], summary_pipeline=deepcopy(meta.get("summary_pipeline", {})),
            source_platform="chzzk", public_report_url="", admin_only={},
            youtube_alignment={}, naver_cafe_destination=None, staleness={}, review_tools_pending=False,
            manager_outline_revisions=history, publish_revisions=self.workspace_revisions(base),
            capabilities=dict(local_edit=True, local_preview=True, local_history=True,
                publish=False, cafe=False, external_models=False))

    def workspace_preview(self, payload):
        identity = self.workspace_id(payload.get("base"))
        current = self.load(identity)
        preview = self.preview(identity, dict(manager_markdown=payload.get("md", current["md"]),
            outline=payload.get("structured_outline", current["structured_outline"]),
            editorial_review_packet=payload.get("editorial_review_packet", current["qg1_review_packet"]),
            expected_edit_revision=current["manager_edit_revision"]))
        return dict(html=self.preview_html(identity, preview["preview_id"]), title=current["title"],
            md=preview["report"]["md"], manager_markdown=preview["report"]["manager_markdown"],
            structured_outline=preview["report"]["structured_outline"],
            qg1_review_packet=preview["report"]["qg1_review_packet"], marker_warnings=[])

    def _workspace_identity(self, payload):
        identity = self.workspace_id(payload.get("base"))
        report = self.load(identity)
        if str(payload.get("video_no")) != report["video_no"]:
            raise ValueError("review_vod_mismatch")
        return identity

    def workspace_save(self, payload):
        identity = self._workspace_identity(payload)
        preview = self.preview(identity, dict(manager_markdown=payload.get("manager_markdown"),
            outline=payload.get("outline"), editorial_review_packet=payload.get("editorial_review_packet"),
            expected_edit_revision=payload.get("expected_edit_revision")))
        self.save(identity, dict(preview_id=preview["preview_id"],
            expected_edit_revision=payload.get("expected_edit_revision")))
        return self.workspace_report(payload["base"])

    def workspace_restore(self, payload):
        identity = self._workspace_identity(payload)
        revision_id = payload.get("revision_id")
        preview = self.preview_revision(identity, dict(revision_id=revision_id,
            expected_edit_revision=payload.get("expected_edit_revision")))
        self.save(identity, dict(preview_id=preview["preview_id"],
            expected_edit_revision=payload.get("expected_edit_revision")))
        return dict(self.workspace_report(payload["base"]), restored_from_revision_id=revision_id)

    def workspace_sync(self, payload):
        identity = self._workspace_identity(payload)
        if payload.get("direction") not in {"markdown", "outline"}:
            raise ValueError("review_sync_direction_invalid")
        context = self._context(self._job(identity))
        markdown, outline, packet = self._draft_content(context, payload)
        return dict(base=context["base"], video_no=str(context["meta"]["video_no"]),
            manager_markdown=markdown, md=markdown, structured_outline=outline, qg1_review_packet=packet)

    def workspace_save_markdown(self, payload):
        if payload.get("publish", False) is not False:
            raise ValueError("review_publication_unavailable")
        identity = self.workspace_id(payload.get("base"))
        current = self.load(identity)
        saved = self.workspace_save(dict(base=current["base"], video_no=current["video_no"],
            manager_markdown=payload.get("md"), outline=current["structured_outline"],
            editorial_review_packet=current["qg1_review_packet"],
            expected_edit_revision=payload.get("expected_edit_revision")))
        return dict(saved, html=self.report_html(identity), saved=True, published=False,
                    publish_status="not_requested", marker_warnings=[])

    def workspace_revisions(self, base):
        from pipeline.editorial_production_job import current_conversation_author_mode
        identity = self.workspace_id(base)
        job = self._job(identity)
        records = []
        for row in self.revisions(identity)["revisions"]:
            context = self._history_context(job, row["revision_id"])
            original = row["revision_id"] == "original"
            actor_kind = "current_codex" if original else "human_manager"
            if original:
                provenance = context["meta"].get("llm_provider_provenance") or {}
                author = provenance.get("author") if isinstance(provenance, dict) else None
                mode = "current_conversation_chatgpt"
                if isinstance(provenance, dict) and (
                        provenance.get("actual_provider") == mode or provenance.get("selected_mode") == mode
                        or isinstance(author, dict) and author.get("host") == "chatgpt"):
                    try:
                        valid = (provenance.get("actual_provider") == provenance.get("selected_mode") == mode
                            and current_conversation_author_mode(author) == mode)
                    except ValueError:
                        valid = False
                    if not valid:
                        raise ValueError("review_original_author_invalid")
                    actor_kind = "current_chatgpt"
            records.append(dict(row, source="ai_original" if original else "admin_save_local",
                actor_kind=actor_kind, test_mode=False,
                publish_state="local_draft", approval={},
                hashes=dict(md=_hash(context["md"].encode()), html=_hash(context["html"].encode())),
                summary=dict(md_lines=len(context["md"].splitlines()), html_bytes=len(context["html"].encode()))))
        return dict(records=records, current_published_revision_id="", publish_audit=dict(events=[]))

    def workspace_revision_html(self, base, revision_id):
        identity = self.workspace_id(base)
        if not any(row["revision_id"] == revision_id for row in self.revisions(identity)["revisions"]):
            raise ValueError("review_revision_not_found")
        return self._history_context(self._job(identity), revision_id)["html"]

    def workspace_revision_draft(self, payload):
        identity = self.workspace_id(payload.get("base"))
        current = self.load(identity)
        result = self.preview_revision(identity, dict(revision_id=payload.get("revision_id"),
            expected_edit_revision=current["manager_edit_revision"]))
        return dict(html=self.preview_html(identity, result["preview_id"]), md=result["report"]["md"],
            structured_outline=result["report"]["structured_outline"],
            qg1_review_packet=result["report"]["qg1_review_packet"], revisions=self.workspace_revisions(current["base"]))

    def workspace_media(self, value, by_id=False, by_base=False):
        if by_id and by_base:
            raise ValueError("review_media_selector_invalid")
        if by_base:
            identity = self.workspace_id(value)
            job = self._job(identity)
        elif by_id:
            identity, job = value, self._job(value)
        else:
            video = str(value)
            if not re.fullmatch(r"[0-9]{1,20}", video):
                raise ValueError("review_vod_invalid")
            matches = [(identity, job) for identity, job in self._jobs()
                if str(self._context(job)["meta"]["video_no"]) == video]
            if not matches:
                raise FileNotFoundError("review_media_not_found")
            if len(matches) != 1:
                raise ValueError("review_media_ambiguous: 보고서 버전을 지정해 영상을 다시 연결해 주세요.")
            identity, job = matches[0]
        context = self._context(job)
        prepared = self._read(job / "prepared.json")
        revision = prepared.get("prepared_revision")
        if (prepared.get("schema_version") != "chzz.current_conversation_prepared.v1"
                or digest({key: item for key, item in prepared.items() if key != "prepared_revision"}) != revision
                or self._read(job / "submission-result.json").get("prepared_revision") != revision
                or self._path(prepared["identity"]["run_root"]) != job
                or str(prepared["identity"]["video_no"]) != str(context["meta"]["video_no"])):
            raise ValueError("review_media_binding_changed")
        author = self._bytes(job / "author-input.json")
        media = prepared.get("media_source")
        if not media:
            raise FileNotFoundError("review_media_not_collected")
        if (not isinstance(media, dict) or set(media) != {"path", "sha256"}
                or not isinstance(media["path"], str) or not isinstance(media["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", media["sha256"])
                or _hash(author) != prepared.get("input_sha256")
                or json.loads(author).get("media_source") != media
                or dict(family="media", **media) not in prepared.get("source_bundle", {}).get("frozen_assets", [])):
            raise ValueError("review_media_binding_invalid")
        video = str(context["meta"]["video_no"])
        path = self._path(media["path"])
        if not path.is_relative_to(self.work / video) or not native_path(path).is_file() or path.suffix.lower() != ".mp4":
            raise ValueError("review_media_source_invalid")
        return dict(id=identity, path=path, sha256=media["sha256"],
            original_url="https://chzzk.naver.com/video/" + video)

    def workspace_catalog(self, *, q="", streamer=None, offset=0, limit=100):
        offset, limit = int(offset), int(limit)
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 1000:
            raise ValueError("review_catalog_page_invalid")
        reports = {}
        for _, job in self._jobs():
            context = self._context(job)
            meta = context["meta"]
            video = str(meta["video_no"])
            record = reports.setdefault(video, dict(video_no=video, summary_versions=[]))
            record["summary_versions"].append(dict(base=context["base"], title=meta.get("title", ""),
                channel=meta.get("channel", ""), channel_id=meta.get("channel_id", ""),
                publish_date=meta.get("publish_date", ""), workspace_ready=True,
                saved_at=context.get("created_at") or meta.get("generated_at") or "",
                summary_label=(meta.get("summary_pipeline") or {}).get("label", "")))
        facets = {}
        for record in reports.values():
            record["summary_versions"].sort(key=lambda row: (row["saved_at"], row["base"]), reverse=True)
            record.update(record["summary_versions"][0], kind="vod", site_only=False,
                summary_version_count=len(record["summary_versions"]), existing_summary_bundle_eligible=False,
                canonical_source="local", canonical_conflict=False,
                status_badges=[dict(level="info", label="로컬 초안")])
            key = record["channel_id"] or record["channel"] or "__unknown__"
            facet = facets.setdefault(key, dict(key=key, label=record["channel"] or key, count=0))
            facet["count"] += 1
        keyword = str(q).casefold()
        selected = [row for row in reports.values()
            if (not keyword or keyword in " ".join(str(row.get(key, ""))
                for key in ("base", "video_no", "title", "channel")).casefold())
            and (not streamer or streamer == (row["channel_id"] or row["channel"] or "__unknown__"))]
        selected.sort(key=lambda row: (row["saved_at"], row["base"]), reverse=True)
        return dict(reports=selected[offset:offset + limit], total=len(reports), matched_total=len(selected),
            offset=offset, limit=limit, has_more=offset + limit < len(selected), streamers=list(facets.values()))
