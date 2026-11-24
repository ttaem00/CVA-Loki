"""Zero-dependency OpenAI/Ollama-compatible 3-pass runner for local LLMs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
import urllib.request

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
NON_CHAT_MODEL_TOKENS = ("embed", "rerank", "whisper", "tts", "bge-", "nomic-", "clip")

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
                    "level": {"type": "string", "enum": ["D1", "D2"]},
                    "parent_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "level", "title", "content", "start", "end", "evidence_refs"],
            },
        },
    },
    "required": ["summary", "summary_evidence_refs", "ranges"],
}

PASS2_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "d2_ref": {"type": "string"},
                    "start_evidence_ref": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "content", "d2_ref", "start_evidence_ref", "evidence_refs"],
            },
        },
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "story_ref": {"type": "string"},
                    "point_ref": {"type": "string"},
                    "title": {"type": "string"},
                    "why_notable": {"type": "string"},
                    "used_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "roles": {
                        "type": "object",
                        "properties": {
                            "setup": {"type": "array", "items": {"type": "string"}},
                            "performance": {"type": "array", "items": {"type": "string"}},
                            "event": {"type": "array", "items": {"type": "string"}},
                            "reaction": {"type": "array", "items": {"type": "string"}},
                            "result": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["setup", "performance", "event", "reaction", "result"],
                    },
                },
                "required": ["story_ref", "point_ref", "title", "why_notable", "used_evidence_ids", "roles"],
            },
        },
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "story_refs": {"type": "array", "items": {"type": "string"}},
                    "point_refs": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "story_summary": {"type": "string"},
                    "selection_reason": {"type": "string"},
                    "source_spans": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_sec": {"type": "number"},
                                "end_sec": {"type": "number"},
                                "story_roles": {"type": "array", "items": {"type": "string"}},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                "reason": {"type": "string"},
                            },
                            "required": ["start_sec", "end_sec", "story_roles", "evidence_refs", "reason"],
                        },
                    },
                    "story_beats": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "span_indexes": {"type": "array", "items": {"type": "integer"}},
                                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                                "reason": {"type": "string"},
                            },
                            "required": ["role", "span_indexes", "evidence_refs", "reason"],
                        },
                    },
                    "estimated_edited_duration": {
                        "type": "object",
                        "properties": {
                            "min_sec": {"type": "number"},
                            "max_sec": {"type": "number"},
                            "category": {"type": "string"},
                        },
                        "required": ["min_sec", "max_sec", "category"],
                    },
                    "missing_information": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "story_refs", "point_refs", "title", "story_summary", "selection_reason",
                    "source_spans", "story_beats", "estimated_edited_duration",
                    "missing_information", "uncertainties",
                ],
            },
        },
        "group_outcomes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "terminal_state": {"type": "string"},
                    "flow_refs": {"type": "array", "items": {"type": "string"}},
                    "range_refs": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["terminal_state", "flow_refs", "reason", "evidence_refs"],
            },
        },
        "unresolved_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
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


def _endpoint_origin(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _fetch_ollama_tags(endpoint: str) -> list[str] | None:
    origin = _endpoint_origin(endpoint)
    try:
        req = urllib.request.Request(origin + "/api/tags", headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return None
        return [
            str(row.get("name") or row.get("model") or "").strip()
            for row in models
            if isinstance(row, dict) and (row.get("name") or row.get("model"))
        ]
    except Exception:
        return None


def resolve_model(endpoint: str, model: str | None = None) -> str:
    requested = (model if isinstance(model, str) else "") or os.environ.get("LOKI_LOCAL_LLM_MODEL", "")
    requested = requested.strip() or "auto"
    if requested != "auto":
        return requested
    raw_ids: list[str] = []
    try:
        req = urllib.request.Request(endpoint + "/models", headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw_ids = [
            str(row.get("id") or "").strip()
            for row in (payload.get("data") or [])
            if isinstance(row, dict) and row.get("id")
        ]
    except Exception:
        ollama_tags = _fetch_ollama_tags(endpoint)
        if ollama_tags is None:
            raise
        raw_ids = ollama_tags
    candidates = [
        mid for mid in raw_ids
        if not any(tok in mid.lower() for tok in NON_CHAT_MODEL_TOKENS)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("local_llm_no_generative_models_available")
    raise ValueError("local_llm_multiple_models_specify_explicitly:" + ",".join(candidates[:8]))


def _is_ollama_endpoint(endpoint: str) -> bool:
    return _fetch_ollama_tags(endpoint) is not None


def _strip_reasoning_and_extract_json(raw_text: str) -> dict:
    cleaned = str(raw_text or "").strip()
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"<\|start\|>[\s\S]*?<\|message\|>", "", cleaned).strip()
    cleaned = re.sub(r"<\|channel\|>[^<]*<\|message\|>", "", cleaned).strip()
    cleaned = re.sub(r"<\|end\|>|<\|return\|>", "", cleaned).strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(cleaned):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(cleaned, idx)
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    raise ValueError("local_llm_invalid_json_output")


def chat_completion(
    endpoint: str,
    model: str,
    messages: list[dict],
    *,
    schema: dict,
    timeout_sec: int = 300,
    ollama_mode: bool | None = None,
    num_ctx: int = 32768,
) -> tuple[dict, str]:
    use_ollama = _is_ollama_endpoint(endpoint) if ollama_mode is None else ollama_mode
    if use_ollama:
        url = _endpoint_origin(endpoint) + "/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.2, "num_ctx": num_ctx},
        }
    else:
        url = endpoint + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "loki_pass", "strict": True, "schema": schema},
            },
        }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if use_ollama:
        content = (raw.get("message") or {}).get("content", "")
    else:
        content = raw["choices"][0]["message"]["content"]
    actual_model = str(raw.get("model") or model)
    return _strip_reasoning_and_extract_json(content), actual_model


def _priority_intervals(prepared: dict, author_input: dict) -> list[list[int]]:
    duration = int(prepared["vod_info"].get("duration") or 0)
    packet = prepared.get("packet") or {}
    supplemental = packet.get("supplemental_sources") or {}
    intervals: list[list[int]] = []

    def add_window(s: float, e: float) -> None:
        a = max(0, int(s) - 10)
        b = max(a + 30, int(e) + 15)
        if duration > 0:
            b = min(duration, b)
        if a < b:
            intervals.append([a, b])

    for prop in packet.get("support_proposals") or []:
        if isinstance(prop, dict):
            s = prop.get("start_sec")
            e = prop.get("end_sec")
            if s is None and isinstance(prop.get("coarse_window"), dict):
                s = prop["coarse_window"].get("start_sec")
                e = prop["coarse_window"].get("end_sec")
            if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                add_window(s, e)
    for clip in packet.get("user_clips") or []:
        if isinstance(clip, dict) and isinstance(clip.get("offset_sec"), (int, float)):
            add_window(clip["offset_sec"], clip["offset_sec"] + max(15, float(clip.get("duration") or 30)))
    for anchor in (supplemental.get("timeline_comment_context") or {}).get("anchors") or []:
        if isinstance(anchor, dict):
            a_sec = anchor.get("time_sec") if anchor.get("time_sec") is not None else anchor.get("sec")
            if isinstance(a_sec, (int, float)):
                add_window(a_sec, a_sec + 45)
    for hint in (author_input.get("local_chunk_hints") or {}).get("windows") or []:
        if isinstance(hint, dict) and isinstance(hint.get("start_sec"), (int, float)):
            add_window(hint["start_sec"], hint.get("end_sec") or hint["start_sec"] + 60)


    records = prepared["source"].get("records") or []
    if len(intervals) < 8 and records:
        step = max(1, len(records) // 10)
        for row in records[::step][:10]:
            s = float(row.get("start_sec", 0))
            add_window(s, s + 40)

    merged: list[list[int]] = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1] + 5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged[:16]


def build_pass_plan(job_root: Path, *, expected_revision: str, endpoint: str, model: str) -> dict:
    """Inspect prepared job and build priority peak/cluster/low-reaction 3-pass plan."""
    from pipeline import editorial_current_conversation as current

    root, prepared, _ = current._load(job_root)
    if expected_revision != prepared["prepared_revision"]:
        raise ValueError("current_conversation_prepare_conflict")
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    intervals = _priority_intervals(prepared, author_input)
    return {
        "schema_version": "loki.local_llm_plan.v2",
        "job_root": str(root),
        "prepared_revision": expected_revision,
        "base_revision": prepared["identity"]["base_revision"],
        "endpoint": endpoint,
        "model": model,
        "record_count": len(prepared["source"].get("records") or []),
        "pass0_intervals_sec": intervals,
        "has_quick_contract": "quick_contract" in author_input,
        "passes": ["pass0_ew_reads", "pass1_d1_d2_map", "pass2_points_stories_proposals", "pass3_normalize_isolate_submit"],
    }


def _single_line(text: object) -> str:
    return " ".join(str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()


def _normalize_local_payload(assembled: dict, *, prepared: dict, root: Path) -> dict:
    assembled["summary"] = _single_line(assembled.get("summary"))
    for r in assembled.get("ranges") or []:
        if not isinstance(r, dict):
            continue
        r["title"] = _single_line(r.get("title"))
        r["content"] = _single_line(r.get("content") or r.get("title"))
        if r.get("level") == "D1":
            r.pop("parent_id", None)
    for p in assembled.get("points") or []:
        if isinstance(p, dict):
            p["title"] = _single_line(p.get("title"))
            p["content"] = _single_line(p.get("content") or p.get("title"))
    if prepared.get("media_source") is None:
        assembled["proposals"] = []
    else:
        for prop in assembled.get("proposals") or []:
            if not isinstance(prop, dict):
                continue
            prop.pop("used_evidence_ids", None)
            prop["title"] = _single_line(prop.get("title"))
            prop["story_summary"] = _single_line(prop.get("story_summary"))
            prop["selection_reason"] = _single_line(prop.get("selection_reason"))
            spans = prop.get("source_spans") or []
            for idx, sp in enumerate(spans):
                if isinstance(sp, dict):
                    if idx == 0 or not sp.get("exclusion_before"):
                        sp.pop("exclusion_before", None)
    outcomes = assembled.get("group_outcomes") or []
    for row in outcomes:
        if isinstance(row, dict):
            row["reason"] = _single_line(row.get("reason"))
            if row.get("terminal_state") != "represented" and not row.get("range_refs"):
                row.pop("range_refs", None)
    has_broadcast = any(
        isinstance(row, dict)
        and "broadcast" in (row.get("flow_refs") or [])
        and row.get("terminal_state") == "represented"
        for row in outcomes
    )
    if not has_broadcast:
        range_ids = [r["id"] for r in (assembled.get("ranges") or []) if isinstance(r, dict) and r.get("id")]
        ev_refs = list(dict.fromkeys(assembled.get("summary_evidence_refs") or []))
        outcomes.insert(0, {
            "terminal_state": "represented",
            "flow_refs": ["broadcast"],
            **({"range_refs": range_ids} if range_ids else {}),
            "reason": assembled["summary"] or "Full broadcast flow covered.",
            "evidence_refs": ev_refs or ["e1"],
        })
        assembled["group_outcomes"] = outcomes

    mm_review = prepared.get("multimodal_final_review") or {}
    mm_ready = (
        mm_review.get("policy") == "consume_or_defer_v1"
        and (mm_review.get("initial_context") or {}).get("status") == "ready"
    )
    has_nav = bool(prepared.get("multimodal_navigation") or ((root / "navigation-receipts").is_dir() and any((root / "navigation-receipts").iterdir())))
    if mm_ready and not has_nav and assembled.get("multimodal_decision") is None:
        assembled["multimodal_decision"] = {"decision": "defer", "reason": "local_llm_text_authoring"}
    return assembled


def _configure_workspace_env(config_path: Path | None) -> tuple[Path | None, dict | None]:
    if config_path is None:
        return None, None
    runtime_dir = Path(__file__).resolve().parent
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    import loki

    root, work, policy = loki.configuration(config_path)
    loki.verify_release()
    os.environ["LOKI_WORKSPACE"] = str(root)
    os.environ["LOKI_CONFIG_PATH"] = str(Path(config_path).resolve())
    os.environ["LOKI_WORK_DIR"] = str(work)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return root, policy


def _drain_continuations(current_mod, root: Path, initial_page: dict, *, expected_revision: str, excerpts: list[str], chat_excerpts: list[str], max_pages: int = 12) -> None:
    def record_rows(page: dict) -> None:
        for row in page.get("records") or []:
            if row.get("family") == "chat":
                chat_excerpts.append(f"chat-original:{row.get('record_id')}: {_single_line(row.get('raw_text'))[:140]}")
            else:
                excerpts.append(f"e{row.get('ordinal', 0) + 1}: {_single_line(row.get('raw_text'))[:160]}")

    page = initial_page
    record_rows(page)
    pages_seen = 0
    while isinstance(page, dict) and page.get("next_cursor") and pages_seen < max_pages:
        rid = page.get("read_id")
        if not rid:
            break
        cont = current_mod.continuation(root, read_id=rid, expected_revision=expected_revision)
        next_action = cont.get("next_original_action") if isinstance(cont, dict) else None
        if not isinstance(next_action, dict):
            break
        page = current_mod.read(root, next_action, expected_revision=expected_revision)
        record_rows(page)
        pages_seen += 1


def run_local_author(
    *,
    job_root: Path,
    expected_revision: str,
    endpoint: str,
    model: str = "auto",
    config_path: Path | None = None,
    allow_remote: bool = False,
    dry_run: bool = False,
) -> dict:
    _configure_workspace_env(config_path)
    from pipeline import editorial_current_conversation as current

    clean_endpoint = validate_endpoint(endpoint, allow_remote=allow_remote)
    if dry_run:
        plan = build_pass_plan(job_root, expected_revision=expected_revision, endpoint=clean_endpoint, model=model)
        return {"status": "dry_run", "plan": plan}

    root, prepared, _ = current._load(job_root)
    if config_path is not None:
        import loki
        _, _, policy = loki.configuration(config_path)
        loki.validate_author_mode(prepared["identity"]["author"], policy["author_mode"])

    selected_model = resolve_model(clean_endpoint, model)
    ollama_mode = _is_ollama_endpoint(clean_endpoint)
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    intervals = _priority_intervals(prepared, author_input)


    excerpts: list[str] = []
    chat_excerpts: list[str] = []
    for idx, interval in enumerate(intervals):
        action = {
            "schema_version": "ew.tool.v1",
            "source_id": prepared["source"]["asset"]["source_id"],
            "world_revision": prepared["world_revision"],
            "question_id": f"local-pass0-{idx}",
            "action": "read_evidence",
            "args": {"intervals_sec": [interval], "families": ["stt", "chat"], "context_cues": 1, "max_bytes": 14000},
        }
        page = current.read(root, action, expected_revision=expected_revision)
        _drain_continuations(current, root, page, expected_revision=expected_revision, excerpts=excerpts, chat_excerpts=chat_excerpts)

    context_blob = json.dumps({
        "vod_info": prepared["vod_info"],
        "quick_contract": author_input.get("quick_contract"),
        "broadcast_flow_context": author_input.get("broadcast_flow_context"),
        "signal_orientation": (prepared.get("packet") or {}).get("navigation_source_reads", {}).get("signal_orientation"),
        "media_available": prepared.get("media_source") is not None,
        "read_excerpts": excerpts[:160],
        "chat_excerpts": chat_excerpts[:80],
    }, ensure_ascii=False)


    pass1, reported_model = chat_completion(
        clean_endpoint, selected_model, [
            {"role": "system", "content": "Author Loki D1/D2 summary and ranges strictly from read_excerpts. D1 omits parent_id; D2 requires parent_id and content. No newlines in text fields."},
            {"role": "user", "content": context_blob},
        ], schema=PASS1_SCHEMA, ollama_mode=ollama_mode,
    )
    pass2, reported_model2 = chat_completion(
        clean_endpoint, selected_model, [
            {"role": "system", "content": "Author Loki points, stories, proposals, group_outcomes preserving entertainment/tiki-taka, distant premises, and counterevidence. Keep story_beats inside source_spans and story used_evidence_ids."},
            {"role": "user", "content": json.dumps({"context": context_blob, "pass1": pass1}, ensure_ascii=False)},
        ], schema=PASS2_SCHEMA, ollama_mode=ollama_mode,
    )
    actual_model = reported_model2 or reported_model or selected_model

    assembled = _normalize_local_payload({
        "base_revision": prepared["identity"]["base_revision"],
        "summary": pass1.get("summary", ""),
        "summary_evidence_refs": pass1.get("summary_evidence_refs", []),
        "ranges": pass1.get("ranges", []),
        "points": pass2.get("points", []),
        "stories": pass2.get("stories", []),
        "proposals": pass2.get("proposals", []),
        "group_outcomes": pass2.get("group_outcomes", []),
        "unresolved_questions": pass2.get("unresolved_questions", []),
    }, prepared=prepared, root=root)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(assembled, ensure_ascii=False, indent=2) + "\n")
        tmp_path = Path(tmp.name)

    try:
        try:
            dry_check = current.submit(root, tmp_path, expected_revision=expected_revision, validate_only=True)
        except current.SubmissionRejected as rej:
            repair_prompt = json.dumps({
                "error": rej.rejection.get("error"),
                "diagnostics": rej.rejection.get("diagnostics"),
                "current_draft": assembled,
            }, ensure_ascii=False)
            repaired, actual_model = chat_completion(
                clean_endpoint, selected_model, [
                    {"role": "system", "content": "Repair only the reported validation error in this Loki JSON response. Keep all valid sections intact."},
                    {"role": "user", "content": repair_prompt},
                ], schema={
                    "type": "object",
                    "properties": {**PASS1_SCHEMA["properties"], **PASS2_SCHEMA["properties"]},
                    "required": ["summary", "summary_evidence_refs", "ranges", "points", "stories", "proposals", "group_outcomes", "unresolved_questions"],
                }, ollama_mode=ollama_mode,
            )
            repaired["base_revision"] = prepared["identity"]["base_revision"]
            assembled = _normalize_local_payload(repaired, prepared=prepared, root=root)
            tmp_path.write_text(json.dumps(assembled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            dry_check = current.submit(root, tmp_path, expected_revision=expected_revision, validate_only=True)


        if config_path is not None:
            import loki
            loki.isolate(root, config_path)

        final_report = current.submit(root, tmp_path, expected_revision=expected_revision)
        run_receipt = {
            "schema_version": "loki.local_llm_run.v1",
            "endpoint": clean_endpoint,
            "requested_model": model,
            "actual_model": actual_model,
            "ollama_mode": ollama_mode,
            "prepared_revision": expected_revision,
            "result_sha256": final_report.get("result_sha256"),
        }
        runs_dir = root / "local-llm-runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_sha = hashlib.sha256(json.dumps(run_receipt, sort_keys=True).encode("utf-8")).hexdigest()
        (runs_dir / f"{run_sha[:16]}.json").write_text(json.dumps(run_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"status": "submitted", "actual_model": actual_model, "dry_check": dry_check, "report": final_report}
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _discover_config_path(job_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    env_cfg = os.environ.get("LOKI_CONFIG_PATH")
    if env_cfg and Path(env_cfg).is_file():
        return Path(env_cfg)
    resolved = job_root.resolve()
    for candidate_dir in (resolved, *resolved.parents):
        cfg = candidate_dir / "loki.json"
        if cfg.is_file():
            return cfg
    cwd_cfg = Path.cwd() / "loki.json"
    if cwd_cfg.is_file():
        return cwd_cfg
    return None


def main(argv=None) -> int:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Workspace-local loki.json (auto-discovered from --job-root if omitted)")
    parser.add_argument("--job-root", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config_path = _discover_config_path(Path(args.job_root), args.config)
    if config_path is None:
        parser.error("--config <workspace/loki.json> could not be auto-discovered from --job-root")
    result = run_local_author(
        job_root=Path(args.job_root),
        expected_revision=args.expected_revision,
        endpoint=args.endpoint,
        model=args.model,
        config_path=config_path,
        allow_remote=args.allow_remote,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
