"""Opt-in transport pagination. No source selection or editorial decisions."""

from collections import defaultdict

import json

from .contracts import EvidenceError, canonical_json, digest

from .index import atomic_json

from .owner_payload import compact_read

from .records import read_records

POLICIES = {"family_serial_v1", "family_round_robin_v1", "balanced_compact_v1"}

def balanced_page(service, *, records, requested, query, query_id, question_id, cursor, context_order):
    """One original fragment per active family turn; unused space is shared.

    A long text is fragmented for transport, never summarized. Family order is
    not chronology. Each family's original asset/ordinal order remains intact.
    The full provenance page is saved even when only its compact view is sized.
    """
    policy = service.read_page_policy
    limit = query["max_bytes"]
    grouped = defaultdict(list)
    for record in records:
        grouped[record["family"]].append(record)
    families = sorted(grouped)
    lanes = {f: dict(offset=0, char_offset=0) for f in families}
    turn = 0
    root = service.run_root / "reads" / query_id
    if cursor:
        if not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor):
            raise EvidenceError("unknown_read_cursor")
        path = root / "cursors" / (cursor + ".json")
        if not path.is_file():
            raise EvidenceError("unknown_read_cursor")
        issued = json.loads(path.read_text(encoding="utf-8"))
        if digest(issued) != cursor or issued.get("query_id") != query_id or issued.get("policy") != policy:
            raise EvidenceError("read_cursor_mismatch")
        lanes, turn = issued["lanes"], issued["next_family_index"]
        if set(lanes) != set(families) or type(turn) is not int or not 0 <= turn < max(1, len(families)):
            raise EvidenceError("read_cursor_mismatch")
        if any(set(v) != {"offset", "char_offset"} or any(type(x) is not int or x < 0 for x in v.values())
               or v["offset"] > len(grouped[f]) for f, v in lanes.items()):
            raise EvidenceError("read_cursor_mismatch")
    initial = digest(lanes)


    staged = [r for f in families for r in grouped[f][lanes[f]["offset"]:lanes[f]["offset"] + limit // 64 + 1]]
    loaded = {}
    for asset_id in {r["asset_id"] for r in staged}:
        loaded.update(read_records(service.assets[asset_id], [r for r in staged if r["asset_id"] == asset_id]))

    def fragment(family, length):
        lane = lanes[family]
        record = grouped[family][lane["offset"]]
        raw = loaded[record["record_id"]]["raw_text"]
        text = raw or ""
        start = lane["char_offset"]
        if start > len(text):
            raise EvidenceError("read_cursor_mismatch")
        end = min(len(text), start + length)
        row = {k: record[k] for k in ("record_id", "asset_id", "source_id", "video_no", "family", "ordinal",
            "native_locator", "legacy_pointer", "raw_time", "time_index", "clock_status", "uncertainties", "playable", "transform")}
        row.update(raw_payload_sha256=record["raw_payload_sha256"],
            role="requested" if record["record_id"] in requested else "adjacent_context",
            raw_text=text[start:end] if raw is not None else None, raw_char_start=start, raw_char_end=end,
            raw_char_total=len(text), fragment_complete=start == 0 and end == len(text),
            text_status="available" if raw is not None else "decode_or_structure_unknown")
        return row

    def result(rows, rid="0" * 64, following="0" * 64):
        body = dict(schema_version="ew.read_result.v1", source_id=service.source_id, world_revision=service.world_revision,
            question_id=question_id, read_query_id=query_id, read_id=rid, records=rows,
            status="partial_page" if following else "complete" if rows else "empty", next_cursor=following,
            meaning_complete=False, original_loaded=True, model_delivered=False, visual_verified=False, audio_listened=False,
            read_page_policy=policy, page_byte_scope="compact_read" if policy == "balanced_compact_v1" else "full_read",
            ordering_policy="Family transport turns; within-family asset/ordinal order, not event or edit order.")
        if context_order == "source_order":
            body.update(context_order=context_order,
                adjacency_policy="Same immutable asset/revision and ordinal only; file order does not confirm chronological adjacency, speaker, meaning or playback.")
        return body

    def fits(rows):
        body = result(rows)
        view = compact_read(body) if policy == "balanced_compact_v1" else body
        return len(canonical_json(view).encode("utf-8")) <= limit

    rows, served = [], set()
    active = [f for f in families if lanes[f]["offset"] < len(grouped[f])]
    if active and not fits([fragment(f, 1) for f in active]):
        raise EvidenceError("page_bytes_too_small")
    while active:
        family = families[turn]
        turn = (turn + 1) % len(families)
        if family not in active:
            continue
        candidate = fragment(family, 1024 if len(active) > 1 else 2 ** 63)


        reserved = [fragment(f, 1) for f in active if f != family and f not in served]
        low, high = 0, candidate["raw_char_end"] - candidate["raw_char_start"]
        while low < high:
            middle = (low + high + 1) // 2
            if fits(rows + [fragment(family, middle)] + reserved):
                low = middle
            else:
                high = middle - 1
        item = fragment(family, low)
        if (low == 0 and item["raw_char_start"] < item["raw_char_total"]) or not fits(rows + [item] + reserved):
            turn = families.index(family)
            break
        rows.append(item)
        served.add(family)
        if item["raw_char_end"] == item["raw_char_total"]:
            lanes[family]["offset"] += 1
            lanes[family]["char_offset"] = 0
        else:
            lanes[family]["char_offset"] = item["raw_char_end"]
        active = [f for f in families if lanes[f]["offset"] < len(grouped[f])]
    if not rows and active:
        raise EvidenceError("page_bytes_too_small")
    issued = dict(query_id=query_id, policy=policy, next_family_index=turn, lanes=lanes)
    following = digest(issued) if active else None
    if following:
        atomic_json(root / "cursors" / (following + ".json"), issued)
    rid = digest([service.world_revision, query_id, initial, rows])
    body = result(rows, rid, following)
    view = compact_read(body) if policy == "balanced_compact_v1" else body
    if len(canonical_json(view).encode("utf-8")) > limit:
        raise EvidenceError("page_bytes_too_small")
    return service._save_read(body)
