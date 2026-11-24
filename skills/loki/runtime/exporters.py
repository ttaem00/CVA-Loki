"""Pure-stdlib Web AI pack exporter, draft importer, and creator timeline exporters."""
from __future__ import annotations

import csv
import hashlib
import io
import json
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

def validate(payload):
    errors = []
    if not isinstance(payload, dict):
        return ["response_must_be_json_object"]
    missing = [k for k in REQUIRED if k not in payload]
    if missing:
        errors.append("missing_fields:" + ",".join(missing))
    for key in ("summary_evidence_refs", "ranges", "points", "stories", "proposals", "group_outcomes", "unresolved_questions"):
        if key in payload and not isinstance(payload[key], list):
            errors.append(f"{key}_must_be_list")
    if isinstance(payload.get("summary"), str) and not payload["summary"].strip():
        errors.append("summary_empty")
    if isinstance(payload.get("ranges"), list) and not payload["ranges"]:
        errors.append("ranges_empty")
    for idx, prop in enumerate(payload.get("proposals") or []):
        if not isinstance(prop, dict):
            continue
        used = set(prop.get("used_evidence_ids") or [])
        for b_idx, beat in enumerate(prop.get("story_beats") or []):
            if not isinstance(beat, dict):
                continue
            for ref in beat.get("evidence_refs") or []:
                if used and ref not in used:
                    errors.append(f"proposal[{idx}].story_beats[{b_idx}].ref_not_in_used_evidence_ids:{ref}")
    return errors

if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    issues = validate(data)
    print(json.dumps({"status": "PASS" if not issues else "FAIL", "errors": issues}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not issues else 2)
'''


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


def export_web_pack(job_root: Path, output_zip: Path, *, expected_revision: str) -> dict:
    """Create a self-contained Web AI ZIP preserving EvidenceWorld clusters and quick contract."""
    from pipeline import editorial_current_conversation as current

    root, prepared, _ = current._load(job_root)
    if expected_revision != prepared["prepared_revision"]:
        raise ValueError("current_conversation_prepare_conflict")
    author_input = json.loads((root / "author-input.json").read_text(encoding="utf-8-sig"))
    vod = prepared["vod_info"]
    records = prepared["source"].get("records") or []
    packet = prepared.get("packet") or {}
    nav_reads = packet.get("navigation_source_reads") or {}
    signal_orientation = nav_reads.get("signal_orientation") or {}
    clusters_meta = (prepared.get("navigation_features") or {}).get("clusters")
    cluster_summary = None
    if clusters_meta and Path(clusters_meta["path"]).is_file():
        try:
            raw_clusters = json.loads(Path(clusters_meta["path"]).read_text(encoding="utf-8"))
            cluster_summary = {
                "profile": raw_clusters.get("profile"),
                "grouping_complete": raw_clusters.get("grouping_complete"),
                "group_count": len(raw_clusters.get("groups") or []),
                "groups_preview": (raw_clusters.get("groups") or [])[:20],
            }
        except (OSError, ValueError):
            cluster_summary = None

    quiet_segments = [
        {"ordinal": r["ordinal"], "ref": f"e{r['ordinal'] + 1}", "start_sec": r.get("start_sec"), "text": r.get("raw_text", "")[:140]}
        for r in records
        if r.get("raw_text")
    ][::max(1, len(records) // 25)]

    evidence_clusters = {
        "schema_version": "loki.web_pack_evidence.v1",
        "prepared_revision": expected_revision,
        "base_revision": prepared["identity"]["base_revision"],
        "execution_mode": prepared.get("execution_mode"),
        "vod_info": vod,
        "quick_contract": author_input.get("quick_contract"),
        "signal_orientation": signal_orientation,
        "support_proposals": packet.get("support_proposals") or [],
        "document_vector_clusters": cluster_summary,
        "local_chunk_hints": author_input.get("local_chunk_hints"),
        "low_reaction_coverage_samples": quiet_segments,
        "authority": "web_ai_orientation_pack; local import verifies original reads before final report",
    }

    transcript_lines = [
        f"# {vod.get('title', 'VOD')} ({vod.get('video_no', '')})",
        "",
        "원문 STT 인용 규칙: `e<N>` ID를 그대로 사용하고 추측으로 장면을 만들지 마세요.",
        "",
    ]
    for row in records:
        ref = f"e{row['ordinal'] + 1}"
        ts = _fmt_hms(row.get("start_sec", 0))
        text = str(row.get("raw_text") or "").replace("\n", " ").strip()
        transcript_lines.append(f"- [{ts}] `{ref}`: {text}")
    transcript_md = "\n".join(transcript_lines) + "\n"

    prompt_md = "\n".join([
        "# CVA-Loki Web AI 작성 안내",
        "",
        f"- 대상 VOD: `{vod.get('video_no', '')}` — {vod.get('title', '')}",
        f"- `base_revision`: `{prepared['identity']['base_revision']}`",
        f"- `prepared_revision`: `{expected_revision}`",
        "",
        "## 핵심 원칙 (Loki 3.0.9)",
        "1. `evidence_clusters.json`의 피크 신호·군집·저반응 구간과 `transcript_condensed.md`의 `e<N>` 원문을 함께 확인합니다.",
        "2. 길이와 무관하게 재미·티키타카·흐름 전환을 보존하고, 큰 관계 가설을 기각해도 성립하는 작은 티키타카는 남깁니다.",
        "3. 먼 전제, 활동 목적, 반례를 본문(`summary_lines`, `ranges`, `points`, `stories`, `proposals`)까지 끝까지 반영합니다.",
        "4. `story_beats`는 해당 `proposals`의 `source_spans`와 `used_evidence_ids` 안에 포함되어야 합니다.",
        "5. `Point-only`(하이라이트 없는 포인트)와 `Point-free`(포인트 없는 하이라이트) 모두 허용됩니다.",
        "6. Code Interpreter가 있다면 `python verify_result.py response.json`으로 9개 필수 필드를 먼저 검증하세요.",
        "7. 최종 응답은 단일 JSON 객체(또는 ````json` 코드블록)로 출력하세요.",
        "",
        "## 필수 9개 필드",
        "`base_revision`, `summary`, `summary_evidence_refs`, `ranges`, `points`, `stories`, `proposals`, `group_outcomes`, `unresolved_questions`",
        "",
    ])

    out = Path(output_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in (
            ("README_WEB_PROMPT.md", prompt_md.encode("utf-8")),
            ("evidence_clusters.json", (json.dumps(evidence_clusters, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
            ("transcript_condensed.md", transcript_md.encode("utf-8")),
            ("verify_result.py", VERIFY_RESULT_SCRIPT.encode("utf-8")),
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return {
        "status": "exported",
        "pack_path": str(out),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "entries": ["README_WEB_PROMPT.md", "evidence_clusters.json", "transcript_condensed.md", "verify_result.py"],
        "prepared_revision": expected_revision,
    }


def extract_json_from_draft(raw_text: str) -> dict:
    text = raw_text.lstrip("\ufeff").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("loki_import_draft_json_not_found")


def import_web_draft(job_root: Path, draft_path: Path, *, expected_revision: str, validate_only: bool = False) -> dict:
    """Parse a Web AI JSON/Markdown draft and route through canonical submit validation."""
    from pipeline import editorial_current_conversation as current

    raw = Path(draft_path).read_text(encoding="utf-8-sig")
    payload = extract_json_from_draft(raw)
    normalized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        tmp.write(normalized)
        tmp_path = Path(tmp.name)
    try:
        return current.submit(job_root, tmp_path, expected_revision=expected_revision, validate_only=validate_only)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _extract_markers(report_data: dict) -> tuple[str, list[dict]]:
    vod_title = str(report_data.get("title") or report_data.get("video_no") or "Loki VOD")
    packet = report_data.get("editorial_review_packet") or report_data.get("full_author_projection") or {}
    bmap = packet.get("broadcast_map") or packet.get("outline") or report_data.get("structured_outline") or {}
    markers = []
    for row in bmap.get("d1") or []:
        if not isinstance(row, dict):
            continue
        start = _sec_from_text(row.get("start_sec") if "start_sec" in row else row.get("start"))
        end = _sec_from_text(row.get("end_sec") if "end_sec" in row else row.get("end"))
        markers.append(dict(kind="D1", start_sec=start, end_sec=max(start, end),
                            title=str(row.get("title") or row.get("summary") or "").strip(),
                            description=str(row.get("summary") or row.get("content") or "").strip()))
    for row in bmap.get("d2") or []:
        if not isinstance(row, dict):
            continue
        start = _sec_from_text(row.get("start_sec") if "start_sec" in row else row.get("start"))
        end = _sec_from_text(row.get("end_sec") if "end_sec" in row else row.get("end"))
        markers.append(dict(kind="D2", start_sec=start, end_sec=max(start, end),
                            title=str(row.get("title") or row.get("summary") or "").strip(),
                            description=str(row.get("summary") or row.get("content") or "").strip()))
    for row in packet.get("editorial_highlights") or report_data.get("highlights") or []:
        if not isinstance(row, dict):
            continue
        spans = row.get("source_spans") or []
        if spans and isinstance(spans[0], dict):
            start = _sec_from_text(spans[0].get("start_sec"))
            end = _sec_from_text(spans[-1].get("end_sec"))
        else:
            start = _sec_from_text(row.get("start_sec"))
            end = _sec_from_text(row.get("end_sec"))
        markers.append(dict(kind="Highlight", start_sec=start, end_sec=max(start, end),
                            title=str(row.get("title") or "").strip(),
                            description=str(row.get("reason") or row.get("summary") or "").strip()))
    markers = [m for m in markers if m["title"]]
    markers.sort(key=lambda m: (m["start_sec"], 0 if m["kind"] == "D1" else 1 if m["kind"] == "D2" else 2))
    return vod_title, markers


def export_report(report_path: Path, *, fmt: str, output_path: Path | None = None) -> dict:
    """Export finished Loki report metadata/result into comment, Premiere/Resolve CSV, or FCPXML."""
    data = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
    title, markers = _extract_markers(data)
    if fmt == "comment":
        lines = [f"[{title}] 타임라인"]
        for m in markers:
            prefix = "★ " if m["kind"] == "Highlight" else ("  - " if m["kind"] == "D2" else "")
            lines.append(f"{prefix}{_fmt_hms(m['start_sec'])} {m['title']}")
        content = "\n".join(lines) + "\n"
    elif fmt == "markers-csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["Marker Name", "Description", "In", "Out", "Duration", "Marker Type"])
        for m in markers:
            dur = max(0.0, m["end_sec"] - m["start_sec"])
            mtype = "Segmentation" if m["kind"] == "Highlight" else "Comment"
            writer.writerow([m["title"], m["description"][:300], _fmt_tc(m["start_sec"]),
                             _fmt_tc(m["end_sec"]), _fmt_tc(dur), mtype])
        content = buf.getvalue()
    elif fmt == "fcpxml":
        root = ET.Element("fcpxml", version="1.9")
        resources = ET.SubElement(root, "resources")
        ET.SubElement(resources, "format", id="r1", frameDuration="1001/30000s", width="1920", height="1080")
        lib = ET.SubElement(root, "library")
        event = ET.SubElement(lib, "event", name="CVA-Loki Export")
        proj = ET.SubElement(event, "project", name=title)
        seq = ET.SubElement(proj, "sequence", format="r1")
        spine = ET.SubElement(seq, "spine")
        max_end = max([m["end_sec"] for m in markers] + [60.0])
        gap = ET.SubElement(spine, "gap", name=title, offset="0s", duration=f"{int(round(max_end))}s")
        for m in markers:
            dur = max(1, int(round(m["end_sec"] - m["start_sec"])))
            ET.SubElement(gap, "marker", start=f"{int(round(m['start_sec']))}s",
                          duration=f"{dur}s", value=f"[{m['kind']}] {m['title']}",
                          note=m["description"][:300])
        content = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    else:
        raise ValueError("loki_unsupported_export_format:" + str(fmt))

    result = {"status": "exported", "format": fmt, "marker_count": len(markers)}
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        result["output_path"] = str(out)
        result["sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
    else:
        result["content"] = content
    return result
