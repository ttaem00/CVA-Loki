"""Candidate source exploration service; no provider, semantic judge, or editable canon."""

from __future__ import annotations

from collections import defaultdict

import json

import math

from pathlib import Path

from .contracts import AssetBinding, EvidenceError, FAMILIES, SourcePolicy, canonical_json, digest, native_path

from .index import atomic_json, build_snapshot, open_snapshot

from .query import search, _ranges, original_read_action

from .records import assert_asset_fresh, register_assets, read_records, utc_ranges, select_utc_records

from .state import EvidenceState

class EvidenceWorldService:
    @classmethod
    def prepare_temporal(cls, *, generation_root, channel_uid, live_id, cache_root,
                         run_root, policy=None, owner="temporal_source_navigation",
                         cold_archive_root=None, node_executable=None):
        from .temporal_source import materialize
        policy = policy or SourcePolicy(allowed_families=("stt", "chat"))
        assets, receipt = materialize(generation_root=generation_root, channel_uid=channel_uid,
            live_id=live_id, derived_root=cache_root, policy=policy,
            cold_archive_root=cold_archive_root,node_executable=node_executable)
        root, manifest = build_snapshot(assets=assets, work_dir=cache_root, policy=policy)

        scoped_run = Path(run_root) / "temporal" / manifest["world_revision"]
        service = cls(root=root, manifest=manifest, run_root=scoped_run, owner=owner)
        service.temporal_receipt = receipt
        return service

    @classmethod
    def prepare(cls, *, video_no, srt_path, source_work_dir, cache_root, run_root,
                duration_sec, policy=None, owner="source_navigation", unit_policy=None, clip_source_path=None):
        policy = policy or SourcePolicy(allowed_families=("stt",))
        assets = register_assets(video_no=video_no, srt_path=srt_path, work_dir=source_work_dir,
                                 duration_sec=duration_sec, policy=policy, clip_source_path=clip_source_path)
        root, manifest = build_snapshot(assets=assets, work_dir=cache_root, policy=policy,
                                        unit_policy=unit_policy)
        return cls(root=root, manifest=manifest, run_root=run_root, owner=owner)

    def __init__(self, *, root, manifest, run_root, owner):
        self.root = native_path(root)
        self.manifest = manifest
        self.world_revision = manifest["world_revision"]
        self.source_id = manifest["identity"]["assets"][0]["source_id"]
        self.video_no = manifest["identity"]["assets"][0]["video_no"]
        duration = manifest["identity"]["assets"][0]["clock_authority"]["duration_sec_decimal"]
        self.duration = None if duration is None else float(duration)
        self.policy = manifest["identity"]["policy"]
        self.run_root = native_path(run_root)
        self.read_page_policy = "family_serial_v1"
        self.state = EvidenceState(self.run_root, source_id=self.source_id,
                                   world_revision=self.world_revision, owner=owner)
        self.family_status = {a["family"]: a["availability"] for a in manifest["identity"]["assets"]}
        with self.open() as db:
            self.assets = {r[0]: AssetBinding(**json.loads(r[1])) for r in
                           db.execute("SELECT asset_id,binding_json FROM assets")}
        self.state.load()
        self.dense_backend = None

    def configure_retrieval(self, config):
        if config is not None:
            from ..editorial_embedding_index import SavedGemmaSearch
            stt=next(a for a in self.assets.values() if a.family=='stt')
            self.dense_backend=SavedGemmaSearch(config,
                source_dir=native_path(stt.source_locator['path']).parent)
        else:
            self.dense_backend = None

    def configure_read(self, policy="family_serial_v1"):
        from .read_pages import POLICIES
        if policy not in POLICIES:
            raise EvidenceError("unknown_read_page_policy")
        self.read_page_policy = policy

    def configure_hierarchy(self, config):
        from .pageindex_tree import prepare_hierarchy
        self.hierarchy = prepare_hierarchy(self, config) if config else None

    def configure_summary_hints(self, *, generation_root, node_executable=None, cold_archive_root=None,
                                finalized_vod=None):
        from .summary_hints import configure
        configure(self, generation_root=generation_root, node_executable=node_executable,
                  cold_archive_root=cold_archive_root, finalized_vod=finalized_vod)

    def retrieval_contract(self):
        return dict(modes=["exact", "lexical", *(["hybrid_branch_v1"] if self.dense_backend else [])],
            **({"summary_hints":dict(profile='loom_summary_hints_v1',view='hints',
                index_page_size=10,current_validation=True,original_read=False,
                clock_domain='utc_epoch_ms',vod_seek_verified=False)}
                if getattr(self,'summary_hints_config',None) else {}),
            **({"hierarchy": {"profile": self.hierarchy["profile"],
                "projection_hash": self.hierarchy["projection_hash"], "provider_calls": 0,
                "derived": True, "raw_read": False}} if getattr(self, "hierarchy", None) else {}),
            **({"dense_identity": self.dense_backend.identity,
                "dense_families": getattr(self.dense_backend,'families',["stt"]),
                "query_encoding": self.dense_backend.strategy} if self.dense_backend else {}))

    def open(self):
        return open_snapshot(self.root, world_revision=self.world_revision, source_id=self.source_id)

    def admission(self):
        return dict(status="ready", scope=("preserved_text_and_derived_metadata_candidate"
                    if {"audio_features", "clip"}.intersection(self.policy["allowed"]) else "stt_chat_text_candidate"),
                    indexed_families=list(self.family_status), family_status=self.family_status,
                    meaning_complete=False, visual_verified=False, audio_listened=False,
                    phrase_navigation=dict(view='phrases',action='browse_evidence',max_window_seconds=120,
                        scope='one interval_sec, native interval_utc_ms or <=32 source record_ids',
                        families=['stt','chat'],original_read=False,meaning_complete=False,
                        policy='Surface/source character spans and lexical contrast; same expression is not same event'))

    def _families(self, families):
        if not isinstance(families, list) or not families or any(not isinstance(f, str) for f in families):
            raise EvidenceError("families_required")
        for family in families:
            if family not in FAMILIES:
                raise EvidenceError("unsupported_family")
            if family not in self.policy["allowed"]:
                raise EvidenceError("excluded_by_policy")
            if self.family_status[family] not in {"present", "partial", "absent_confirmed"}:
                raise EvidenceError("family_" + self.family_status[family])
        return sorted(set(families))

    def search(self, *, question_id, query, page=None, per_query_ms=10000):
        result = search(self.root, world_revision=self.world_revision, query=query, page=page,
                        per_query_ms=per_query_ms, run_root=self.run_root,
                        dense_backend=self.dense_backend, scope_id=question_id)
        metrics = {key: result.pop(key) for key in ("elapsed_sec", "cache_hit", "output_bytes")}
        self.state.event("search_returned", question_id=question_id,
                         payload=dict(query_id=result["query_id"],
                                      unit_ids=[h["unit_id"] for h in result["hits"]],
                                      next_cursor=result["next_cursor"]))
        atomic_json(self.run_root / "metrics" / (digest([query, page]) + ".json"), metrics)
        unlocated = [h for h in result["hits"] if h["location_status"] != "located"]
        located = [h for h in result["hits"] if h["location_status"] == "located"]
        response = dict(result, schema_version="ew.search_result.v1", source_id=self.source_id,
                    status="partial_page" if result["next_cursor"] else "complete" if result["hits"] else "empty",
                    hits=located, unlocated_hits=unlocated, question_id=question_id,
                    absence_claim=False, index_status="ready")
        if getattr(self,'hierarchy',None):
            from .pageindex_tree import attach_search_nodes
            attach_search_nodes(self,response,max_bytes=page.get('max_bytes',16000) if page else 16000)
        return response

    def read(self, *, question_id, record_ids=None, intervals_sec=None, families=None,
             context_cues=2, max_bytes=16000, cursor=None, context_order="time_adjacent", intervals_utc_ms=None,
             read_id=None):
        if read_id is not None:


            if (any(x is not None for x in (record_ids, intervals_sec, intervals_utc_ms, families, cursor))
                    or context_cues != 2 or context_order != "time_adjacent"):
                raise EvidenceError("invalid_tool_args")
            if type(max_bytes) is not int or max_bytes < 2000:
                raise EvidenceError("invalid_transport_page")
            result = self.load_read(read_id)
            view = result
            if result.get('read_page_policy') == 'balanced_compact_v1':
                from .owner_payload import compact_read
                view = compact_read(result)
            if len(canonical_json(view).encode("utf-8")) > max_bytes:
                raise EvidenceError("page_bytes_too_small")
            return result
        if intervals_sec and self.duration is None:
            raise EvidenceError("source_clock_unverified")
        if sum(bool(x) for x in (record_ids, intervals_sec, intervals_utc_ms)) != 1:
            raise EvidenceError("read_requires_records_or_intervals")
        if intervals_utc_ms is not None:
            utc_ranges(self, intervals_utc_ms)
        if type(context_cues) is not int or context_cues < 0 or context_cues > 12:
            raise EvidenceError("invalid_context_page")
        if context_order not in {"time_adjacent", "source_order"}:
            raise EvidenceError("invalid_context_order")
        if context_order == "source_order" and not record_ids:
            raise EvidenceError("source_order_requires_record_ids")
        if type(max_bytes) is not int or max_bytes < 2000:
            raise EvidenceError("invalid_transport_page")
        if record_ids is not None and (not isinstance(record_ids, list) or any(not isinstance(r, str) for r in record_ids)):
            raise EvidenceError("invalid_record_ids")
        families = self._families(families or list(self.policy["required"]))
        query = dict(record_ids=list(dict.fromkeys(record_ids or [])), intervals_sec=intervals_sec,
                     families=families, context_cues=context_cues, max_bytes=max_bytes)
        if intervals_utc_ms is not None:
            query['intervals_utc_ms'] = intervals_utc_ms

        if context_order == "source_order":
            query["context_order"] = context_order
        if self.read_page_policy != "family_serial_v1":
            query["read_page_policy"] = self.read_page_policy
        query_id = digest([self.world_revision, question_id, query, "read.v1"])
        query_root = self.run_root / "reads" / query_id
        with self.open() as db:
            if record_ids:
                records = []
                for rid in query["record_ids"]:
                    row = db.execute("SELECT payload_json FROM records WHERE record_id=?", (rid,)).fetchone()
                    if not row:
                        raise EvidenceError("unknown_or_wrong_source_record")
                    record = json.loads(row[0])
                    if record["family"] not in families:
                        raise EvidenceError("record_family_mismatch")
                    records.append(record)
            elif intervals_utc_ms is not None:
                records = select_utc_records(db, families, intervals_utc_ms)
            else:
                ranges = _ranges({"intervals_sec": intervals_sec})
                records = []
                for family in families:
                    for a, b in ranges:
                        rows = db.execute("SELECT payload_json FROM records WHERE family=? AND "
                                          "((time_kind='instant' AND start_us>=? AND start_us<?) OR "
                                          "(time_kind='interval' AND start_us<? AND end_us>?)) ORDER BY ordinal",
                                          (family, int(a), int(b), int(b), int(a)))
                        records.extend(json.loads(r[0]) for r in rows)
            requested = {r["record_id"] for r in records}
            expanded = {r["record_id"]: r for r in records}

            for record in records:
                if record["family"] != "stt" or (context_order == "time_adjacent" and not record["time_index"]):
                    continue
                for direction in (-1, 1):
                    previous = record
                    for distance in range(1, context_cues + 1):
                        row = db.execute("SELECT payload_json FROM records WHERE asset_id=? AND ordinal=?",
                                         (record["asset_id"], record["ordinal"] + direction * distance)).fetchone()
                        if not row:
                            break
                        other = json.loads(row[0])
                        if other["asset_id"] != record["asset_id"] or other["family"] != record["family"]:
                            raise EvidenceError("source_order_asset_mismatch")
                        if context_order == "time_adjacent":
                            if not other["time_index"]:
                                break
                            left, right = (other, previous) if direction < 0 else (previous, other)
                            if right["time_index"]["start_us"] - left["time_index"]["end_us"] > 5000000:
                                break
                        expanded[other["record_id"]] = other
                        previous = other
            records = sorted(expanded.values(), key=lambda r: (r["family"], r["asset_id"], r["ordinal"]))
        if self.read_page_policy != "family_serial_v1":
            from .read_pages import balanced_page
            return balanced_page(self, records=records, requested=requested, query=query, query_id=query_id,
                question_id=question_id, cursor=cursor, context_order=context_order)
        offset, char_offset = 0, 0
        if cursor:
            if not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor):
                raise EvidenceError("unknown_read_cursor")
            path = query_root / "cursors" / (cursor + ".json")
            if not path.is_file():
                raise EvidenceError("unknown_read_cursor")
            issued = json.loads(path.read_text(encoding="utf-8"))
            if issued["query_id"] != query_id:
                raise EvidenceError("read_cursor_mismatch")
            offset, char_offset = issued["offset"], issued["char_offset"]

        page_records = records[offset:offset + max(1, max_bytes // 800)]
        loaded = {}
        for asset_id in {r["asset_id"] for r in page_records}:
            loaded.update(read_records(self.assets[asset_id], [r for r in page_records if r["asset_id"] == asset_id]))
        rows, used, next_offset, next_char = [], 800, offset, char_offset
        for record in page_records:
            raw = loaded[record["record_id"]]["raw_text"]
            start = next_char
            text = raw or ""

            finish = len(text)
            base = {key: record[key] for key in ("record_id", "asset_id", "source_id", "video_no", "family",
                    "ordinal", "native_locator", "legacy_pointer", "raw_time", "time_index", "clock_status",
                    "uncertainties", "playable", "transform")}
            base.update(role="requested" if record["record_id"] in requested else "adjacent_context",
                        raw_payload_sha256=record["raw_payload_sha256"])
            available = max_bytes - used - len(canonical_json(base).encode("utf-8")) - 120
            if available < 4:
                break
            low, high = 0, len(text) - start
            while low < high:
                middle = (low + high + 1) // 2
                if len(canonical_json(text[start:start + middle]).encode("utf-8")) <= available:
                    low = middle
                else:
                    high = middle - 1
            fragment = text[start:start + low]
            if not fragment and start < len(text):
                break
            finish = start + len(fragment)
            row = dict(base, raw_text=fragment if raw is not None else None,
                       raw_char_start=start, raw_char_end=finish, raw_char_total=len(text),
                       fragment_complete=start == 0 and finish == len(text),
                       text_status="available" if raw is not None else "decode_or_structure_unknown")
            rows.append(row)
            used += len(canonical_json(row).encode("utf-8"))
            if finish < len(text):
                next_char = finish
                break
            next_offset += 1
            next_char = 0
        if not rows and offset < len(records):
            raise EvidenceError("page_bytes_too_small")
        next_cursor = None
        if next_offset < len(records):
            issued = dict(query_id=query_id, offset=next_offset, char_offset=next_char)
            next_cursor = digest(issued)
            atomic_json(query_root / "cursors" / (next_cursor + ".json"), issued)
        read_id = digest([self.world_revision, query_id, offset, char_offset, rows])
        result = dict(schema_version="ew.read_result.v1", source_id=self.source_id,
                      world_revision=self.world_revision, question_id=question_id,
                      read_query_id=query_id, read_id=read_id, records=rows,
                      status="partial_page" if next_cursor else "complete" if rows else "empty",
                      next_cursor=next_cursor, meaning_complete=False,
                      original_loaded=True, model_delivered=False,
                      visual_verified=False, audio_listened=False)
        if context_order == "source_order":
            result.update(context_order=context_order,
                adjacency_policy="Same immutable asset/revision and ordinal only; file order does not confirm chronological adjacency, speaker, meaning or playback.")
        if len(canonical_json(result).encode("utf-8")) > max_bytes:
            raise EvidenceError("page_bytes_too_small")
        return self._save_read(result)

    def _save_read(self, result):
        read_id, query_id = result["read_id"], result["read_query_id"]
        rows, next_cursor, question_id = result["records"], result["next_cursor"], result["question_id"]
        atomic_json(self.run_root / "read_results" / (read_id + ".json"), result)
        self.state.event("read_materialized", question_id=question_id,
                         payload=dict(read_query_id=query_id, read_id=read_id, next_cursor=next_cursor,
                                      result_sha256=digest(result),
                                      fragments=[{k: r[k] for k in ("record_id", "family", "raw_char_start", "raw_char_end")} for r in rows]))
        return result

    def browse(self, *, question_id, families=None, order="chronological", cursor=0, limit=20,
               view="coverage", max_bytes=12000, interval_sec=None, interval_utc_ms=None, record_ids=None,
               report_id=None, report_revision=None, query=None):
        if view == 'question':
            if report_id is not None or report_revision is not None:
                raise EvidenceError('browse_scope_unsupported_for_view')
            from .question_view import browse_question
            return browse_question(self, question_id=question_id, families=families,
                query=query, interval_sec=interval_sec, interval_utc_ms=interval_utc_ms,
                record_ids=record_ids, cursor=cursor, max_bytes=max_bytes)
        if query is not None:
            raise EvidenceError('browse_scope_unsupported_for_view')
        if view == 'hints':
            if any(x is not None for x in (interval_sec,interval_utc_ms,record_ids)):
                raise EvidenceError('browse_scope_unsupported_for_view')
            from .summary_hints import browse_hints
            return browse_hints(self,question_id=question_id,families=families,cursor=cursor,limit=limit,
                max_bytes=max_bytes,report_id=report_id,report_revision=report_revision)
        if report_id is not None or report_revision is not None:
            raise EvidenceError('browse_scope_unsupported_for_view')
        if view == 'phrases':
            from .phrase_view import browse_phrases
            return browse_phrases(self,question_id=question_id,families=families,cursor=cursor,limit=limit,
                max_bytes=max_bytes,interval_sec=interval_sec,interval_utc_ms=interval_utc_ms,record_ids=record_ids)
        if any(x is not None for x in (interval_sec,interval_utc_ms,record_ids)):
            raise EvidenceError('browse_scope_unsupported_for_view')
        if view == "source_order":
            return self.browse_source_order(question_id=question_id, families=families,
                cursor=cursor, limit=limit, max_bytes=max_bytes)
        if view == "hierarchy":
            from .pageindex_tree import browse_hierarchy
            return browse_hierarchy(self, question_id=question_id, families=families,
                                    cursor=cursor, limit=limit, max_bytes=max_bytes)
        if view == "signals":
            from .signal_view import browse_signals
            return browse_signals(self, question_id=question_id, families=families,
                                  cursor=cursor, limit=limit, max_bytes=max_bytes)
        if view != "coverage":
            raise EvidenceError("unknown_browse_view")
        if self.duration is None:
            raise EvidenceError("source_clock_unverified_use_source_order")
        families = self._families(families or list(self.policy["required"]))
        if order not in {"chronological", "unread", "low_reaction"} or type(limit) is not int or limit < 1:
            raise EvidenceError("invalid_browse_request")
        if cursor != 0 and (not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor)):
            raise EvidenceError("unknown_browse_cursor")
        if order == "low_reaction" and ("chat" not in self.family_status or self.family_status["chat"] not in {"present", "partial"}):
            raise EvidenceError("reaction_data_unavailable")
        cells = [dict(index=i, start_sec=i * 60, end_sec=min((i + 1) * 60, self.duration),
                      families={f: dict(record_count=0, raw_delivered=False) for f in families},
                      meaning_complete=False) for i in range(math.ceil(self.duration / 60))]
        with self.open() as db:
            for family in families:
                for r in db.execute("SELECT ordinal,start_us,end_us,time_kind FROM records WHERE family=? AND start_us IS NOT NULL", (family,)):
                    lo = min(int(r[1] // 60000000), len(cells) - 1)
                    hi = min(int(max(r[1], r[2] - 1) // 60000000), len(cells) - 1)
                    for i in range(lo, hi + 1):
                        cells[i]["families"][family]["record_count"] += 1
            chat_counts = defaultdict(int)
            if order == "low_reaction":
                for row in db.execute("SELECT start_us FROM records WHERE family='chat' AND start_us IS NOT NULL"):
                    chat_counts[int(row[0] // 60000000)] += 1
                for cell in cells:
                    cell["chat_count"] = chat_counts[cell["index"]]
                    cell["reaction_collection_status"] = self.family_status["chat"]
        saved = self.state.load()
        delivered = {rid for e in saved["events"] if e["kind"] == "raw_delivered" for rid in e["payload"]["read_ids"]}
        for rid in delivered:
            result = self.load_read(rid)
            if result["question_id"] != question_id:
                continue
            for record in result["records"]:
                clock = record["time_index"]
                family = record["family"]
                if clock and family in families:
                    i = min(int(clock["start_us"] // 60000000), len(cells) - 1)

                    cells[i]["families"][family]["raw_delivered"] = True
        if order == "unread":
            cells.sort(key=lambda c: (any(f["raw_delivered"] for f in c["families"].values()), c["index"]))
        elif order == "low_reaction":
            cells.sort(key=lambda c: (c["chat_count"], c["index"]))
        browse_id = digest([self.world_revision, question_id, families, order, limit])
        browse_root = self.run_root / "browse" / browse_id
        offset = 0
        if cursor:
            path = browse_root / "cursors" / (cursor + ".json")
            if not path.is_file():
                raise EvidenceError("unknown_browse_cursor")
            issued = json.loads(path.read_text(encoding="utf-8"))
            snapshot = json.loads((browse_root / (issued["snapshot"] + ".json")).read_text(encoding="utf-8"))
            if digest(snapshot) != issued["snapshot"] or snapshot["browse_id"] != browse_id:
                raise EvidenceError("browse_snapshot_changed")
            cells, offset = snapshot["cells"], issued["offset"]
        else:
            snapshot = dict(browse_id=browse_id, cells=cells, state_revision=digest(saved))
        snapshot_id = digest(snapshot)
        atomic_json(browse_root / (snapshot_id + ".json"), snapshot)
        selected = cells[offset:offset + limit]


        selected = [dict(cell, **({"next_action": original_read_action(
            families=[f for f, data in cell["families"].items() if data["record_count"]],
            intervals_sec=[[cell["start_sec"], cell["end_sec"]]], context_cues=2)}
            if any(data["record_count"] for data in cell["families"].values()) else {})) for cell in selected]
        next_cursor = None
        if offset + len(selected) < len(cells):
            issued = dict(snapshot=snapshot_id, offset=offset + len(selected))
            next_cursor = digest(issued)
            atomic_json(browse_root / "cursors" / (next_cursor + ".json"), issued)
        return dict(schema_version="ew.browse_result.v1", source_id=self.source_id,
                    world_revision=self.world_revision, question_id=question_id,
                    duration_sec=self.duration, total_cells=len(cells), cell_seconds=60,
                    overview_bands=[dict(start_sec=self.duration * i / 12, end_sec=self.duration * (i + 1) / 12) for i in range(12)],
                    cells=selected, order=order, next_cursor=next_cursor,
                    coverage_snapshot_revision=snapshot["state_revision"],
                    status="partial_page" if next_cursor else "complete", family_status=self.family_status,
                    questions=saved["questions"], meaning_complete=False,
                    pagination_scope="optional_map_pages_not_original_reads_or_meaning")

    def browse_source_order(self, *, question_id, families=None, cursor=0, limit=20, max_bytes=12000):
        """Original addresses including unknown clocks; no inferred chronology."""
        families = self._families(families or list(self.policy["required"]))
        if (not isinstance(question_id, str) or not question_id or type(limit) is not int or
                not 1 <= limit <= 100 or type(max_bytes) is not int or not 2000 <= max_bytes <= 128000):
            raise EvidenceError("invalid_source_order_browse")
        binding = dict(world_revision=self.world_revision, question_id=question_id,
                       families=families, limit=limit, max_bytes=max_bytes)
        offset = 0
        if not (type(cursor) is int and cursor == 0):
            if not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor):
                raise EvidenceError("source_order_cursor_mismatch")
            path = self.run_root / "source-order-cursors" / (cursor + ".json")
            if not path.is_file():
                raise EvidenceError("source_order_cursor_mismatch")
            issued = json.loads(path.read_text(encoding="utf-8"))
            if issued.get("binding") != binding or digest(issued) != cursor:
                raise EvidenceError("source_order_cursor_mismatch")
            offset = issued["offset"]
        marks = ",".join("?" for _ in families)
        with self.open() as db:
            total = db.execute(f"SELECT count(*) FROM records WHERE family IN ({marks})", families).fetchone()[0]
            rows = list(db.execute(f"SELECT payload_json FROM records WHERE family IN ({marks}) "
                "ORDER BY family,asset_id,ordinal LIMIT ? OFFSET ?", [*families, limit, offset]))
        result = dict(schema_version="ew.source-order.v1", source_id=self.source_id,
            world_revision=self.world_revision, records=[], next_cursor=None, total_records=total,
            original_read=False, chronological_order_verified=False, meaning_complete=False)
        for row in rows:
            record = json.loads(row[0])
            address = {key: record[key] for key in ("record_id", "family", "ordinal", "clock_status", "raw_time")}
            address["next_action"] = original_read_action(record_ids=[record["record_id"]],
                families=[record["family"]], context_cues=2, context_order="source_order")
            trial = dict(result, records=[*result["records"], address], next_cursor="0" * 64)
            if len(canonical_json(trial).encode("utf-8")) > max_bytes:
                if not result["records"]:
                    raise EvidenceError("source_order_page_bytes_too_small")
                break
            result["records"].append(address)
        next_offset = offset + len(result["records"])
        if next_offset < total:
            issued = dict(binding=binding, offset=next_offset)
            result["next_cursor"] = digest(issued)
            atomic_json(self.run_root / "source-order-cursors" / (result["next_cursor"] + ".json"), issued)
        result["status"] = "partial_page" if result["next_cursor"] else "complete"
        return result

    def load_read(self, read_id):
        if not isinstance(read_id, str) or len(read_id) != 64 or any(c not in "0123456789abcdef" for c in read_id):
            raise EvidenceError("unknown_read_id")
        path = self.run_root / "read_results" / (read_id + ".json")
        if not path.is_file():
            raise EvidenceError("unknown_read_id")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["world_revision"] != self.world_revision or result["source_id"] != self.source_id:
            raise EvidenceError("read_revision_mismatch")

        events = [e for e in self.state.load()["events"] if e["kind"] == "read_materialized"
                  and e["payload"]["read_id"] == read_id]
        if not events:
            raise EvidenceError("read_not_materialized")
        if any(e["payload"]["result_sha256"] != digest(result) for e in events):
            raise EvidenceError("preserved_read_changed")



        for asset in self.assets.values():
            assert_asset_fresh(asset)
        return result

    def dispatch(self, envelope, *, per_query_ms=10000):
        try:
            if not isinstance(envelope, dict) or envelope.get("schema_version") != "ew.tool.v1":
                raise EvidenceError("invalid_tool_envelope")
            if envelope.get("source_id") != self.source_id:
                raise EvidenceError("wrong_source")
            if envelope.get("world_revision") != self.world_revision:
                raise EvidenceError("world_revision_mismatch")
            question_id = envelope.get("question_id")
            if not isinstance(question_id, str) or not question_id.strip():
                raise EvidenceError("question_id_required")
            args = envelope.get("args", {})
            if not isinstance(args, dict):
                raise EvidenceError("invalid_tool_args")
            name = envelope.get("action")
            if name == "search_evidence":
                return self.search(question_id=question_id, per_query_ms=per_query_ms, **args)
            if name == "read_evidence":
                return self.read(question_id=question_id, **args)
            if name == "browse_evidence":
                return self.browse(question_id=question_id, **args)
            if name == "browse_chat":
                from .chat_view import browse_chat
                return browse_chat(self, question_id=question_id, **args)
            if name == "resolve_evidence_question":
                return dict(status="complete", question=self.state.resolve(question_id=question_id, **args))
            raise EvidenceError("unsupported_action")
        except (EvidenceError, TypeError) as exc:
            code = str(exc) if isinstance(exc, EvidenceError) else "invalid_tool_args"
            return dict(schema_version="ew.error.v1", status="error", source_id=self.source_id,
                        world_revision=self.world_revision, error_code=code, hits=[], records=[],
                        next_cursor=None, retry_only=code == "query_budget_exhausted",
                        meaning_complete=False)
