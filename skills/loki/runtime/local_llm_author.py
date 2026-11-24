"""Zero-dependency OpenAI-compatible 3-pass runner for local LLMs (Ollama/vLLM/LM Studio)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import urllib.request

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

PASS1_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "summary_evidence_refs": {"type": "array", "items": {"type": "string"}},
        "ranges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "level": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "level", "title", "start", "end", "evidence_refs"],
            },
        },
    },
    "required": ["summary", "summary_evidence_refs", "ranges"],
}

PASS2_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {"type": "array", "items": {"type": "object"}},
        "stories": {"type": "array", "items": {"type": "object"}},
        "proposals": {"type": "array", "items": {"type": "object"}},
        "group_outcomes": {"type": "array", "items": {"type": "object"}},
        "unresolved_questions": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "points", "stories", "proposals", "group_outcomes", "unresolved_questions",
    ],
}


def validate_endpoint(endpoint: str, *, allow_remote: bool = False) -> str:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("local_llm_endpoint_invalid")
    if not allow_remote and parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise ValueError("local_llm_non_loopback_requires_explicit_allow_remote")
    return endpoint.rstrip("/")


def chat_completion(endpoint: str, model: str, messages: list[dict], *, schema: dict, timeout_sec: int = 300) -> dict:
    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "loki_pass", "strict": True, "schema": schema},
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = raw["choices"][0]["message"]["content"]
    return json.loads(content)


def build_pass_plan(job_root: Path, *, expected_revision: str, endpoint: str, model: str) -> dict:
    """Inspect prepared job and build bounded 3-pass prompt plan without calling an LLM."""
    from pipeline import editorial_current_conversation as current

    root, prepared, _ = current._load(job_root)
    if expected_revision != prepared["prepared_revision"]:
        raise ValueError("current_conversation_prepare_conflict")
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    duration = int(prepared["vod_info"].get("duration") or 0)
    records = prepared["source"].get("records") or []
    step = max(1, len(records) // 12)
    sample_intervals = []
    for row in records[::step][:12]:
        s = float(row.get("start_sec", 0))
        sample_intervals.append([max(0, int(s) - 5), min(max(duration, int(s) + 30), int(s) + 30)])
    return {
        "schema_version": "loki.local_llm_plan.v1",
        "job_root": str(root),
        "prepared_revision": expected_revision,
        "base_revision": prepared["identity"]["base_revision"],
        "endpoint": endpoint,
        "model": model,
        "record_count": len(records),
        "pass0_intervals_sec": sample_intervals,
        "has_quick_contract": "quick_contract" in author_input,
        "passes": ["pass0_ew_reads", "pass1_d1_d2_map", "pass2_points_stories_proposals", "pass3_validate_and_submit"],
    }


def run_local_author(
    *,
    job_root: Path,
    expected_revision: str,
    endpoint: str,
    model: str,
    allow_remote: bool = False,
    dry_run: bool = False,
) -> dict:
    from pipeline import editorial_current_conversation as current

    clean_endpoint = validate_endpoint(endpoint, allow_remote=allow_remote)
    plan = build_pass_plan(job_root, expected_revision=expected_revision, endpoint=clean_endpoint, model=model)
    if dry_run:
        return {"status": "dry_run", "plan": plan}

    root, prepared, _ = current._load(job_root)
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    read_excerpts = []
    for idx, interval in enumerate(plan["pass0_intervals_sec"]):
        action = {
            "schema_version": "ew.tool.v1",
            "source_id": prepared["source"]["asset"]["source_id"],
            "world_revision": prepared["world_revision"],
            "question_id": f"local-pass0-{idx}",
            "action": "read_evidence",
            "args": {"intervals_sec": [interval], "families": ["stt"], "context_cues": 1, "max_bytes": 12000},
        }
        page = current.read(root, action, expected_revision=expected_revision)
        for row in page.get("records") or []:
            read_excerpts.append(f"e{row.get('ordinal', 0) + 1}: {row.get('raw_text', '')[:160]}")

    context_blob = json.dumps({
        "vod_info": prepared["vod_info"],
        "quick_contract": author_input.get("quick_contract"),
        "read_excerpts": read_excerpts[:120],
    }, ensure_ascii=False)

    pass1 = chat_completion(clean_endpoint, model, [
        {"role": "system", "content": "Author Loki D1/D2 summary_lines and ranges strictly from read_excerpts. Never invent citations."},
        {"role": "user", "content": context_blob},
    ], schema=PASS1_SCHEMA)

    pass2 = chat_completion(clean_endpoint, model, [
        {"role": "system", "content": "Author Loki points, stories, proposals preserving entertainment/tiki-taka, distant premises, and counterevidence. Keep story_beats inside source_spans and used_evidence_ids."},
        {"role": "user", "content": json.dumps({"context": context_blob, "pass1": pass1}, ensure_ascii=False)},
    ], schema=PASS2_SCHEMA)

    assembled = {
        "base_revision": prepared["identity"]["base_revision"],
        "summary": pass1.get("summary", ""),
        "summary_evidence_refs": pass1.get("summary_evidence_refs", []),
        "ranges": pass1.get("ranges", []),
        "points": pass2.get("points", []),
        "stories": pass2.get("stories", []),
        "proposals": pass2.get("proposals", []),
        "group_outcomes": pass2.get("group_outcomes", []),
        "unresolved_questions": pass2.get("unresolved_questions", []),
    }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(assembled, ensure_ascii=False, indent=2) + "\n")
        tmp_path = Path(tmp.name)
    try:
        dry_check = current.submit(root, tmp_path, expected_revision=expected_revision, validate_only=True)
        final_report = current.submit(root, tmp_path, expected_revision=expected_revision)
        return {"status": "submitted", "dry_check": dry_check, "report": final_report}
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_local_author(
        job_root=Path(args.job_root),
        expected_revision=args.expected_revision,
        endpoint=args.endpoint,
        model=args.model,
        allow_remote=args.allow_remote,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
