"""Bounded internal facade over the unchanged first-party TTaempad grammar.

Documents/entries contain private source. Only the name/position projections
may be considered for the public candidate allow-list in its owning module.
"""

from __future__ import annotations

import json

from pathlib import Path

import shutil

import subprocess

MAX_INPUT_BYTES = 1024 * 1024

MAX_OUTPUT_BYTES = 8 * 1024 * 1024

class EditorCommentParserError(ValueError):
    """A sanitized unavailable/invalid parser result, without source contents."""

def parse_editor_documents(input_or_documents, *, video_no=None, duration_sec=None,
                           completeness="partial", continuous_lines=True,
                           source_revision="unversioned"):
    """Return cva.editor_comments.v1; does not read files, collect or call AI.

    Accept a documents list, the JS input envelope, or a structured
    chzzk.comment-timeline v1 object. Explicit facade scope arguments bind an
    envelope/structured file to the caller's video and media duration.
    """
    if isinstance(input_or_documents, list):
        data = dict(documents=input_or_documents, completeness=completeness,
                    continuous_lines=continuous_lines, source_revision=source_revision)
    elif isinstance(input_or_documents, dict):
        data = dict(timeline=input_or_documents) if input_or_documents.get("format") == "chzzk.comment-timeline" else dict(input_or_documents)
    else:
        raise EditorCommentParserError("editor_comment_input_invalid")
    if video_no is not None:
        if "video_no" in data and str(data["video_no"]) != str(video_no):
            raise EditorCommentParserError("editor_comment_video_conflict")
        data["video_no"] = str(video_no)
    if duration_sec is not None:
        if "duration_sec" in data and data["duration_sec"] != duration_sec:
            raise EditorCommentParserError("editor_comment_duration_conflict")
        data["duration_sec"] = duration_sec
    try:
        serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
        size = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise EditorCommentParserError("editor_comment_input_invalid") from None
    if size > MAX_INPUT_BYTES:
        raise EditorCommentParserError("editor_comment_input_limit")
    node = shutil.which("node")
    if not node:
        raise EditorCommentParserError("editor_comment_parser_unavailable")
    command = Path(__file__).resolve().parents[1] / "tools" / "parse_editor_comments.js"
    try:
        completed = subprocess.run([node, str(command)], input=serialized, text=True,
            encoding="utf-8", capture_output=True, timeout=15, check=True)
        if len(completed.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise EditorCommentParserError("editor_comment_output_limit")
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):

        raise EditorCommentParserError("editor_comment_parse_failed") from None
    if not isinstance(result, dict) or result.get("schema_version") != "cva.editor_comments.v1" or any(
        not isinstance(result.get(field), list) for field in ("documents", "entries", "diagnostics", "roster_candidates", "periods")
    ):
        raise EditorCommentParserError("editor_comment_response_invalid")
    return result
