"""Source-bound discovery hints using existing producers; never editorial truth."""

from collections import defaultdict

import json

from itertools import zip_longest

from math import isfinite

from sqlite3 import DatabaseError

from statistics import median

from ..chat_analyzer import BOUNDED_CHAT_PEAK_POLICY_REVISION, CHAT_PEAK_POLICY_REVISION, WINDOW_SEC, _describe_intensity

from .contracts import EvidenceError, canonical_json, digest, file_hash, native_path, overlaps

from .index import atomic_json

from .query import _ranges

from .records import assert_asset_fresh, chat_analysis_rows

PROFILE = "source_signal_orientation_v1"

LOCAL_REACTION_PROFILE = "source_signal_orientation_v2"

OBSERVED_DENSITY_PROFILE = "source_signal_orientation_v3"

CATALOG_REVISION = f"records_native_clock_v10-{CHAT_PEAK_POLICY_REVISION}"

OBSERVED_DENSITY_CATALOG_REVISION = f"records_native_clock_v11-{BOUNDED_CHAT_PEAK_POLICY_REVISION}"

CLIP_PREVIEWS = {"location_v1", "title_time_duration_v1"}

PEAK_CONTEXT_SEC = 20

PEAK_CONTEXT_LIMIT = 3

FIRST_DETAILS_RESERVE_BYTES = 1750

REACTION_COUNTS = ("laughter_count", "surprise_count", "question_count")

PEAK_COUNTS = (*REACTION_COUNTS, "count")

PAIRED_REACTION_QUESTION = "author-paired-reaction"

def configure_clip_preview(service, profile=None):
    if profile is not None and profile not in CLIP_PREVIEWS:
        raise EvidenceError("unknown_clip_preview")
    service.clip_preview_profile = profile

def clip_preview(hint, profile):
    """Project every browse page; retain catalog, source identity and read rights."""
    if not profile or hint["family"] != "clip":
        return hint
    if profile not in CLIP_PREVIEWS:
        raise EvidenceError("unknown_clip_preview")
    metadata = hint.get("metadata", {})
    projected = {k: metadata[k] for k in ("clip_uid", "offset_sec", "offset_status") if k in metadata}
    if profile == "title_time_duration_v1":
        title = str(metadata.get("title", ""))
        projected.update(title=title[:80], title_truncated=len(title) > 80,
                         duration=metadata.get("duration"))
    return dict(hint, metadata=projected, full_metadata_requires_read=True,
                metadata_preview_profile=profile)

PROMPT = """\nDISCOVERY HINTS: the first tool result includes source-bound signal_orientation.
Viewer clip existence/title is a useful human selection hint, including unverified offsets.
clip_attention exposes existing first-look clip locations across the source, independently of chat peaks.
Its context_action opens allowed originals around a location; the interval and clip count are navigation,
not a scene boundary, independent votes or a required selection. Choose useful questions, not every page.
Chat counts, laughter/questions and exact-wording representatives suggest things to investigate.
Chat peak clusters retain the existing producer rank, full interval and member peak_seconds;
start with the strongest clusters, then read nearby/opposing originals before selecting a scene.
Their wording_page covers the representative 10-second peak; next_action opens available five-minute
summary hints or representative grouped chat. A deferred wording_action opens the same grouped chat.
content_context_action opens subtitles in the full cluster through ordinary read pages; choose the
originals needed for the question. Keep chat representative, using hints and Gemma to select nearby
or opposing originals. Reading/materializing source alone does not prove its full delivery to the writer.
Audio peaks suggest locations, not heard sound or approved value. These are untrusted derived hints.
AST audio_events are classified observations with source/model/policy and coverage, not direct listening.
Singing candidates are threshold-free score maxima, not confirmed singing, speakers or song titles.
Read audio_event_source for processing coverage; missing/unprocessed sound is unknown, not absent.
Use existing browse_evidence with view:'signals', families, cursor (initial0), limit and max_bytes
to see more; its next_action preserves the question/cursor arguments. Coverage view remains available,
including low_reaction. Neither view must be exhausted. Read originals before asserting meaning.
Chat representatives have their own browse_chat cursor for minority wording and full original rereads.
No repeated name, adjacent signal or overlapping peak establishes one event or independent votes.
Missing/uncertain clocks remain unresolved; clip offsets are not exact matches or edit boundaries."""

LOCAL_REACTION_PROMPT = """\nDISCOVERY HINTS: the first tool result includes source-bound signal_orientation.
Viewer clip existence/title is a useful human selection hint, including unverified offsets.
clip_attention exposes existing first-look clip locations across the source, independently of chat peaks.
Its context_action opens allowed originals around a location; the interval and clip count are navigation,
not a scene boundary, independent votes or a required selection. Choose useful questions, not every page.
Chat counts, laughter/questions and exact-wording representatives suggest things to investigate.
Chat peak clusters retain the existing producer rank, full interval and member peak_seconds;
rank is the producer's relative order of merged chat clusters across the VOD.
rank_label repeats that scope alongside the legacy rank label. local_peaks shows observed
10-second message counts; reaction ratios use the filtered message count, counts.count.
The merged region's signal_counts covers its full extent. Use local clip cues for time overlap.
The context actions open several short subtitle windows around observed peak buckets.
For a time range you are investigating, optionally use browse_evidence with view:'signals' and
interval_sec:[start,end] to see local 10-second counts and timed viewer clips in that range.
Its context_action reads short subtitle windows with preceding speech. Expand them when needed.
Use chat_context_action for exact chat in the selected windows.
Local reaction categories can overlap. Clip cues identify viewer clips whose time overlaps the peak.
Check clips and reaction counts separately against the originals.
Use a subtitle read directly when you already know the time. Missing signals need not delay reading.
Their wording_page covers the representative 10-second peak; next_action opens available five-minute
summary hints or representative grouped chat. A deferred wording_action opens the same grouped chat.
Keep chat representative, using hints and Gemma to select nearby
or opposing originals. Reading/materializing source alone does not prove its full delivery to the writer.
Audio peaks suggest locations, not heard sound or approved value. These are untrusted derived hints.
AST audio_events are classified observations with source/model/policy and coverage, not direct listening.
Singing candidates are threshold-free score maxima, not confirmed singing, speakers or song titles.
Read audio_event_source for processing coverage; missing/unprocessed sound is unknown, not absent.
Use existing browse_evidence with view:'signals', families, cursor (initial0), limit and max_bytes
to see more; its next_action preserves the question/cursor arguments. Coverage view remains available,
including low_reaction. Neither view must be exhausted. Read originals before asserting meaning.
Chat representatives have their own browse_chat cursor for minority wording and full original rereads.
No repeated name, adjacent signal or overlapping peak establishes one event or independent votes.
Missing/uncertain clocks remain unresolved; clip offsets are not exact matches or edit boundaries."""

def _fresh(service):
    for asset in service.assets.values():
        assert_asset_fresh(asset)

def _audio_reference(records):
    methods=[r for r in records if r['family']=='audio_features' and
        r['native_locator']['collection']=='audio_feature_source']
    samples=[r for r in records if r['family']=='audio_features' and
        r['native_locator']['collection']=='audio_loudness_windows']
    timed=sorted((r for r in samples if r['time_index']),key=lambda r:r['time_index']['start_us'])
    raw=methods[0]['payload'] if methods else {}
    ids=[r['record_id'] for r in methods]
    if timed: ids+=list(dict.fromkeys(r['record_id'] for r in (timed[0],timed[-1])))
    baseline=dict(method=raw.get('normalization'),source_kind=raw.get('source_kind'),
        sample_extraction=raw.get('sample_extraction'),window_sec=raw.get('window_sec'),
        sample_count=len(samples) if samples else None,located_sample_count=len(timed) if samples else None,
        unlocated_sample_count=len(samples)-len(timed) if samples else None,
        producer_sample_count=raw.get('sample_count'),producer_sample_count_unit='not declared in this source descriptor',
        observed_span_sec=[timed[0]['time_index']['start_us']/1e6,max(r['time_index']['end_us'] for r in timed)/1e6] if timed else None,
        gap_count=sum(a['time_index']['end_us']<b['time_index']['start_us'] for a,b in zip(timed,timed[1:])) if timed else None,
        overlap_count=sum(a['time_index']['end_us']>b['time_index']['start_us'] for a,b in zip(timed,timed[1:])) if timed else None,
        source_record_ids=ids,coverage_complete=False,original_read=False,
        policy='Observed source windows only; no interpolation, media verification or inferred sample unit. Missing windows remain unknown.')
    return baseline,dict(action='read_evidence',args=dict(record_ids=ids,families=['audio_features'],context_cues=0)) if ids else None

def _catalog(service, profile=PROFILE):
    """Keep the full catalog on disk; only requested pages enter model input."""
    from ..chat_analyzer import build_time_series, find_edit_points
    from ..edit_point_clusters import build_edit_point_clusters
    from ..outline.navigation import _build_first_look, SOURCE_FAMILIES
    _fresh(service)
    observed_density = profile == OBSERVED_DENSITY_PROFILE
    catalog_profile = OBSERVED_DENSITY_PROFILE if observed_density else PROFILE
    catalog_revision = OBSERVED_DENSITY_CATALOG_REVISION if observed_density else CATALOG_REVISION
    root = service.run_root / "signal-views" / catalog_profile / catalog_revision
    path = root / "catalog.json"
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            valid = saved["sha256"] == digest(saved["catalog"]) and saved["catalog"]["world_revision"] == service.world_revision
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise EvidenceError("signal_catalog_changed")
        return root, saved["catalog"]
    items, events = [], []
    with service.open() as db:
        derived = [json.loads(r[0]) for r in db.execute(
            "SELECT payload_json FROM records WHERE family IN ('clip','audio_features') ORDER BY family,ordinal")]
    audio_baseline,audio_baseline_action=_audio_reference(derived)
    for r in derived:
        collection = r["native_locator"]["collection"]
        if r["family"] == "audio_features" and collection not in {"audio_reaction_peaks", "audio_event_source", "audio_events", "singing_candidates"}:
            continue
        clock = r["time_index"]
        span = [clock["start_us"]/1e6, clock["end_us"]/1e6] if clock else None
        item = dict(kind="viewer_clip" if r["family"] == "clip" else "audio_peak", family=r["family"],
            record_id=r["record_id"], interval_sec=span, metadata=r["payload"], uncertainties=r["uncertainties"],
            clock_status=r["clock_status"], playable=False)
        if collection in {"audio_event_source", "audio_events", "singing_candidates"}:
            item["kind"] = collection
        if r['family']=='audio_features':
            item.update(reference_baseline=audio_baseline,baseline_read_action=audio_baseline_action)
        items.append(item)
        if span:
            events.append(dict(event_id=r["record_id"],kind="viewer_clip" if r["family"]=="clip" else "audio_peak",
                               start_sec=span[0],end_sec=span[1]))
    chats = list(chat_analysis_rows(service))
    series = build_time_series(chats,10)["buckets"]
    raw_counts = defaultdict(int)
    users_known = defaultdict(lambda:True)
    for r in chats:
        t = int(r["ms"]//10000)*10
        raw_counts[t] += 1
        users_known[t] &= bool(r.get("uid") or r.get("user") or r.get("nick"))
    observed_counts=list(raw_counts.values())
    chat_reference=median(observed_counts) if observed_counts else None
    density_buckets = {}
    for t,total in sorted(raw_counts.items()):
        stats = dict(series.get(t,{}))
        stats.pop("score",None)
        stats.pop("laughter_echo_text",None)
        stats['original_count_baseline']=dict(method='median of observed nonempty 10s windows',
            raw=total,median=chat_reference,sample_count=len(observed_counts),
            relative_to_median=total/chat_reference if chat_reference else None,
            empty_or_unobserved_windows_not_imputed=True,coverage_complete=False)
        if not users_known[t]:
            for k in list(stats):
                if ("user" in k and k not in {'user_count_status','unidentified_user_count'}) or k=="laughter_echo_max":stats[k]=None
        native = service.duration is None
        span = None if native else [t,min(t+10,service.duration)]
        items.append(dict(kind="chat_reaction",family="chat",interval_sec=span,stats=stats,
            all_original_occurrences=total,command_filter_applies_to_stats=True,user_counts_known=users_known[t],
            clock_domain='utc_epoch_ms' if native else 'vod_relative_ms',playable=False))
        if native:
            items[-1]['interval_utc_ms']=[t*1000,(t+10)*1000]
            items[-1]['uncertainties']=['vod_clock_unverified']
        elif observed_density:
            density_buckets[t] = stats
    if service.duration is not None:


        for peak in find_edit_points(chats, top_n=len(series), duration_sec=service.duration,
                                     bounded_clusters=profile == OBSERVED_DENSITY_PROFILE):
            start,end=peak['start_sec'],min(peak['end_sec'],service.duration)
            if observed_density:

                for member in peak.get('peak_members', ()):
                    bucket = density_buckets.get(member['sec'])
                    z_count = member.get('z_count')
                    if (bucket is not None and bucket.get('count') == member.get('count')
                            and type(z_count) in (int, float) and isfinite(z_count)):
                        bucket['z_count'] = z_count
            stats=dict(peak)
            stats.pop('laughter_echo_text',None)


            stats.pop('peak_members',None)
            stats['family_peaks'] = {family: {k: v for k, v in member.items() if k != 'laughter_echo_text'}
                                     for family, member in (stats.get('family_peaks') or {}).items()}
            for member in stats['family_peaks'].values():
                if member.get('user_count_status') != 'observed':
                    for key in list(member):
                        if key.endswith('_user_count') or key == 'laughter_echo_max':
                            member[key] = None
            located=[t for t in raw_counts if start<=t<end]
            known=all(users_known[t] for t in located)
            if not known:
                for k in list(stats):
                    if ('user' in k and k not in {'user_count_status','unidentified_user_count'}) or k=='laughter_echo_max':stats[k]=None
            items.append(dict(kind='chat_peak_cluster',family='chat',interval_sec=[start,end],stats=stats,rank=peak['rank'],
                all_original_occurrences=sum(raw_counts[t] for t in located),
                command_filter_applies_to_stats=True,user_counts_known=known,
                clock_domain='vod_relative_ms',playable=False))
            events.append(dict(event_id=f"chat-cluster:{start}",kind='chat_reaction',start_sec=start,end_sec=end))
    clustered = build_edit_point_clusters(events,duration_sec=service.duration,max_clusters=None) if service.duration is not None else {'clusters':[]}
    zones = [dict(zone_id=c["cluster_id"],start_sec=c["start_sec"],end_sec=c["end_sec"],
        signal_families=c["signal_families"],signal_count=c["signal_count"],family_count=c["family_count"])
        for c in clustered["clusters"]]
    mapping = dict(stt="stt",chat="chat",density="chat",waveform="audio_features",audio_peaks="audio_features",clips="clip")
    readiness = {f:dict(status="partial",count=0,reason=service.family_status.get(mapping[f],"excluded_by_policy"),
        producer="existing source producers",source_ref="stored EW asset binding",source_fingerprint=service.world_revision)
        for f in SOURCE_FAMILIES[:-1]}
    first = _build_first_look(video_no=service.video_no,duration_sec=service.duration,readiness=readiness,
                             zones=zones,source_fingerprint=service.world_revision) if service.duration is not None else dict(
                                 buckets=[],status='source_clock_unverified',playable=False)
    if 'live_metadata' in service.policy['allowed']:
        from .viewer_signals import viewer_items
        with service.open() as db:
            metadata=[json.loads(r[0]) for r in db.execute(
                "SELECT payload_json FROM records WHERE family='live_metadata' ORDER BY ordinal")]
        items.extend(viewer_items(metadata))
    for i,item in enumerate(items):
        item["hint_ref"] = f"s{i}"
        span=item["interval_sec"]
        item["first_look_supported"] = bool(span and any(b["start_sec"]<=span[1] and b["end_sec"]>=span[0] for b in first["buckets"]))


    items.sort(key=lambda r:(r["kind"] != "audio_event_source",r['kind'] != 'chat_peak_cluster',
                            r['stats']['rank'] if r['kind']=='chat_peak_cluster' else 0,not r["first_look_supported"],
                            (r.get('interval_utc_ms') or r["interval_sec"] or [float('inf')])[0],r["hint_ref"]))

    queues = [[r for r in items if r['family']==f] for f in ('clip','chat','audio_features','live_metadata')]
    items = [r for row in zip_longest(*queues) for r in row if r is not None]
    catalog = dict(profile=catalog_profile,reader_revision=catalog_revision,source_id=service.source_id,world_revision=service.world_revision,items=items,
        family_status=service.family_status,first_look=first,
        chat_baseline=dict(total_originals=len(chats),duration_sec=service.duration,
                           mean_originals_per_10s=len(chats)/max(1,service.duration/10) if service.duration is not None else None,
                           clock_domain='utc_epoch_ms' if service.duration is None else 'vod_relative_ms'),
        raw_read_complete=False,meaning_complete=False)
    _fresh(service)
    atomic_json(path,dict(catalog=catalog,sha256=digest(catalog)))
    return root,catalog

def _legacy_peak_attention(catalog, allowed):
    """Deliver existing merged peaks as exploration units, without re-clustering."""
    from ..outline.navigation import FIRST_LOOK_CONTEXT_SEC
    peaks = [r for r in catalog['items'] if r['kind']=='chat_peak_cluster' and r['family'] in allowed]
    duration = catalog['chat_baseline']['duration_sec']
    context_families = [f for f in ('stt','chat') if f in allowed]
    result = []
    for peak in sorted(peaks,key=lambda r:r['rank']):
        a,b = peak['interval_sec']
        counts = defaultdict(int)
        for hint in catalog['items']:
            span = hint['interval_sec']
            if (hint['family'] in allowed and span and
                hint['kind'] in {'chat_peak_cluster','viewer_clip','audio_peak','audio_events','singing_candidates'} and
                span[0] < b and span[1] >= a):
                counts[hint['family']] += 1
        result.append(dict(interval_sec=[a,b], rank=peak['rank'],
            merged_peak_count=peak['stats'].get('cluster_count'),signal_counts=dict(counts),
            source_hint_ref=peak['hint_ref'], original_read=False,
            context_action=dict(action='read_evidence',args=dict(
                intervals_sec=[[max(0,a-FIRST_LOOK_CONTEXT_SEC),min(duration,b+FIRST_LOOK_CONTEXT_SEC)]],
                families=context_families,context_cues=0))))
    return result

def _chat_buckets(catalog):
    return {row['interval_sec'][0]: row for row in catalog['items']
            if row['kind'] == 'chat_reaction' and row['interval_sec']}

def _peak_seconds(peaks):
    return {sec for peak in peaks for sec in peak['stats'].get('peak_seconds', ())}

def _representative_peaks(seconds, buckets):
    """Choose separated observed buckets by existing counts, one measure at a time."""
    remaining = sorted(seconds & buckets.keys())
    selected = []
    for field in PEAK_COUNTS:
        if not remaining or len(selected) == PEAK_CONTEXT_LIMIT:
            break
        sec = max(remaining, key=lambda t: buckets[t]['stats'].get(field, 0))
        if not buckets[sec]['stats'].get(field, 0):
            continue
        selected.append(sec)
        remaining = [t for t in remaining if abs(t - sec) >= 2 * PEAK_CONTEXT_SEC]
    return sorted(selected)

def _subtitle_action(seconds, duration, allowed):
    if not seconds or 'stt' not in allowed:
        return None
    return dict(action='read_evidence', args=dict(
        intervals_sec=[[max(0, t - PEAK_CONTEXT_SEC), min(duration, t + PEAK_CONTEXT_SEC)]
                       for t in seconds], families=['stt'], context_cues=0))

def _overlaps(span, interval):
    if not span:
        return False
    if span[0] == span[1]:
        return interval[0] <= span[0] < interval[1]
    return span[0] < interval[1] and interval[0] < span[1]

def _reaction_source_available(service):
    return (getattr(service, "signal_view_profile", None) == OBSERVED_DENSITY_PROFILE
            and service.duration is not None and service.duration > 0
            and service.family_status.get("chat") in {"present", "partial"}
            and "chat" in service.policy["allowed"])

def _observed_reaction_buckets(service):
    _, catalog = _catalog(service, OBSERVED_DENSITY_PROFILE)
    buckets = _chat_buckets(catalog)
    peaks = _peak_seconds(row for row in catalog["items"] if row["kind"] == "chat_peak_cluster")
    return [buckets[second] for second in sorted(peaks & buckets.keys())
            if "z_count" in buckets[second]["stats"]]

def _reaction_key(bucket):
    stats = bucket["stats"]
    return (max(stats.get(field, 0) for field in REACTION_COUNTS),
            stats.get("count", 0), -bucket["interval_sec"][0])

def _short_stt_read_spans(service, action, original):
    args = action.get("args", {})
    if (action.get("action") != "read_evidence" or not args.get("intervals_sec")
            or args.get("cursor") or set(args.get("families") or ()) != {"stt"}
            or not original.get("read_id") or original.get("source_id") != service.source_id
            or original.get("world_revision") != service.world_revision):
        return []
    timed_stt = [row["time_index"] for row in original.get("records", ())
                 if row["family"] == "stt" and row.get("time_index") and row.get("raw_text")]
    return [(start, end) for start, end in _ranges(args)
            if end - start <= 2 * PEAK_CONTEXT_SEC * 1_000_000
            and any(overlaps(clock, start, end) for clock in timed_stt)]

def _reaction_counts(bucket):
    return {field: bucket["stats"].get(field, 0) for field in (*PEAK_COUNTS, "z_count")}

def _reaction_lookup_hint(bucket, duration, allowed):
    interval = bucket["interval_sec"]
    counts = _reaction_counts(bucket)
    ratios = ({field: round(counts[field] / counts["count"], 4) for field in REACTION_COUNTS}
              if counts["count"] else {})
    return dict(interval_sec=interval, counts=counts, reaction_ratios=ratios,
                context_action=_subtitle_action([interval[0]], duration, allowed),
                chat_context_action=dict(action="read_evidence", args=dict(
                    intervals_sec=[interval], families=["chat"], context_cues=0)))

def chronological_reaction_hints(service, *, max_bytes=3_200):
    """Bound optional lookup hints across the clock without rescoring the producer's data."""
    if not _reaction_source_available(service):
        return None
    try:
        buckets = _observed_reaction_buckets(service)
    except (EvidenceError, OSError, DatabaseError, json.JSONDecodeError):
        return None
    if not buckets:
        return None
    for bands in (8, 4, 2, 1):
        representatives = {}
        for bucket in buckets:
            band = min(bands - 1, int(bucket["interval_sec"][0] * bands / service.duration))
            if band not in representatives or _reaction_key(bucket) > _reaction_key(representatives[band]):
                representatives[band] = bucket
        hint = dict(source_id=service.source_id, world_revision=service.world_revision, original_read=False,
            local_peaks=[_reaction_lookup_hint(representatives[band], service.duration, service.policy["allowed"])
                         for band in sorted(representatives)])
        if len(canonical_json(hint).encode("utf-8")) <= max_bytes:
            return hint
    return None

def near_end_reaction_hint(service, action, original, *, max_bytes=750):
    """Offer one explicit lookup for an observed bucket just after a short STT read."""
    if not _reaction_source_available(service):
        return None
    try:
        spans = _short_stt_read_spans(service, action, original)
        if not spans:
            return None
        candidates = []
        for bucket in _observed_reaction_buckets(service):
            start, end = (round(second * 1_000_000) for second in bucket["interval_sec"])
            if any(start < right and left < end for left, right in spans):
                continue
            gaps = [start - right for _, right in spans if 0 <= start - right <= PEAK_CONTEXT_SEC * 1_000_000]
            if gaps:
                candidates.append((min(gaps), start, bucket))
        if not candidates:
            return None
        gap, _, bucket = min(candidates, key=lambda candidate: candidate[:2])
        hint = dict(source_id=service.source_id, world_revision=service.world_revision, original_read=False,
                    after_read_gap_sec=float(gap / 1_000_000),
                    **_reaction_lookup_hint(bucket, service.duration, service.policy["allowed"]))
        if len(canonical_json(hint).encode("utf-8")) <= max_bytes:
            return hint
    except (EvidenceError, OSError, DatabaseError, json.JSONDecodeError):
        return None
    return None

def read_nearby_reaction(service, action, original, delivered_refs, *, max_bytes):
    """Open at most one exact chat page overlapping an already-read short STT window."""
    if not _reaction_source_available(service):
        return None
    try:
        spans = _short_stt_read_spans(service, action, original)
        if not spans:
            return None
        choices = []
        for bucket in _observed_reaction_buckets(service):
            start, end = bucket["interval_sec"]
            if any(start * 1_000_000 < right and left < end * 1_000_000 for left, right in spans):
                choices.append(bucket)
        if delivered_refs and choices:
            with service.open() as db:
                def unseen(bucket):
                    start, end = bucket["interval_sec"]
                    rows = db.execute("SELECT record_id FROM records WHERE family='chat' AND time_kind='instant' "
                                      "AND start_us>=? AND start_us<?", (int(start * 1_000_000), int(end * 1_000_000)))
                    return not any(row[0] in delivered_refs for row in rows)
                choices = [bucket for bucket in choices if unseen(bucket)]
        if not choices:
            return None
        peak = max(choices, key=_reaction_key)
        interval = peak["interval_sec"]
        page = service.read(question_id=PAIRED_REACTION_QUESTION,
                            intervals_sec=[interval], families=["chat"], context_cues=0,
                            max_bytes=min(8_000, max_bytes))
    except (EvidenceError, OSError, DatabaseError, json.JSONDecodeError):
        return None
    if not any(row.get("raw_text") and row.get("fragment_complete") for row in page["records"]):
        return None
    return dict(read_id=page["read_id"], peak_sec=interval[0], interval_sec=interval,
                counts=_reaction_counts(peak), partial_page=bool(page["next_cursor"]))

def _peak_attention(catalog, allowed, buckets):
    """Keep the producer's merged rank and use its member buckets for short reads."""
    peaks = [r for r in catalog['items'] if r['kind']=='chat_peak_cluster' and r['family'] in allowed]
    duration = catalog['chat_baseline']['duration_sec']
    result = []
    for peak in peaks:
        a,b = peak['interval_sec']
        counts = defaultdict(int)
        for hint in catalog['items']:
            span = hint['interval_sec']
            if (hint['family'] in allowed and span and
                hint['kind'] in {'chat_peak_cluster','viewer_clip','audio_peak','audio_events','singing_candidates'} and
                _overlaps(span, (a, b))):
                counts[hint['family']] += 1
        region = dict(interval_sec=[a,b],
            merged_peak_count=peak['stats'].get('cluster_count'),signal_counts=dict(counts),
            source_hint_ref=peak['hint_ref'], original_read=False,
            context_action=_subtitle_action(_representative_peaks(_peak_seconds([peak]), buckets),
                                            duration, allowed))
        rank = peak.get('rank')
        if type(rank) is int and rank > 0:
            region.update(rank=rank, rank_label=f"{rank}위 · 방송 내 채팅 클러스터 순위 ({_describe_intensity(peak)})")
        result.append(region)
    return sorted(result, key=lambda r: r.get('rank', float('inf')))

def _reaction_valley(a, b, buckets):
    """Separate nearby peaks when two observed buckets show a sustained drop."""
    left, right = sorted((a, b))
    floor = min(buckets[t]['stats']['count'] for t in (left, right)) / 2
    return any(t in buckets and t + WINDOW_SEC in buckets
               and buckets[t]['stats']['count'] < floor
               and buckets[t + WINDOW_SEC]['stats']['count'] < floor
               for t in range(left + WINDOW_SEC, right - WINDOW_SEC, WINDOW_SEC))

def _reaction_focus(peak, buckets):
    """Use neighboring observed counts for a local comparison and reaction extent."""
    sec = peak['stats']['sec']
    counts = _reaction_counts(buckets[sec])
    nearby = [buckets[t]['stats'] for t in range(sec - 60, sec + 61, WINDOW_SEC)
              if t != sec and t in buckets]
    reference = ({field: round(median(b.get(field, 0) for b in nearby), 1)
                  for field in PEAK_COUNTS} if len(nearby) >= 4 else {})
    if not reference:
        return reference, buckets[sec]['interval_sec']
    field = max(PEAK_COUNTS, key=lambda k: counts[k] - reference[k])
    threshold = (counts[field] + reference[field]) / 2
    start, end = buckets[sec]['interval_sec']
    lower, upper = peak['interval_sec']
    while start - WINDOW_SEC >= lower and buckets.get(start - WINDOW_SEC, {}).get('stats', {}).get(field, -1) > threshold:
        start -= WINDOW_SEC
    while end < upper and buckets.get(end, {}).get('stats', {}).get(field, -1) > threshold:
        end = buckets[end]['interval_sec'][1]
    return reference, [start, end]

def _first_reaction_scenes(catalog, allowed, buckets, limit):
    """Keep one first-look entry per nearby reaction, without merging the catalog."""
    if 'chat' not in allowed or 'stt' not in allowed:
        return []
    duration = catalog['chat_baseline']['duration_sec']
    peaks = sorted((row for row in catalog['items'] if row['kind'] == 'chat_peak_cluster'),
                   key=lambda row: row['rank'])
    vicinity = PEAK_CONTEXT_SEC + WINDOW_SEC
    distinct = []
    for peak in peaks:
        sec = peak['stats']['sec']
        if sec not in buckets:
            continue
        reference, reaction_span = _reaction_focus(peak, buckets)
        counts = _reaction_counts(buckets[sec])
        if reference and not any(counts[field] > typical for field, typical in reference.items()):
            continue
        start, end = peak['interval_sec']
        if any(abs(sec - other['stats']['sec']) <= 2 * vicinity
               and (other['interval_sec'][0] - vicinity <= sec <= other['interval_sec'][1] + vicinity
                    or start - vicinity <= other['stats']['sec'] <= end + vicinity)
               and not _reaction_valley(sec, other['stats']['sec'], buckets)
               for other in distinct):
            continue
        distinct.append(dict(peak, nearby_median_counts=reference, reaction_span=reaction_span))



    strong_count = max(1, limit - 1)
    selected = distinct[:strong_count]
    covered = {int(4 * peak['stats']['sec'] / duration) for peak in selected}
    for peak in distinct[strong_count:]:
        if len(selected) >= limit:
            break
        band = int(4 * peak['stats']['sec'] / duration)
        if band not in covered:
            covered.add(band)
            selected.append(peak)
    selected_ranks = {peak['rank'] for peak in selected}
    ordered = selected + [peak for peak in distinct if peak['rank'] not in selected_ranks]
    result = []
    for peak in ordered[:limit]:
        sec = peak['stats']['sec']
        bucket = buckets[sec]
        start, end = bucket['interval_sec']
        lead = max(0, start - 3 * PEAK_CONTEXT_SEC, peak['reaction_span'][0] - 2 * PEAK_CONTEXT_SEC)
        scene = dict(
            interval_sec=[start, end], counts=_reaction_counts(bucket),
            nearby_median_counts=peak['nearby_median_counts'],
            reaction_interval_sec=peak['reaction_span'],
            rank=peak['rank'], rank_label=_describe_intensity(peak),
            source_hint_ref=peak['hint_ref'],
            context_action=dict(action='read_evidence', args=dict(
                intervals_sec=[[lead, min(duration, end + PEAK_CONTEXT_SEC)]],
                families=['stt'], context_cues=0, max_bytes=8_000)),
        )
        if 'clip' in allowed:
            clip = min((row for row in catalog['items'] if row['kind'] == 'viewer_clip'
                        and row['metadata'].get('offset_status') == 'ok'
                        and _overlaps(row['interval_sec'], (start, end))
                        and abs(row['interval_sec'][0] - sec) <= vicinity),
                       key=lambda row: abs(row['interval_sec'][0] - sec), default=None)
            if clip:
                scene['clip_hint_refs'] = [clip['hint_ref']]
        result.append(scene)
    return result

def _scoped_peak_attention(catalog, candidates, interval, allowed, buckets, preview):
    if 'chat' not in allowed:
        return None
    clusters = [r for r in catalog['items'] if r['kind'] == 'chat_peak_cluster']
    seconds = {t for t in _peak_seconds(clusters)
               if t in buckets and _overlaps(buckets[t]['interval_sec'], interval)}
    selected = _representative_peaks(seconds, buckets)
    if not selected:
        return None
    clips = [r for r in candidates if r['kind'] == 'viewer_clip' and r['interval_sec']
             and r['metadata'].get('offset_status') == 'ok']
    peaks = []
    clip_cues = {}
    for t in selected:
        bucket = buckets[t]
        span = bucket['interval_sec']
        matches = [r for r in clips if _overlaps(r['interval_sec'], span)]
        matches.sort(key=lambda r: abs(t - r['interval_sec'][0]))
        stats = bucket['stats']
        ratios = {key: round(stats[key], 3) for key in ('laughter_ratio', 'surprise_ratio', 'question_ratio')
                  if key in stats}
        counts = {key: stats.get(key, 0) for key in PEAK_COUNTS}
        if catalog['profile'] == OBSERVED_DENSITY_PROFILE and 'z_count' in stats:
            counts['z_count'] = stats['z_count']
        peaks.append(dict(interval_sec=span, counts=counts,
                          ratios=ratios,
                          all_original_occurrences=bucket['all_original_occurrences'],
                          clip_hint_refs=[r['hint_ref'] for r in matches[:2]]))
        if matches:
            nearest = matches[0]
            cue = dict(interval_sec=nearest['interval_sec'])
            title = clip_preview(nearest, preview)['metadata'].get('title')
            if title:
                cue.update(title=str(title)[:48], title_truncated=len(str(title)) > 48)
            clip_cues[nearest['hint_ref']] = cue
    return dict(local_peaks=peaks, clip_cues=clip_cues, context_action=_subtitle_action(
        selected, catalog['chat_baseline']['duration_sec'], allowed),
        chat_context_action=dict(action='read_evidence', args=dict(
            intervals_sec=[row['interval_sec'] for row in peaks], families=['chat'], context_cues=0)),
        original_read=False)

def _clip_attention(catalog, allowed):
    """Expose existing clip first-look intervals without another detector or rank."""
    if 'clip' not in allowed:
        return []
    from ..edit_point_clusters import build_edit_point_clusters
    from ..outline.navigation import _build_first_look

    clips = [r for r in catalog['items'] if r['family'] == 'clip' and r['interval_sec']]
    duration = catalog['chat_baseline']['duration_sec']
    if not clips or duration is None:
        return []

    clustered = build_edit_point_clusters([
        dict(event_id=r['record_id'], kind='viewer_clip', start_sec=r['interval_sec'][0],
             end_sec=r['interval_sec'][1]) for r in clips], duration_sec=duration, max_clusters=None)
    first = _build_first_look(video_no=catalog['first_look']['video_no'], duration_sec=duration,
        readiness=catalog['first_look']['readiness'], source_fingerprint=catalog['world_revision'],
        zones=[dict(zone_id=c['cluster_id'], start_sec=c['start_sec'], end_sec=c['end_sec'],
                    signal_families=c['signal_families'], signal_count=c['signal_count'],
                    family_count=c['family_count']) for c in clustered['clusters']])
    context_families = [f for f in ('stt', 'chat') if f in allowed] or ['clip']
    regions = []
    for bucket in first['buckets']:
        a, b = bucket['start_sec'], bucket['end_sec']
        members = [r for r in clips if r['interval_sec'][0] < b and r['interval_sec'][1] >= a]
        if members:
            cues = []
            for hint in members:
                metadata = hint['metadata']
                title = str(metadata.get('title') or '')
                cues.append([hint['hint_ref'], title[:80], metadata.get('offset_sec'),
                             metadata.get('duration'), len(title) > 80])
            regions.append(dict(interval_ref='clip_' + bucket['interval_id'], interval_sec=[a, b],
                clip_count=len(members), clip_cue_rows=cues,
                context_action=dict(action='read_evidence', args=dict(
                    intervals_sec=[[a, b]], families=context_families, context_cues=0))))
    return regions

def browse_signals(service, *, question_id, families=None, cursor=0, limit=8, max_bytes=12000,
                   interval_sec=None):
    profile = getattr(service, 'signal_view_profile', None) or PROFILE
    if profile not in (PROFILE, LOCAL_REACTION_PROFILE, OBSERVED_DENSITY_PROFILE):
        raise EvidenceError('unknown_signal_view_profile')
    local_reactions = profile != PROFILE
    if interval_sec is not None and not local_reactions:
        raise EvidenceError('browse_scope_unsupported_for_view')
    if type(limit) is not int or not 1<=limit<=20 or type(max_bytes) is not int or not 3000<=max_bytes<=32000:
        raise EvidenceError("invalid_signal_page_budget")
    if families is not None and (not isinstance(families,list) or not all(isinstance(f,str) for f in families)):
        raise EvidenceError('invalid_signal_families')
    allowed = sorted(set(families if families is not None else service.policy["allowed"]))
    if not allowed or not set(allowed)<=set(service.policy["allowed"]):raise EvidenceError("excluded_by_policy")
    scope = {}
    if interval_sec is not None:
        if service.duration is None:
            raise EvidenceError('source_clock_unverified_use_source_order')
        if (not isinstance(interval_sec, list) or len(interval_sec) != 2
                or any(type(t) not in (int, float) for t in interval_sec)
                or not 0 <= interval_sec[0] < interval_sec[1] <= service.duration):
            raise EvidenceError('invalid_time_filter')
        scope['interval_sec'] = interval_sec
    root,catalog = _catalog(service, profile)
    identity = dict(profile=profile,world_revision=service.world_revision,question_id=question_id,
                    families=allowed,limit=limit,max_bytes=max_bytes,catalog_sha256=digest(catalog),**scope)
    preview = getattr(service, "clip_preview_profile", None)
    if preview:
        identity["clip_preview"] = preview
    offset=0
    if cursor:
        if not isinstance(cursor,str) or len(cursor)!=64 or any(c not in '0123456789abcdef' for c in cursor):
            raise EvidenceError("invalid_signal_cursor")
        try:
            saved=json.loads((root/'cursors'/(cursor+'.json')).read_text(encoding='utf-8'))
        except (OSError,ValueError):
            raise EvidenceError('unknown_signal_cursor') from None
        if digest(saved)!=cursor or saved['identity']!=identity:raise EvidenceError('signal_cursor_changed')
        offset=saved['offset']
    candidates=[r for r in catalog['items'] if r['family'] in allowed]
    if scope:
        candidates = [r for r in candidates
                      if r['kind'] != 'chat_peak_cluster' and _overlaps(r['interval_sec'], interval_sec)]
    buckets = _chat_buckets(catalog) if local_reactions else {}
    page=dict(schema_version='ew.signal_page.v1',profile=profile,source_id=service.source_id,
        world_revision=service.world_revision,question_id=question_id,catalog_sha256=digest(catalog),
        query_sha256=digest(identity),page_offset=offset,
        family_status={f:service.family_status[f] for f in allowed},chat_baseline=catalog['chat_baseline'],
        hints=[],total_hints=len(candidates),next_cursor=None,original_read=False,meaning_complete=False,
        policy='Derived discovery hints, not original reads, consensus, listening, matching or editorial approval. '
               'Overlapping signals are not independent votes. Quiet source regions remain accessible with coverage view.',
        **scope)
    if not cursor and scope:
        attention = _scoped_peak_attention(catalog, candidates, interval_sec, allowed, buckets, preview)
        if attention:
            page['peak_attention'] = attention
    if not cursor and not scope:

        orientation_base = dict(page)
        clip_regions = _clip_attention(catalog, allowed)
        clip_attention = dict(total_regions=len(clip_regions), regions=[],
                              original_read=False, meaning_complete=False)
        for region in clip_regions[:limit]:

            location = {k: v for k, v in region.items() if k != 'clip_cue_rows'}
            proposed = dict(clip_attention, regions=clip_attention['regions'] + [location])
            if len(canonical_json(dict(orientation_base, clip_attention=proposed)).encode()) > max_bytes // 3:
                break
            clip_attention = proposed
        if clip_attention['regions']:
            clip_attention.update(clip_cue_columns=['hint_ref', 'title', 'offset_sec', 'duration', 'title_truncated'],
                cue_status='untrusted_viewer_metadata', detail_action=dict(action='browse_evidence', args=dict(
                    view='signals', families=['clip'], limit=limit, max_bytes=max_bytes)))
            for location, region in zip(clip_attention['regions'], clip_regions):
                location['clip_cue_rows'] = []
                location['clip_cues_omitted'] = len(region['clip_cue_rows'])
                for cue in region['clip_cue_rows']:
                    location['clip_cue_rows'].append(cue)
                    location['clip_cues_omitted'] -= 1
                    if len(canonical_json(dict(orientation_base, clip_attention=clip_attention)).encode()) > max_bytes // 2:
                        location['clip_cue_rows'].pop()
                        location['clip_cues_omitted'] += 1
                        break
            page['clip_attention'] = clip_attention
        retrieval = service.retrieval_contract()
        navigation = dict(search_modes=retrieval['modes'])
        scenes = (_first_reaction_scenes(catalog, allowed, buckets, min(8, limit))
                  if profile == OBSERVED_DENSITY_PROFILE and service.duration else [])
        regions = ([] if scenes else _peak_attention(catalog, allowed, buckets) if local_reactions
                   else _legacy_peak_attention(catalog, allowed))
        rank_preview = dict(total_regions=len(regions), regions=[], navigation=navigation,
                            original_read=False, meaning_complete=False)
        local = {}
        detail_budget = max_bytes - FIRST_DETAILS_RESERVE_BYTES
        if local_reactions:
            rank_preview['rank_basis'] = 'producer rank of merged chat clusters across this VOD'
        if scenes:
            rank_preview['total_regions'] = sum(row['kind'] == 'chat_peak_cluster' for row in candidates)
            rank_preview['selection'] = 'distinct_reaction_scenes'
            rank_preview['chat_lookup'] = dict(action='browse_chat', interval_from='local_peaks[].interval_sec',
                                                args=dict(order='discovery', limit=6))
            for scene in scenes:
                proposed = {**rank_preview, 'local_peaks': [*local.get('local_peaks', []), scene]}
                if len(canonical_json(dict(page, peak_attention=proposed)).encode()) > detail_budget:
                    break
                local = {'local_peaks': proposed['local_peaks']}
        elif local_reactions and regions:
            observation = _scoped_peak_attention(catalog, candidates, [0, service.duration], allowed, buckets, preview)
            if (observation and len(canonical_json(dict(
                    page, peak_attention={**rank_preview, **observation})).encode()) <= detail_budget):
                local = observation
        for region in regions[:limit]:
            proposed = dict(rank_preview, regions=rank_preview['regions'] + [region])
            if (len(canonical_json(dict(orientation_base, peak_attention=proposed)).encode()) > max_bytes // 3
                    or local_reactions and len(canonical_json(dict(
                        page, peak_attention={**proposed, **local})).encode()) > detail_budget):
                break
            rank_preview = proposed
        if rank_preview['regions'] or local:
            page['peak_attention'] = {**rank_preview, **local}
    next_offset=offset
    for item in candidates[offset:offset+limit]:
        hint=clip_preview(dict(item), preview)
        if 'metadata' in hint and len(canonical_json(hint['metadata']).encode())>1500:
            hint['metadata'] = dict(excerpt=canonical_json(hint['metadata'])[:240],
                                   truncated=True,full_metadata_requires_read=True)
        if hint['family']=='chat':
            window = {'interval_utc_ms':hint['interval_utc_ms']} if hint.get('clock_domain')=='utc_epoch_ms' else {'interval_sec':hint['interval_sec']}
            if hint['kind']=='chat_peak_cluster':
                sec=hint['stats']['sec']
                window={'interval_sec':[sec,min(sec+WINDOW_SEC,service.duration)]}
                hint['next_action']=(dict(action='browse_evidence',args=dict(view='hints',limit=10))
                    if getattr(service,'summary_hints_config',None) else
                    dict(action='browse_chat',args=dict(**window,limit=3,max_bytes=6000)))
                if local_reactions:
                    action = _subtitle_action(_representative_peaks(_peak_seconds([hint]), buckets),
                                              service.duration, service.policy['allowed'])
                    if action:
                        hint['content_context_action'] = action
                else:
                    hint['content_context_action'] = dict(action='read_evidence', args=dict(
                        intervals_sec=[hint['interval_sec']], families=['stt'], context_cues=0))
            wording_args = dict(**window, limit=3, max_bytes=6000)
            if scope or profile == OBSERVED_DENSITY_PROFILE:
                hint['wording_action'] = dict(action='browse_chat', args=wording_args)
            else:
                from .chat_view import browse_chat
                hint['wording_page']=browse_chat(service,question_id=question_id,**wording_args)
        else:
            hint['next_action']=dict(action='read_evidence',args=dict(record_ids=hint.get('source_record_ids') or [hint['record_id']],families=[hint['family']],context_cues=0))
            if hint['family']=='live_metadata' and hint.get('interval_utc_ms'):
                context_families=[f for f in ('stt','chat') if f in service.policy['allowed']]
                if context_families:
                    a,b=hint['interval_utc_ms']
                    hint['content_context_action']=dict(action='read_evidence',args=dict(
                        intervals_utc_ms=[[max(0,a-120000),b+120000]],families=context_families,
                        context_cues=0,max_bytes=12000))
        if ('wording_page' in hint
                and len(canonical_json(dict(page,hints=page['hints']+[hint])).encode())>max_bytes-850):
            hint.pop('wording_page')
            hint['wording_action'] = dict(action='browse_chat', args=wording_args)
        if len(canonical_json(dict(page,hints=page['hints']+[hint])).encode())>max_bytes-850:
            if not page['hints'] and not (scope and page.get('peak_attention')):
                raise EvidenceError('signal_page_too_small')
            break
        page['hints'].append(hint);next_offset+=1
    if next_offset<len(candidates):
        token=dict(identity=identity,offset=next_offset);page['next_cursor']=digest(token)
        atomic_json(root/'cursors'/(page['next_cursor']+'.json'),token)
        page['next_action']=dict(action='browse_evidence',question_id=question_id,args=dict(
            view='signals',families=allowed,cursor=page['next_cursor'],limit=limit,max_bytes=max_bytes,**scope))
    page['status']='partial_page' if page['next_cursor'] else 'complete_hints' if candidates else 'empty'
    if len(canonical_json(page).encode())>max_bytes:
        raise EvidenceError('signal_page_too_small')
    _fresh(service)
    atomic_json(root/'pages'/(digest(page)+'.json'),page)
    service.state.event('signal_page_returned',question_id=question_id,payload=dict(page_sha256=digest(page),original_read=False))
    return page

def delivered(service, response, evidence):
    page=response.get('signal_orientation') or (response if response.get('schema_version')=='ew.signal_page.v1' else None)
    if page:
        service.state.event('signal_page_delivered',question_id=page['question_id'],payload=dict(
            page_sha256=digest(page),request_ref=str(native_path(evidence['raw_ref']).parent/'request.json'),
            request_file_sha256=file_hash(native_path(evidence['raw_ref']).parent/'request.json')))

def handoff(service, exploration_root):
    deliveries=[e['payload'] for e in service.state.load()['events'] if e['kind']=='signal_page_delivered'
                and native_path(e['payload']['request_ref']).is_relative_to(native_path(exploration_root))]
    deliveries=list({(d['request_ref'],d['page_sha256']):d for d in deliveries}.values())
    pages=[]
    for delivery in deliveries:
        path=native_path(delivery['request_ref'])
        if file_hash(path)!=delivery['request_file_sha256']:
            raise EvidenceError('signal_delivery_changed')
        req=json.loads(path.read_text(encoding='utf8'));body=json.loads(req['user_prompt']);result=body['tool_result']
        page=result.get('signal_orientation') or result
        if digest(page)!=delivery['page_sha256'] or page['world_revision']!=service.world_revision:
            raise EvidenceError('signal_delivery_changed')
        pages.append(page)
    if not pages:return None
    latest={}
    for page in pages:
        key=page['query_sha256']
        if key not in latest or page['page_offset']>=latest[key]['page_offset']:
            latest[key]=page
    return dict(profile=pages[0]['profile'],first_page=pages[0],delivered_page_refs=deliveries,
        remaining_cursors=[dict(question_id=p['question_id'],next_action=p['next_action']) for p in latest.values() if p.get('next_cursor')],
        original_read=False,meaning_complete=False)

def request_view(orientation):
    """Keep delivery provenance on disk, with one bounded hint page in input."""
    return {k:orientation[k] for k in ('profile','first_page','remaining_cursors','original_read','meaning_complete')}

def defer_chat_wording(orientation):
    """Omit eager wording pages from a new writer's imported orientation."""
    if not isinstance(orientation, dict):
        return orientation
    if isinstance(orientation.get('first_page'), dict):
        return dict(orientation, first_page=defer_chat_wording(orientation['first_page']))
    hints = []
    for hint in orientation.get('hints', []):
        if 'wording_page' in hint:
            hint = dict(hint)
            wording = hint.pop('wording_page')
            scope = {key: wording[key] for key in ('interval_sec', 'interval_utc_ms') if wording.get(key)}
            if scope:
                hint['wording_action'] = dict(action='browse_chat', args=dict(**scope, order='discovery'))
        hints.append(hint)
    return dict(orientation, hints=hints) if 'hints' in orientation else orientation
