"""Source roster names for replay lookup, never timed attendance declarations.

Only explicit roster syntax is projected. Prose mentions, voices and the host's
own name do not authorize participant identity or a participation interval.
"""

from __future__ import annotations

from copy import deepcopy

import hashlib

import json

import logging

from pathlib import Path

import re

import unicodedata

from .editor_comment_parser import EditorCommentParserError, parse_editor_documents

_KINDS = {"viewer_comment", "manager_context", "vod_title"}

_LOG = logging.getLogger(__name__)

def _collected_documents(video, directory):
    """Read preserved v3/v4 private bodies without migrating collector caches.

    v3 predates thread ordering, which roster lookup does not need. The normal
    collector still owns freshness/refetch policy; this is source recovery only.
    """
    cache = directory / "timeline_comments.json"
    try:
        if not cache.is_file() or cache.stat().st_size > 2000000:
            return []
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return []
    if (not isinstance(data, dict) or data.get("schema_version") not in {3, 4}
            or str(data.get("video_no")) != video or data.get("status") != "ok"
            or data.get("source_url") != f"https://chzzk.naver.com/video/{video}"
            or not isinstance(data.get("comments"), list) or len(data["comments"]) > 100):
        return []
    documents = [{"raw": c["private_prompt_body"]} for c in data["comments"]
                 if isinstance(c, dict) and isinstance(c.get("private_prompt_body"), str)]
    if sum(len(d["raw"]) for d in documents) > 250000:
        return []
    return documents

def comment_collaboration_periods(documents, video_no, duration_sec, *, host_name=""):
    """Project bounded source context through the original TTaempad parser.

    Comment/memo chapter scopes are provisional replay lookup evidence, not
    verified attendance. Missing ends and conflicting scopes remain unknown.
    No source text, author, comment ID or filesystem locator goes public.
    """
    from .chatgpt_export.contract.collaboration_periods import validate_collaboration_periods
    if not documents or not re.fullmatch(r"[1-9][0-9]{0,19}", str(video_no)):
        return []
    if (not isinstance(documents, list) or len(documents) > 100
            or any(not isinstance(d, dict) or not isinstance(d.get("raw"), str) for d in documents)
            or sum(len(d["raw"]) for d in documents) > 250000):
        _LOG.warning("collaboration_comment_input_limit video_no=%s", video_no)
        return []
    try:
        parsed = parse_editor_documents(documents, video_no=video_no, duration_sec=duration_sec)["periods"]
    except EditorCommentParserError:
        _LOG.warning("collaboration_comment_parser_failed video_no=%s", video_no)
        return []
    source_refs = {f"comment-{i + 1}": ("manager-context-" if d.get("manager_context") else "comment-") + hashlib.sha256(
        (str(video_no) + "\n" + d["raw"]).encode("utf-8")).hexdigest()[:32]
        for i, d in enumerate(documents)}
    priorities = {f"comment-{i + 1}": 0 if d.get("manager_context") else 1 for i, d in enumerate(documents)}
    intervals = []
    for row in parsed:
        names = list(dict((_key(n), n) for n in row["members"]
                          if isinstance(n, str) and _key(n) != _key(host_name)).values())
        if not names and row["mode"] != "none":
            continue
        item = dict(id="source-range", start_sec=row["start_sec"], end_sec=row["end_sec"],
                    members=names, source_kind="source_evidence", support_status="provisional",
                    evidence_refs=[source_refs[row["document_id"]]])
        try:
            validate_collaboration_periods([item], duration_sec)
        except ValueError:
            continue
        intervals.append((item, priorities[row["document_id"]]))


    boundaries = sorted({t for row, _ in intervals for t in (row["start_sec"], row["end_sec"])})
    result = []
    for start, end in zip(boundaries, boundaries[1:]):
        covered = [(r, priority) for r, priority in intervals if r["start_sec"] <= start and end <= r["end_sec"]]
        priority = min((p for _, p in covered), default=1)
        active = [r for r, p in covered if p == priority]
        if not active or len({tuple(sorted(_key(n) for n in r["members"])) for r in active}) != 1:
            continue
        refs = sorted({ref for r in active for ref in r["evidence_refs"]})
        if result and result[-1]["end_sec"] == start and result[-1]["members"] == active[0]["members"] and result[-1]["evidence_refs"] == refs:
            result[-1]["end_sec"] = end
        else:
            result.append(dict(active[0], id="comment-period-" + str(len(result) + 1),
                               start_sec=start, end_sec=end, evidence_refs=refs))
    if len(result) > 128:
        return []
    validate_collaboration_periods(result, duration_sec)
    return result

def load_comment_collaboration_periods(video_no, work_root, duration_sec, *, host_name=""):
    """Reuse the exact-VOD Loki comment cache and existing manager memo."""
    video = str(video_no)
    if not work_root or not re.fullmatch(r"[1-9][0-9]{0,19}", video):
        return []
    root = Path(work_root)
    directory = root if root.name == video else root / video
    documents = _collected_documents(video, directory)
    memo = directory / f"{video}_context.md"
    if memo.is_file() and memo.stat().st_size <= 1000000:
        try:
            documents.append({"raw": memo.read_text(encoding="utf-8"), "manager_context": True})
        except (OSError, UnicodeError):
            _LOG.warning("collaboration_context_unreadable video_no=%s", video)
    return comment_collaboration_periods(documents, video, duration_sec, host_name=host_name)

def merge_period_roster_candidates(candidates, periods):
    """Use the same names for small report chips and the existing public reader."""
    rows = list(candidates)
    for period in periods:
        names = list(period["members"])
        if not names:
            continue
        kind = "manager_context" if any(ref.startswith("manager-context-") for ref in period.get("evidence_refs", [])) else "viewer_comment"


        identity = (kind, tuple(sorted(_key(n) for n in names)))
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
        rows.append(dict(id="roster-" + digest, members=names, source_kind=kind,
                         support_status="provisional", lookup_scope="document"))
    return public_collaboration_candidates(list({r["id"]: r for r in rows}.values())[:128])

def _key(name):
    return unicodedata.normalize("NFKC", name).casefold()

def _public_name(name):
    return (isinstance(name, str) and bool(name) and name == name.strip() and len(name) <= 64
            and not any(unicodedata.category(c).startswith("C") for c in name))

def _bounded_roster_rows(rows, *, source_kind):
    if len(rows) > 128:
        _LOG.warning("collaboration_roster_projection_partial source_kind=%s projected_rows=128 omitted_rows=%s omitted_names=%s",
                     source_kind, len(rows) - 128, sum(len(row["members"]) for row in rows[128:]))
    return rows[:128]

def _roster_rows(rosters, *, source_kind, host_name=""):
    """Only the existing public name contract escapes the internal parser."""
    result, seen = [], set()
    for roster in rosters:
        names = roster["members"]
        if not names or any(not _public_name(n) for n in names):
            continue
        names = list(dict((_key(n), n) for n in names if _key(n) != _key(host_name)).values())


        for start in range(0, len(names), 32):
            members = names[start:start + 32]
            identity = (source_kind, tuple(sorted(_key(n) for n in members)))
            if identity in seen:
                continue
            seen.add(identity)
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
            result.append(dict(id="roster-" + digest, members=members, source_kind=source_kind,
                               support_status="provisional", lookup_scope="document"))
    return _bounded_roster_rows(result, source_kind=source_kind)

def roster_candidates(text, *, source_kind, host_name=""):
    """Parse a raw text or document batch once; titles keep explicit with lists."""
    if not isinstance(source_kind, str) or source_kind not in _KINDS:
        raise ValueError("collaboration_candidate_source_invalid")
    if source_kind == "vod_title":

        match = re.search(r"(?:\bw[./]|\bwith\b)\s*([\w가-힣.-]+(?:\s*[,，]\s*[\w가-힣.-]+)*)", str(text), re.I)
        return _roster_rows([{"members": [part.strip() for part in re.split(r"[,，]", match[1])]}] if match else [],
                            source_kind=source_kind, host_name=host_name)
    if isinstance(text, list):
        documents = text
    elif isinstance(text, str) and text.strip():
        documents = [{"raw": text}]
    else:
        return []
    try:
        parsed = parse_editor_documents(documents)
    except EditorCommentParserError:
        _LOG.warning("collaboration_roster_parser_failed source_kind=%s", source_kind)
        return []
    return _roster_rows(parsed["roster_candidates"], source_kind=source_kind, host_name=host_name)

def public_collaboration_candidates(rows):
    """Allow-list the wire contract; no raw comments, memo or private locators."""
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError("collaboration_candidates_invalid")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {
                "id", "members", "source_kind", "support_status", "lookup_scope"}
                or not isinstance(row["id"], str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", row["id"])
                or row["id"] in seen or not isinstance(row["source_kind"], str)
                or row["source_kind"] not in _KINDS
                or row["support_status"] != "provisional" or row["lookup_scope"] != "document"):
            raise ValueError("collaboration_candidate_shape_invalid")
        names = row["members"]
        if (not isinstance(names, list) or not 1 <= len(names) <= 32

                or any(not _public_name(n) for n in names)
                or len({_key(n) for n in names}) != len(names)):
            raise ValueError("collaboration_candidate_members_invalid")
        seen.add(row["id"])
    return deepcopy(rows)

def load_collaboration_candidates(video_no, work_root, *, host_name="", title=""):
    """Reuse collected sources of this exact VOD without fetches or model calls."""
    video = str(video_no)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", video):
        return []
    rows = roster_candidates(title, source_kind="vod_title", host_name=host_name)
    if not work_root:
        return rows
    root = Path(work_root)
    directory = root if root.name == video else root / video
    documents = _collected_documents(video, directory)
    memo = directory / f"{video}_context.md"
    if memo.is_file() and memo.stat().st_size <= 1000000:
        try:
            text = memo.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            _LOG.warning("collaboration_context_unreadable video_no=%s", video)
        else:
            documents.append({"raw": text, "manager_context": True})
    if documents:
        try:
            parsed = parse_editor_documents(documents, video_no=video)
        except EditorCommentParserError:
            _LOG.warning("collaboration_roster_parser_failed video_no=%s", video)
        else:
            kinds = {f"comment-{i + 1}": "manager_context" if doc.get("manager_context") else "viewer_comment"
                     for i, doc in enumerate(documents)}
            for candidate in parsed["roster_candidates"]:
                rows.extend(_roster_rows([candidate], source_kind=kinds[candidate["document_id"]], host_name=host_name))
    unique = {row["id"]: row for row in rows}
    return public_collaboration_candidates(_bounded_roster_rows(list(unique.values()), source_kind="mixed"))
