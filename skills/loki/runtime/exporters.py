"""Pure-stdlib Web AI pack exporter, draft importer, and creator timeline exporters."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

VERIFY_RESULT_SCRIPT = '''"""Standalone stdlib validator for Web AI Loki responses (no external deps)."""
import json
import sys
from pathlib import Path

REQUIRED = [
    "base_revision", "summary", "summary_evidence_refs",
    "ranges", "points", "stories", "proposals",
    "group_outcomes", "unresolved_questions",
]
ROLE_KEYS = {"setup", "performance", "event", "reaction", "result"}


def _no_newline(errors, path, value):
    if isinstance(value, str) and ("\\n" in value or "\\r" in value):
        errors.append(f"{path}_contains_newline")


def _extract_refs(value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in {"summary_evidence_refs", "evidence_refs", "used_evidence_ids", "start_evidence_ref", "end_evidence_ref"}:
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend(x for x in v if isinstance(x, str))
            else:
                _extract_refs(v, out)
    elif isinstance(value, list):
        for item in value:
            _extract_refs(item, out)


def validate(payload, clusters=None):
    errors = []
    if not isinstance(payload, dict):
        return ["response_must_be_json_object"]
    missing = [k for k in REQUIRED if k not in payload]
    if missing:
        errors.append("missing_fields:" + ",".join(missing))
    for key in ("summary_evidence_refs", "ranges", "points", "stories", "proposals", "group_outcomes", "unresolved_questions"):
        if key in payload and not isinstance(payload[key], list):
            errors.append(f"{key}_must_be_list")
    if isinstance(payload.get("summary"), str):
        if not payload["summary"].strip():
            errors.append("summary_empty")
        _no_newline(errors, "summary", payload["summary"])
    if isinstance(payload.get("ranges"), list) and not payload["ranges"]:
        errors.append("ranges_empty")

    d1_ids = set()
    for idx, row in enumerate(payload.get("ranges") or []):
        if not isinstance(row, dict):
            errors.append(f"ranges[{idx}]_must_be_object")
            continue
        level = row.get("level")
        if level == "D1":
            if "parent_id" in row and row["parent_id"] is not None:
                errors.append(f"ranges[{idx}].d1_forbids_parent_id")
            if isinstance(row.get("id"), str):
                d1_ids.add(row["id"])
        elif level == "D2":
            if not row.get("parent_id"):
                errors.append(f"ranges[{idx}].d2_requires_parent_id")
            elif d1_ids and row["parent_id"] not in d1_ids:
                errors.append(f"ranges[{idx}].d2_parent_not_found:{row['parent_id']}")
        if not str(row.get("content") or "").strip():
            errors.append(f"ranges[{idx}].content_required")
        _no_newline(errors, f"ranges[{idx}].title", row.get("title"))
        _no_newline(errors, f"ranges[{idx}].content", row.get("content"))

    point_ids = []
    for idx, row in enumerate(payload.get("points") or []):
        if not isinstance(row, dict):
            continue
        point_ids.append(row.get("id"))
        _no_newline(errors, f"points[{idx}].title", row.get("title"))
        _no_newline(errors, f"points[{idx}].content", row.get("content"))

    stories = payload.get("stories") or []
    story_map = {}
    story_point_refs = [s.get("point_ref") for s in stories if isinstance(s, dict)]
    if story_point_refs != point_ids:
        errors.append("stories_must_align_1to1_with_points")
    for idx, story in enumerate(stories):
        if not isinstance(story, dict):
            continue
        if isinstance(story.get("story_ref"), str):
            story_map[story["story_ref"]] = story
        roles = story.get("roles")
        if not isinstance(roles, dict) or set(roles) != ROLE_KEYS:
            errors.append(f"stories[{idx}].roles_must_contain_all_5_keys")

    for idx, prop in enumerate(payload.get("proposals") or []):
        if not isinstance(prop, dict):
            continue
        if "used_evidence_ids" in prop:
            errors.append(f"proposals[{idx}].forbids_used_evidence_ids_on_proposal")
        _no_newline(errors, f"proposals[{idx}].title", prop.get("title"))
        _no_newline(errors, f"proposals[{idx}].story_summary", prop.get("story_summary"))
        _no_newline(errors, f"proposals[{idx}].selection_reason", prop.get("selection_reason"))
        spans = prop.get("source_spans") or []
        if spans and isinstance(spans[0], dict) and spans[0].get("exclusion_before") is not None:
            errors.append(f"proposals[{idx}].source_spans[0]_forbids_exclusion_before")
        span_refs = {ref for sp in spans if isinstance(sp, dict) for ref in (sp.get("evidence_refs") or [])}
        linked_story_refs = set()
        for s_ref in prop.get("story_refs") or []:
            st = story_map.get(s_ref)
            if st:
                linked_story_refs.update(st.get("used_evidence_ids") or [])
        for b_idx, beat in enumerate(prop.get("story_beats") or []):
            if not isinstance(beat, dict):
                continue
            for si in beat.get("span_indexes") or []:
                if not isinstance(si, int) or si < 0 or si >= len(spans):
                    errors.append(f"proposals[{idx}].story_beats[{b_idx}].invalid_span_index:{si}")
            for ref in beat.get("evidence_refs") or []:
                if span_refs and ref not in span_refs:
                    errors.append(f"proposals[{idx}].story_beats[{b_idx}].ref_outside_source_spans:{ref}")
                if linked_story_refs and ref not in linked_story_refs:
                    errors.append(f"proposals[{idx}].story_beats[{b_idx}].ref_not_in_story_used_evidence_ids:{ref}")

    outcomes = payload.get("group_outcomes") or []
    has_broadcast = any(
        isinstance(row, dict)
        and "broadcast" in (row.get("flow_refs") or [])
        and row.get("terminal_state") == "represented"
        for row in outcomes
    )
    if not has_broadcast:
        errors.append("group_outcomes_requires_broadcast_represented")
    for idx, row in enumerate(outcomes):
        if isinstance(row, dict):
            _no_newline(errors, f"group_outcomes[{idx}].reason", row.get("reason"))

    mm = payload.get("multimodal_decision")
    if mm is not None:
        if not isinstance(mm, dict) or mm.get("decision") != "defer" or not str(mm.get("reason") or "").strip():
            errors.append("multimodal_decision_invalid")

    if isinstance(clusters, dict):
        if clusters.get("media_available") is False and (payload.get("proposals") or []):
            errors.append("non_media_job_forbids_proposals")
        valid_ids = set(clusters.get("valid_record_ids") or [])
        valid_chats = set(clusters.get("exported_chat_ids") or [])
        if valid_ids:
            cited = []
            _extract_refs(payload, cited)
            for ref in cited:
                if ref.startswith("chat-original:") and valid_chats and ref not in valid_chats:
                    errors.append(f"cited_chat_ref_not_exported:{ref}")
                elif re.fullmatch(r"e\\d+", ref) and ref not in valid_ids:
                    errors.append(f"cited_record_ref_not_exported:{ref}")
    return errors


if __name__ == "__main__":
    import re

    target = Path(sys.argv[1])
    data = json.loads(target.read_text(encoding="utf-8-sig"))
    cluster_data = None
    for candidate in (target.parent / "evidence_clusters.json", Path(__file__).with_name("evidence_clusters.json")):
        if candidate.is_file():
            try:
                cluster_data = json.loads(candidate.read_text(encoding="utf-8-sig"))
                break
            except (OSError, ValueError):
                pass
    issues = validate(data, clusters=cluster_data)
    print(json.dumps({"status": "PASS" if not issues else "FAIL", "errors": issues}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not issues else 2)
'''


def _single_line(value: object) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()


def _fmt_hms(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_tc(seconds: float, fps: int = 30) -> str:
    total_frames = max(0, int(round(float(seconds or 0) * fps)))
    frames = total_frames % fps
    total_sec = total_frames // fps
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def _sec_from_text(value) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip()
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return 0.0
    return 0.0


def _build_low_reaction_windows(records: list[dict], support_proposals: list[dict], duration_sec: float) -> list[dict]:
    """Identify uncovered gaps between highlight peaks across full broadcast duration."""
    busy = []
    for prop in support_proposals or []:
        if not isinstance(prop, dict):
            continue
        start = prop.get("start_sec")
        end = prop.get("end_sec")
        if start is None and isinstance(prop.get("coarse_window"), dict):
            start = prop["coarse_window"].get("start_sec")
            end = prop["coarse_window"].get("end_sec")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            busy.append((max(0.0, float(start)), min(float(duration_sec or end), float(end))))
    busy.sort()
    merged: list[list[float]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1] + 5.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    limit_end = float(duration_sec or 0.0)
    for s, e in merged:
        if s > cursor + 15.0:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if limit_end > cursor + 15.0:
        gaps.append((cursor, limit_end))
    if not gaps and limit_end > 0:
        gaps = [(0.0, limit_end)]

    windows = []
    for start, end in gaps[:16]:
        in_gap = [
            r for r in records
            if r.get("raw_text") and isinstance(r.get("start_sec"), (int, float)) and start <= r["start_sec"] <= end
        ]
        sample = in_gap[::max(1, len(in_gap) // 3)][:3] if in_gap else []
        windows.append({
            "start_sec": round(start, 2),
            "end_sec": round(end, 2),
            "sample_refs": [
                {"ref": f"e{r['ordinal'] + 1}", "start_sec": r.get("start_sec"), "text": _single_line(r.get("raw_text"))[:140]}
                for r in sample
            ],
        })
    return windows


def _shard_transcript_files(title: str, video_no: str, records: list[dict], *, max_parts: int = 5, target_chunk_bytes: int = 220_000) -> list[tuple[str, bytes]]:
    header = [
        f"# {title} ({video_no})",
        "",
        "원문 STT 인용 규칙: `e<N>` ID를 그대로 사용하고 추측으로 장면을 만들지 마세요.",
        "",
    ]
    header_text = "\n".join(header) + "\n"
    stt_lines = []
    for row in records:
        if row.get("family", "stt") not in {"stt", "subtitle", ""}:
            continue
        ref = f"e{row['ordinal'] + 1}"
        ts = _fmt_hms(row.get("start_sec", 0))
        text = _single_line(row.get("raw_text"))
        stt_lines.append(f"- [{ts}] `{ref}`: {text}")
    full_bytes = (header_text + "\n".join(stt_lines) + "\n").encode("utf-8")
    if len(full_bytes) <= target_chunk_bytes:
        return [("transcript_condensed.md", full_bytes)]

    chunk_limit = max(target_chunk_bytes, math.ceil(len(full_bytes) / max(1, max_parts)))
    parts: list[list[str]] = []
    current_lines: list[str] = []
    current_bytes = len(header_text.encode("utf-8")) + 64
    for line in stt_lines:
        line_bytes = len((line + "\n").encode("utf-8"))
        if current_lines and current_bytes + line_bytes > chunk_limit and len(parts) + 1 < max_parts:
            parts.append(current_lines)
            current_lines = []
            current_bytes = len(header_text.encode("utf-8")) + 64
        current_lines.append(line)
        current_bytes += line_bytes
    if current_lines:
        parts.append(current_lines)

    out: list[tuple[str, bytes]] = []
    total = len(parts)
    for idx, part_lines in enumerate(parts, start=1):
        banner = f"# {title} ({video_no}) — Part {idx}/{total}\n\n"
        out.append((f"transcript_part{idx}.md", (banner + "\n".join(part_lines) + "\n").encode("utf-8")))
    return out


def export_web_pack(job_root: Path, output_zip: Path, *, expected_revision: str) -> dict:
    """Create both Web AI ZIP and <=8-file flat folder preserving all EvidenceWorld assets."""
    from pipeline import editorial_current_conversation as current
    from pipeline.evidence_world.contracts import canonical_json

    root, prepared, _ = current._load(job_root)
    if expected_revision != prepared["prepared_revision"]:
        raise ValueError("current_conversation_prepare_conflict")
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    vod = prepared["vod_info"]
    records = prepared["source"].get("records") or []
    packet = prepared.get("packet") or {}
    nav_reads = packet.get("navigation_source_reads") or {}
    signal_orientation = nav_reads.get("signal_orientation") or {}
    supplemental = packet.get("supplemental_sources") or {}

    clusters_meta = (prepared.get("navigation_features") or {}).get("clusters")
    cluster_summary = None
    if clusters_meta and Path(clusters_meta["path"]).is_file():
        try:
            raw_clusters = json.loads(Path(clusters_meta["path"]).read_text(encoding="utf-8"))
            all_groups = raw_clusters.get("groups") or []
            cluster_summary = {
                "profile": raw_clusters.get("profile"),
                "grouping_complete": raw_clusters.get("grouping_complete"),
                "group_count": len(all_groups),
                "groups": all_groups,
            }
        except (OSError, ValueError):
            cluster_summary = None

    quiet_samples = [
        {"ordinal": r["ordinal"], "ref": f"e{r['ordinal'] + 1}", "start_sec": r.get("start_sec"), "text": _single_line(r.get("raw_text"))[:140]}
        for r in records
        if r.get("raw_text")
    ][::max(1, len(records) // 25)]
    low_reaction_windows = _build_low_reaction_windows(
        records, packet.get("support_proposals") or [], float(vod.get("duration") or 0),
    )

    chat_records = [r for r in records if r.get("family") == "chat" and r.get("record_id")]
    chat_samples = [
        {
            "ref": f"chat-original:{r['record_id']}",
            "start_sec": r.get("start_sec"),
            "text": _single_line(r.get("raw_text"))[:160],
        }
        for r in chat_records[::max(1, len(chat_records) // 40)]
    ]
    exported_chat_ids = [f"chat-original:{r['record_id']}" for r in chat_records]
    valid_record_ids = [f"e{r['ordinal'] + 1}" for r in records]
    media_available = bool(prepared.get("media_source"))

    evidence_clusters = {
        "schema_version": "loki.web_pack_evidence.v2",
        "prepared_revision": expected_revision,
        "base_revision": prepared["identity"]["base_revision"],
        "execution_mode": prepared.get("execution_mode"),
        "media_available": media_available,
        "vod_info": vod,
        "quick_contract": author_input.get("quick_contract"),
        "signal_orientation": signal_orientation,
        "phrase_view": nav_reads.get("phrase_view") or author_input.get("phrase_view") or [],
        "support_proposals": packet.get("support_proposals") or [],
        "document_vector_clusters": cluster_summary,
        "local_chunk_hints": author_input.get("local_chunk_hints"),
        "broadcast_flow_context": author_input.get("broadcast_flow_context") or {},
        "user_clips": packet.get("user_clips") or [],
        "timeline_comments": supplemental.get("timeline_comment_context") or {},
        "chat_rows_sample": chat_samples,
        "low_reaction_coverage_windows": low_reaction_windows,
        "low_reaction_coverage_samples": quiet_samples,
        "valid_record_ids": valid_record_ids,
        "exported_chat_ids": exported_chat_ids,
        "authority": "web_ai_orientation_pack; local import verifies exported refs and completes EW reads before submit",
    }

    transcript_files = _shard_transcript_files(
        str(vod.get("title") or "VOD"), str(vod.get("video_no") or ""), records, max_parts=5,
    )
    transcript_names = [name for name, _ in transcript_files]

    prompt_md = "\n".join([
        "# CVA-Loki Web AI 작성 안내",
        "",
        f"- 대상 VOD: `{vod.get('video_no', '')}` — {vod.get('title', '')}",
        f"- `base_revision`: `{prepared['identity']['base_revision']}`",
        f"- `prepared_revision`: `{expected_revision}`",
        f"- `media_available`: `{media_available}` (False일 경우 `proposals`는 반드시 빈 배열 `[]`)",
        f"- 분할 자막 파일({len(transcript_names)}개): {', '.join(transcript_names)}",
        "",
        "## 플랫폼별 업로드 방법 (최대 8파일)",
        "- **ChatGPT Web (Projects / Code Interpreter):** ZIP 또는 `<name>_files/` 폴더 전체를 업로드한 뒤, 가능하면 `python verify_result.py response.json`으로 검증 후 JSON을 출력하세요.",
        "- **Claude Web (Projects):** 200K 컨텍스트 한도를 넘지 않도록 분할된 `transcript_part*.md`와 `evidence_clusters.json`을 함께 올려 작성하세요.",
        "- **Gemini Web (Gems):** ZIP 대신 평면 폴더 `<name>_files/`의 전체 파일(≤8파일)을 그대로 첨부하세요.",
        "",
        "## 핵심 원칙 (Loki 3.0.9.1)",
        "1. `evidence_clusters.json`의 `phrase_view`·피크·전체 군집·클립·댓글·저반응 구간과 자막의 `e<N>` 원문을 함께 확인합니다.",
        "2. 길이와 무관하게 재미·티키타카·흐름 전환을 보존하고, 큰 관계 가설을 기각해도 성립하는 작은 티키타카는 남깁니다.",
        "3. 먼 전제, 활동 목적, 반례를 `summary`, `ranges`, `points`, `stories`, `proposals` 본문까지 남깁니다.",
        "4. D1 `ranges`에는 `parent_id`를 넣지 않으며, D2 `ranges`에는 유효한 `parent_id`와 `content`를 반드시 포함합니다.",
        "5. `proposals` 객체 자체에는 `used_evidence_ids` 키를 두지 않고, 연결된 `stories[].used_evidence_ids` 및 `source_spans[].evidence_refs`를 `story_beats`가 인용하도록 맞춥니다.",
        "6. `Point-only`(하이라이트 없는 포인트)와 `Point-free`(포인트 없는 하이라이트) 모두 허용됩니다. 모든 단일 행 문자열(`summary`, `title`, `content`, `story_summary`, `selection_reason`, `reason`)에는 개행을 넣지 마세요.",
        "7. `group_outcomes`에는 반드시 `{\"flow_refs\": [\"broadcast\"], \"terminal_state\": \"represented\", ...}`를 포함하세요.",
        "",
        "## 필수 9개 필드 (+ 선택 `multimodal_decision`)",
        "`base_revision`, `summary`, `summary_evidence_refs`, `ranges`, `points`, `stories`, `proposals`, `group_outcomes`, `unresolved_questions`",
        "",
    ])

    payload_files: list[tuple[str, bytes]] = [
        ("README_WEB_PROMPT.md", prompt_md.encode("utf-8")),
        ("evidence_clusters.json", (json.dumps(evidence_clusters, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
        *transcript_files,
        ("verify_result.py", VERIFY_RESULT_SCRIPT.encode("utf-8")),
    ]

    out = Path(output_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    files_dir = out.parent / f"{out.stem}_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for existing in files_dir.iterdir():
        if existing.is_file():
            existing.unlink()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in payload_files:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
            (files_dir / name).write_bytes(data)

    zip_sha = hashlib.sha256(out.read_bytes()).hexdigest()
    pack_receipt = {
        "schema_version": "loki.web_pack_receipt.v1",
        "pack_sha256": zip_sha,
        "prepared_revision": expected_revision,
        "exported_refs": valid_record_ids,
        "exported_record_ids": [r["record_id"] for r in records if r.get("record_id")],
        "exported_chat_ids": exported_chat_ids,
        "entries": [name for name, _ in payload_files],
    }
    receipt_dir = root / "web-packs"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{zip_sha}.json"
    if not receipt_path.exists():
        receipt_path.write_text(canonical_json(pack_receipt) + "\n", encoding="utf-8")

    return {
        "status": "exported",
        "pack_path": str(out),
        "files_dir": str(files_dir),
        "receipt_path": str(receipt_path),
        "sha256": zip_sha,
        "pack_sha256": zip_sha,
        "bytes": out.stat().st_size,
        "entries": [name for name, _ in payload_files],
        "prepared_revision": expected_revision,
    }


def extract_json_from_draft(raw_text: str) -> dict:
    """Extract the authored JSON object from raw JSON, Markdown code fences, or mixed prose."""
    text = raw_text.lstrip("\ufeff").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    for block in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE):
        inner = block.group(1).strip()
        try:
            obj = json.loads(inner)
            if isinstance(obj, dict):
                candidates.append(obj)
                continue
        except ValueError:
            pass
        for idx, ch in enumerate(inner):
            if ch == "{":
                try:
                    obj, _ = decoder.raw_decode(inner, idx)
                    if isinstance(obj, dict):
                        candidates.append(obj)
                        break
                except ValueError:
                    continue

    if not candidates:
        for idx, ch in enumerate(text):
            if ch == "{":
                try:
                    obj, _ = decoder.raw_decode(text, idx)
                    if isinstance(obj, dict):
                        candidates.append(obj)
                except ValueError:
                    continue

    for obj in candidates:
        if "base_revision" in obj and "ranges" in obj:
            return obj
    if candidates:
        return candidates[0]
    raise ValueError("loki_import_draft_json_not_found")


def _complete_read_with_continuations(current_mod, root: Path, action: dict, *, expected_revision: str, max_pages: int = 24) -> None:
    page = current_mod.read(root, action, expected_revision=expected_revision)
    pages_seen = 0
    while isinstance(page, dict) and page.get("next_cursor") and pages_seen < max_pages:
        read_id = page.get("read_id")
        if not read_id:
            break
        cont = current_mod.continuation(root, read_id=read_id, expected_revision=expected_revision)
        next_action = cont.get("next_original_action") if isinstance(cont, dict) else None
        if not isinstance(next_action, dict):
            break
        page = current_mod.read(root, next_action, expected_revision=expected_revision)
        pages_seen += 1


def import_web_draft(job_root: Path, draft_path: Path, *, expected_revision: str, validate_only: bool = False) -> dict:
    """Parse a Web AI JSON/Markdown draft, complete EW original reads for exported refs, and submit."""
    from pipeline import editorial_current_conversation as current

    root, prepared, _ = current._load(job_root)
    if expected_revision != prepared["prepared_revision"]:
        raise ValueError("current_conversation_prepare_conflict")

    author_mode = (prepared.get("identity") or {}).get("summary_author_mode")
    if author_mode not in {"current_conversation_chatgpt", "current_conversation_claude_web", "current_conversation_gemini_web"}:
        raise ValueError("loki_import_draft_requires_web_host_mode")

    pack_dir = root / "web-packs"
    receipt_files = sorted(pack_dir.glob("*.json")) if pack_dir.is_dir() else []
    if not receipt_files:
        raise ValueError("loki_import_draft_requires_exported_web_pack")

    exported_record_ids: set[str] = set()
    exported_chat_ids: set[str] = set()
    latest_pack_sha = receipt_files[-1].stem
    for rpath in receipt_files:
        rec = json.loads(rpath.read_text(encoding="utf-8"))
        latest_pack_sha = str(rec.get("pack_sha256") or latest_pack_sha)
        exported_record_ids.update(str(x) for x in (rec.get("exported_record_ids") or []))
        exported_chat_ids.update(str(x) for x in (rec.get("exported_chat_ids") or []))

    raw = Path(draft_path).read_text(encoding="utf-8-sig")
    payload = extract_json_from_draft(raw)

    records = prepared["source"].get("records") or []
    ref_to_record_id = {f"e{r['ordinal'] + 1}": r["record_id"] for r in records if r.get("record_id")}
    cited_map = current._citation_paths(payload)
    needed_record_ids: list[str] = []
    for ref in sorted(cited_map):
        if ref in ref_to_record_id:
            rid = ref_to_record_id[ref]
            if exported_record_ids and rid not in exported_record_ids:
                raise ValueError("loki_import_draft_cited_unexported_ref:" + ref)
            needed_record_ids.append(rid)
        elif ref.startswith("chat-original:"):
            if exported_chat_ids and ref not in exported_chat_ids:
                raise ValueError("loki_import_draft_cited_unexported_ref:" + ref)
            chat_rid = ref[len("chat-original:"):]
            if chat_rid not in exported_record_ids and f"chat-original:{chat_rid}" not in exported_chat_ids:
                raise ValueError("loki_import_draft_cited_unexported_ref:" + ref)
            needed_record_ids.append(chat_rid)

    receipts, complete_ids, _ = current._reads(root, prepared)
    unread_ids = [rid for rid in dict.fromkeys(needed_record_ids) if rid not in complete_ids]
    if not receipts and not unread_ids and records:
        first_id = records[0].get("record_id")
        if first_id:
            unread_ids = [first_id]

    for start in range(0, len(unread_ids), 24):
        batch = unread_ids[start:start + 24]
        action = {
            "schema_version": "ew.tool.v1",
            "source_id": prepared["source"]["asset"]["source_id"],
            "world_revision": prepared["world_revision"],
            "question_id": f"web-pack-import:{latest_pack_sha[:12]}",
            "action": "read_evidence",
            "args": {"record_ids": batch, "context_cues": 0, "max_bytes": 16000},
        }
        _complete_read_with_continuations(current, root, action, expected_revision=expected_revision)

    mm_review = prepared.get("multimodal_final_review") or {}
    mm_ready = (
        mm_review.get("policy") == "consume_or_defer_v1"
        and (mm_review.get("initial_context") or {}).get("status") == "ready"
    )
    has_nav = bool(prepared.get("multimodal_navigation") or ((root / "navigation-receipts").is_dir() and any((root / "navigation-receipts").iterdir())))
    if mm_ready and not has_nav and payload.get("multimodal_decision") is None:
        payload["multimodal_decision"] = {"decision": "defer", "reason": "web_pack_offline_import"}

    normalized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        tmp.write(normalized)
        tmp_path = Path(tmp.name)
    try:
        return current.submit(root, tmp_path, expected_revision=expected_revision, validate_only=validate_only)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _extract_creator_items(report_data: dict) -> tuple[str, list[dict]]:
    vod_title = str(report_data.get("title") or report_data.get("video_no") or "Loki VOD")
    packet = report_data.get("editorial_review_packet") or report_data.get("full_author_projection") or {}
    bmap = packet.get("broadcast_map") or packet.get("outline") or report_data.get("structured_outline") or {}
    items: list[dict] = []

    def _desc(row: dict) -> str:
        for key in ("content", "story_summary", "selection_reason", "reason", "summary"):
            val = _single_line(row.get(key))
            if val:
                return val
        return ""

    for row in bmap.get("d1") or []:
        if not isinstance(row, dict):
            continue
        start = _sec_from_text(row.get("start_sec") if "start_sec" in row else row.get("start"))
        end = _sec_from_text(row.get("end_sec") if "end_sec" in row else row.get("end"))
        items.append(dict(
            kind="D1",
            id=str(row.get("id") or ""),
            start_sec=start,
            end_sec=max(start + 1.0, end),
            title=_single_line(row.get("title") or row.get("summary")),
            description=_desc(row),
        ))
    for row in bmap.get("d2") or []:
        if not isinstance(row, dict):
            continue
        start = _sec_from_text(row.get("start_sec") if "start_sec" in row else row.get("start"))
        end = _sec_from_text(row.get("end_sec") if "end_sec" in row else row.get("end"))
        items.append(dict(
            kind="D2",
            id=str(row.get("id") or ""),
            parent_id=str(row.get("parent_id") or ""),
            start_sec=start,
            end_sec=max(start + 1.0, end),
            title=_single_line(row.get("title") or row.get("summary")),
            description=_desc(row),
        ))
    for row in bmap.get("points") or []:
        if not isinstance(row, dict):
            continue
        ts = _sec_from_text(row.get("timestamp") if "timestamp" in row else row.get("start_sec"))
        items.append(dict(
            kind="Point",
            id=str(row.get("id") or ""),
            d2_ref=str(row.get("d2_ref") or ""),
            start_sec=ts,
            end_sec=ts,
            title=_single_line(row.get("title")),
            description=_desc(row),
        ))
    for row in packet.get("editorial_highlights") or report_data.get("highlights") or []:
        if not isinstance(row, dict):
            continue
        raw_spans = [s for s in (row.get("source_spans") or []) if isinstance(s, dict)]
        spans: list[tuple[float, float]] = []
        for sp in raw_spans:
            sp_s = _sec_from_text(sp.get("start_sec"))
            sp_e = max(sp_s + 1.0, _sec_from_text(sp.get("end_sec")))
            if spans and sp_s < spans[-1][1]:
                sp_s = spans[-1][1]
            if sp_e > sp_s:
                spans.append((sp_s, sp_e))
        if spans:
            h_start, h_end = spans[0][0], max(spans[0][0] + 1.0, spans[-1][1])
        else:
            h_start = _sec_from_text(row.get("start_sec"))
            h_end = max(h_start + 1.0, _sec_from_text(row.get("end_sec")))
            spans = [(h_start, h_end)]
        items.append(dict(
            kind="Highlight",
            start_sec=h_start,
            end_sec=h_end,
            spans=spans,
            title=_single_line(row.get("title")),
            description=_desc(row),
        ))

    order = {"D1": 0, "D2": 1, "Point": 2, "Highlight": 3}
    items = [item for item in items if item["title"]]
    items.sort(key=lambda m: (m["start_sec"], order.get(m["kind"], 9)))
    return vod_title, items


def _split_comment_chunks(text: str, *, max_chars: int = 5000) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    buf: list[str] = []
    cur_len = 0
    for line in text.splitlines(keepends=True):
        if cur_len + len(line) > max_chars and buf:
            chunks.append("".join(buf).rstrip() + "\n")
            buf = []
            cur_len = 0
        buf.append(line)
        cur_len += len(line)
    if buf:
        chunks.append("".join(buf).rstrip() + "\n")
    return chunks


def _render_ttaempad_comment(items: list[dict]) -> tuple[str, list[str]]:
    d1s = [m for m in items if m["kind"] == "D1"]
    d2s = [m for m in items if m["kind"] == "D2"]
    points = [m for m in items if m["kind"] == "Point"]
    highlights = [m for m in items if m["kind"] == "Highlight"]

    if not d1s:
        end_all = max([m["end_sec"] for m in items] + [60.0])
        d1s = [dict(kind="D1", id="d1_auto", start_sec=0.0, end_sec=end_all, title="전체 방송", description="")]

    def _find_parent_d1(item: dict) -> dict:
        for d1 in d1s:
            if d1["start_sec"] <= item["start_sec"] < d1["end_sec"]:
                return d1
        prior = [d1 for d1 in d1s if d1["start_sec"] <= item["start_sec"]]
        return prior[-1] if prior else d1s[0]

    def _find_parent_d2(item: dict, children_d2: list[dict]) -> dict | None:
        for d2 in children_d2:
            if d2["id"] and item.get("d2_ref") == d2["id"] and d2["start_sec"] <= item["start_sec"] <= d2["end_sec"]:
                return d2
        for d2 in children_d2:
            if d2["start_sec"] <= item["start_sec"] < d2["end_sec"]:
                return d2
        return None

    lines: list[str] = []
    for d1 in d1s:
        d1_s = int(round(d1["start_sec"]))
        d1_e = max(d1_s + 1, int(round(d1["end_sec"])))
        lines.append(f"# {_fmt_hms(d1_s)} ~ {_fmt_hms(d1_e)} {d1['title']}")
        if d1.get("description"):
            lines.append(f"설명: {d1['description'][:240]}")

        child_d2s = [d2 for d2 in d2s if _find_parent_d1(d2) is d1]
        d1_items = [m for m in points + highlights if _find_parent_d1(m) is d1]

        direct_items = [m for m in d1_items if _find_parent_d2(m, child_d2s) is None]
        for item in sorted(direct_items, key=lambda x: x["start_sec"]):
            _append_ttaempad_entry(lines, item, d1_s, d1_e)

        for d2 in child_d2s:
            d2_s = max(d1_s, int(round(d2["start_sec"])))
            d2_e = min(d1_e, max(d2_s + 1, int(round(d2["end_sec"]))))
            if d2_e <= d2_s:
                d2_e = d2_s + 1
            lines.append(f"## {_fmt_hms(d2_s)} ~ {_fmt_hms(d2_e)} {d2['title']}")
            if d2.get("description"):
                lines.append(f"설명: {d2['description'][:240]}")
            nested = [m for m in d1_items if _find_parent_d2(m, child_d2s) is d2]
            for item in sorted(nested, key=lambda x: x["start_sec"]):
                _append_ttaempad_entry(lines, item, d2_s, d2_e)
        lines.append("")

    full = "\n".join(lines).rstrip() + "\n"
    return full, _split_comment_chunks(full, max_chars=5000)


def _append_ttaempad_entry(lines: list[str], item: dict, cap_start: int, cap_end: int) -> None:
    if item["kind"] == "Point":
        ts = max(cap_start, min(cap_end - 1, int(round(item["start_sec"])))) if cap_end > cap_start else int(round(item["start_sec"]))
        lines.append(f"- {_fmt_hms(ts)} [P] {item['title']}")
        if item.get("description"):
            lines.append(f"설명: {item['description'][:240]}")
    else:
        raw_spans = item.get("spans") or [(item["start_sec"], item["end_sec"])]
        norm_spans: list[tuple[int, int]] = []
        for sp_s, sp_e in raw_spans:
            a = max(cap_start, int(round(sp_s)))
            b = min(cap_end, max(a + 1, int(round(sp_e))))
            if norm_spans and a < norm_spans[-1][1]:
                a = norm_spans[-1][1]
            if b > a:
                norm_spans.append((a, b))
        if not norm_spans:
            a = max(cap_start, min(cap_end - 1, int(round(item["start_sec"]))))
            b = min(cap_end, max(a + 1, int(round(item["end_sec"]))))
            norm_spans = [(a, max(a + 1, b))]
        h_s, h_e = norm_spans[0][0], norm_spans[-1][1]
        lines.append(f"- {_fmt_hms(h_s)} ~ {_fmt_hms(h_e)} ★ [H] {item['title']}")
        if item.get("description"):
            lines.append(f"설명: {item['description'][:240]}")
        if norm_spans:
            span_str = "; ".join(f"{_fmt_hms(a)} ~ {_fmt_hms(b)}" for a, b in norm_spans)
            lines.append(f"원본 구간: {span_str}")


def _render_viewer_comment(title: str, items: list[dict]) -> tuple[str, list[str]]:
    lines = [f"[{title}] 타임라인", ""]
    chapters = [m for m in items if m["kind"] in {"D1", "D2"}]
    for ch in chapters:
        indent = "  " if ch["kind"] == "D2" else ""
        lines.append(f"{indent}{_fmt_hms(ch['start_sec'])} {ch['title']}")
    highlights = [m for m in items if m["kind"] == "Highlight"]
    if highlights:
        lines.extend(["", "[하이라이트]"])
        for hl in highlights:
            lines.append(f"★ {_fmt_hms(hl['start_sec'])} ~ {_fmt_hms(hl['end_sec'])} {hl['title']}")
    full = "\n".join(lines).rstrip() + "\n"
    return full, _split_comment_chunks(full, max_chars=5000)


def _expand_nle_rows(items: list[dict]) -> list[dict]:
    """Expand multi-span highlights into separate sequential cut entries for NLE exports."""
    rows: list[dict] = []
    for item in items:
        if item["kind"] == "Highlight" and item.get("spans"):
            spans = item["spans"]
            total = len(spans)
            for idx, (sp_s, sp_e) in enumerate(spans, start=1):
                suffix = f" ({idx}/{total})" if total > 1 else ""
                rows.append({**item, "start_sec": sp_s, "end_sec": sp_e, "title": item["title"] + suffix})
        else:
            rows.append(item)
    return rows


def export_report(report_path: Path, *, fmt: str, output_path: Path | None = None, style: str = "ttaempad") -> dict:
    """Export finished Loki report into ttaempad/viewer comments, Premiere/Resolve CSV, or FCPXML."""
    data = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
    title, items = _extract_creator_items(data)
    parts: list[str] | None = None
    if fmt == "comment":
        if style == "viewer":
            content, parts = _render_viewer_comment(title, items)
        elif style == "ttaempad":
            content, parts = _render_ttaempad_comment(items)
        else:
            raise ValueError("loki_unsupported_comment_style:" + str(style))
        marker_count = len(items)
    elif fmt == "markers-csv":
        nle_rows = _expand_nle_rows(items)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"])
        for m in nle_rows:
            dur = max(0.0, m["end_sec"] - m["start_sec"])
            mtype = "Segmentation" if m["kind"] == "Highlight" else "Comment"
            writer.writerow([
                m["title"],
                m["description"][:300],
                _fmt_tc(m["start_sec"], 30),
                _fmt_tc(m["end_sec"], 30),
                _fmt_tc(dur, 30),
                mtype,
            ])
        content = buf.getvalue()
        marker_count = len(nle_rows)
    elif fmt == "fcpxml":
        nle_rows = _expand_nle_rows(items)
        root = ET.Element("fcpxml", version="1.9")
        resources = ET.SubElement(root, "resources")
        ET.SubElement(resources, "format", id="r1", frameDuration="1/30s", width="1920", height="1080")
        ET.SubElement(resources, "asset", id="a1", name=title, start="0s", hasVideo="1", hasAudio="1")
        lib = ET.SubElement(root, "library")
        event = ET.SubElement(lib, "event", name="CVA-Loki Export")
        proj = ET.SubElement(event, "project", name=title)
        seq = ET.SubElement(proj, "sequence", format="r1")
        spine = ET.SubElement(seq, "spine")
        hl_rows = [m for m in nle_rows if m["kind"] == "Highlight"]
        if hl_rows:
            timeline_cursor = 0
            for m in hl_rows:
                clip_start = int(round(m["start_sec"]))
                clip_dur = max(1, int(round(m["end_sec"] - m["start_sec"])))
                clip = ET.SubElement(
                    spine, "asset-clip", name=m["title"], ref="a1",
                    offset=f"{timeline_cursor}s", start=f"{clip_start}s", duration=f"{clip_dur}s",
                )
                ET.SubElement(clip, "marker", start=f"{clip_start}s", duration=f"{clip_dur}s",
                              value=f"[Highlight] {m['title']}", note=m["description"][:300])
                timeline_cursor += clip_dur
            marker_anchor = spine.find("asset-clip")
            for m in nle_rows:
                if m["kind"] != "Highlight" and marker_anchor is not None:
                    dur = max(1, int(round(m["end_sec"] - m["start_sec"])))
                    ET.SubElement(marker_anchor, "marker", start=f"{int(round(m['start_sec']))}s",
                                  duration=f"{dur}s", value=f"[{m['kind']}] {m['title']}",
                                  note=m["description"][:300])
        else:
            max_end = max([int(round(m["end_sec"])) for m in nle_rows] + [60])
            gap = ET.SubElement(spine, "gap", name=title, offset="0s", duration=f"{max_end}s")
            for m in nle_rows:
                dur = max(1, int(round(m["end_sec"] - m["start_sec"])))
                ET.SubElement(gap, "marker", start=f"{int(round(m['start_sec']))}s",
                              duration=f"{dur}s", value=f"[{m['kind']}] {m['title']}",
                              note=m["description"][:300])
        content = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
        marker_count = len(nle_rows)
    else:
        raise ValueError("loki_unsupported_export_format:" + str(fmt))

    result = {"status": "exported", "format": fmt, "marker_count": marker_count}
    if fmt == "comment":
        result["style"] = style
        result["parts"] = parts
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        result["output_path"] = str(out)
        result["sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
    else:
        result["content"] = content
    return result
