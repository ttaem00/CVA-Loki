"""Metadata-only collaboration watch registration and report reference projection.

The runtime owns observation state. This adapter never collects media, invokes a
model, rewrites an answer/Markdown, or publishes a site. The main VOD clock stays
the authority; replay overlap is not a same-scene synchronization proof.
"""

from __future__ import annotations

from copy import deepcopy

from datetime import datetime, timezone, timedelta

import html

import json

from pathlib import Path

import re

import sys

from .chatgpt_export.contract.collaboration_periods import MAX_PERIOD_LINKS

STATE_SCHEMA = "cva.collaboration_replays.v1"

START_MARKER = "<!-- collaboration-replay-links:start -->"

END_MARKER = "<!-- collaboration-replay-links:end -->"

MAX_MEMBERS = 32

_VIDEO = re.compile(r"[1-9][0-9]{0,19}\Z")

_CHANNEL = re.compile(r"[a-f0-9]{32}\Z")

_HASH = re.compile(r"[a-f0-9]{64}\Z")

_EMBEDDED = re.compile(r'(<script\s+id="viewerEditorToolsData"\s+type="application/json">)(.*?)(</script>)', re.S)

_STATUSES = {"pending", "live", "open", "waiting_vod", "linked", "no_vod", "complete", "unknown", "identity_ambiguous", "unavailable", "ambiguous", "vod_ambiguous", "retired"}

def _video(value):
    value = str(value)
    if not _VIDEO.fullmatch(value):
        raise ValueError("collaboration_video_invalid")
    return value

def _name(value):
    return isinstance(value, str) and value == value.strip() and 0 < len(value) <= 80 and not any(ord(c) < 32 for c in value)

def _time(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**14

def _status(value):
    return value if isinstance(value, str) and value in _STATUSES else "unknown"

def watch_root(root=None):
    if root is None:
        from .runtime_root import runtime_data_root
        root = runtime_data_root() / "data" / "collaboration_watch"
    return Path(root).resolve()

def _read(path, limit=2 * 1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None

def public_replays(state, *, video_no, duration_sec=None, channel_id=None):
    """Allow-list only. Raw notes, credentials, file paths and revisions stay local."""
    video = _video(video_no)
    if (not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA
            or str(state.get("video_no")) != video or not isinstance(state.get("request_revision"), str)
            or not _HASH.fullmatch(state["request_revision"]) or not isinstance(state.get("members"), list)
            or len(state["members"]) > MAX_MEMBERS or not isinstance(state.get("main", {}), dict)):
        raise ValueError("collaboration_state_identity_invalid")
    main = state.get("main", {})
    if duration_sec is not None and main.get("duration_sec") != duration_sec:
        raise ValueError("collaboration_state_duration_mismatch")
    if channel_id and main.get("channel_id") != channel_id:
        raise ValueError("collaboration_state_channel_mismatch")
    result = {"schema_version": STATE_SCHEMA, "video_no": video, "members": []}
    for row in state.get("members", []):
        if not isinstance(row, dict) or not isinstance(row.get("broadcasts"), list) or len(row["broadcasts"]) > MAX_PERIOD_LINKS:
            raise ValueError("collaboration_state_member_invalid")
        name, cid = row.get("name"), row.get("channel_id")
        if not _name(name) or cid is not None and (not isinstance(cid, str) or not _CHANNEL.fullmatch(cid)):
            raise ValueError("collaboration_state_member_invalid")
        member = {"name": name, "status": _status(row.get("status")), "broadcasts": []}
        if cid:
            member["channel_id"] = cid
        for record in row.get("broadcasts", []):
            if not isinstance(record, dict):
                raise ValueError("collaboration_state_broadcast_invalid")
            start, end = record.get("start_at_ms"), record.get("end_at_ms")
            status = _status(record.get("status"))


            end_only = (start is None and _time(end) and
                        record.get("end_basis") == "observed_not_live" and
                        status in {"waiting_vod", "no_vod"})
            if not end_only and (not _time(start) or end is not None and (not _time(end) or end < start)):
                continue
            item = {"start_at_ms": start, "end_at_ms": end, "end_basis": record.get("end_basis") if record.get("end_basis") in {"provider_close", "observed_not_live", "vod_duration"} else None,
                    "status": status, "vod_no": None, "alignment": "broadcast_start_unverified"}
            number = str(record.get("vod_no", ""))
            if status == "linked" and _VIDEO.fullmatch(number) and number != video and cid:
                item["vod_no"] = number
            member["broadcasts"].append(item)
        result["members"].append(member)
    return result

def load_replays(video_no, *, duration_sec=None, channel_id=None, root=None):
    video = _video(video_no)
    folder = watch_root(root)
    request = _read(folder / f"{video}.json")
    state = _read(folder / f"{video}.state.json")
    if not request or not state or state.get("request_revision") != request.get("revision"):
        return None
    try:
        return public_replays(state, video_no=video, duration_sec=duration_sec, channel_id=channel_id)
    except ValueError:
        return None

def normalize_public_replays(value, *, video_no):
    """Recheck imported public metadata without trusting private state fields."""
    if (not isinstance(value, dict) or set(value) != {"schema_version", "video_no", "members"}
            or value.get("schema_version") != STATE_SCHEMA or str(value.get("video_no")) != _video(video_no)
            or not isinstance(value.get("members"), list) or len(value["members"]) > MAX_MEMBERS):
        raise ValueError("collaboration_public_replays_invalid")
    private = deepcopy(value)
    private.update(request_revision="0" * 64, main={})
    seen = set()
    for member in private["members"]:
        if not isinstance(member, dict) or set(member) - {"name", "status", "channel_id", "broadcasts"}:
            raise ValueError("collaboration_public_member_invalid")
        if not _name(member.get("name")) or member["name"] in seen or not isinstance(member.get("status"), str) or member["status"] not in _STATUSES:
            raise ValueError("collaboration_public_member_invalid")
        seen.add(member.get("name"))
        if not isinstance(member.get("broadcasts"), list) or len(member["broadcasts"]) > MAX_PERIOD_LINKS:
            raise ValueError("collaboration_public_broadcast_invalid")
        for record in member["broadcasts"]:
            if not isinstance(record, dict) or set(record) != {"start_at_ms", "end_at_ms", "end_basis", "status", "vod_no", "alignment"}:
                raise ValueError("collaboration_public_broadcast_invalid")
            if not isinstance(record.get("status"), str) or record["status"] not in _STATUSES or record.get("alignment") != "broadcast_start_unverified":
                raise ValueError("collaboration_public_broadcast_invalid")
            number = record.get("vod_no")
            if number is not None and (not isinstance(number, str) or not _VIDEO.fullmatch(number)
                    or record["status"] != "linked" or not member.get("channel_id")):
                raise ValueError("collaboration_public_vod_invalid")
    normalized = public_replays(private, video_no=video_no)
    if normalized != value:
        raise ValueError("collaboration_public_broadcast_invalid")
    return normalized

def attach_member_vods(periods, replays, *, main_start_ms, declared_periods=None):
    """Append verified split links in explicit periods; preserve declared choices.

    The normal renderer starts from declared periods. A later reference refresh
    supplies the original metadata periods so an earlier automatic link does
    not hide a second VOD that appeared later. Missing authority stays protected.
    """
    output = deepcopy(periods)
    if not _time(main_start_ms) or not isinstance(replays, dict):
        return output
    members = {row["name"]: row for row in replays.get("members", [])}
    declared_by_id = {row.get("id"): row for row in declared_periods or []}
    for period in output:
        declared = declared_by_id.get(period.get("id"))
        same_interval = declared is not None and all(
            declared.get(key) == period.get(key) for key in ("start_sec", "end_sec", "members"))
        protected = declared if same_interval else period
        existing = {row["member_name"] for row in protected.get("member_vods", [])}
        links = deepcopy(period.get("member_vods", []))
        used_numbers = {row["vod_no"] for row in links}
        for name in period.get("members", []):
            if name in existing or name not in members:
                continue
            row = members[name]
            start = main_start_ms + period["start_sec"] * 1000
            end = main_start_ms + period["end_sec"] * 1000
            records = [r for r in row["broadcasts"] if r["vod_no"] and _time(r["start_at_ms"]) and r["start_at_ms"] < end
                       and r["end_at_ms"] is not None and r["end_at_ms"] > start]


            for record in sorted(records, key=lambda item: (item["start_at_ms"], item["vod_no"])):
                if record["vod_no"] in used_numbers:
                    continue
                if len(links) >= MAX_PERIOD_LINKS:
                    raise ValueError("collaboration_member_vods_invalid")
                links.append({"member_name": name, "vod_no": record["vod_no"], "channel_id": row["channel_id"]})
                used_numbers.add(record["vod_no"])
        if links:
            period["member_vods"] = links
    return output

def render_replay_links(replays):
    labels = {"live": "방송 중", "open": "방송 중", "waiting_vod": "다시보기 대기", "no_vod": "다시보기 없음", "linked": "다시보기", "identity_ambiguous": "채널 확인 필요", "vod_ambiguous": "다시보기 선택 필요", "retired": "명단에서 제외"}
    rows = []
    for member in (replays or {}).get("members", []):
        records = member.get("broadcasts", [])
        if not records:
            rows.append('<span class="collaboration-chip">' + html.escape(member["name"]) + " · " + labels.get(member["status"], "확인 중") + '</span>')
        for record in records:
            title = member["name"] + " · " + labels.get(record["status"], "확인 중")
            stamp = (datetime.fromtimestamp(record["start_at_ms"] / 1000, timezone(timedelta(hours=9))).strftime("%m.%d %H:%M")
                     if record["start_at_ms"] is not None else "시작 미상")
            title += " · " + stamp
            if record.get("end_at_ms"):
                title += "–" + datetime.fromtimestamp(record["end_at_ms"] / 1000, timezone(timedelta(hours=9))).strftime("%H:%M")
                if record.get("end_basis") == "observed_not_live":
                    title += " (종료 확인 시각)"
            escaped = html.escape(title)
            if record.get("vod_no"):
                rows.append(f'<a class="collaboration-chip" href="https://chzzk.naver.com/video/{record["vod_no"]}" target="_blank" rel="noopener noreferrer" title="방송 시작 기준 · 장면 동기화 미검증">{escaped}</a>')
            else:
                rows.append(f'<span class="collaboration-chip">{escaped}</span>')
    content = ('<nav class="collaboration-chips" aria-label="합방 다시보기"><span class="collaboration-chips-label">합방 영상</span>' + ''.join(rows) + '</nav>') if rows else ""
    return START_MARKER + content + END_MARKER

def project_report_references(output_dir, video_no, *, root=None):
    """Refresh the reference lane through the existing report family transaction.

    Only reports created with our explicit lane markers and exact embedded VOD
    identity qualify. Summary answers and Markdown are preserved; artifact
    hashes/lineage are committed together. A competing save owns the same lock.
    Normal rerender/save reads the same state through the render adapter.
    """
    video = _video(video_no)
    output = Path(output_dir).resolve()
    from .report_artifact_family import _report_family_lock
    with _report_family_lock(output, video_no=video):
        return _project_locked_report_references(output, video, root=root)

def _project_locked_report_references(output, video, *, root):
    from .report_artifact_family import (ReportArtifactFamilyError,
        validate_report_artifact_family, next_generation_lineage, commit_report_artifact_family)
    changed = 0
    for meta_path in sorted(output.glob(f"*{video}*_metadata.json"))[:16]:
        meta = _read(meta_path, 12 * 1024 * 1024)
        if not meta or str(meta.get("video_no")) != video:
            continue
        meta_before = meta_path.read_bytes()
        if json.loads(meta_before) != meta:
            continue
        replays = load_replays(video, duration_sec=meta.get("duration"), channel_id=meta.get("channel_id"), root=root)
        if not replays:
            continue
        page = meta_path.with_name(meta_path.name.removesuffix("_metadata.json") + ".html")
        markdown = page.with_suffix(".md")
        if page.is_symlink() or not page.is_file() or page.stat().st_size > 32 * 1024 * 1024:
            continue
        if markdown.is_symlink() or not markdown.is_file() or markdown.stat().st_size > 16 * 1024 * 1024:
            continue
        try:
            family = validate_report_artifact_family(markdown, page, meta_path)
        except (OSError, ValueError, ReportArtifactFamilyError):

            continue
        before = page.read_bytes()
        source = before.decode("utf-8")
        match = _EMBEDDED.search(source)
        if source.count(START_MARKER) != 1 or source.count(END_MARKER) != 1 or not match:
            continue
        try:
            payload = json.loads(match.group(2))
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("video_no")) != video or payload.get("duration_sec") != meta.get("duration"):
            continue
        payload["collaboration_replays"] = replays
        request = _read(watch_root(root) / f"{video}.json")
        if payload.get("collaboration_periods"):
            from .chatgpt_export.contract.collaboration_periods import validate_collaboration_periods
            validate_collaboration_periods(payload["collaboration_periods"], meta["duration"], require_evidence_refs=False)
            payload["collaboration_periods"] = attach_member_vods(payload["collaboration_periods"], replays,
                main_start_ms=request["main"]["broadcast_start_ms"], declared_periods=meta.get("collaboration_periods"))
            validate_collaboration_periods(payload["collaboration_periods"], meta["duration"], require_evidence_refs=False)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
        rendered = source[:match.start(2)] + encoded + source[match.end(2):]
        start = rendered.index(START_MARKER)
        end = rendered.index(END_MARKER, start) + len(END_MARKER)
        rendered = rendered[:start] + render_replay_links(replays) + rendered[end:]
        after = rendered.encode()
        if after == before:
            continue
        if (page.read_bytes() != before or meta_path.read_bytes() != meta_before
                or load_replays(video, duration_sec=meta.get("duration"), channel_id=meta.get("channel_id"), root=root) != replays):
            continue
        alias = meta["report_artifact_family"]["artifacts"].get("candidate_alias", {}).get("name")
        meta["collaboration_replays"] = replays
        sequence, parent = next_generation_lineage(output, video_no=video)
        commit_report_artifact_family(markdown_path=markdown, html_path=page, metadata_path=meta_path,
            candidate_alias_path=meta_path.with_name(alias) if alias else None,
            markdown_text=markdown.read_text(encoding="utf-8"), html_text=rendered, metadata=meta,
            generation_sequence=sequence, parent_generation_id=parent,
            authority_revision_id=family["authority_revision_id"])
        changed += 1
    return {"status": "projected", "video_no": video, "updated_reports": changed}

def main():
    request = json.loads(sys.stdin.read(4097))
    if set(request) != {"video_no", "action"} or request["action"] != "project":
        raise ValueError("collaboration_projection_request_invalid")
    from .config import load_config
    cfg = load_config()
    result = project_report_references(cfg["output_dir"], request["video_no"])
    sys.stdout.write(json.dumps(result))

if __name__ == "__main__":
    main()
