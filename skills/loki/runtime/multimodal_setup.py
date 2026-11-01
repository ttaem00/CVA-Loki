"""Install the pinned offline embedding model into one Loki workspace."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


MODEL = "google/embeddinggemma-2"
REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
MODEL_FILES = {
    "config.json": "b8f1e9931b57fbc054acdb445c41765d55b0074c58d145fa82839941ad1b5bb3",
    "config_sentence_transformers.json": "031e56a498d33c349ab489a21885bcfe25b4fcba841149dc99e1e90d4a7c28f5",
    "modules.json": "3d02572a0455b832de67fb8e63a54981bc7e8b46e337c95e917bd8122a533bfd",
    "1_Pooling/config.json": "8759bdf7c77efc7df7723f64856a593c8943b71ee38baf2a88771fbaf78438f9",
    "2_Normalize/config.json": "cdb09dfca347a56aa2d691744e38d5ad3c7cbc2834e7181272b9a15328b82524",
    "preprocessor_config.json": "ea2ae257e901064abdd98dceb19f2b0da06af600bed15e0f99f5c85c37ee9d78",
    "processor_config.json": "168f6a08522f3ce5dea596d94d003af2fd691742d4f41fe1f9d8cce76bfbf69c",
    "sentence_bert_config.json": "b1bcd9f2dce3ae863b359e87d0710b5dbc3314a59ecb4e2f97c7778fc8e4b228",
    "tokenizer.json": "4d777ef5bdc1aa36227abdfb77c3e49e7b9c892d16e1b6bda41c393504828be4",
    "tokenizer.model": "e594c8a90eb08d8bda498ff4747977dc827ae0c3c56b5c0d41a605a22d02ef03",
    "tokenizer_config.json": "17bd5d6e9364ca49a534e1502076593317c298d4a663623091ed45388f004874",
    "chat_template.jinja": "4b852efc0b9960283e735363331e6f325b33bc74bdbaa076f595bc4e9b94d85e",
    "model.safetensors": "197a32965d4b1105faf060417baa899e193fb73cd401f42ec9295234d5553d79",
}
SDK_VERSIONS = {"transformers": "5.19.0", "sentence-transformers": "6.1.0"}
LIMITS = dict(cpu_threads=4, max_seconds=600, max_memory_bytes=12_000_000_000,
              max_requests=2, max_total_seconds=600)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def plain_path(path):
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("multimodal_path_link_forbidden")
    return absolute.resolve()


def write_json(path, value):
    path = plain_path(path)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("multimodal_existing_metadata_changed: " + path.name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def installed_sdk():
    if sys.prefix == sys.base_prefix:
        raise ValueError("multimodal_requires_virtual_environment")
    roots = []
    for package, version in SDK_VERSIONS.items():
        name = package.replace("-", "_")
        spec = util.find_spec(name)
        if spec is None or not spec.origin or metadata.version(package) != version:
            raise ValueError("multimodal_sdk_version_mismatch: " + package)
        origin = Path(spec.origin).resolve(strict=True)
        if not origin.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError("multimodal_sdk_outside_environment")
        roots.append(origin.parent.parent)
    if len(set(roots)) != 1:
        raise ValueError("multimodal_sdk_origins_differ")
    return roots[0]


def hardware_detection():
    path = Path(__file__).with_name("device_setup.py")
    spec = util.spec_from_file_location("loki_device_setup", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.detect_hardware()


def select_device(requested="auto", *, hardware=None, torch_module=None):
    if requested not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("multimodal_device_invalid")
    if torch_module is None:
        import torch as torch_module
    hardware = hardware_detection() if hardware is None else hardware
    cuda = bool(torch_module.cuda.is_available())
    mps_backend = getattr(torch_module.backends, "mps", None)
    mps = bool(mps_backend is not None and mps_backend.is_available())
    if requested == "cpu":
        selected = "cpu"
    elif requested == "cuda":
        if not cuda:
            raise ValueError("multimodal_requested_cuda_unavailable")
        selected = "cuda"
    elif requested == "mps":
        if not mps:
            raise ValueError("multimodal_requested_mps_unavailable")
        selected = "mps"
    else:
        if hardware.get("unsupported_gpu") and not (cuda or mps):
            raise ValueError("multimodal_detected_gpu_unsupported: " + str(hardware.get("reason", "")))
        if hardware.get("nvidia_detected") and not cuda:
            raise ValueError("multimodal_detected_nvidia_cuda_unavailable")
        if hardware.get("mps_candidate") and not mps:
            raise ValueError("multimodal_detected_apple_mps_unavailable")
        selected = "cuda" if cuda else "mps" if mps else "cpu"
    return dict(requested=requested,
        detected=dict(hardware=hardware, cuda_available=cuda, mps_available=mps),
        selected=selected, actual=None,
        cuda_device_name=torch_module.cuda.get_device_name(0) if selected == "cuda" else None)


def actual_model_device(model, selected):
    actual = str(model.device)
    kind = str(getattr(model.device, "type", actual.split(":", 1)[0]))
    if kind != selected:
        raise ValueError("multimodal_model_device_mismatch: selected=" + selected + ", actual=" + actual)
    return actual


def verification_cache(workspace, device):
    cache = plain_path(workspace / ".models" / "verification-cache" / device / uuid.uuid4().hex)
    cache.mkdir(parents=True, exist_ok=False)
    plain_path(cache / "lance")
    return cache


def model_storage(workspace):
    models = plain_path(workspace / ".models")
    owner = models / "loki-model-owner.json"
    identity = dict(schema_version="loki.model-owner.v1", model=MODEL, revision=REVISION)
    if models.exists() and any(models.iterdir()) and not owner.is_file():
        raise ValueError("multimodal_model_directory_unowned")
    models.mkdir(parents=True, exist_ok=True)
    write_json(owner, identity)
    return models


def copy_model_file(source, target, expected, staging):
    target = plain_path(target)
    if target.exists():
        if not target.is_file() or digest(target) != expected:
            raise ValueError("multimodal_existing_model_changed: " + target.name)
        return
    if not source.is_file() or digest(source) != expected:
        raise ValueError("multimodal_model_hash_mismatch: " + target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = plain_path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=staging, prefix=target.name + ".",
                                         suffix=".part", delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream, length=1024 * 1024)
        if digest(temporary) != expected:
            raise ValueError("multimodal_model_changed_during_copy: " + target.name)
        if target.exists():
            raise ValueError("multimodal_model_destination_appeared")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_file(name, cache):
    from huggingface_hub import hf_hub_download
    downloaded = Path(hf_hub_download(repo_id=MODEL, filename=name, revision=REVISION,
        token=False, cache_dir=str(cache), endpoint="https://huggingface.co")).resolve(strict=True)
    if not downloaded.is_relative_to(cache):
        raise ValueError("multimodal_download_outside_workspace")
    return downloaded


def verify(workspace, runtime, manifest, device):
    import numpy as np
    from PIL import Image
    from pipeline.editorial_embedding_index import encode_cached, encode_media_cached
    from pipeline.editorial_multimodal_navigation import _load_local_model
    cache = verification_cache(workspace, device)
    with redirect_stdout(sys.stderr):
        model = _load_local_model(manifest, runtime["sdk_root"], LIMITS["cpu_threads"], device=device)
        actual = actual_model_device(model, device)
        vectors, first = encode_cached(["A red square.", "A short pure tone."], role="document",
            root=cache, runtime=runtime, model=model)
        repeated, second = encode_cached(["A red square.", "A short pure tone."], role="document",
            root=cache, runtime=runtime, model=model)
        if first["encoded_rows"] != 2:
            raise ValueError("multimodal_fresh_inference_not_observed")
        if not np.array_equal(vectors, repeated) or second["encoded_rows"] != 0:
            raise ValueError("multimodal_text_cache_not_reused")
        image = Image.new("RGB", (64, 64), (220, 20, 20))
        audio = (np.sin(np.arange(16000, dtype=np.float32) * (2 * np.pi * 220 / 16000)) * .1).astype(np.float32)
        video = np.stack([np.asarray(image)] * 2)
        checked = {"text": vectors}
        for modality, content in (("image", image), ("audio", audio), ("video", video)):
            data = np.asarray(content).tobytes()
            source_hash = hashlib.sha256(data).hexdigest()
            arguments = dict(modality=modality, root=cache, runtime=runtime,
                media_sha256=source_hash, window=dict(start=0, duration=1))
            result = encode_media_cached(content, model=model, **arguments)
            again = encode_media_cached(content, model=None, **arguments)
            if not np.array_equal(result, again):
                raise ValueError("multimodal_media_cache_not_reused: " + modality)
            checked[modality] = result
        for modality, values in checked.items():
            if (values.ndim != 2 or values.shape[1] != 768 or not np.isfinite(values).all()
                    or not np.allclose(np.linalg.norm(values, axis=1), 1, atol=.005)):
                raise ValueError("multimodal_verification_vector_invalid: " + modality)
    return dict(status="passed", scope="synthetic_model_execution_not_editorial_quality",
        modalities=list(checked), dimension=768, finite_unit_vectors=True, cache_reused=True,
        text_encoded_rows=first["encoded_rows"], device=actual, selected_device=device,
        actual_device=actual, cache_root=str(cache), fresh_inference=True)


def setup(workspace, *, model_source=None, device="auto", run_verification=False):
    sdk = installed_sdk()
    selection = select_device(device)
    selected = selection["selected"]
    workspace = plain_path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    source = plain_path(model_source) if model_source is not None else None
    if source is not None and not source.is_dir():
        raise ValueError("multimodal_model_source_missing")
    models = model_storage(workspace)
    cache = plain_path(models / "huggingface")
    os.environ.update(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"),
        HF_TOKEN_PATH=str(cache / "token"), HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
        HF_HUB_OFFLINE="1" if source is not None else "0")
    model_root = plain_path(models / "embeddinggemma-2" / REVISION)
    model_root.mkdir(parents=True, exist_ok=True)
    staging = plain_path(models / "staging")
    rows = []
    for name, expected in sorted(MODEL_FILES.items()):
        target = plain_path(model_root / name)
        if target.exists():
            copy_model_file(target, target, expected, staging)
        else:
            original = plain_path(source / name) if source is not None else download_file(name, cache)
            copy_model_file(original, target, expected, staging)
        rows.append(dict(file=name, sha256=digest(target), bytes=target.stat().st_size))
    for path in model_root.rglob("*"):
        plain_path(path)
        if path.is_file() and path.relative_to(model_root).as_posix() not in MODEL_FILES:
            raise ValueError("multimodal_unexpected_model_file")
    manifest = dict(schema_version="loki.local-model.v1", model=MODEL, revision=REVISION,
                    root=str(model_root), files=rows)
    manifest_path = models / "embeddinggemma-2" / "model-files.json"
    write_json(manifest_path, manifest)
    model_ref = dict(path=str(manifest_path), sha256=digest(manifest_path))
    runtime = dict(model_ref=model_ref, sdk_root=str(sdk), limits=dict(LIMITS), device=selected)
    runtime_path = models / ("multimodal-runtime." + selected + ".json")
    write_json(runtime_path, runtime)
    verification = verify(workspace, runtime, manifest, selected) if run_verification else dict(status="not_run")
    if run_verification:
        selection["actual"] = verification["actual_device"]
    return dict(status="ready", model_ref=model_ref,
        runtime_ref=dict(path=str(runtime_path), sha256=digest(runtime_path)),
        model=dict(id=MODEL, revision=REVISION, files=len(rows), bytes=sum(row["bytes"] for row in rows)),
        device=selected, device_selection=selection, verification=verification)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model-source")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = setup(args.workspace, model_source=args.model_source,
                       device=args.device, run_verification=args.verify)
    except Exception as error:
        print(json.dumps(dict(status="failed", error_type=type(error).__name__, reason=str(error))))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
