"""Synthetic install contract check using this installed Loki runtime.

This fixed fixture checks source reads, response validation and rendering.
It is not a model-authored report or editorial quality evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


KIND = "synthetic_contract_selftest"
SEMANTIC_AUTHOR = "synthetic_fixture_not_a_model"


def require(condition, error):
    if not condition:
        raise ValueError(error)


def original_records(page):
    """Read the serial or lossless owner view without changing source text."""
    if page.get("schema_version") == "ew.read_result.v1":
        records = page.get("records")
        require(isinstance(records, list) and all(isinstance(row, dict) for row in records),
                "selftest_original_record_shape")
        return records
    require(page.get("schema_version") == "ew.owner_read.v1", "selftest_original_read_schema")
    defaults, columns, rows = (page.get(name) for name in ("record_defaults", "record_columns", "record_rows"))
    require(isinstance(defaults, dict) and isinstance(columns, list)
            and all(isinstance(column, str) for column in columns)
            and len(columns) == len(set(columns)) and not set(defaults).intersection(columns)
            and isinstance(rows, list)
            and all(isinstance(row, list) and len(row) == len(columns) for row in rows),
            "selftest_original_record_shape")
    assets = page.get("assets")
    require(isinstance(assets, list) and bool(assets)
            and all(isinstance(asset, dict) and asset.get("source_id") == page.get("source_id")
                    for asset in assets), "selftest_original_identity_mismatch")
    return [dict(defaults, **dict(zip(columns, row))) for row in rows]


def run(runtime, workspace):
    """Exercise the installed CLI, writing only a new verification workspace."""
    runtime = Path(runtime).resolve(strict=True)
    cli = runtime / "loki.py"
    require(cli.is_file(), "selftest_runtime_entrypoint_missing")
    workspace = Path(workspace).absolute()
    if workspace.exists():
        raise ValueError("smoke_workspace_must_be_new")

    def call(*args, error=None):
        cmd = [sys.executable, "-B", str(cli)]
        if args[0] != "init":
            cmd += ["--config", str(workspace / "loki.json")]
        result = subprocess.run(cmd + list(args), cwd=runtime, text=True,
                                encoding="utf-8", capture_output=True, timeout=120)
        if error is not None:
            if result.returncode == 0 or error not in result.stderr:
                raise ValueError("selftest_expected_rejection_missing: " + error + ": " + result.stdout + result.stderr)
            return {"rejected": error}
        if result.returncode:
            raise RuntimeError("selftest_cli_failed: " + args[0] + ": " + result.stdout + result.stderr)
        return json.loads(result.stdout)

    call("init", "--directory", str(workspace))
    doctor = call("doctor")
    require(isinstance(doctor, dict) and doctor.get("status") == "ready" and doctor.get("missing_dependencies") == [],
            "selftest_dependencies_not_ready: " + json.dumps(doctor, ensure_ascii=False))
    manifest = json.loads((runtime / "source-manifest.json").read_text(encoding="utf-8"))
    policy = json.loads((runtime / "vendor/chat-analyze-engine/config/loki_versions.json").read_text(encoding="utf-8"))
    selected = policy["current_pipeline"]
    version = policy["pipelines"][selected]["version"]
    require(manifest["version"] == version, "selftest_source_version_mismatch")
    require(doctor.get("version") == version, "selftest_runtime_version_mismatch")
    source = workspace / "work/1/sources"
    source.mkdir(parents=True)
    srt = source / "source.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\ninitial claim\n\n"
                   "2\n00:00:05,000 --> 00:00:06,000\nunrelated schedule\n\n"
                   "3\n00:00:10,000 --> 00:00:12,000\nquiet rebuttal\n", encoding="utf-8", newline="\n")
    source_hash = hashlib.sha256(srt.read_bytes()).hexdigest()
    base = workspace / "base.md"
    base.write_text("Existing manager report; preserve this.\n", encoding="utf-8", newline="\n")
    base_bytes = base.read_bytes()
    spec = {"vod_info": {"video_no": "1", "title": "Synthetic conversation", "channel_id": "",
                         "channel_name": "Synthetic", "duration": 20, "publish_date": "2026-01-01"},
            "source_work_dir": "work/1/sources", "srt_path": "work/1/sources/source.srt",
            "base_path": "base.md", "job_id": "synthetic-contract-fixture",
            "author": {"thread_id": "synthetic-contract-fixture", "turn_id": "fixture-no-model-call", "actual_model": "unknown"}}

    def write(name, value):
        path = workspace / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        return str(path)

    spec_path = write("spec.json", spec)
    prepared = call("prepare", "--spec", spec_path)
    require(call("prepare", "--spec", spec_path) == prepared, "selftest_prepare_replay_changed")
    job = prepared["job_root"]
    saved_preparation = json.loads((Path(job) / "prepared.json").read_text(encoding="utf-8"))
    require(saved_preparation["identity"]["selected_pipeline"] == selected, "selftest_selected_pipeline_mismatch")
    revision = prepared["prepared_revision"]
    identity = ["--job-root", job, "--expected-revision", revision]
    payload = {"base_revision": prepared["base_revision"],
        "summary": "A later quiet reply changes the initial claim.", "summary_evidence_refs": ["e1", "e3"],
        "ranges": [
            {"id": "d1", "level": "D1", "title": "Conversation", "content": "A claim and later reply",
             "start": "00:00:00", "end": "00:00:20", "evidence_refs": ["e1", "e3"]},
            {"id": "d2", "level": "D2", "parent_id": "d1", "title": "Reply", "content": "The later reply",
             "start": "00:00:09", "end": "00:00:20", "evidence_refs": ["e3"]}],
        "points": [{"id": "p", "title": "Quiet reply", "content": "A contradiction", "timestamp": "00:00:10",
                    "d2_ref": "d2", "start_evidence_ref": "e3", "evidence_refs": ["e3"]}],
        "stories": [{"story_ref": "s", "point_ref": "p", "title": "Quiet reply", "why_notable": "The claim changes",
                     "used_evidence_ids": ["e3"], "roles": {"setup": [], "performance": [], "event": ["e3"], "reaction": [], "result": []}}],
        "proposals": [{"story_refs": ["s"], "point_refs": ["p"], "title": "Reply", "story_summary": "quiet rebuttal",
            "selection_reason": "understand the changed claim", "source_spans": [{"start_sec": 10, "end_sec": 12,
                "story_roles": ["conversation"], "evidence_refs": ["e3"], "reason": "actual reply"}],
            "story_beats": [{"role": "core_event", "span_indexes": [0], "evidence_refs": ["e3"], "reason": "reply"}],
            "estimated_edited_duration": {"min_sec": 2, "max_sec": 2, "category": "short"},
            "missing_information": [], "uncertainties": []}],
        "group_outcomes": [{"terminal_state": "represented", "flow_refs": ["broadcast"], "range_refs": ["d1", "d2"],
            "reason": "The conversation retains the initial claim and later quiet rebuttal.", "evidence_refs": ["e1", "e3"]}],
        "unresolved_questions": []}
    response = write("response.json", payload)
    call("submit", *identity, "--response", response, error="current_conversation_cited_original_not_read")
    action = {"schema_version": "ew.tool.v1", "source_id": prepared["source_id"],
              "world_revision": prepared["world_revision"], "question_id": "read-originals", "action": "read_evidence",
              "args": {"intervals_sec": [[0, 20]], "families": ["stt"], "context_cues": 0, "max_bytes": 16000}}
    originals = call("read", *identity, "--action", write("action.json", action))
    records = original_records(originals)
    require(originals.get("original_loaded") is True and originals.get("status") == "complete",
            "selftest_originals_not_loaded")
    require(isinstance(records, list) and len(records) == 3, "selftest_original_record_count")
    require(originals.get("next_cursor") is None, "selftest_original_read_incomplete")
    require([row.get("raw_text") for row in records] == ["initial claim", "unrelated schedule", "quiet rebuttal"],
            "selftest_original_text_mismatch")
    require(all(row.get("fragment_complete") is True for row in records), "selftest_original_fragment_incomplete")
    require(all(isinstance(row.get("record_id"), str) and len(row["record_id"]) == 64
                and all(char in "0123456789abcdef" for char in row["record_id"]) for row in records),
            "selftest_original_record_identity_invalid")
    require(originals.get("source_id") == prepared["source_id"]
            and originals.get("world_revision") == prepared["world_revision"]
            and isinstance(originals.get("read_id"), str) and bool(originals["read_id"]),
            "selftest_original_identity_mismatch")
    original_path = write("originals.json", originals)
    note = {"question_id": "read-originals", "text": "The quiet rebuttal changes the earlier claim; compare originals before adoption.",
            "read_ids": [originals["read_id"]]}
    call("remember", *identity, "--note", write("note.json", note))
    recalled = call("memory", *identity, "--query", "rebuttal")
    require(len(recalled["notes"]) == 1 and recalled["provider_calls"] == 0, "selftest_note_recall_failed")
    submitted = call("submit", *identity, "--response", response)
    require(submitted["external_transport_calls"] == 0 and not submitted["manager_saved"] and not submitted["published"],
            "selftest_submission_authority_changed")
    require(call("submit", *identity, "--response", response) == submitted, "selftest_submit_replay_changed")
    payload["summary"] += " Changed after settlement."
    call("submit", *identity, "--response", write("changed-response.json", payload), error="current_conversation_submit_conflict")
    require(base.read_bytes() == base_bytes, "selftest_existing_report_changed")
    require(hashlib.sha256(srt.read_bytes()).hexdigest() == source_hash, "selftest_original_source_changed")
    paths = submitted["paths"]
    require(all(Path(paths[key]).is_file() for key in ("html", "markdown", "metadata")),
            "selftest_report_file_missing")
    html = Path(paths["html"]).read_text(encoding="utf-8")
    require("Quiet reply" in html and "A later quiet reply" in html, "selftest_rendered_content_missing")
    metadata = json.loads(Path(paths["metadata"]).read_text(encoding="utf-8-sig"))
    require(isinstance(metadata, dict), "selftest_report_metadata_invalid")
    report_pipeline = metadata.get("summary_pipeline", {})
    require(report_pipeline.get("actual_version") == selected
            and report_pipeline.get("mechanism_version") == version, "selftest_report_version_mismatch")
    result = {"status": "PASS", "kind": KIND, "semantic_author": SEMANTIC_AUTHOR,
              "editorial_quality": "not_evaluated", "doctor": doctor, "source_hash": source_hash,
              "runtime": str(runtime), "workspace": str(workspace), "report_paths": paths,
              "version": version, "selected_pipeline": selected, "report_pipeline": report_pipeline,
              "original_read": {"path": original_path, "read_id": originals["read_id"],
                  "source_id": originals["source_id"], "world_revision": originals["world_revision"],
                  "record_count": len(records), "complete_record_count": len(records),
                  "cited_stt_ref_count": len(set(payload["summary_evidence_refs"])),
                  "record_ids": [row["record_id"] for row in records]},
              "checks": ["installed runtime CLI", "doctor ready with no missing dependencies", "prepare replay",
                         "unread citation rejection", "complete original texts and source-bound receipt",
                         "source-linked note and recall", "synthetic fixture response contract validation",
                         "normal HTML/Markdown/metadata renderer", "submit replay", "changed settled response conflict",
                         "source and existing report preserved", "zero model transport"]}
    write("selftest-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="A new verification workspace; existing paths are refused")
    args = parser.parse_args(argv)
    try:
        result = run(Path(__file__).resolve().parent, args.workspace)
    except (ValueError, OSError, RuntimeError, KeyError, AssertionError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "FAIL", "kind": KIND, "semantic_author": SEMANTIC_AUTHOR,
                          "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
