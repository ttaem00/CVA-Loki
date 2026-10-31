"""Source-bound retrieval; scores describe matching, never editing value."""

from __future__ import annotations

from collections import defaultdict

from decimal import Decimal

import json

import sqlite3

import time

from .contracts import EvidenceError, FAMILIES, canonical_json, digest, native_path, overlaps

from .index import atomic_json, build_lock, gram_tokens, normalize, open_snapshot, word_tokens

QUERY_REVISION = "ew.query.rrf60.v2"

RECORD_SCOPE_REVISION = "ew.query.record-scope.v1"

HYBRID_MODES = {"hybrid_branch_v1", "hybrid_dbsf_v1"}

def original_read_action(**args):
    """Shared hit/cell affordance; the caller retains its validated tool envelope."""
    return dict(action="read_evidence", args=args)

def _fts_or(tokens):
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in sorted(set(tokens)))

def _ranges(query):
    result = []
    try:
        for span in query.get("intervals_sec", []):
            start, end = (Decimal(str(v)) * 1000000 for v in span)
            if not start.is_finite() or not end.is_finite() or start < 0 or end <= start:
                raise ValueError
            result.append((start, end))
    except (ValueError, TypeError, ArithmeticError):
        raise EvidenceError("invalid_time_filter") from None
    return result

def _scoped_text(db, unit, query):
    ranges = _ranges(query)
    record_ids = set(query["record_ids"]) if "record_ids" in query else None
    kept, runs, active = [], [], []
    last_clock_kind = None
    for member in json.loads(unit["members_json"]):
        if record_ids is not None and member["record_id"] not in record_ids:


            if active:
                runs.append(normalize("\n".join(active)))
                active = []
            last_clock_kind = None
            continue
        saved = db.execute("SELECT payload_json FROM records WHERE record_id=?",
                           (member["record_id"],)).fetchone()
        if saved is None:
            raise EvidenceError("unit_record_missing")
        record = json.loads(saved[0])
        clock = record["time_index"]
        eligible = (clock is not None and (not ranges or any(overlaps(clock, a, b) for a, b in ranges)))
        if clock is None:
            eligible = query.get("include_unlocated", not ranges)
        clock_kind = "located" if clock else "unlocated"
        if not eligible or last_clock_kind not in {None, clock_kind}:
            if active:
                runs.append(normalize("\n".join(active)))
                active = []
        if not eligible:
            last_clock_kind = None
            continue
        last_clock_kind = clock_kind
        kept.append(member)
        active.append((record["raw_text"] or "")[member["raw_char_start"]:member["raw_char_end"]])
    if active:
        runs.append(normalize("\n".join(active)))
    if any(not any(normalize(p) in run for run in runs) for p in query.get("must_phrases", [])):
        return [], []
    if any(normalize(t) in run for t in query.get("exclude_terms", []) for run in runs):
        return [], []
    return runs, kept

def _calculate(db, query, *, deadline, representation):
    scores, methods, members_by_pk = defaultdict(float), defaultdict(set), {}
    queries = [query["text"], *query.get("variants", [])]
    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        for family in sorted(query["families"]):
            for variant_no, variant in enumerate(queries):
                if time.monotonic() >= deadline:
                    raise EvidenceError("query_budget_exhausted")
                words, grams = word_tokens(variant), gram_tokens(variant, representation)
                if query["mode"] == "exact" or not grams:
                    branches = [("exact" if query["mode"] == "exact" else "short_query_scan", None)]
                else:
                    branches = [("word_fts", words), ("gram_fts", grams)]
                for branch, tokens in branches:
                    if tokens is None:
                        rows = db.execute("SELECT * FROM units WHERE family=? ORDER BY unit_id", (family,))
                    else:
                        expression = _fts_or(tokens)
                        if not expression:
                            continue
                        rows = db.execute(f"SELECT u.* FROM {branch} JOIN units u "
                                          f"ON u.unit_pk={branch}.rowid WHERE {branch} MATCH ? "
                                          f"AND u.family=? ORDER BY bm25({branch}),u.unit_id",
                                          (expression, family))
                    rank = 0
                    for unit in rows:
                        if time.monotonic() >= deadline:
                            raise EvidenceError("query_budget_exhausted")
                        runs, members = _scoped_text(db, unit, query)
                        if not members:
                            continue
                        if tokens is None:
                            match = any(normalize(variant) in run for run in runs)
                        elif branch == "word_fts":
                            match = any(set(words) & set(word_tokens(run)) for run in runs)
                        else:
                            match = any(set(grams) & set(gram_tokens(run, representation)) for run in runs)
                        if not match:
                            continue
                        rank += 1
                        pk = unit["unit_pk"]
                        scores[pk] += 1.0 / (60 + rank)
                        methods[pk].add(f"{branch}:variant-{variant_no}")
                        members_by_pk[pk] = members
    finally:
        db.set_progress_handler(None, 0)
    hits = []
    for pk, score in scores.items():
        unit = db.execute("SELECT * FROM units WHERE unit_pk=?", (pk,)).fetchone()
        hits.append(dict(unit_id=unit["unit_id"], retrieval_rank_score=score,
                         family=unit["family"], methods=sorted(methods[pk]),
                         evidence_members=members_by_pk[pk]))
    return sorted(hits, key=lambda h: (-h["retrieval_rank_score"], h["unit_id"]))

def _hydrate(db, hit, query):
    clocks, texts = [], []
    for member in hit["evidence_members"]:
        row = db.execute("SELECT payload_json FROM records WHERE record_id=?",
                         (member["record_id"],)).fetchone()
        if not row:
            raise EvidenceError("unit_record_missing")
        record = json.loads(row[0])
        if record["time_index"]:
            clocks.append(record["time_index"])
        texts.append((record["raw_text"] or "")[member["raw_char_start"]:member["raw_char_end"]])
    text = normalize("\n".join(texts))
    tokens = word_tokens(query["text"])
    matched = [token for token in tokens if token in text]
    return dict(hit, start_us=min(t["start_us"] for t in clocks) if clocks else None,
                end_us=max(t["end_us"] for t in clocks) if clocks else None,
                location_status="located" if len(clocks) == len(hit["evidence_members"]) else "unlocated",
                excerpt=text[:320], matched_tokens=matched,
                unmatched_tokens=[t for t in tokens if t not in matched],
                retrieval_relevance_only=True,
                uncertainty="retrieval_relevance_is_not_confirmed_meaning" if query.get("mode") in HYBRID_MODES else "lexical_match_is_not_confirmed_meaning",
                next_action=original_read_action(families=[hit["family"]],
                    record_ids=list(dict.fromkeys(m["record_id"] for m in hit["evidence_members"]))))

def search(root, *, world_revision, query, page=None, per_query_ms, run_root, dense_backend=None, scope_id=None):
    """Persist a complete ranking per run. A timeout publishes no partial ranking."""
    mode = query.get("mode", "lexical")
    if mode not in {"exact", "lexical", *HYBRID_MODES}:
        raise EvidenceError("unsupported_search_mode")
    if mode in HYBRID_MODES and dense_backend is None:
        raise EvidenceError("dense_backend_not_configured")
    if not isinstance(query.get("text"), str) or not normalize(query["text"]):
        raise EvidenceError("empty_query")
    families = query.get("families")
    if not isinstance(families, list) or not families or any(not isinstance(f, str) for f in families):
        raise EvidenceError("search_families_required")
    for key in ("variants", "must_phrases", "exclude_terms"):
        if not isinstance(query.get(key, []), list) or any(not isinstance(v, str) or not normalize(v)
                                                         for v in query.get(key, [])):
            raise EvidenceError("invalid_query_terms")
    query = dict(query, mode=mode, families=sorted(set(families)))
    if "record_ids" in query:
        ids = query["record_ids"]
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(value, str) or not value for value in ids)
                or len(set(ids)) != len(ids)):
            raise EvidenceError("invalid_search_record_scope")
        query["record_ids"] = sorted(ids)
    _ranges(query)
    page = page or {}
    limit, max_bytes = page.get("limit", 20), page.get("max_bytes", 16000)
    if type(limit) is not int or limit <= 0 or type(max_bytes) is not int or max_bytes < 512:
        raise EvidenceError("invalid_transport_page")
    cursor = page.get("cursor")
    started = time.monotonic()
    ranking_started=started

    with open_snapshot(root, world_revision=world_revision) as db:
        manifest = json.loads((native_path(root) / "manifest.json").read_text(encoding="utf-8"))
        allowed = manifest["identity"]["policy"]["allowed"]
        family_status = {a["family"]: a["availability"] for a in manifest["identity"]["assets"]}
        for family in query["families"]:
            if family not in FAMILIES:
                raise EvidenceError("unsupported_family")
            if family not in allowed:
                raise EvidenceError("excluded_by_policy")
            if family_status[family] not in {"present", "partial", "absent_confirmed"}:
                raise EvidenceError("family_" + family_status[family])
        identity_parts = [world_revision, QUERY_REVISION, query]
        if "record_ids" in query:


            for record_id in query["record_ids"]:
                row = db.execute("SELECT family FROM records WHERE record_id=?", (record_id,)).fetchone()
                if row is None:
                    raise EvidenceError("search_record_not_in_world")
                if row[0] not in query["families"]:
                    raise EvidenceError("search_record_family_outside_scope")
            identity_parts.append(RECORD_SCOPE_REVISION)
        if mode in HYBRID_MODES:
            if not set(query["families"]) <= set(getattr(dense_backend,'families',["stt"])):
                raise EvidenceError("dense_family_unsupported")
            dense_backend.validate(db, world_revision=world_revision, deadline=started + per_query_ms / 1000)


            for text in [query["text"], *query.get("variants", [])]:
                dense_backend.query_vector(text, deadline=started + per_query_ms / 1000)
            if getattr(dense_backend,'prepares_query_outside_ranking_budget',False):
                ranking_started=time.monotonic()
            identity_parts.extend([dense_backend.identity, scope_id])
        identity = digest(identity_parts)
        cache_root = native_path(run_root) / "queries" / identity
        path = cache_root / "ranking.json"
        cached = path.is_file()
        if cursor and not cached:
            raise EvidenceError("unknown_query_cursor")
        with build_lock(cache_root):
            if path.is_file():
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                if snapshot["query_id"] != identity or snapshot["world_revision"] != world_revision:
                    raise EvidenceError("query_revision_mismatch")
                if snapshot["rank_sha256"] != digest(snapshot["hits"]):
                    raise EvidenceError("query_snapshot_changed")
            else:
                try:
                    hits = _calculate(db, query, deadline=ranking_started + per_query_ms / 1000,
                                      representation=manifest["identity"]["representation"])
                    if mode in HYBRID_MODES:
                        hits = dense_backend.calculate(db, query, hits, deadline=ranking_started + per_query_ms / 1000)
                except sqlite3.OperationalError as exc:
                    if "interrupted" not in str(exc).lower():
                        raise
                    raise EvidenceError("query_budget_exhausted") from exc
                snapshot = dict(query_id=identity, world_revision=world_revision, query=query,
                                hits=hits, rank_sha256=digest(hits))
                if mode in HYBRID_MODES:
                    snapshot["retrieval_identity"] = dense_backend.identity
                atomic_json(path, snapshot)
            if cursor:
                if not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor):
                    raise EvidenceError("unknown_or_cross_query_cursor")
                cursor_path = cache_root / "cursors" / (cursor + ".json")
                if not cursor_path.is_file():
                    raise EvidenceError("unknown_or_cross_query_cursor")
                issued = json.loads(cursor_path.read_text(encoding="utf-8"))
                if issued["page"] != [limit, max_bytes] or issued["query_id"] != identity:
                    raise EvidenceError("cursor_page_mismatch")
                offset = issued["offset"]
            else:
                offset = 0
            selected, used = [], 512
            for hit in snapshot["hits"][offset:]:
                hydrated = _hydrate(db, hit, query)
                if mode in HYBRID_MODES:
                    hydrated["retrieval_position"] = offset + len(selected) + 1
                size = len(canonical_json(hydrated).encode("utf-8"))
                if len(selected) >= limit or used + size > max_bytes:
                    break
                selected.append(hydrated)
                used += size
            if not selected and offset < len(snapshot["hits"]):
                raise EvidenceError("page_bytes_too_small")
            next_offset = offset + len(selected)
            next_cursor = None
            if next_offset < len(snapshot["hits"]):
                issued = dict(query_id=identity, offset=next_offset, page=[limit, max_bytes])
                next_cursor = digest(issued)
                atomic_json(cache_root / "cursors" / (next_cursor + ".json"), issued)
    return dict(schema_version="ew.search.v1", world_revision=world_revision, query_id=identity,
                status="retrieved" if selected else ("no_matching_evidence_not_absence" if mode in HYBRID_MODES else "no_lexical_match_not_absence"),
                hits=selected, total_eligible_hits=len(snapshot["hits"]), next_cursor=next_cursor,
                family_status=family_status, cache_hit=cached, elapsed_sec=time.monotonic() - started,
                output_bytes=used, read_required=True,
                **({"retrieval_identity": dense_backend.identity,
                    "branch_order": "dbsf_frontier20_preserve_tail_v1" if mode == "hybrid_dbsf_v1" else "lexical_first_interleave",
                    "dense_family_scope": getattr(dense_backend,'families',["stt"])} if mode in HYBRID_MODES else {}))
