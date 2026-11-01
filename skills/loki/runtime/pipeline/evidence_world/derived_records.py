"""Read existing audio-feature/clip sidecars as fallible location hints.

No acquisition, recomputation, listening or clip matching. A source file and
row locator preserve the full producer output; only public evidence fields are
projected for model delivery. Their clocks never authorize playback boundaries.
"""

import json

import math

from .contracts import EvidenceError, canonical_json, digest, native_path, time_projection

REVISION = "ew-derived-sidecars-v4-audio-boundary"

PATHS = {"audio_features": ".audio_features/audio_reaction_metadata.json", "clip": "user_clips.json"}

def _load(path):
    value = json.loads(native_path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise EvidenceError("derived_source_object_required")


    canonical_json(value)
    return value

def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False

def _audio_policy_identity(data):
    supplied = data.get('policy_id')
    if isinstance(supplied, str) and supplied:
        return supplied


    if supplied is None and data.get('policy') == dict(
            laughter_threshold=.15, music_onset=.65, music_release=.45):
        return 'legacy-E2-observation-values-v0'
    return None

def _validate_audio_observations(data, duration):
    """Reject malformed optional metadata before it can interrupt writing."""
    from ..subtitle_enhancement.audio_events import observation_grid, observation_window_on_grid
    number = _finite_number
    for analysis in data["analyses"]:
        if (not isinstance(analysis, dict) or not number(analysis.get("window"))
                or not number(analysis.get("hop")) or min(analysis["window"], analysis["hop"]) <= 0
                or not isinstance(analysis.get("rows"), list)):
            raise ValueError("invalid_audio_analysis")
        expected = analysis.get("expected_window_count")
        grid = observation_grid(duration, analysis['window'], analysis['hop'])
        if grid is None or (expected is not None and (
                type(expected) is not int or expected != grid[3] or expected < len(analysis['rows']))):
            raise ValueError("invalid_audio_coverage")
        previous_start = -1
        for row in analysis["rows"]:
            if (not isinstance(row, dict) or not all(number(row.get(k)) for k in ("start", "end", "center"))
                    or not 0 <= row["start"] <= row["center"] <= row["end"] <= duration
                    or row["start"] == row["end"]
                    or any(k in row and (not number(row[k]) or not 0 <= row[k] <= 1)
                           for k in ("laughter", "music", "speech", "singing"))):
                raise ValueError("invalid_audio_observation")
            if row["start"] <= previous_start:
                raise ValueError("duplicate_or_unordered_audio_window")
            if expected is not None and not observation_window_on_grid(row, grid):
                raise ValueError('invalid_audio_window_grid')
            previous_start = row["start"]
    for key in ("events", "partial_events", "singing_candidates"):
        rows = data.get(key, [])
        if not isinstance(rows, list):
            raise ValueError("invalid_audio_events")
        for row in rows:
            if (not isinstance(row, dict) or not number(row.get("at")) or not 0 <= row["at"] <= duration
                    or not number(row.get("score")) or not 0 <= row["score"] <= 1
                    or row.get("category") not in ({'singing_candidate'} if key == 'singing_candidates' else {'music', 'laughter'})):
                raise ValueError("invalid_audio_event")
            for field in ("window_start", "window_end", "end"):
                if field in row and (not number(row[field]) or not 0 <= row[field] <= duration):
                    raise ValueError("invalid_audio_event_clock")
            if "window_start" in row and "window_end" in row and not row["window_start"] <= row["at"] <= row["window_end"]:
                raise ValueError("invalid_audio_event_window")

def bind_audio_events(root, *, srt_path, duration_sec):
    """Discover only same-source immutable AST results in the normal cache."""
    from ..subtitle_enhancement.cache import evidence_path, SCHEMA
    from ..subtitle_enhancement.audio_events import MODEL_ID, MODEL_REVISION, MODEL_SHA256, POLICY, POLICY_ID
    from .contracts import file_hash
    state = dict(status="not_collected", audio_listened=False, unprocessed_audio_is_unknown=True)
    timing_path = evidence_path(srt_path)
    try:
        timing = _load(timing_path)
        if (timing.get("schema_version") != SCHEMA or not isinstance(timing.get("clock"), dict)
                or timing.get("baseline_sha256") != file_hash(srt_path)
                or timing.get("clock", {}).get("origin") != "input_media"
                or timing.get("clock", {}).get("source_offset") != 0):
            return None, dict(state, status="conflict", reason="audio_event_speech_clock_mismatch")
        source_sha = timing["source_sha256"]
        if not isinstance(source_sha, str) or len(source_sha) != 64:
            raise ValueError("audio_event_source_binding_invalid")
    except (OSError, ValueError, KeyError):
        return None, dict(state, reason="audio_event_source_binding_missing")
    candidates = []
    for path in sorted((root / ".subtitles").glob("audio-*/e-*/**/result.json")):
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            data = _load(path)
            model = data.get("model", {})
            policy = data.get("policy", {})
            if not isinstance(model, dict) or not isinstance(policy, dict):
                raise ValueError("audio_event_model_policy_shape")
            if data.get("source_sha256") != source_sha:
                continue
            if (model.get("id") != MODEL_ID or model.get("revision") != MODEL_REVISION
                    or model.get("sha256") != MODEL_SHA256):
                state = dict(state, status="conflict", reason="audio_event_model_mismatch")
                continue
            policy_id = _audio_policy_identity(data)
            if (not policy_id
                    or set(policy) != set(POLICY)
                    or any(not _finite_number(v) or not 0 <= v <= 1 for v in policy.values())
                    or (policy_id == POLICY_ID and policy != POLICY)):
                state = dict(state, status="conflict", reason="audio_event_policy_mismatch")
                continue
            clock = data.get("clock", {})
            if not isinstance(clock, dict):
                raise ValueError("audio_event_clock_shape")
            if (not _finite_number(data.get("duration"))
                    or data["duration"] <= 0 or abs(float(data["duration"])-float(duration_sec)) > .5
                    or (clock and (clock.get("origin") != "input_media" or clock.get("source_offset") != 0))):
                state = dict(state, status="conflict", reason="audio_event_clock_mismatch")
                continue
            if not isinstance(data.get("events"), list) or not isinstance(data.get("analyses"), list):
                raise ValueError("audio_event_shape")
            _validate_audio_observations(data, data["duration"])
            coverage_count = sum(len(a.get("rows", [])) for a in data["analyses"])
            candidates.append((data.get("status") == "ok", coverage_count, str(path), path, data))
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            state = dict(state, status="collection_failed", reason="invalid_audio_event_result")
    if not candidates:
        return None, state
    _, _, _, path, data = max(candidates, key=lambda row: row[:3])
    return dict(path=str(path.resolve()), sha256=file_hash(path), timing_path=str(timing_path.resolve()),
                timing_sha256=file_hash(timing_path)), dict(state, status=data.get("status"),
                    model=data["model"], policy_id=_audio_policy_identity(data), source_sha256=source_sha)

def load_asset_data(asset):
    """Keep the primary cache unchanged and reopen its hash-bound AST sidecar."""
    data = {} if asset.source_locator.get('kind') == 'ast_audio_only' else _load(asset.source_locator["path"])
    event_path = asset.source_locator.get("audio_event_path")
    if event_path:
        data = dict(data, _ast_audio_events=_load(event_path))
    return data

def _audio_event_rows(data):
    from ..subtitle_enhancement.audio_events import observation_coverage, singing_candidates, scores_to_events, POLICY_ID
    ast = data.get("_ast_audio_events")
    if not isinstance(ast, dict):
        return []
    analyses = ast.get("analyses", [])
    descriptor = dict(status=ast.get("status"), reason=ast.get("reason"), model=ast.get("model"),
        policy_id=_audio_policy_identity(ast), policy=ast.get("policy"), source_sha256=ast.get("source_sha256"),
        duration=ast.get("duration"), clock=ast.get("clock", dict(origin="input_media", source_offset=0)),
        coverage=observation_coverage(analyses, duration=ast.get('duration')), audio_listened=False,
        unprocessed_audio_is_unknown=True, scores_calibrated=False)
    if not ast.get('policy_id'):
        descriptor['policy_identity_source'] = 'legacy_exact_policy_values_not_active_policy'
    if not ast.get('clock'):
        descriptor['clock_identity_source'] = 'same_source_hash_bound_timing_receipt_not_ast_clock_declaration'
    provenance = {k:descriptor[k] for k in ("model", "policy_id", "source_sha256", "clock", "audio_listened", "scores_calibrated")}
    partial = ast.get("partial_events")
    if partial is None and ast.get("status") != "ok" and ast.get("policy_id") == POLICY_ID:
        partial = scores_to_events(
            next((a["rows"] for a in analyses if a["window"] == 3), []),
            next((a["rows"] for a in analyses if a["window"] == 10), []), duration=ast["duration"])
    events = [dict(row, **provenance, producer_status=ast.get("status"),
                   partial_observation=ast.get("status") != "ok")
              for row in (ast["events"] if ast.get("status") == "ok" else partial or [])]
    long = next((a.get("rows", []) for a in analyses if a.get("window") == 10), [])
    singing = singing_candidates(long, duration=ast["duration"])
    return [("audio_event_source", [descriptor]), ("audio_events", events),
            ("singing_candidates", [dict(row, model=descriptor["model"], source_sha256=descriptor["source_sha256"],
                                        clock=descriptor["clock"], audio_listened=False) for row in singing])]

def collection_state(path, *, family, video_no):
    try:
        data = _load(path)
    except (ValueError, OSError):
        return "collection_failed", {"reason": "unreadable_derived_source"}
    state = {"producer": "pipeline.audio_features" if family == "audio_features" else "pipeline.chzzk_user_clips",
             "source_binding": "caller_work_dir", "audio_listened": False, "clip_match_verified": False}
    if "video_no" in data and str(data["video_no"]) != str(video_no):
        return "conflict", dict(state, reason="source_video_mismatch")
    if family == "clip":
        state.update(status=data.get("status"), schema_version=data.get("schema_version"))
        if data.get("schema_version") not in (3, 4) or not isinstance(data.get("clips"), list):
            return "collection_failed", dict(state, reason="unsupported_clip_sidecar")
        if "video_no" not in data:
            return "conflict", dict(state, reason="source_video_missing")
        if data.get("status") == "no_clips" and not data["clips"] and data.get("fetched_at"):
            return "absent_confirmed", state
        if data.get("status") in {"fetch_failed", "error", "timeout", "browser_failed"}:
            return "collection_failed", state
        return ("present" if data.get("status") == "ok" and data["clips"] else "partial"), state
    failure = data.get("audio_reaction_status")
    if failure:
        status = str(failure.get("status", "")) if isinstance(failure, dict) else "unknown"
        return ("not_collected" if status.startswith("skipped_") else "collection_failed"), dict(state, status=status)
    if not isinstance(data.get("audio_reaction_peaks", []), list) or not isinstance(data.get("audio_waveform", {}), dict):
        return "collection_failed", dict(state, reason="unsupported_audio_sidecar")
    if not isinstance(data.get("audio_waveform", {}).get("samples", []), list):
        return "collection_failed", dict(state, reason="unsupported_audio_waveform_samples")


    return "partial", dict(state, reason="audio_media_revision_unbound")

def _collections(data, family):
    if family == "clip":
        return [("clips", data["clips"])]
    source=data.get('audio_feature_source')
    windows=data.get('audio_loudness_windows')
    return [
        ("audio_reaction_peaks", data.get("audio_reaction_peaks", [])),
        ("audio_waveform.samples", data.get("audio_waveform", {}).get("samples", [])),
        ('audio_feature_source',[source] if isinstance(source,dict) else []),
        ('audio_loudness_windows',windows if isinstance(windows,list) else [])] + _audio_event_rows(data)

def _rows(data, family):
    return [(name,i,row) for name,rows in _collections(data,family) for i,row in enumerate(rows)]

def _projection(row, family, collection=None):
    if not isinstance(row, dict):
        return {"invalid_row_type": type(row).__name__}
    if collection in {"audio_event_source", "audio_events", "singing_candidates"}:
        return dict(row)
    fields = ("clip_uid", "title", "duration", "video_no", "offset_sec", "offset_status",
        "created_date", "read_count", "like_count", "play_count", "engagement_status",
        "source_vod_duration_sec", "selection_reason") if family == "clip" else (
        "start_sec", "end_sec", "center_sec", "sec", "value", "loudness_db", "peak_db",
        "percentile", "z_score", "normalized", "score", "labels", "primary_label", "caution")
    if family=='audio_features' and collection=='audio_feature_source':
        fields=('kind','source_kind','sample_extraction','normalization','sample_count',
            'window_sec','include_lufs','min_percentile','min_gap_sec','chat_overlap_sec','max_peaks','decision_rule')
    return {k: row[k] for k in fields if k in row}

def iter_derived_records(asset):
    from .records import assert_asset_fresh, _record
    assert_asset_fresh(asset)
    data = load_asset_data(asset)
    for ordinal, (collection, index, row) in enumerate(_rows(data, asset.family)):
        projected=_projection(row,asset.family,collection)
        text = canonical_json(projected)
        warnings = ["derived_metadata_not_original_media", "not_a_playback_boundary"]
        original = row if isinstance(row, dict) else {}
        if asset.family == "clip":
            warnings += ["clip_offset_is_collector_anchor_not_precision_match", "clip_pixels_unread"]
            start = original.get("offset_sec")
            try:
                end = float(start) + float(original.get("duration"))
                if not math.isfinite(end):
                    end = None
            except (ValueError, TypeError):
                end = None
            located = original.get("offset_status") == "ok" and str(original.get("video_no")) == asset.video_no
            if not located:
                warnings.append("clip_anchor_unverified_or_wrong_video")
        else:
            warnings += ["audio_not_listened", "audio_media_revision_unbound"]
            start = original.get("window_start", original.get("start_sec", original.get("center_sec", original.get("sec"))))
            end = original.get("window_end", original.get("end_sec", start))
            if collection in {"audio_event_source", "audio_events", "singing_candidates"}:
                warnings.remove("audio_media_revision_unbound")
                warnings += ["classified_observation_not_direct_listening", "window_not_exact_sound_boundary"]
            located = True
        times, _ = time_projection(start, end, unit="sec", duration_sec=asset.clock_authority["duration_sec_decimal"],
                                   instant=start == end)
        if not located:
            times = None
        if times is None:
            warnings.append("unlocated_derived_clock_not_repaired")
        locator = dict(kind="derived_json_row", collection=collection, row_ordinal=index)
        if collection in {"audio_event_source", "audio_events", "singing_candidates"}:
            locator.update(source_path=asset.source_locator["audio_event_path"],
                           source_sha256=asset.source_locator["audio_event_sha256"])
        record = _record(asset, ordinal, locator, digest(row), text,
            dict(unit="sec", start=start, end=end), times,
            "derived_location_hint" if times else "unknown", payload=projected, warnings=warnings)
        classified = collection in {"audio_event_source", "audio_events", "singing_candidates"}
        record.update(derived=True, playable=False, transform=dict(
            producer="pipeline.subtitle_enhancement.audio_events" if classified else asset.collection_status.get("producer", "pipeline.audio_features"),
            revision=REVISION, model=projected.get("model") if classified else None,
            representation="selected producer fields serialized as JSON; original row bound by hash"))
        yield record

def read_derived(asset, record, data):
    locator = record["native_locator"]
    allowed = dict(_collections(data,asset.family))
    if locator["collection"] not in allowed:
        raise EvidenceError("unsupported_derived_locator")
    row = allowed[locator["collection"]][locator["row_ordinal"]]
    if digest(row) != record["raw_payload_sha256"]:
        raise EvidenceError("raw_payload_changed")
    projected = _projection(row,asset.family,locator['collection'])
    text = canonical_json(projected)
    return dict(raw_text=text, raw_payload=projected, bytes_read=len(text.encode("utf-8")))
