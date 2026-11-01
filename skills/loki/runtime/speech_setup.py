"""Prepare checksum-pinned local Whisper, Silero and the existing F2 speaker assets."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from importlib import metadata, util
import json
import os
from pathlib import Path
import sys

try:
    from .multimodal_setup import copy_model_file, digest, model_storage, plain_path, write_json
except ImportError:
    from multimodal_setup import copy_model_file, digest, model_storage, plain_path, write_json

MODEL = "openai/whisper-large-v3-turbo"
REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
MODEL_FILES = {
    "added_tokens.json": "3c51f66c4c21f9e126970078f11ae77a78c74aee8df606ee9daba86e467108e0",
    "config.json": "c5b526b3e3cd64cd8940dabb45e8ba726629e22d8ed389c29b552f9140daf04a",
    "generation_config.json": "cce11bfe3aaa6ae9e072ea2637caaec8795e68d9b67e655a5af16ee509681a4c",
    "merges.txt": "2df2990a395e35e8dfbc7511e08c12d56018d8d04691e0133e5d63b21e154dc6",
    "model.safetensors": "542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1",
    "normalizer.json": "bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd",
    "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    "special_tokens_map.json": "baea4ea09372eb4fca86b4e4346139fd73cb807d5087e9de0948e971739c3e74",
    "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
    "tokenizer_config.json": "844b642c73a91359722f47b35705f7174686df33d252695d8572cf9ac03a6389",
    "vocab.json": "e2aa043ef015641d363d8288e7c241c85e36a5c761fb303598e0710233344387",
}
RUNTIME_SCHEMA = "loki.speech-runtime.v1"


def _inside(workspace, value, *, models=False):
    root = plain_path(workspace)
    path = plain_path(value)
    boundary = root / ".models" if models else root
    if path == boundary or not path.is_relative_to(boundary):
        raise ValueError("speech_path_outside_workspace")
    return path


def _ref(path):
    return dict(path=str(path), sha256=digest(path))


def _read_ref(workspace, ref):
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ValueError("speech_asset_ref_invalid")
    path = _inside(workspace, ref["path"], models=True)
    if path.stat().st_size > 128_000 or digest(path) != ref["sha256"]:
        raise ValueError("speech_asset_metadata_changed")
    return json.loads(path.read_text(encoding="utf-8"))


def _package(workspace, name, version=None):
    spec = util.find_spec(name)
    if spec is None or not spec.origin:
        raise ValueError("speech_dependency_missing: " + name)
    folder = plain_path(Path(spec.origin).parent)
    if not folder.is_relative_to(plain_path(workspace) / ".venv"):
        raise ValueError("speech_dependency_outside_environment: " + name)
    if version is not None and metadata.version(name.replace("_", "-")) != version:
        raise ValueError("speech_dependency_version_changed: " + name)
    return folder


def _device(requested):
    path = Path(__file__).with_name("device_setup.py")
    spec = util.spec_from_file_location("loki_speech_device", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.select_device(requested)


def setup(workspace, *, model_source=None, device="auto"):
    workspace = plain_path(workspace)
    if sys.prefix == sys.base_prefix or plain_path(sys.prefix) != workspace / ".venv":
        raise ValueError("speech_requires_owned_virtual_environment")
    selection = _device(device)
    for package in ("torch", "transformers"):
        _package(workspace, package)
    _package(workspace, "sherpa_onnx", "1.13.7")
    _package(workspace, "accelerate", "1.15.0")
    vad_package = _package(workspace, "silero_vad", "6.2.3")
    vad_jit = plain_path(vad_package / "data/silero_vad.jit")
    if not vad_jit.is_file():
        raise ValueError("speech_silero_jit_missing")
    models = model_storage(workspace)
    root = _inside(workspace, models / "whisper-large-v3-turbo" / REVISION, models=True)
    root.mkdir(parents=True, exist_ok=True)
    cache = _inside(workspace, models / "huggingface-speech", models=True)
    os.environ.update(LOKI_WORKSPACE=str(workspace), LOKI_SOURCE_PREPARATION=str(workspace),
        HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"), HF_TOKEN_PATH=str(cache / "token"),
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    source = plain_path(model_source) if model_source is not None else None
    if source is not None and not source.is_dir():
        raise ValueError("speech_model_source_missing")
    rows = []
    for name, expected in sorted(MODEL_FILES.items()):
        target = plain_path(root / name)
        if target.exists():
            original = target
        elif source is not None:
            original = plain_path(source / name)
        else:
            from huggingface_hub import hf_hub_download
            with redirect_stdout(sys.stderr):
                original = Path(hf_hub_download(repo_id=MODEL, filename=name, revision=REVISION,
                    cache_dir=str(cache), token=False, local_files_only=False))
        copy_model_file(original, target, expected, models / "staging")
        rows.append(dict(file=name, sha256=digest(target), bytes=target.stat().st_size))
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(MODEL_FILES):
        raise ValueError("speech_unexpected_model_file")
    manifest = dict(schema_version="loki.speech-model.v1", model=MODEL, revision=REVISION,
                    root=str(root), files=rows)
    manifest_path = root.parent / "model-files.json"
    write_json(manifest_path, manifest)
    vad_path = models / "speech" / "silero-vad-6.2.3.json"
    write_json(vad_path, dict(schema_version="loki.silero-assets.v1", version="6.2.3",
        package_root=str(vad_package), jit=_ref(vad_jit)))
    from pipeline.speaker_turn_runtime import ensure_sherpa_models
    segmentation, embedding = ensure_sherpa_models()
    f2_path = models / "speech" / "sherpa-f2.json"
    write_json(f2_path, dict(schema_version="loki.sherpa-assets.v1", version="1.13.7",
        segmentation=_ref(_inside(workspace, segmentation, models=True)),
        embedding=_ref(_inside(workspace, embedding, models=True))))
    runtime = dict(schema_version=RUNTIME_SCHEMA, model_ref=_ref(manifest_path),
        vad_ref=_ref(vad_path), speaker_turn_ref=_ref(f2_path), device=selection["selected"],
        vad_prescan_workers=1, speaker_turn_split_enabled=True, nemotron_turns_enabled=False)
    runtime_path = models / "speech" / ("runtime." + selection["selected"] + ".json")
    write_json(runtime_path, runtime)
    pointer = workspace / ".loki-speech.json"
    temporary = plain_path(pointer.with_suffix(".json.part"))
    temporary.write_text(json.dumps(_ref(runtime_path), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(plain_path(pointer))
    return dict(status="ready", runtime_ref=_ref(runtime_path), device=selection["selected"],
        device_selection=selection, model=dict(id=MODEL, revision=REVISION, files=len(rows),
        bytes=sum(row["bytes"] for row in rows)), vad_ref=_ref(vad_path), speaker_turn_ref=_ref(f2_path),
        verification="assets_only_not_transcription")


def load_runtime(workspace):
    workspace = plain_path(workspace)
    pointer = plain_path(workspace / ".loki-speech.json")
    ref = json.loads(pointer.read_text(encoding="utf-8"))
    runtime = _read_ref(workspace, ref)
    if (runtime.get("schema_version") != RUNTIME_SCHEMA or runtime.get("device") not in {"cpu", "cuda", "mps"}
            or runtime.get("vad_prescan_workers") != 1 or runtime.get("speaker_turn_split_enabled") is not True
            or runtime.get("nemotron_turns_enabled") is not False):
        raise ValueError("speech_runtime_policy_changed")
    model = _read_ref(workspace, runtime["model_ref"])
    if model.get("model") != MODEL or model.get("revision") != REVISION:
        raise ValueError("speech_model_identity_changed")
    root = _inside(workspace, model["root"], models=True)
    rows = {row["file"]: row for row in model["files"]}
    if set(rows) != set(MODEL_FILES):
        raise ValueError("speech_model_inventory_changed")
    for name, expected in MODEL_FILES.items():
        if rows[name]["sha256"] != expected or digest(plain_path(root / name)) != expected:
            raise ValueError("speech_model_changed: " + name)
    vad = _read_ref(workspace, runtime["vad_ref"])
    package = _package(workspace, "silero_vad", "6.2.3")
    jit = plain_path(vad["jit"]["path"])
    if package != Path(vad["package_root"]) or jit != package / "data/silero_vad.jit" or digest(jit) != vad["jit"]["sha256"]:
        raise ValueError("speech_silero_assets_changed")
    f2 = _read_ref(workspace, runtime["speaker_turn_ref"])
    from pipeline.speaker_turn_runtime import SEGMENTATION_MODEL_SHA256, EMBEDDING_MODEL_SHA256
    for name, expected in (("segmentation", SEGMENTATION_MODEL_SHA256), ("embedding", EMBEDDING_MODEL_SHA256)):
        path = _inside(workspace, f2[name]["path"], models=True)
        if f2[name]["sha256"] != expected or digest(path) != expected:
            raise ValueError("speech_speaker_assets_changed")
    return runtime, model


def load_models(log_func=print, whisper_model="large-v3-turbo"):
    if whisper_model != "large-v3-turbo":
        raise ValueError("speech_only_pinned_whisper_is_installed")
    workspace = os.environ.get("LOKI_WORKSPACE")
    if not workspace:
        raise ValueError("speech_workspace_required")
    runtime, manifest = load_runtime(workspace)
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    device = runtime["device"]
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("speech_selected_cuda_unavailable_no_cpu_fallback")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("speech_selected_mps_unavailable_no_cpu_fallback")
    dtype = torch.float16 if device == "cuda" else torch.float32
    log_func("Loading pinned local Whisper on " + device)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(manifest["root"], torch_dtype=dtype,
        low_cpu_mem_usage=True, local_files_only=True, trust_remote_code=False, token=False)
    model.to(device)
    if str(model.device).split(":", 1)[0] != device:
        raise ValueError("speech_actual_device_mismatch")
    processor = AutoProcessor.from_pretrained(manifest["root"], local_files_only=True,
        trust_remote_code=False, token=False)
    from silero_vad import load_silero_vad, get_speech_timestamps
    return model, processor, device, dtype, load_silero_vad(), (get_speech_timestamps,)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model-source")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    args = parser.parse_args(argv)
    try:
        result = setup(args.workspace, model_source=args.model_source, device=args.device)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(json.dumps(dict(status="failed", error=str(error))))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
