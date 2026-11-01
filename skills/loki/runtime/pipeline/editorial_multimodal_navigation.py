"""Source-bound multimodal retrieval hints for the same editorial writer.

Cached vectors propose occurrences, never relations, listened audio or cut clocks.
Validation/retrieval are read only. Explicit local generation reuses the public pin.
"""

from __future__ import annotations

from copy import deepcopy

import json

import math

from pathlib import Path

import re

from .evidence_world.contracts import digest, file_hash, native_path, time_projection

SCHEMA = "editorial.multimodal-navigation.v1"

MODEL = "google/embeddinggemma-2"

REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"

DIMENSION = 768

WEIGHT_SHA256 = "197a32965d4b1105faf060417baa899e193fb73cd401f42ec9295234d5553d79"

MODALITIES = ("text", "audio", "image", "video")

REVIEW_POLICY = ("Retrieval hints only. Read nearby STT/chat and necessary original media yourself; "
    "same words or similar vectors do not confirm the same event, cause, speaker or cut. "
    "Retain opposing evidence and unlocated originals. Audio embedding is not writer listening.")

LOCAL_LIMITS = dict(cpu_threads=4, max_seconds=600, max_memory_bytes=12_000_000_000,
                    max_requests=2, max_total_seconds=600)

LOCAL_MODEL_FILES = {"config.json", "config_sentence_transformers.json", "modules.json",
    "1_Pooling/config.json", "2_Normalize/config.json", "preprocessor_config.json", "processor_config.json",
    "sentence_bert_config.json", "tokenizer.json", "tokenizer.model", "tokenizer_config.json",
    "chat_template.jinja", "model.safetensors"}

def _fail(code):
    raise ValueError("full_author_navigation_" + code)

def _sha(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None

def _interval(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
        and all(type(t) in (int, float) and math.isfinite(t) for t in value)
        and 0 <= value[0] < value[1])

def validate_navigation(value, *, source, evidence_catalog, world_revision=None, media_hashes=None):
    """Validate historical judged hints or explicit retrieval_only hints.

    Passing a media hash requires the caller's verified, same-source media scope.
    A judge is optional only for retrieval_only; that shape cannot contain decisions.
    """
    fields = {"schema_version", "binding", "encoder", "questions", "candidates"}
    if not isinstance(value, dict):
        _fail("schema")
    retrieval_only = value.get("mode") == "retrieval_only"
    expected_fields = fields | ({"mode"} if retrieval_only else {"judge"})
    if set(value) != expected_fields or value.get("schema_version") != SCHEMA:
        _fail("schema")
    binding = value["binding"]
    expected = dict(source_id=source["asset"]["source_id"], source_revision=source["sha256"],
        stt_sha256=source["asset"]["bytes_sha256"], clock_sha256=digest(source["asset"]["clock_authority"]))
    if (not isinstance(binding, dict) or set(binding) != {*expected, "world_revision", "media_sha256"}
            or any(binding[k] != v for k, v in expected.items())
            or any(not _sha(binding[k]) for k in ("world_revision", "media_sha256"))):
        _fail("source")
    if world_revision is not None and binding["world_revision"] != world_revision:
        _fail("world")
    if media_hashes is None:
        _fail("requires_bound_media")
    if binding["media_sha256"] not in media_hashes:
        _fail("media")
    encoder = value["encoder"]
    if (not isinstance(encoder, dict)
            or set(encoder) != {"model", "revision", "dimension", "pooling", "processor_sha256", "input_sha256"}
            or encoder["model"] != MODEL or encoder["revision"] != REVISION
            or encoder["dimension"] != DIMENSION or encoder["pooling"] != "mean_l2"
            or not _sha(encoder["processor_sha256"]) or not _sha(encoder["input_sha256"])):
        _fail("encoder")
    questions = value["questions"]
    if (not isinstance(questions, list) or not 1 <= len(questions) <= 8
            or any(not isinstance(q, str) or not q.strip() or len(q.encode("utf-8")) > 2000 for q in questions)):
        _fail("questions")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or not (0 if retrieval_only else 1) <= len(candidates) <= 8:
        _fail("candidates")
    refs, candidate_ids = set(), set()
    for row in candidates:
        if (not isinstance(row, dict)
                or set(row) != {"ref", "interval_sec", "retrieval", "originals", "media_refs"}
                or not isinstance(row["ref"], str) or not row["ref"] or row["ref"] in candidate_ids
                or not isinstance(row["retrieval"], dict) or set(row["retrieval"]) != set(MODALITIES)
                or not isinstance(row["originals"], list) or not row["originals"]
                or not isinstance(row["media_refs"], list)
                or any(not isinstance(r, str) or not r.strip() for r in row["media_refs"])
                or not retrieval_only and not row["media_refs"]):
            _fail("candidates")
        if not _interval(row["interval_sec"]):
            _fail("clock")
        duration = source["asset"]["clock_authority"].get("duration_sec_decimal")
        if duration is not None and row["interval_sec"][1] > float(duration):
            _fail("clock")
        if retrieval_only:
            for modality, score in row["retrieval"].items():
                if score is None:
                    continue
                if (not isinstance(score, dict) or set(score) != {"cosine", "rank"}
                        or type(score["cosine"]) not in (int, float) or not math.isfinite(score["cosine"])
                        or not -1.000001 <= score["cosine"] <= 1.000001
                        or type(score["rank"]) is not int or score["rank"] < 1):
                    _fail("retrieval")
                if modality != "text" and not row["media_refs"]:
                    _fail("media")
        for original in row["originals"]:
            if (not isinstance(original, dict) or set(original) != {"ref", "text"}
                    or original["ref"] not in evidence_catalog
                    or evidence_catalog[original["ref"]].get("source_type") not in {"stt", "replay_chat"}
                    or original["text"] != evidence_catalog[original["ref"]]["source_text"]):
                _fail("original")
            refs.add(original["ref"])
        candidate_ids.add(row["ref"])
    if retrieval_only:
        return deepcopy(value)
    judge = value["judge"]
    if (not isinstance(judge, dict) or set(judge) != {"model", "response_sha256", "decisions"}
            or judge["model"] not in {"jev-1.13.0", "gpt-5.6-terra", "gpt-6-sol"}
            or not _sha(judge["response_sha256"])
            or not isinstance(judge["decisions"], list) or len(judge["decisions"]) != len(candidates)):
        _fail("judge")
    seen = set()
    for row in judge["decisions"]:
        if (not isinstance(row, dict)
                or set(row) != {"candidate_ref", "relation", "support_refs", "opposition_refs", "next_context"}
                or row["candidate_ref"] not in candidate_ids or row["candidate_ref"] in seen
                or row["relation"] not in {"prior_rule", "same_event", "later_reference", "different_event", "insufficient_context"}
                or row["next_context"] not in {"already_read", "nearby_original", "distant_original", "media_or_clock", "soft_defer"}
                or any(not isinstance(row[k], list) or any(r not in refs for r in row[k])
                       for k in ("support_refs", "opposition_refs"))):
            _fail("judge")
        seen.add(row["candidate_ref"])
    return deepcopy(value)

def _bound_json(ref, *, max_bytes=8_000_000):
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"} or not _sha(ref["sha256"]):
        _fail("cache_binding")
    path = native_path(ref["path"])
    if not path.is_file() or path.stat().st_size > max_bytes or file_hash(path) != ref["sha256"]:
        _fail("cache_changed")
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    if file_hash(path) != ref["sha256"]:
        _fail("cache_changed")
    return value, path.resolve()

def _record_interval(row):
    index = row.get("time_index")
    if not index:
        return None
    if (not isinstance(index, dict) or any(type(index.get(k)) not in (int, float)
            or not math.isfinite(index[k]) or index[k] < 0 for k in ("start_us", "end_us"))):
        return None
    if row.get("family") == "chat":

        instant = index["start_us"] / 1_000_000
        return [instant, instant]
    interval = [index["start_us"] / 1_000_000, index["end_us"] / 1_000_000]

    if interval[0] == interval[1]:
        return interval
    return interval if _interval(interval) else None

def _original_aliases(originals, source, evidence_catalog, *, current_chat_records=None):
    """Resolve exact occurrences, never equal text at another native ordinal."""
    by_id, aliases = {}, {}
    native_stt = {r["ordinal"]: r for r in source["records"]}
    for ref, item in evidence_catalog.items():
        native_ref = item.get("native_ref") or item.get("exact_source_ref")
        if native_ref:
            aliases.setdefault(native_ref, []).append(ref)
    for row in originals:
        if (row.get("source_id") != source["asset"]["source_id"] or row.get("family") not in {"stt", "chat"}
                or not isinstance(row.get("record_id"), str) or row["record_id"] in by_id
                or row.get("fragment_complete") is not True):
            _fail("cache_original")
        native = "stt-occ:" + row["record_id"][:24] if row["family"] == "stt" else "chat-original:" + row["record_id"]
        matches = [r for r in aliases.get(native, []) if evidence_catalog[r]["source_text"] == row.get("raw_text")]
        if row["family"] == "stt":
            original = native_stt.get(row.get("ordinal"))
            if (not original or any(original.get(k) != row.get(k) for k in
                    ("record_id", "asset_id", "raw_text", "raw_time", "time_index", "clock_status"))):
                _fail("cache_original")
            ref = "e" + str(row["ordinal"] + 1)
            if ref in evidence_catalog and evidence_catalog[ref]["source_text"] == row.get("raw_text"):
                matches = [ref]
        if len(matches) != 1 or evidence_catalog[matches[0]].get("source_type") != (
                "stt" if row["family"] == "stt" else "replay_chat"):
            _fail("cache_original")
        interval = _record_interval(row)
        entry = evidence_catalog[matches[0]]
        if row["family"] == "chat":
            if current_chat_records is not None:
                original = current_chat_records.get(row['record_id'])
                if (not isinstance(original, dict) or any(original.get(k) != row.get(k) for k in
                        ('source_id', 'family', 'record_id', 'asset_id', 'ordinal',
                         'raw_text', 'raw_time', 'time_index', 'clock_status'))):
                    _fail('cache_original')
            raw = row.get("raw_time")
            if (not isinstance(raw, dict) or set(raw) != {"unit", "start", "end"}
                    or raw["unit"] != "ms" or raw["start"] != raw["end"]):
                _fail("cache_original")
            projected, status = time_projection(raw["start"], raw["end"], unit=raw["unit"],
                duration_sec=source["asset"]["clock_authority"].get("duration_sec_decimal"), instant=True)
            if (projected != row.get("time_index") or status != row.get("clock_status")
                    or (entry.get("time_sec") is None) != (projected is None)):
                _fail("cache_original")
        if interval and entry.get("time_sec") != interval[0]:
            _fail("cache_original")
        by_id[row["record_id"]] = dict(row, author_ref=matches[0])
    return by_id

def prepare_cached_candidates(cache_ref, *, source, evidence_catalog, world_revision, media_hashes,
        questions=None, question_id="multimodal-navigation", top_k=3, context_sec=30,
        current_original_projection=False, current_chat_records=None, query_vectors=None):
    """Rank the completed pinned prototype cache; return hints and original-read actions.

    Context expands by time overlap into eligible STT/chat. Unknown-clock originals
    stay unlocated. Closed result/NPY files are never rewritten or inferred again.
    """
    if not isinstance(cache_ref, dict) or set(cache_ref) != {"input", "result", "model"}:
        _fail("cache_binding")
    if media_hashes is None:
        _fail("requires_bound_media")
    if type(current_original_projection) is not bool:
        _fail("cache_projection_option")
    if type(top_k) is not int or not 1 <= top_k <= 8 or type(context_sec) not in (int, float) or not 0 <= context_sec <= 120:
        _fail("query_scope")
    material, _ = _bound_json(cache_ref["input"])
    result, result_path = _bound_json(cache_ref["result"])
    model, _ = _bound_json(cache_ref["model"])
    contract = material.get("contract", {})
    if current_original_projection and (not _sha(contract.get('clock_sha256'))
                                       or not _sha(contract.get('source_revision'))):
        _fail('cache_source')
    if (contract.get("schema") != "aemeth302.temporal-cross-retrieval-input.v1"
            or contract.get("source_id") != source["asset"]["source_id"]
            or contract.get("stt_sha256") != source["asset"]["bytes_sha256"]
            or not _sha(contract.get("world_revision"))
            or (not current_original_projection and contract.get("world_revision") != world_revision)
            or (contract.get("media") or {}).get("sha") not in media_hashes):
        _fail("cache_source")
    if ((not current_original_projection and contract.get("source_revision", source["sha256"]) != source["sha256"])
            or contract.get("clock_sha256", digest(source["asset"]["clock_authority"])) != digest(source["asset"]["clock_authority"])):
        _fail("cache_source")
    if (result.get("status") != "PASS_SCOPED_LOCAL_VECTORS"
            or result.get("attempt", {}).get("source_input_sha256") != digest(material)
            or result.get("attempt", {}).get("model_revision") != REVISION
            or model.get("model") != MODEL or model.get("revision") != REVISION):
        _fail("cache_encoder")
    if (contract.get("model_id", MODEL) != MODEL or contract.get("model_revision", REVISION) != REVISION):
        _fail("cache_encoder")
    originals, windows = material.get("original_records"), material.get("windows")
    if (not isinstance(originals, list) or not originals
            or not isinstance(windows, list) or not 1 <= len(windows) <= 8):
        _fail("cache_scope")
    if query_vectors is None and questions is not None and questions != material.get("questions"):
        _fail("query_vector_unavailable")
    stored_questions = material.get("questions")
    questions = stored_questions if query_vectors is None else questions
    if (not isinstance(questions, list) or not 1 <= len(questions) <= 8
            or any(not isinstance(q, str) or not q.strip() or len(q.encode('utf-8')) > 2000 for q in questions)
            or not isinstance(stored_questions, list) or not 1 <= len(stored_questions) <= 8):
        _fail("questions")
    if (current_original_projection and any(r.get('family') == 'chat' for r in originals)
            and not isinstance(current_chat_records, dict)):
        _fail('cache_original')
    by_id = _original_aliases(originals, source, evidence_catalog,
        current_chat_records=current_chat_records if current_original_projection else None)
    stages = result.get("stages")
    if not isinstance(stages, list) or len(stages) != 2 + 3 * len(windows):
        _fail("cache_vectors")
    import numpy as np
    vectors = {}
    for stage in stages:
        name = stage["stage"]
        path = native_path(stage["path"]).resolve()
        if (name in vectors or not path.is_relative_to(result_path.parent)
                or not path.is_file() or file_hash(path) != stage["sha256"]):
            _fail("cache_changed")
        array = np.load(path, allow_pickle=False)
        if (list(array.shape) != stage["shape"] or array.ndim != 2 or array.shape[1] != DIMENSION
                or not np.isfinite(array).all() or not np.allclose(np.linalg.norm(array, axis=1), 1, atol=.005)):
            _fail("cache_vectors")
        if file_hash(path) != stage["sha256"]:
            _fail("cache_changed")
        vectors[name] = array
    stage_map = {s["stage"]: s for s in stages}
    if (stage_map.get("text", {}).get("refs") != [r["record_id"] for r in originals]
            or stage_map.get("queries", {}).get("refs") != stored_questions
            or vectors.get("text", np.empty((0,))).shape != (len(originals), DIMENSION)
            or vectors.get("queries", np.empty((0,))).shape != (len(stored_questions), DIMENSION)):
        _fail("cache_vectors")


    if query_vectors is not None:
        if (not isinstance(query_vectors, np.ndarray) or query_vectors.dtype != np.float32
                or query_vectors.shape != (len(questions), DIMENSION)
                or not np.isfinite(query_vectors).all()
                or not np.allclose(np.linalg.norm(query_vectors, axis=1), 1, atol=.005)):
            _fail('query_vectors')
        vectors['queries'] = query_vectors
    candidates, unlocated, actions = {}, [], []
    for window in windows:
        if (window.get("source_id") != source["asset"]["source_id"]
                or window.get("stt_sha256") != source["asset"]["bytes_sha256"]
                or window.get("world_revision") != contract.get("world_revision")
                or window.get("media_sha256") not in media_hashes
                or not _interval([window.get("start"), window.get("start", 0) + window.get("duration", 0)])
                or any(r not in by_id for r in window.get("members", []))):
            _fail("cache_media")
        for modality in MODALITIES[1:]:
            name = window["ref"] + "-" + modality
            media_refs = stage_map.get(name, {}).get("refs", {})
            if (vectors.get(name, np.empty((0,))).shape != (1, DIMENSION)
                    or not isinstance(media_refs, dict) or media_refs.get("window") != window["ref"]
                    or media_refs.get("members") != window.get("members")
                    or "source_interval" in media_refs and media_refs["source_interval"] != [window["start"], window["start"] + window["duration"]]
                    or "media_sha256" in media_refs and media_refs["media_sha256"] != window["media_sha256"]):
                _fail("cache_vectors")

    def offer(ref, interval, modality, score, rank, media_refs):
        if ref not in candidates:
            candidates[ref] = dict(ref=ref, interval_sec=interval, retrieval={k: None for k in MODALITIES},
                originals=[], media_refs=media_refs)
        prior = candidates[ref]["retrieval"][modality]
        if prior is None or rank < prior["rank"] or rank == prior["rank"] and score > prior["cosine"]:
            candidates[ref]["retrieval"][modality] = dict(cosine=float(score), rank=rank)

    for query in vectors["queries"]:
        scores = query @ vectors["text"].T
        for rank, index in enumerate(sorted(range(len(originals)), key=lambda i: (-float(scores[i]), i))[:top_k], 1):
            row = by_id[originals[index]["record_id"]]
            interval = _record_interval(row)
            if not interval or interval[0] == interval[1]:
                unlocated.append(dict(ref=row["author_ref"], family=row["family"], original_text=row["raw_text"],
                    uncertainty="not_a_playable_anchor"))
                continue
            offer("text-" + row["record_id"][:24], interval, "text", scores[index], rank, [])
        for modality in MODALITIES[1:]:
            ranked = sorted(windows, key=lambda w: (-float((query @ vectors[w["ref"] + "-" + modality].T)[0]), w["ref"]))
            for rank, w in enumerate(ranked[:top_k], 1):
                offer(w["ref"], [w["start"], w["start"] + w["duration"]], modality,
                    (query @ vectors[w["ref"] + "-" + modality].T)[0], rank, [w["ref"]])
    for candidate in candidates.values():
        a, b = candidate["interval_sec"]
        selected = [r for r in by_id.values() if not _record_interval(r)
            or _record_interval(r)[0] <= b + context_sec and _record_interval(r)[1] >= max(0, a - context_sec)]
        candidate["originals"] = [dict(ref=r["author_ref"], text=r["raw_text"]) for r in selected]
        actions.append(dict(schema_version="ew.tool.v1", source_id=source["asset"]["source_id"],
            world_revision=world_revision, question_id=question_id + "-" + candidate["ref"], action="read_evidence",
            args=dict(record_ids=[r["record_id"] for r in selected], families=sorted({r["family"] for r in selected}),
                context_cues=0, max_bytes=128000)))
    navigation = dict(schema_version=SCHEMA, mode="retrieval_only",
        binding=dict(source_id=source["asset"]["source_id"], source_revision=source["sha256"],
            stt_sha256=source["asset"]["bytes_sha256"], clock_sha256=digest(source["asset"]["clock_authority"]),
            world_revision=world_revision, media_sha256=contract["media"]["sha"]),
        encoder=dict(model=MODEL, revision=REVISION, dimension=DIMENSION, pooling="mean_l2",
            processor_sha256=digest(model["files"]), input_sha256=digest(material)), questions=questions,
        candidates=[])
    rows = list(candidates.values())
    pages = [dict(deepcopy(navigation), candidates=rows[offset:offset + 8])
        for offset in range(0, max(1, len(rows)), 8)]
    for page in pages:
        validate_navigation(page, source=source, evidence_catalog=evidence_catalog,
            world_revision=world_revision, media_hashes=media_hashes)
    return dict(navigation=pages[0], navigation_pages=pages, read_actions=actions, unlocated_hits=unlocated,
        availability=dict(cache_reused=True, model_calls=0, source_present=True, retrieved=True,
            original_read=False, writer_pixels_seen=False, audio_listened=False, meaning_complete=False,
            **(dict(current_original_projection=True,
                cached_world_revision=contract['world_revision'], current_world_revision=world_revision,
                exact_cached_originals_validated=len(by_id)) if current_original_projection else {})),
        review_policy=REVIEW_POLICY)

def _write_new(path, value):
    from .evidence_world.contracts import canonical_json
    with path.open("xb") as stream:
        stream.write((canonical_json(value) + "\n").encode("utf-8"))

def _memory():
    """Process resident bytes and free host RAM; no runtime/profile discovery."""
    import ctypes
    import os
    import sys
    if sys.platform.startswith('linux'):
        import resource
        try:
            resident_pages = int(Path('/proc/self/statm').read_text().split()[1])
            resident = resident_pages * os.sysconf('SC_PAGE_SIZE')
            available = next(int(line.split()[1])*1024
                for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
            peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024
            return resident, peak, available
        except (OSError, ValueError, IndexError, StopIteration):
            _fail('local_resource_probe_failed')
    if os.name != "nt":
        _fail("local_resource_probe_unsupported")
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
            *[(name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        _fail("local_resource_probe_failed")
    class Counters(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong),
            *[(name, ctypes.c_size_t) for name in ("peak", "resident", "pool_peak", "pool", "nonpool_peak", "nonpool", "page", "page_peak")]]
    counters = Counters()
    counters.size = ctypes.sizeof(counters)
    get = ctypes.windll.psapi.GetProcessMemoryInfo
    get.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not get(handle, ctypes.byref(counters), counters.size):
        _fail("local_resource_probe_failed")
    return int(counters.resident), int(counters.peak), int(status.available)

def _load_local_model(model, sdk_root, cpu_threads, *, device='auto'):
    """Load only the explicitly supplied public pin and existing isolated SDK."""
    import sys
    _validate_local_model_manifest(model)
    folder = native_path(model["root"]).resolve(strict=True)
    for row in model["files"]:
        path = (folder / row["file"]).resolve(strict=True)
        if not path.is_relative_to(folder) or file_hash(path) != row["sha256"]:
            _fail("local_model_changed")
    sdk = native_path(sdk_root).resolve(strict=True)
    sys.path.insert(0, str(sdk))
    try:
        import transformers
        import sentence_transformers
        import torch
        if (transformers.__version__ != "5.19.0" or sentence_transformers.__version__ != "6.1.0"
                or not native_path(transformers.__file__).resolve().is_relative_to(sdk)
                or not native_path(sentence_transformers.__file__).resolve().is_relative_to(sdk)):
            _fail("local_sdk_mismatch")
        torch.set_num_threads(cpu_threads)
        if device not in {'auto','cpu','cuda','mps'}:
            _fail('local_device')
        cuda = torch.cuda.is_available()
        if device == 'auto':
            device = 'cuda' if cuda else ('mps' if torch.backends.mps.is_available() else 'cpu')
        if device == 'cuda' and not cuda:
            _fail('local_cuda_unavailable')
        if device == 'cuda' and torch.cuda.mem_get_info()[0] < 4*1024**3:
            _fail('local_insufficient_vram')
        if device == 'mps' and not torch.backends.mps.is_available():
            _fail('local_mps_unavailable')
        dtype = torch.bfloat16 if device=='cuda' and torch.cuda.is_bf16_supported() else torch.float32
        instance = sentence_transformers.SentenceTransformer(str(folder), device=device, local_files_only=True,
            trust_remote_code=False, token=False, model_kwargs={"dtype": dtype},
            processor_kwargs={"model_max_length": 8192})
        instance[0].processing_kwargs = {"text": {"truncation": False},
            "audio": {"sampling_rate": 16000}, "video": {"do_sample_frames": False}}
        if instance.get_embedding_dimension() != DIMENSION:
            _fail("local_model_dimension")
        return instance
    finally:
        sys.path.remove(str(sdk))

def _validate_local_model_manifest(model):
    files = model.get('files') if isinstance(model, dict) else None
    if (not isinstance(model, dict) or model.get('model') != MODEL or model.get('revision') != REVISION
            or not isinstance(model.get('root'), str) or not model['root']
            or not isinstance(files, list) or len(files) != len(LOCAL_MODEL_FILES)
            or any(not isinstance(r, dict) or not isinstance(r.get('file'), str)
                   or not _sha(r.get('sha256')) for r in files)
            or {r.get('file') for r in files} != LOCAL_MODEL_FILES
            or next(r['sha256'] for r in files if r['file'] == 'model.safetensors') != WEIGHT_SHA256):
        _fail('local_model_pin')

RECOVERABLE_LOCAL_ERRORS = frozenset({
    'local_media_missing', 'local_model_metadata_missing', 'local_model_file_missing',
    'local_sdk_missing', 'local_vod_clock_required', 'local_resource_probe_unsupported',
    'local_session_budget_exhausted', 'local_request_pending_no_retry',
    'local_request_failed_no_retry', 'local_unsettled_output_no_retry',
    'local_process_deadline', 'local_worker_failed', 'local_insufficient_ram',
    'local_resource_limit', 'local_original_scope_no_truncation', 'local_window_clock_anchor',
})

def unavailable_navigation(exc, session):
    code = str(exc).removeprefix('full_author_navigation_')
    if code not in RECOVERABLE_LOCAL_ERRORS:
        raise exc
    return dict(status='unavailable', reason=code, multimodal_context=session.snapshot(),
                read_actions=[], original_read=False)

def resolve_local_runtime(value=None):
    if value is not None:
        return validate_local_runtime(value)
    import importlib.util
    import os
    workspace = os.environ.get('LOKI_WORKSPACE')
    if not workspace:
        return None
    loader = importlib.util.spec_from_file_location('loki_configured_runtime', Path(__file__).resolve().parents[1] / 'loki.py')
    entry = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(entry)
    config = entry.contained(Path(workspace).resolve(), os.environ.get('LOKI_CONFIG_PATH') or Path(workspace) / 'loki.json')
    root, _, policy = entry.configuration(config)
    if root != Path(workspace).resolve():
        _fail('local_workspace_config')
    return entry.multimodal_runtime(root, policy) if policy['features'] == 'multimodal' else None

def validate_local_runtime(value):
    """Normalize trusted offline assets/limits; never import or run an encoder."""
    if (not isinstance(value, dict) or set(value) != {'model_ref', 'sdk_root', 'limits', 'device'}
            or value['device'] not in {'cpu', 'cuda', 'mps'}
            or not isinstance(value['sdk_root'], str) or not value['sdk_root']
            or not isinstance(value['limits'], dict) or set(value['limits']) - set(LOCAL_LIMITS)):
        _fail('local_runtime')
    ref = value['model_ref']
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
            or not isinstance(ref['path'], str) or not ref['path'] or not _sha(ref['sha256'])):
        _fail('local_runtime')
    limits = {**LOCAL_LIMITS, **value['limits']}
    caps = dict(cpu_threads=4, max_seconds=600, max_memory_bytes=12_000_000_000,
                max_requests=2, max_total_seconds=1200)
    for name, cap in caps.items():
        v = limits[name]
        if (type(v) not in ((int,) if name in {'cpu_threads', 'max_memory_bytes', 'max_requests'} else (int, float))
                or not 1 <= v <= cap):
            _fail('local_limits')
    normalized = dict(model_ref=dict(path=str(native_path(ref['path']).resolve()), sha256=ref['sha256']),
        sdk_root=str(native_path(value['sdk_root']).resolve()), limits=limits, device=value['device'])
    if native_path(ref['path']).is_file():
        model, _ = _bound_json(normalized['model_ref'])
        _validate_local_model_manifest(model)
    return normalized

def _local_runtime_assets(runtime, *, verify_files=False):
    """Read only public model/SDK metadata; missing assets are explicit."""
    from importlib.metadata import distributions
    ref = runtime['model_ref']
    if not native_path(ref['path']).is_file():
        return None, 'local_model_metadata_missing'
    model, _ = _bound_json(ref)
    _validate_local_model_manifest(model)
    folder = native_path(model['root']).resolve()
    for row in model['files']:
        path = (folder / row['file']).resolve()
        if not path.is_relative_to(folder):
            _fail('local_model_changed')
        if not path.is_file():
            return None, 'local_model_file_missing'
        if verify_files and file_hash(path) != row['sha256']:
            _fail('local_model_changed')
    sdk = native_path(runtime['sdk_root'])
    if not sdk.is_dir() or any(not (sdk / package / '__init__.py').is_file()
                              for package in ('transformers', 'sentence_transformers')):
        return None, 'local_sdk_missing'
    versions = {d.metadata.get('Name', '').replace('_', '-').lower(): d.version
                for d in distributions(path=[str(sdk)])}
    if versions.get('transformers') != '5.19.0' or versions.get('sentence-transformers') != '6.1.0':
        _fail('local_sdk_mismatch')
    return model, None

def _decode_window(media, window, folder, deadline):
    import subprocess
    import time
    import wave
    import numpy as np
    from PIL import Image
    from .media_tools.executor import _tool_paths
    ffmpeg, _ = _tool_paths()
    remaining = min(120, deadline - time.monotonic())
    if remaining <= 0:
        _fail("local_deadline")
    from .collected_vod_media import collected_media_window
    original_media = native_path(media)
    media, offset = collected_media_window(original_media, window, folder)


    seek = (["-i", str(media), "-ss", str(offset)] if media != original_media
            else ["-ss", str(offset), "-i", str(media)])
    common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-threads", "2", *seek,
        "-t", str(window["duration"]), "-threads", "2"]
    audio = subprocess.run([*common, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
        capture_output=True, timeout=remaining, check=True).stdout
    samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
    if not len(samples) or len(samples) > 31 * 16000:
        _fail("local_audio_scope")
    audio_path = folder / "requested.wav"
    with wave.open(str(audio_path), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(audio)
    frame_count = math.ceil(window["duration"])
    remaining = min(120, deadline - time.monotonic())
    if remaining <= 0:
        _fail("local_deadline")
    decoded = subprocess.run([*common, "-an", "-vf", "fps=1,scale=256:144", "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, timeout=remaining, check=True).stdout
    if not decoded or len(decoded) % (144 * 256 * 3):
        _fail("local_video_decode")
    video = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 144, 256, 3)
    if not 1 <= len(video) <= 30:
        _fail("local_video_scope")
    indices = sorted({0, len(video) // 2, len(video) - 1})
    images, frames = [], []
    for i in indices:
        image = Image.fromarray(video[i]).convert("RGB")
        path = folder / ("frame-" + str(i) + ".png")
        image.save(path)
        images.append(image)
        frames.append(dict(path=str(path), sha256=file_hash(path), source_time_estimate_sec=window["start"] + i,
            sampling_clock="declared seek plus fps=1 sample ordinal; original frame PTS not independently verified"))
    video_path = folder / "decoded-1fps.npy"
    np.save(video_path, video, allow_pickle=False)
    return samples, images, video, dict(audio=dict(path=str(audio_path), sha256=file_hash(audio_path), sampling_rate=16000),
        frames=frames, video=dict(path=str(video_path), sha256=file_hash(video_path), fps=1,
            frames=len(video), audio_track_embedded=False))

def build_local_cache(*, source, evidence_catalog, world_revision, media_ref, questions, windows, originals,
        output_root, model_ref, sdk_root, device='auto', cpu_threads=4, max_seconds=600, max_memory_bytes=12_000_000_000):
    """Encode only explicitly selected new-job windows on CPU with existing assets.

    The output directory must not exist. Calling this opt-in function is the only
    model execution path; cache loading and hint validation never start inference.
    """
    import os
    import time
    from .evidence_world.records import assert_asset_fresh
    from .evidence_world.contracts import AssetBinding
    if (type(cpu_threads) is not int or not 1 <= cpu_threads <= 4
            or type(max_seconds) not in (int, float) or not 1 <= max_seconds <= 600
            or type(max_memory_bytes) is not int or not 1 <= max_memory_bytes <= 12_000_000_000
            or not isinstance(questions, list) or not 1 <= len(questions) <= 8
            or any(not isinstance(q, str) or not q.strip() or len(q.encode("utf-8")) > 2000 for q in questions)
            or not isinstance(originals, list) or not originals
            or not isinstance(windows, list) or not 1 <= len(windows) <= 3 or not _sha(world_revision)):
        _fail("local_scope")
    if source["asset"]["clock_authority"].get("kind") == "live":
        _fail("local_vod_clock_required")
    by_id = _original_aliases(originals, source, evidence_catalog)
    model, _ = _bound_json(model_ref)
    if not isinstance(media_ref, dict) or set(media_ref) != {"path", "sha256"} or not _sha(media_ref["sha256"]):
        _fail("local_media_binding")
    media = native_path(media_ref["path"]).resolve(strict=True)
    if not media.is_file() or file_hash(media) != media_ref["sha256"]:
        _fail("local_media_changed")
    from .collected_vod_media import assert_collected_media_fresh
    assert_collected_media_fresh(media, intervals=[[max(0,w['start']-5),w['start']+w['duration']] for w in windows])
    duration = float(source["asset"]["clock_authority"]["duration_sec_decimal"])
    refs, record_ids = set(), {r["record_id"] for r in originals}
    for w in windows:
        if (not isinstance(w, dict) or set(w) != {"ref", "start", "duration", "members"}
                or not isinstance(w["ref"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", w["ref"])
                or w["ref"] in refs or type(w["duration"]) not in (int, float) or not 1 <= w["duration"] <= 30
                or not _interval([w["start"], w["start"] + w["duration"]])
                or w["start"] + w["duration"] > duration
                or not isinstance(w["members"], list) or not w["members"] or not set(w["members"]) <= record_ids):
            _fail("local_window")
        refs.add(w["ref"])
        if not any(by_id[r]["family"] == "stt" and (t := _record_interval(by_id[r]))
                and t[0] < w["start"] + w["duration"] and t[1] > w["start"] for r in w["members"]):
            _fail("local_window_clock_anchor")
    root = native_path(output_root).resolve()
    if root.exists():
        _fail("local_new_output_required")
    assert_asset_fresh(AssetBinding(**source["asset"]))
    _, _, free = _memory()
    if free < 8 * 1024 ** 3:
        _fail("local_insufficient_ram")
    root.mkdir(parents=True)
    started, deadline, stages = time.monotonic(), time.monotonic() + max_seconds, []
    settings = dict(HF_HOME=str(root / "offline-cache"), HF_HUB_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_TELEMETRY="1", TRANSFORMERS_OFFLINE="1")
    prior = {k: os.environ.get(k) for k in settings}
    os.environ.update(settings)
    try:
        import numpy as np
        model_instance = _load_local_model(model, sdk_root, cpu_threads, device=device)
        tokenizer = model_instance[0].processor.tokenizer
        if any(len(tokenizer(t)["input_ids"]) > 8192 for t in [*(r["raw_text"] for r in originals), *questions]):
            _fail("local_text_context")
        window_inputs = []
        for w in windows:
            folder = root / "media" / w["ref"]
            folder.mkdir(parents=True)
            audio, images, video, derived = _decode_window(media, w, folder, deadline)
            window_inputs.append((w, audio, images, video, derived))
        material = dict(contract=dict(schema="aemeth302.temporal-cross-retrieval-input.v1",
            source_id=source["asset"]["source_id"], source_revision=source["sha256"],
            stt_sha256=source["asset"]["bytes_sha256"], clock_sha256=digest(source["asset"]["clock_authority"]),
            world_revision=world_revision, model_id=MODEL, model_revision=REVISION,
            media=dict(path=str(media), sha=media_ref["sha256"], start=0, end=duration),
            coverage="All STT in selected text scope plus media anchors, representative chat only; media uses separate short windows, not whole-broadcast coverage"),
            original_records=deepcopy(originals), questions=questions,
            windows=[dict(w, source_id=source["asset"]["source_id"], stt_sha256=source["asset"]["bytes_sha256"],
                world_revision=world_revision, media_sha256=media_ref["sha256"], derived_media=derived,
                audio_listened=False, clock="source-bound declared VOD window; no ASR or PTS retiming")
                for w, _, _, _, derived in window_inputs], source_clock_uncertainties_preserved=True,
            new_provider_calls=0, whole="NOT_DONE")
        _write_new(root / "input.json", material)

        def record(name, vector, refs):
            array = np.asarray(vector, dtype=np.float32).reshape(-1, DIMENSION)
            count = len(refs) if isinstance(refs, list) else 1
            if (array.shape != (count, DIMENSION) or not np.isfinite(array).all()
                    or not np.allclose(np.linalg.norm(array, axis=1), 1, atol=.005)):
                _fail("local_nonfinite_vector")
            resident, peak, _ = _memory()
            if resident > max_memory_bytes or time.monotonic() >= deadline:
                _fail("local_resource_limit")
            path = root / ("vector-" + name + ".npy")
            np.save(path, array, allow_pickle=False)
            stages.append(dict(stage=name, shape=list(array.shape), path=str(path), sha256=file_hash(path),
                refs=refs, working_set_bytes=resident, peak_working_set_bytes=peak))

        from importlib.util import find_spec
        shared = find_spec('lancedb') is not None
        if shared:
            from .editorial_embedding_index import encode_cached,encode_media_cached,query_cache_root
            from .config import runtime_data_root


            shared_root=native_path(runtime_data_root())/'work'/str(source['asset']['video_no'])/'embedding-index'
            runtime=dict(model_ref=model_ref,sdk_root=sdk_root,limits=dict(cpu_threads=cpu_threads),device=device)
            documents,_=encode_cached([r['raw_text'] for r in originals],role='document',
                root=shared_root,runtime=runtime,model=model_instance)
            queries,_=encode_cached(questions,role='query',root=query_cache_root(),runtime=runtime,model=model_instance)
        else:
            documents=model_instance.encode_document([r['raw_text'] for r in originals],
                normalize_embeddings=True,batch_size=2,show_progress_bar=False)
            queries=model_instance.encode_query(questions,normalize_embeddings=True,batch_size=2,show_progress_bar=False)
        record('text',documents,[r['record_id'] for r in originals])
        record('queries',queries,questions)
        for w, audio, images, video, _ in window_inputs:
            for modality, content in (("image", images), ("audio", audio), ("video", video)):
                vector=(encode_media_cached(content,modality=modality,root=shared_root,runtime=runtime,
                    model=model_instance,media_sha256=media_ref['sha256'],window=w) if shared else
                    model_instance.encode({modality:content},normalize_embeddings=True,batch_size=1,show_progress_bar=False))
                record(w["ref"] + "-" + modality, vector, dict(window=w["ref"], members=w["members"],
                    source_interval=[w["start"], w["start"] + w["duration"]], media_sha256=media_ref["sha256"]))
        assert_asset_fresh(AssetBinding(**source["asset"]))
        if file_hash(media) != media_ref["sha256"]:
            _fail("local_media_changed")
        assert_collected_media_fresh(media, intervals=[[max(0,w['start']-5),w['start']+w['duration']] for w in windows])
        result = dict(status="PASS_SCOPED_LOCAL_VECTORS", attempt=dict(source_input_sha256=digest(material),
            model_revision=REVISION, cpu_threads=cpu_threads,
            gpu=int(str(getattr(model_instance,'device','cpu')).split(':')[0] in {'cuda','mps'}),
            device=str(model_instance.device), provider_calls=0), stages=stages,
            elapsed_sec=time.monotonic() - started, meaning="UNVERIFIED; retrieval candidates only", whole="NOT_DONE")
        _write_new(root / "result.json", result)
        return dict(input=dict(path=str(root / "input.json"), sha256=file_hash(root / "input.json")),
            result=dict(path=str(root / "result.json"), sha256=file_hash(root / "result.json")), model=deepcopy(model_ref))
    except Exception as exc:
        _write_new(root / "failure.json", dict(status="BLOCKED_EXACT_LOCAL_CACHE", error_type=type(exc).__name__,
            error=str(exc)[:300], stages=stages, provider_calls=0, elapsed_sec=time.monotonic() - started))
        raise
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

class LocalNavigationSession:
    """One job's bounded producer/retrieval tool, sharing the existing offline worker.

    Trusted caller supplies paths/assets. The semantic writer supplies only questions
    and VOD intervals. Persisted attempts survive repair turns and process restarts.
    """
    def __init__(self, source, service, media_ref, runtime, root):
        self.source, self.service = deepcopy(source), service
        self.runtime = validate_local_runtime(runtime)
        self.root = native_path(root).resolve()
        if media_ref is not None and (not isinstance(media_ref, dict) or set(media_ref) != {'path', 'sha256'}
                or not isinstance(media_ref['path'], str) or not media_ref['path'] or not _sha(media_ref['sha256'])):
            _fail('local_media_binding')
        self.media_ref = (dict(path=str(native_path(media_ref['path']).resolve()), sha256=media_ref['sha256'])
                          if media_ref else None)
        asset = self.source['asset']
        current = next((a for a in service.assets.values() if a.family == 'stt'), None)
        if (current is None or service.source_id != asset['source_id']
                or any(current.internal().get(k) != asset.get(k) for k in
                       ('asset_id', 'bytes_sha256', 'clock_authority'))):
            _fail('local_source')
        self.unavailable_clock = (asset['clock_authority'].get('kind') == 'live'
                                  or asset['source_locator'].get('kind') == 'temporal_projection')
        self.binding = dict(schema='editorial.local-navigation-session.v1',
            source_id=service.source_id, world_revision=service.world_revision,
            source_revision=self.source['sha256'], stt_sha256=asset['bytes_sha256'],
            clock_sha256=digest(asset['clock_authority']), media=self.media_ref, runtime=self.runtime)

    def _state(self):
        path = self.root / 'state.json'
        if not path.is_file():
            if any(self.root.glob('request-*')):
                _fail('local_session_state_missing_no_retry')
            return dict(binding=self.binding, requests=[])
        stored = json.loads(path.read_text(encoding='utf-8'))
        state = stored.get('payload')
        if (not isinstance(state, dict) or stored.get('sha256') != digest(state)
                or state.get('binding') != self.binding or not isinstance(state.get('requests'), list)):
            _fail('local_session_binding_changed')
        return state

    def _save(self, state):
        from .evidence_world.index import atomic_json
        atomic_json(self.root / 'state.json', dict(payload=state, sha256=digest(state)))

    def snapshot(self):
        import os
        import sys
        state = self._state()
        _, reason = _local_runtime_assets(self.runtime)
        if not self.media_ref or not native_path(self.media_ref['path']).is_file():
            reason = 'local_media_missing'
        if self.unavailable_clock:
            reason = 'local_vod_clock_required'
        search_reason = reason
        settled_count = sum(r['status'] == 'settled' for r in state['requests'])
        spent = sum(r['charged_seconds'] for r in state['requests'])
        limits = self.runtime['limits']
        if len(state['requests']) >= limits['max_requests'] or limits['max_total_seconds']-spent < 1:
            reason = 'local_session_budget_exhausted'
        if any(r['status'] == 'pending' for r in state['requests']):
            reason = 'local_request_pending_no_retry'
        if not reason and os.name != 'nt' and not sys.platform.startswith('linux'):
            reason = 'local_resource_probe_unsupported'
        return dict(status='unavailable' if reason else 'ready', reason=reason,
            status_scope='new_media_production',
            saved_search=dict(status='unavailable' if search_reason or not settled_count else 'ready',
                reason=search_reason or (None if settled_count else 'local_settled_request_missing'),
                settled_requests=settled_count, whole_broadcast=False,
                coverage_policy="Only settled windows in this job; exact intervals_sec are returned with search results. Other jobs and unindexed areas are outside this search."),
            limits=deepcopy(self.runtime['limits']), used=dict(requests=len(state['requests']), elapsed_seconds=spent),
            model=dict(id=MODEL, revision=REVISION, dimension=DIMENSION),
            coverage_policy='All STT in text_intervals_sec plus media anchors; representative chat only from text scope. Text defaults to intervals_sec; image/audio/video use only 1..30 second intervals_sec windows.',
            validation_policy='Available files are not an encoder run; execute rechecks source/model/media hashes.',
            video_sampling=dict(fps=1, original_pts_verified=False), original_read=False,
            writer_pixels_seen=False, audio_listened=False, meaning_complete=False)

    def _validate_action(self, action):
        if (not isinstance(action, dict)
                or set(action) != {'schema_version', 'source_id', 'world_revision', 'question_id', 'action', 'args'}
                or action.get('schema_version') != 'ew.tool.v1' or action.get('action') != 'navigate_multimodal'
                or not isinstance(action.get('question_id'), str) or not action['question_id'].strip()):
            _fail('local_action')
        if action['source_id'] != self.service.source_id:
            _fail('local_source')
        if action['world_revision'] != self.service.world_revision:
            _fail('local_world')
        if self.unavailable_clock:
            _fail('local_vod_clock_required')
        args = action['args']
        if (not isinstance(args, dict) or 'questions' not in args
                or set(args) - {'questions', 'intervals_sec', 'text_intervals_sec'}):
            _fail('local_args')
        questions = args['questions']
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 8
                or any(not isinstance(q, str) or not q.strip() or len(q.encode('utf-8')) > 2000 for q in questions)):
            _fail('local_questions')
        if 'intervals_sec' not in args:
            if 'text_intervals_sec' in args:
                _fail('local_text_intervals')
            return deepcopy(args)
        intervals = args['intervals_sec']
        duration = float(self.source['asset']['clock_authority']['duration_sec_decimal'])
        try:
            valid = (isinstance(intervals, list) and 1 <= len(intervals) <= 3
                and all(_interval(w) and 1 <= w[1]-w[0] <= 30 and w[1] <= duration for w in intervals)
                and len({tuple(w) for w in intervals}) == len(intervals))
        except OverflowError:
            valid = False
        if not valid:
            _fail('local_intervals')
        if 'text_intervals_sec' in args:
            text_intervals = args['text_intervals_sec']
            try:
                valid = (isinstance(text_intervals, list) and bool(text_intervals)
                    and all(_interval(w) and w[1] <= duration for w in text_intervals))
            except OverflowError:
                valid = False
            if not valid:
                _fail('local_text_intervals')
        return deepcopy(args)

    def _assert_sources(self):
        from .evidence_world.records import assert_asset_fresh
        if not self.media_ref:
            _fail('local_media_missing')
        for asset in self.service.assets.values():
            assert_asset_fresh(asset)
        path = native_path(self.media_ref['path'])
        if not path.is_file() or file_hash(path) != self.media_ref['sha256']:
            _fail('local_media_changed')
        _, reason = _local_runtime_assets(self.runtime)
        if reason:
            _fail(reason)

    def _native_material(self, text_intervals, media_intervals=None):
        """Embed exact STT and semantic chat representatives; retain native chat lookup."""
        media_intervals = text_intervals if media_intervals is None else media_intervals
        clauses, values = [], []

        for a, b in dict.fromkeys(tuple(w) for w in [*text_intervals, *media_intervals]):
            clauses.append("(family='stt' AND start_us<? AND end_us>?)")
            values.extend((b*1_000_000, a*1_000_000))
        for a, b in text_intervals:
            clauses.append("(family='chat' AND start_us>=? AND start_us<?)")
            values.extend((a*1_000_000, b*1_000_000))
        with self.service.open() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM records WHERE '
                + ' OR '.join(clauses) + ' ORDER BY family,ordinal', values)]
        source_map = {r['record_id']: r for r in self.source['records']}
        keys = ('source_id', 'family', 'record_id', 'asset_id', 'ordinal',
                'raw_text', 'raw_time', 'time_index', 'clock_status')
        originals = []
        for row in rows:
            if row.get('source_id') != self.service.source_id:
                _fail('local_original_source')
            if not isinstance(row.get('raw_text'), str) or not _record_interval(row):
                continue
            if row['family'] == 'stt':
                original = source_map.get(row['record_id'])
                if not original or any(row.get(k) != original.get(k) for k in keys):
                    _fail('local_original_source')
            originals.append(dict(row, fragment_complete=True))
        chats = {r['record_id']: r for r in originals if r['family'] == 'chat'}
        from .evidence_world.records import chat_analysis_rows
        from .chat_semantic_sampler import sample_semantic_chat_rows_with_stats
        analysis = list(chat_analysis_rows(self.service, list(chats.values())))
        sampled = sample_semantic_chat_rows_with_stats(analysis,
            context_text='\n'.join(r['raw_text'] for r in originals if r['family'] == 'stt'),
            include_source_index=True)
        selected = {analysis[r['source_index']]['record_id'] for r in sampled.rows}
        originals = [r for r in originals if r['family'] == 'stt' or r['record_id'] in selected]
        windows = []
        for a, b in media_intervals:
            members = [r for r in originals if (t := _record_interval(r))
                and (t[0] < b and t[1] > a if r['family'] == 'stt' else a <= t[0] < b)]
            if not any(r['family'] == 'stt' and _interval(_record_interval(r)) for r in members):
                _fail('local_window_clock_anchor')
            windows.append(dict(ref='w-' + digest([a,b])[:20], start=a, duration=b-a,
                                members=[r['record_id'] for r in members]))
        from .editorial_full_author import full_source_context
        _, evidence = full_source_context(source=self.source, packet=dict(chat_rows=[dict(
            ref='chat-original:' + r['record_id'], text=r['raw_text'], time_sec=_record_interval(r)[0])
            for r in chats.values()]))
        return originals, windows, evidence['evidence_catalog'], chats

    def _retrieve(self, ref, *, evidence, chats, question_id):
        return prepare_cached_candidates(ref, source=self.source, evidence_catalog=evidence,
            world_revision=self.service.world_revision, media_hashes={self.media_ref['sha256']},
            question_id=question_id, current_original_projection=True, current_chat_records=chats)

    def _search_settled(self, args, *, question_id, allow_encode):
        """Search this job's existing vectors; only the new question is encoded."""
        import numpy as np
        from .evidence_world.index import build_lock
        from .editorial_multimodal_search import _basis, _query_vector
        from .editorial_full_author import full_source_context
        current = [{key: entry[key] for key in ('request_key', 'result_ref')}
                   for entry in self._state()['requests'] if entry['status'] == 'settled']
        if not current:
            _fail('local_settled_request_missing')
        with build_lock(self.root):
            scope_path = self.root/'_multimodal-search'/('scope-' + digest(args) + '.json')
            if scope_path.is_file():
                stored = json.loads(scope_path.read_text(encoding='utf-8'))
                scope = stored.get('payload')
                if (not isinstance(scope, dict) or stored.get('sha256') != digest(scope)
                        or scope.get('args') != args or not isinstance(scope.get('requests'), list)
                        or not scope['requests'] or any(entry not in current for entry in scope['requests'])):
                    _fail('local_query_scope_changed')
            else:
                if not allow_encode:
                    _fail('local_query_scope_missing_no_retry')


                scope = dict(args=args, requests=current)
                scope_path.parent.mkdir(exist_ok=True)
                _write_new(scope_path, dict(payload=scope, sha256=digest(scope)))
            settled = scope['requests']
        loaded = []
        for entry in settled:
            cached, _ = _bound_json(entry['result_ref'])
            ref = cached['cache_ref']
            material, _ = _bound_json(ref['input'])
            chat_ids = [r['record_id'] for r in material['original_records'] if r['family'] == 'chat']
            chats = {}
            with self.service.open() as db:
                for offset in range(0, len(chat_ids), 500):
                    batch = chat_ids[offset:offset+500]
                    for row in db.execute('SELECT record_id,payload_json FROM records WHERE record_id IN ('
                            + ','.join('?' for _ in batch) + ')', batch):
                        chats[row[0]] = json.loads(row[1])
            _, evidence = full_source_context(source=self.source, packet=dict(chat_rows=[dict(
                ref='chat-original:' + r['record_id'], text=r['raw_text'], time_sec=_record_interval(r)[0])
                for r in chats.values()]))
            kwargs = dict(source=self.source, evidence_catalog=evidence['evidence_catalog'],
                world_revision=self.service.world_revision, media_hashes={self.media_ref['sha256']},
                current_original_projection=True, current_chat_records=chats, question_id=question_id)
            prepare_cached_candidates(ref, **kwargs)
            loaded.append((ref, kwargs, material))
        models = [_bound_json(ref['model'])[0] for ref, _, _ in loaded]
        profile = _basis(models[0])
        if any(_basis(model) != profile for model in models):
            _fail('cache_encoder')
        encoded = [_query_vector(q, work_dir=self.root, profile=profile, runtime=self.runtime,
                                 allow_encode=allow_encode) for q in args['questions']]
        self._assert_sources()
        vectors = np.concatenate([vector for vector, _ in encoded])
        results = [prepare_cached_candidates(ref, questions=args['questions'], query_vectors=vectors, **kwargs)
                   for ref, kwargs, _ in loaded]
        pages = [page for result in results for page in result['navigation_pages']]
        intervals = sorted({(w['start'], w['start']+w['duration'])
                            for _, _, material in loaded for w in material['windows']})
        originals = {r['record_id'] for _, _, material in loaded for r in material['original_records']}
        return dict(navigation=pages[0], navigation_pages=pages,
            read_actions=list({digest(a):a for result in results for a in result['read_actions']}.values()),
            unlocated_hits=[h for result in results for h in result['unlocated_hits']],
            availability=dict(cache_reused=True, model_calls=0, source_present=True, retrieved=True,
                original_read=False, writer_pixels_seen=False, audio_listened=False, meaning_complete=False),
            coverage=dict(intervals_sec=[list(w) for w in intervals], native_originals=len(originals),
                whole_broadcast=False, original_pts_verified=False), review_policy=REVIEW_POLICY,
            used=self.snapshot()['used'], encoder_executed=False,
            query_encoded=any(not reused for _, reused in encoded),
            request_replayed=all(reused for _, reused in encoded))

    def execute(self, action, *, allow_encode=True):
        """Settle once; repeat only verified cache reads, never an uncertain encoder call."""
        import time
        from .evidence_world.index import build_lock, atomic_json
        if type(allow_encode) is not bool:
            _fail('local_encode_option')
        args = self._validate_action(action)
        if not allow_encode and not (self.root / 'state.json').is_file():
            _fail('local_settled_request_missing')
        self._assert_sources()
        if 'intervals_sec' not in args:
            return self._search_settled(args, question_id=action['question_id'], allow_encode=allow_encode)
        originals, windows, evidence, chats = self._native_material(
            args.get('text_intervals_sec', args['intervals_sec']), args['intervals_sec'])

        request_key = digest(args)
        with build_lock(self.root):
            state = self._state()
            previous = next((r for r in state['requests'] if r['request_key'] == request_key), None)
            if previous:
                if previous['status'] != 'settled':
                    _fail('local_request_' + previous['status'] + '_no_retry')
                cached, _ = _bound_json(previous['result_ref'])
                retrieved = self._retrieve(cached['cache_ref'], evidence=evidence, chats=chats,
                                           question_id=action['question_id'])
                return {**cached['result'], **retrieved, 'used':self.snapshot()['used'],
                        'encoder_executed':False, 'request_replayed':True}
            if not allow_encode:
                _fail('local_settled_request_missing')
            if any(r['status'] == 'pending' for r in state['requests']):
                _fail('local_request_pending_no_retry')
            limits = self.runtime['limits']
            spent = sum(r['charged_seconds'] for r in state['requests'])
            remaining = limits['max_total_seconds'] - spent
            if len(state['requests']) >= limits['max_requests'] or remaining < 1:
                _fail('local_session_budget_exhausted')
            request_limit = min(limits['max_seconds'], remaining)
            folder = self.root / ('request-' + request_key)
            if folder.exists():
                _fail('local_unsettled_output_no_retry')
            folder.mkdir()
            entry = dict(request_key=request_key, status='pending', charged_seconds=request_limit,
                         reserved_seconds=request_limit)
            state['requests'].append(entry)
            self._save(state)
            started = time.monotonic()
            try:
                spec = dict(source=self.source, evidence_catalog=evidence, world_revision=self.service.world_revision,
                    media_ref=self.media_ref, questions=args['questions'], windows=windows, originals=originals,
                    output_root=str(folder / 'cache'), model_ref=self.runtime['model_ref'], sdk_root=self.runtime['sdk_root'],
                    device=self.runtime['device'],
                    cpu_threads=limits['cpu_threads'], max_seconds=request_limit,
                    max_memory_bytes=limits['max_memory_bytes'])
                spec_path = folder / 'spec.json'
                _write_new(spec_path, spec)
                ref = run_local_spec(spec_path)
                self._assert_sources()
                retrieved = self._retrieve(ref, evidence=evidence, chats=chats, question_id=action['question_id'])
                result = dict(**retrieved, encoder_executed=True, request_replayed=False,
                    coverage=dict(intervals_sec=args['intervals_sec'], native_originals=len(originals),
                        whole_broadcast=False, original_pts_verified=False),
                    cache_receipt={k:v['sha256'] for k,v in ref.items()})
                result_path = folder / 'settled.json'
                _write_new(result_path, dict(cache_ref=ref, result=result))
                entry.update(status='settled', charged_seconds=max(0., time.monotonic()-started),
                    result_ref=dict(path=str(result_path), sha256=file_hash(result_path)))
                self._save(state)
                return {**result, 'used':self.snapshot()['used']}
            except Exception as exc:
                entry.update(status='failed', charged_seconds=max(0., time.monotonic()-started), error_type=type(exc).__name__)
                atomic_json(folder / 'failed.json', dict(status='failed', error_type=type(exc).__name__,
                    elapsed_seconds=entry['charged_seconds'], automatic_retry=False))
                self._save(state)
                raise

def main(argv=None):
    """Explicit local spec with an outer process deadline; no fallback/discovery."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        spec = json.loads(native_path(args.spec).read_text(encoding="utf-8-sig"))
        kind = spec.pop('kind', None)
        if kind not in {None, 'search_query'}:
            _fail('local_spec_kind')
        if kind == 'search_query':
            from .editorial_multimodal_search import build_query_cache
            ref = build_query_cache(**spec)
        else:
            ref = build_local_cache(**spec)
        print(json.dumps(ref, ensure_ascii=False))
        return 0
    print(json.dumps(run_local_spec(args.spec), ensure_ascii=False))
    return 0

def run_local_spec(spec_path):
    """Shared existing worker/deadline; capture only child stdout, never app stdout."""
    import subprocess
    import sys
    import time
    spec = json.loads(native_path(spec_path).read_text(encoding='utf-8-sig'))
    limit = spec.get("max_seconds", 600)
    if type(limit) not in (int, float) or not 1 <= limit <= 600:
        _fail("local_scope")
    root = native_path(spec["output_root"]).resolve()
    if root.exists():
        _fail("local_new_output_required")
    started = time.monotonic()
    try:
        completed = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "multimodal_worker.py"),
            "--workspace", __import__("os").environ["LOKI_WORKSPACE"], "--spec", str(spec_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=limit)
    except subprocess.TimeoutExpired:

        root.mkdir(parents=True, exist_ok=True)
        _write_new(root / "process-deadline.json", dict(status="BLOCKED_EXACT_LOCAL_CACHE",
            error="local_process_deadline", elapsed_sec=time.monotonic() - started, max_seconds=limit,
            provider_calls=0, gpu=0))
        _fail("local_process_deadline")
    if completed.returncode:
        root.mkdir(parents=True, exist_ok=True)
        streams = {}
        for name in ('stdout', 'stderr'):
            raw = (getattr(completed, name, None) or '').encode('utf-8', errors='replace')
            streams[name] = dict(text=raw[:65536].decode('utf-8', errors='replace'),
                                 actual_bytes=len(raw), truncated=len(raw)>65536)
        _write_new(root / 'process-result.json', dict(status='failed', exit_code=completed.returncode,
            elapsed_sec=time.monotonic()-started, streams=streams, automatic_retry=False))
        _fail('local_worker_failed')
    ref = json.loads(completed.stdout)
    if not isinstance(ref, dict) or set(ref) != {'input', 'result', 'model'}:
        _fail('local_worker_result')
    return ref

if __name__ == "__main__":
    raise SystemExit(main())
