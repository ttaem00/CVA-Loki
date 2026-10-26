"""Source-bound native cue timing for the existing Highlight author.

Legacy chunk clocks remain historical observations. A verified transformation
receipt can link one original occurrence to one old canonical source identity;
time alone never supplies this link. Cue intervals are not mandatory edit cuts.
"""

from __future__ import annotations

from collections import defaultdict

from .evidence_world.contracts import SourcePolicy, digest, native_path

from .evidence_world.records import register_assets, iter_srt_records

CONTRACT = "editorial.native-cue-timing.v1"

def bound_live_source_clock(source):
    """Keep the canonical live clock on native author reads, never a VOD alias."""
    asset = source["asset"]
    authority = asset.get("clock_authority") or {}
    if asset["source_locator"].get("kind") == "temporal_projection":
        return None
    from .live_editorial_source import clock_summary, load_live_source_clock
    current = clock_summary(load_live_source_clock(asset["source_locator"]["path"],
        video_no=asset["video_no"], duration_sec=authority.get("duration_sec", authority.get("duration_sec_decimal"))))
    if current is None and authority.get("kind") != "live":
        return None
    if current is None or any(authority.get(key) != value for key, value in current.items()):
        raise ValueError("native_live_source_clock_changed")
    return current

def prepare_source_timing(*, video_no, duration_sec, work_dir, chunks, srt_path=None):
    from .broadcast_map_evidence import _canonical_stt_rows, _canonical_source_revision
    from .timed_evidence import build_timeline_source_evidence

    root = native_path(work_dir).resolve()
    if srt_path is None:
        candidates = sorted(root.glob("*complete.srt")) or sorted(root.glob("*.srt"))
        if len(candidates) != 1:
            raise ValueError("highlight_requires_unambiguous_native_transcript")
        srt_path = candidates[0]
    transcript = native_path(srt_path).resolve()
    if not transcript.is_relative_to(root) or not transcript.is_file():
        raise ValueError("highlight_prepared_transcript_invalid")
    asset, = register_assets(video_no=video_no, srt_path=transcript, work_dir=root,
        duration_sec=duration_sec, policy=SourcePolicy(("stt",), ("stt",)))
    records = list(iter_srt_records(asset))
    legacy_rows = _canonical_stt_rows(chunks)
    revision = _canonical_source_revision("stt", legacy_rows)
    occurrences = defaultdict(list)
    for record in records:
        raw_time = record.get("raw_time") or {}
        if raw_time.get("unit") != "ms" or record.get("raw_text") is None:
            continue


        text = " ".join(line.strip() for line in record["raw_text"].splitlines() if line.strip())
        occurrences[(int(raw_time["start"]) // 1000, text)].append(record)
    if any(not occurrences[(row["start_sec"], row["text"])] for row in legacy_rows):
        raise ValueError("native_source_chunk_mismatch")
    links = {}
    unresolved = []
    for row in legacy_rows:
        matches = occurrences[(row["start_sec"], row["text"])]
        manifest = build_timeline_source_evidence(video_id=str(video_no), duration_sec=None,
                                                  subtitle_cues=[row])
        item = next(iter(manifest.get("items") or []), None)
        if not item:
            continue
        exact = f"stt:{revision[:16]}:{item['source_identity']}"
        if len(matches) != 1:
            unresolved.append({"exact_source_ref": exact, "state": "ambiguous" if matches else "unmatched"})
            continue
        links[exact] = matches[0]["record_id"]
    body = dict(contract=CONTRACT, asset=asset.internal(), records=records,
                legacy_source_revision=revision, verified_legacy_links=links,
                unresolved_legacy_links=unresolved,
                transform="chunker.cues_to_txt + canonical_stt_rows + timed_evidence; unique occurrence only")
    return {**body, "sha256": digest(body)}

def native_author_rows(source):
    rows = []
    for record in source["records"]:
        clock = record.get("time_index")
        if not clock or record.get("raw_text") is None:
            continue
        rows.append(dict(ref="stt-occ:" + record["record_id"][:24],
                         time_sec=clock["start_us"] / 1_000_000,
                         end_sec=clock["end_us"] / 1_000_000,
                         text=record["raw_text"], ordinal=record["ordinal"]))
    return rows

def validate_native_reference_intersections(*, refs, spans, catalog):
    """Reject proven disjoint references; overlapping cues are only partial proof.

    This verifies observed time compatibility, not semantic completeness or
    audibility. It never moves a boundary to satisfy a reference.
    """
    for ref in refs:
        row = catalog.get(ref) or {}
        if "native_start_sec" not in row or "native_end_sec" not in row:
            if row.get("source_type") == "stt":
                raise ValueError("native_evidence_occurrence_unresolved")
            continue
        start, end = row["native_start_sec"], row["native_end_sec"]
        if not any(float(span["start_sec"]) < end and start < float(span["end_sec"]) for span in spans):
            raise ValueError("native_evidence_outside_assigned_source_span")
