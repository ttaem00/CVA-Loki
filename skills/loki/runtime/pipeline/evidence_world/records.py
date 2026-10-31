"""Original-order SRT/chat records and source-file rereads.

The old SRT parser remains unchanged. Its sorted positions are aliases only
when the original clock/text signature has exactly one occurrence.
"""

from __future__ import annotations

from collections import defaultdict

import hashlib

import json

import math

import re

from ..chunker import TIME_LINE_RE, _ts_to_ms, parse_srt

from .contracts import (AssetBinding, EvidenceError, PARSER_REVISION, RECORD_SCHEMA, canonical_json,
                        digest, file_hash, native_path, time_projection)

def register_assets(*, video_no, srt_path, work_dir, duration_sec, policy, clip_source_path=None,
                    chat_source_path=None):
    """Only caller-bound paths; never walk a directory or accept model paths."""
    source_id = f"chzzk:{video_no}"
    root = native_path(work_dir)
    from ..live_editorial_source import load_live_source_clock, clock_summary, source_clock_path
    live_source = load_live_source_clock(srt_path, video_no=video_no, duration_sec=duration_sec)
    live_clock = clock_summary(live_source)
    if live_clock:
        source_id = live_clock["source_id"]
    paths = {"stt": native_path(srt_path), "chat": root / f"{video_no}_chat.log.json"}
    if chat_source_path is not None:
        paths['chat'] = native_path(chat_source_path)
    from .derived_records import PATHS, REVISION, collection_state
    paths.update({family: root / rel for family, rel in PATHS.items()})
    if clip_source_path is not None:
        paths['clip'] = native_path(clip_source_path)
    bindings = []
    for family in policy.allowed_families:
        path = paths.get(family)
        status, collection, meta_hash = "unsupported", {}, None
        locator = {}
        if path is not None:
            locator["path"] = str(path.resolve())
            status = "present" if path.is_file() else "not_collected"
        from ..live_vod_chat import is_bound_chat
        if family == 'chat' and status == 'present' and chat_source_path is not None and is_bound_chat(path):
            from ..live_vod_chat import descriptor_status, KIND
            collection = descriptor_status(path)
            identity = json.loads(path.read_text(encoding='utf8'))
            locator.update(kind=KIND, archive=identity['archive'],
                           producer_source_id=identity['producer_source_id'])
        elif family == "chat" and status == "present":
            meta_path = path.with_suffix('.meta.json')
            locator["meta_path"] = str(meta_path.resolve())
            if meta_path.is_file():
                meta_hash = file_hash(meta_path)
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
                    collection = {k: meta[k] for k in ("status", "video_no") if k in meta}
                    if "video_no" in meta and str(meta["video_no"]) != str(video_no):
                        status = "conflict"
                    elif meta.get("status") != "complete":
                        status = "partial"
                except (ValueError, OSError, TypeError):
                    status = "collection_failed"
            else:
                status = "partial"
            locator["meta_sha256"] = meta_hash
        if live_clock and family == "stt":
            locator["meta_path"] = str(source_clock_path(srt_path).resolve())
            meta_hash = file_hash(source_clock_path(srt_path))
            locator["meta_sha256"] = meta_hash
            status, collection = "partial", live_clock
        elif live_clock and path is not None and path.is_file():
            raise EvidenceError("live_source_mixed_clock_support_rejected")
        if family in PATHS and status == "present":
            status, collection = collection_state(path, family=family, video_no=video_no)
        if family == "audio_features" and not live_clock:
            from .derived_records import bind_audio_events
            event, event_state = bind_audio_events(root, srt_path=srt_path, duration_sec=duration_sec)
            collection["audio_events"] = event_state
            if event:
                locator.update(audio_event_path=event["path"], audio_event_sha256=event["sha256"],
                    audio_event_timing_path=event["timing_path"], audio_event_timing_sha256=event["timing_sha256"])
                meta_hash = digest(event)
                if status in {'not_collected', 'collection_failed', 'conflict'}:
                    collection['primary_status'] = status
                    path = native_path(event["path"])
                    locator.update(path=str(path), kind="ast_audio_only")
                    status = "partial"
        asset_key = family + ":primary"
        sha = file_hash(path) if path is not None and path.is_file() else None
        clock = {"source_id": source_id, "duration_sec_decimal": str(duration_sec),
                 "authority": "caller_declared_source_clock"}
        if live_clock:
            clock.update(live_clock)
        parser_revision = REVISION if family in PATHS else PARSER_REVISION
        asset_id = digest([source_id, family, asset_key, sha, parser_revision, meta_hash])
        bindings.append(AssetBinding(
            asset_id, source_id, str(video_no), family, asset_key, locator, sha,
            parser_revision, status, collection, clock, digest(policy.payload())))
    return bindings

def assert_asset_fresh(asset):
    path = asset.source_locator.get("path")
    actual = file_hash(path) if path and native_path(path).is_file() else None
    if actual != asset.bytes_sha256:
        raise EvidenceError("source_revision_changed")
    if asset.source_locator.get('kind') == 'verified_live_vod_chat':
        from ..live_vod_chat import descriptor_status
        descriptor_status(path)
    meta_path = asset.source_locator.get("meta_path")
    if meta_path:
        actual = file_hash(meta_path) if native_path(meta_path).is_file() else None
        if actual != asset.source_locator.get("meta_sha256"):
            raise EvidenceError("source_collection_revision_changed")
    for key, sha_key in (("audio_event_path", "audio_event_sha256"),
                         ("audio_event_timing_path", "audio_event_timing_sha256")):
        sidecar = asset.source_locator.get(key)
        if sidecar:
            actual = file_hash(sidecar) if native_path(sidecar).is_file() else None
            if actual != asset.source_locator[sha_key]:
                raise EvidenceError("source_audio_event_revision_changed")

def _byte_blocks(raw):
    """End-exclusive original byte locations, without newline normalization."""
    start, ordinal = 0, 0
    for match in re.finditer(rb"\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*", raw):
        if match.start() > start:
            yield ordinal, start, match.start(), raw[start:match.start()]
            ordinal += 1
        start = match.end()
    if start < len(raw):
        yield ordinal, start, len(raw), raw[start:]

def _record(asset, ordinal, locator, raw_hash, raw_text, raw_time,
            time_index, clock_status, *, payload, legacy_pointer=None, warnings=()):
    return dict(
        schema_version=RECORD_SCHEMA,
        record_id=digest([asset.asset_id, locator, raw_hash]),
        occurrence_key=digest([asset.source_id, asset.family, asset.asset_key,
                               locator["kind"], ordinal]),
        asset_id=asset.asset_id, source_id=asset.source_id, video_no=asset.video_no,
        family=asset.family, ordinal=ordinal, native_locator=locator,
        legacy_pointer=legacy_pointer, raw_time=raw_time, time_index=time_index,
        clock_status=clock_status, raw_text=raw_text, search_text=raw_text,
        raw_payload_sha256=raw_hash, payload=payload, derived=False,
        transform={"producer": "records." + asset.family, "revision": PARSER_REVISION,
                   "model": None},
        parent_record_id=None, time_hints=[], uncertainties=list(warnings),
        playable=time_index is not None and asset.clock_authority.get("kind") != "live")

def iter_srt_records(asset):
    assert_asset_fresh(asset)
    path = native_path(asset.source_locator["path"])
    raw = path.read_bytes()
    from ..live_editorial_source import load_live_source_clock
    live_source = load_live_source_clock(path, video_no=asset.video_no,
        duration_sec=float(asset.clock_authority["duration_sec_decimal"]))
    aliases = defaultdict(list)
    for index, cue in enumerate(parse_srt(str(path))):
        aliases[(cue.start_ts, cue.end_ts, tuple(cue.text_lines))].append(f"stt:{index}")
    duration = asset.clock_authority["duration_sec_decimal"]
    for ordinal, begin, end, block in _byte_blocks(raw):
        locator = dict(kind="srt_block", block_ordinal=ordinal, byte_span=[begin, end],
                       cue_label=None)
        sha = hashlib.sha256(block).hexdigest()
        if live_source:
            locator["original_live_occurrence"] = live_source["origins"][ordinal]
        try:
            text = block.decode("utf-8-sig")
        except UnicodeDecodeError:
            yield _record(asset, ordinal, locator, sha, None, None, None, "unknown",
                          payload={"raw_bytes_hex": block.hex()},
                          warnings=["utf8_decode_failed", "text_search_unsupported"])
            continue
        lines = text.splitlines()
        time_i = next((i for i, line in enumerate(lines[:6]) if TIME_LINE_RE.match(line)), None)
        if time_i is None:
            yield _record(asset, ordinal, locator, sha, None, None, None, "unknown",
                          payload={"raw_block": text}, warnings=["srt_time_line_missing"])
            continue
        match = TIME_LINE_RE.match(lines[time_i])
        start_ts, end_ts = match.groups()
        locator["cue_label"] = lines[0] if time_i else None
        start_ms, end_ms = _ts_to_ms(start_ts), _ts_to_ms(end_ts)
        times, status = time_projection(start_ms, end_ms, unit="ms", duration_sec=duration)
        possible = aliases[(start_ts, end_ts, tuple(lines[time_i + 1:]))]
        alias = possible[0] if len(possible) == 1 else None
        warnings = [] if alias else ["legacy_alias_ambiguous"]
        if status == "conflict":
            warnings.append("source_clock_conflict_not_repaired")
        yield _record(
            asset, ordinal, locator, sha, "\n".join(lines[time_i + 1:]),
            dict(unit="ms", start=str(start_ms), end=str(end_ms),
                 original_time_line=lines[time_i]), times, status,
            payload={"raw_block": text}, legacy_pointer=alias, warnings=warnings)

def _chat_rows(asset):
    payload = json.loads(native_path(asset.source_locator["path"]).read_text(encoding="utf-8-sig"))
    rows = payload if isinstance(payload, list) else payload.get("chats") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise EvidenceError("chat_rows_unavailable")
    return rows

def iter_chat_records(asset):
    assert_asset_fresh(asset)
    duration = asset.clock_authority["duration_sec_decimal"]
    if asset.source_locator.get('kind') == 'verified_live_vod_chat':
        from ..live_vod_chat import iter_bound_chat
        for ordinal, (native, row) in enumerate(iter_bound_chat(asset.source_locator['path'])):
            locator = dict(kind='live_vod_chat_row', primary_key=native['message_pk'],
                archive=asset.source_locator['archive'],
                producer_source_id=asset.source_locator['producer_source_id'],
                observations=native['observations'])
            timing, status = time_projection(row['ms'], row['ms'], unit='ms',
                duration_sec=duration, instant=True)
            yield _record(asset, ordinal, locator, digest(native), row['msg'],
                dict(unit='ms', start=row['ms'], end=row['ms'], original_utc_ms=native['sent_at_ms']),
                timing, status, payload={k:row[k] for k in ('ms', 'msg', 'event_kind', 'chat_evidence') if k in row},
                warnings=[] if timing else ['native_replay_clock_unresolved'])
        return
    for ordinal, row in enumerate(_chat_rows(asset)):
        locator = dict(kind="chat_row", row_ordinal=ordinal)
        sha = digest(row)
        if not isinstance(row, dict):
            yield _record(asset, ordinal, locator, sha, None, None, None, "unknown",
                          payload={"row_type": type(row).__name__},
                          warnings=["unsupported_chat_row"])
            continue
        time, status = time_projection(row.get("ms"), row.get("ms"), unit="ms",
                                       duration_sec=duration, instant=True)
        text = row.get("msg") if isinstance(row.get("msg"), str) else None
        yield _record(asset, ordinal, locator, sha, text,
                      dict(unit="ms", start=row.get("ms"), end=row.get("ms")),
                      time, status, payload={k: row[k] for k in ("ms", "msg") if k in row},
                      legacy_pointer=f"chat:{ordinal}",
                      warnings=[] if time else ["source_clock_conflict_not_repaired"])

def iter_records(asset):
    if asset.availability not in {"present", "partial"}:
        return
    if asset.source_locator.get("kind") == "temporal_projection":
        from .temporal_source import iter_temporal_records
        yield from iter_temporal_records(asset)
        return
    if asset.family == "stt":
        yield from iter_srt_records(asset)
    elif asset.family == "chat":
        yield from iter_chat_records(asset)
    elif asset.family in {"audio_features", "clip"}:
        from .derived_records import iter_derived_records
        yield from iter_derived_records(asset)

def read_native(asset, record, *, _verified=False, _chat_data=None, _derived_data=None):
    """Reopen the source, verify this occurrence, and return raw (not FTS) text."""
    if not _verified:
        assert_asset_fresh(asset)
    if record["asset_id"] != asset.asset_id:
        raise EvidenceError("record_asset_mismatch")
    locator = record["native_locator"]
    if locator['kind'] == 'live_vod_chat_row':
        from ..live_vod_chat import read_bound_chat
        rows = _derived_data if _derived_data is not None else read_bound_chat(
            asset.source_locator['path'], [locator['primary_key']])
        row = rows.get(locator['primary_key'])
        if row is None or digest(row) != record['raw_payload_sha256']:
            raise EvidenceError('raw_payload_changed')
        return dict(raw_text=row['text'], raw_payload=record['payload'],
                    bytes_read=len(canonical_json(record['payload']).encode('utf8')))
    if locator["kind"] == "temporal_row":
        from .temporal_source import read_temporal
        data = _derived_data if _derived_data is not None else json.loads(
            native_path(asset.source_locator["path"]).read_text(encoding="utf-8"))
        return read_temporal(asset, record, data)
    if locator["kind"] == "derived_json_row":
        from .derived_records import load_asset_data, read_derived
        return read_derived(asset, record, _derived_data if _derived_data is not None else load_asset_data(asset))
    if locator["kind"] == "srt_block":
        begin, end = locator["byte_span"]
        with native_path(asset.source_locator["path"]).open("rb") as stream:
            stream.seek(begin)
            raw = stream.read(end - begin)
        if hashlib.sha256(raw).hexdigest() != record["raw_payload_sha256"]:
            raise EvidenceError("raw_payload_changed")
        text = raw.decode("utf-8-sig", errors="strict") if record["raw_text"] is not None else None
        if text is not None:
            lines = text.splitlines()
            time_i = next(i for i, line in enumerate(lines[:6]) if TIME_LINE_RE.match(line))
            text = "\n".join(lines[time_i + 1:])
        return dict(raw_text=text, raw_payload={"raw_block_hex": raw.hex()} if text is None
                    else {"raw_block": raw.decode("utf-8-sig")}, bytes_read=len(raw))
    if locator["kind"] == "chat_row":
        rows = _chat_data if _chat_data is not None else _chat_rows(asset)
        row = rows[locator["row_ordinal"]]
        if digest(row) != record["raw_payload_sha256"]:
            raise EvidenceError("raw_payload_changed")
        return dict(raw_text=row.get("msg") if isinstance(row, dict) else None,
                    raw_payload={k: row[k] for k in ("ms", "msg") if k in row}
                    if isinstance(row, dict) else {},
                    bytes_read=len(str(row).encode("utf-8")))
    raise EvidenceError("unsupported_native_locator")

def read_records(asset, records):
    """Verify and parse once per asset/request; preserve each occurrence."""
    assert_asset_fresh(asset)
    temporal = asset.source_locator.get("kind") == "temporal_projection"
    live_chat = asset.source_locator.get('kind') == 'verified_live_vod_chat'
    chat_data = _chat_rows(asset) if asset.family == "chat" and not temporal and not live_chat else None
    derived_data = None
    if live_chat:
        from ..live_vod_chat import read_bound_chat
        derived_data = read_bound_chat(asset.source_locator['path'],
            [record['native_locator']['primary_key'] for record in records])
    if temporal:
        derived_data = json.loads(native_path(asset.source_locator["path"]).read_text(encoding="utf-8"))
    if asset.family in {"audio_features", "clip"}:
        from .derived_records import load_asset_data
        derived_data = load_asset_data(asset)
    result = {record["record_id"]: read_native(asset, record, _verified=True,
                                               _chat_data=chat_data, _derived_data=derived_data) for record in records}
    assert_asset_fresh(asset)
    return result

def utc_ranges(service, ranges):
    """UTC is an original clock, never an inferred VOD seek clock."""
    if service.duration is not None:
        raise EvidenceError("source_clock_domain_mismatch")
    if (not isinstance(ranges, list) or not ranges or any(
            not isinstance(r, list) or len(r) != 2 or
            any(type(t) is not int or t < 0 for t in r) or r[0] >= r[1]
            for r in ranges)):
        raise EvidenceError("invalid_utc_interval")
    return ranges

def select_utc_records(db, families, ranges):
    """Query the preserved native clock without migrating immutable worlds."""
    records = {}
    for family in families:
        for start, end in ranges:
            rows = db.execute("SELECT payload_json FROM records WHERE family=? "
                "AND json_extract(payload_json,'$.raw_time.unit')='utc_epoch_ms' "
                "AND ((json_extract(payload_json,'$.raw_time.start')>=? "
                "AND json_extract(payload_json,'$.raw_time.start')<?) OR "
                "(json_extract(payload_json,'$.raw_time.start')<? "
                "AND json_extract(payload_json,'$.raw_time.end')>?)) ORDER BY ordinal",
                (family, start, end, end, start))
            for row in rows:
                record = json.loads(row[0])
                records[record['record_id']] = record
    return list(records.values())

def chat_analysis_rows(service, records=None):
    """One records contract for signals and wording; private identities stay local.

    Existing VOD sampler identities are reopened only in this original-reader
    layer. Temporal projections intentionally have no user identity. Unlocated
    VOD rows are not assigned a timestamp; UTC stays in its native millisecond
    domain. Returning analysis rows is not an original-read completion claim.
    """
    assets = {a.asset_id: a for a in service.assets.values() if a.family == 'chat'}
    for asset in assets.values():
        assert_asset_fresh(asset)
    legacy = {}
    with service.open() as db:
        source = records if records is not None else (



            json.loads(row[0]) for asset_id in sorted(assets) for row in db.execute(
                "SELECT payload_json FROM records WHERE asset_id=? AND family='chat' ORDER BY ordinal",
                (asset_id,)))
        for record in source:
            if record['family'] != 'chat' or record['asset_id'] not in assets:
                raise EvidenceError('record_asset_mismatch')
            if not isinstance(record['raw_text'], str):
                continue
            clock = record['time_index']
            raw_time = record.get('raw_time') or {}
            if clock and service.duration is not None:
                ms, domain = clock['start_us'] / 1000, 'vod_relative_ms'
            elif (service.duration is None and raw_time.get('unit') == 'utc_epoch_ms'
                  and type(raw_time.get('start')) is int and raw_time['start'] >= 0):
                ms, domain = raw_time['start'], 'utc_epoch_ms'
            else:
                continue
            if not math.isfinite(ms):
                continue
            row = {}
            if record['native_locator']['kind'] == 'live_vod_chat_row':
                aid = record['asset_id']
                if aid not in legacy:
                    from ..live_vod_chat import load_bound_chat
                    legacy[aid] = {r['source_message_pk']:r for r in load_bound_chat(assets[aid].source_locator['path'])}
                row = dict(legacy[aid][record['native_locator']['primary_key']])
            elif record['native_locator']['kind'] == 'chat_row':
                aid = record['asset_id']
                if aid not in legacy:
                    legacy[aid] = _chat_rows(assets[aid])
                original = legacy[aid][record['native_locator']['row_ordinal']]
                if digest(original) != record['raw_payload_sha256']:
                    raise EvidenceError('raw_payload_changed')
                row = dict(original)
            else:

                row['event_kind'] = record['payload'].get('event_kind')
            yield dict(row, ms=ms, msg=record['raw_text'], record_id=record['record_id'],
                       clock_domain=domain)
    for asset in assets.values():
        assert_asset_fresh(asset)
