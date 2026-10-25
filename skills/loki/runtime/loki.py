"""Workspace-local entrypoint for the current Codex to author Loki reports."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

SCHEMA = "loki.workspace.v1"
DEFAULTS = {"schema_version": SCHEMA, "language": "ko", "max_input_bytes": 50_000_000,
            "max_media_bytes": 20_000_000_000, "features": "core", "multimodal_runtime": None,
            "network": "deny", "author_mode": "current_codex", "work_dir": "work"}


def load_json(path, max_bytes=50_000_000):
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("loki_input_exceeds_policy")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def contained(root, path):
    value = str(path)
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value[4:]
    path = value
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("loki_path_outside_workspace")

    current = root
    for part in path.absolute().relative_to(root).parts:
        current /= part
        if current.is_symlink() or (current.exists() and getattr(current.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("loki_workspace_link_forbidden")
    return resolved


def configuration(path):
    path = Path(path).resolve(strict=True)
    value = load_json(path, 64_000)
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("loki_configuration_shape")
    if (value["schema_version"] != SCHEMA or value["language"] not in {"ko", "en"}
            or type(value["max_input_bytes"]) is not int or not 1024 <= value["max_input_bytes"] <= 200_000_000
            or type(value["max_media_bytes"]) is not int or not 1024 <= value["max_media_bytes"] <= 100_000_000_000
            or value["features"] not in {"core", "media", "multimodal"}
            or value["multimodal_runtime"] is not None and (
                not isinstance(value["multimodal_runtime"], dict)
                or set(value["multimodal_runtime"]) != {"path", "sha256"})
            or value["network"] != "deny" or value["author_mode"] != "current_codex"
            or not isinstance(value["work_dir"], str) or Path(value["work_dir"]).is_absolute()):
        raise ValueError("loki_configuration_policy")
    root = path.parent
    work = contained(root, value["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    return root, work, value


def isolate(root, config_path=None):
    os.environ["LOKI_WORKSPACE"] = str(root)
    os.environ["LOKI_CONFIG_PATH"] = str(contained(root, config_path or root / "loki.json"))
    _, current_work, _ = configuration(Path(os.environ["LOKI_CONFIG_PATH"]))
    os.environ["LOKI_WORK_DIR"] = str(current_work)
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                      HF_HUB_DISABLE_TELEMETRY="1", TRANSFORMERS_OFFLINE="1")
    os.environ.update(HF_HOME=str(contained(root, root / ".cache/huggingface")),
                      HF_TOKEN_PATH=str(contained(root, root / ".cache/huggingface/token")),
                      TORCH_HOME=str(contained(root, root / ".cache/torch")))

    for key in ("CHZZ_EDITORIAL_MULTIMODAL_RUNTIME_PATH", "CHZZ_PIPELINE_RUNTIME_ROOT",
                "CHZZ_RUNTIME_DATA_ROOT", "CHZZ_REPORT_ADMIN_DATA_ROOT", "REPORT_ADMIN_DATA_ROOT",
                "LOKI_SOURCE_PREPARATION"):
        os.environ.pop(key, None)




    if load_json(Path(os.environ["LOKI_CONFIG_PATH"]), 64_000)["features"] == "multimodal":
        import lancedb.background_loop

    spec = importlib.util.spec_from_file_location("loki_local_tools", Path(__file__).with_name("local_tools.py"))
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    tools.install(Path(root).resolve())


def multimodal_runtime(root, policy):
    ref = policy["multimodal_runtime"]
    if ref is None:
        return None
    if not isinstance(ref.get("sha256"), str):
        raise ValueError("loki_multimodal_runtime_hash")
    path = contained(root, ref["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        raise ValueError("loki_multimodal_runtime_changed")
    value = load_json(path, 16_384)
    if (not isinstance(value, dict) or set(value) != {"model_ref", "sdk_root", "limits", "device"}
            or value["device"] not in {"cpu", "cuda", "mps"}):
        raise ValueError("loki_multimodal_runtime_shape")
    model_ref = value["model_ref"]
    if not isinstance(model_ref, dict) or set(model_ref) != {"path", "sha256"}:
        raise ValueError("loki_multimodal_model_ref")
    model_path = contained(root, model_ref["path"])
    sdk = contained(root, value["sdk_root"])
    if not sdk.is_relative_to(root / ".venv"):
        raise ValueError("loki_multimodal_sdk_outside_environment")
    model = load_json(model_path, 64_000)
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != model_ref["sha256"]:
        raise ValueError("loki_multimodal_model_manifest_changed")
    contained(root, model["root"])
    from pipeline.editorial_multimodal_navigation import validate_local_runtime
    return validate_local_runtime(value)


def prepare_spec(root, work, policy, path):
    spec = load_json(path, policy["max_input_bytes"])
    if not isinstance(spec, dict):
        raise ValueError("loki_prepare_spec_shape")


    allowed = {"vod_info", "work_root", "source_work_dir", "srt_path", "base_path", "job_id",
               "code_version", "author", "packet_path", "orientation_paths", "hierarchy", "retrieval", "media_path"}
    if set(spec) - allowed:
        raise ValueError("loki_unavailable_prepare_option")
    spec["work_root"] = str(work)
    for key in ("source_work_dir", "srt_path", "base_path", "packet_path", "media_path"):
        if key in spec:
            spec[key] = str(contained(root, spec[key]))
    spec["orientation_paths"] = [str(contained(root, p)) for p in spec.get("orientation_paths", [])]
    source = Path(spec["source_work_dir"])
    if not source.is_relative_to(work) or not Path(spec["srt_path"]).is_relative_to(source):
        raise ValueError("loki_source_outside_work")
    media = Path(spec["media_path"]) if spec.get("media_path") else None
    if media is not None:
        if policy["features"] == "core":
            raise ValueError("loki_media_feature_not_installed")
        video = str(spec["vod_info"]["video_no"])
        if (source.name != video or not media.is_relative_to(source) or not media.is_file()
                or media.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"}):
            raise ValueError("loki_media_requires_vod_owned_video")
        if media.stat().st_size > policy["max_media_bytes"]:
            raise ValueError("loki_media_exceeds_policy")
    for orientation in spec["orientation_paths"]:
        if Path(orientation).stat().st_size > policy["max_input_bytes"]:
            raise ValueError("loki_input_exceeds_policy")
    for key in ("srt_path", "base_path", "packet_path"):
        if key in spec and Path(spec[key]).stat().st_size > policy["max_input_bytes"]:
            raise ValueError("loki_input_exceeds_policy")
    for source_path in source.rglob("*"):
        if source_path.is_file():
            contained(root, source_path)
            if source_path.resolve() != media and source_path.stat().st_size > policy["max_input_bytes"]:
                raise ValueError("loki_input_exceeds_policy")
    manifest = load_json(Path(__file__).with_name("source-manifest.json"))
    spec["code_version"] = manifest["source_commit"]
    spec["source_output_dir"] = str(root / "output")
    spec["source_data_dir"] = str(root / "data")
    if policy["features"] == "multimodal":
        spec["multimodal_runtime"] = multimodal_runtime(root, policy)
        if spec["multimodal_runtime"] is None:
            raise ValueError("loki_multimodal_setup_required")
    return spec


def verify_release():
    here = Path(__file__).resolve().parent
    manifest = load_json(here / "source-manifest.json")
    for row in manifest["files"]:
        candidate = contained(here, row["source_path"])
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != row["export_sha256"]:
            raise ValueError("loki_release_source_changed: " + row["source_path"])
    return manifest


def main(argv=None):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Workspace-local loki.json; required except init")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--directory", required=True)
    sub.add_parser("doctor")
    prep = sub.add_parser("prepare")
    prep.add_argument("--spec", required=True)
    for command in ("read", "submit", "remember", "memory"):
        child = sub.add_parser(command)
        child.add_argument("--job-root", required=True)
        child.add_argument("--expected-revision", required=True)
        field = {"read": "action", "submit": "response", "remember": "note"}.get(command)
        if field:
            child.add_argument("--" + field, required=True)
        else:
            child.add_argument("--query")
    args = parser.parse_args(argv)
    if args.command == "init":
        root = Path(args.directory).absolute()
        if root.exists() and any(root.iterdir()):
            raise ValueError("loki_init_requires_empty_directory")
        root.mkdir(parents=True, exist_ok=True)
        write_new(root / "loki.json", DEFAULTS)
        (root / "base.md").write_text("", encoding="utf-8")
        (root / "work").mkdir()
        print(json.dumps({"config": str(root / "loki.json"), "workspace": str(root)}))
        return 0
    if not args.config:
        parser.error("--config <workspace/loki.json> is required")
    manifest = verify_release()
    root, work, policy = configuration(args.config)
    isolate(root, args.config)
    if args.command == "doctor":
        dependencies = ["numpy", "requests", "bs4", "PIL"]
        if policy["features"] == "multimodal":
            dependencies += ["torch", "transformers", "accelerate", "sherpa_onnx", "silero_vad", "Cryptodome"]
        missing = [name for name in dependencies if importlib.util.find_spec(name) is None]
        tools_ready = (root / ".loki-tools.json").is_file()
        model_ready, speech_ready, speech_error = False, False, None
        if policy["features"] == "multimodal" and policy["multimodal_runtime"] is not None:
            from pipeline.editorial_multimodal_navigation import _local_runtime_assets
            _, reason = _local_runtime_assets(multimodal_runtime(root, policy))
            model_ready = not reason
        if policy["features"] == "multimodal" and (root / ".loki-speech.json").is_file():
            try:
                from speech_setup import load_runtime
                load_runtime(root)
                speech_ready = True
            except (ValueError, OSError, ImportError) as error:
                speech_error = str(error)
        ready = not missing and (policy["features"] == "core" or tools_ready) and (
            policy["features"] != "multimodal" or model_ready and speech_ready)
        result = {"status": "ready" if ready else "setup_required", "version": "3.0.3", "source_commit": manifest["source_commit"],
                  "workspace": str(root), "network": "deny", "author": "current_codex",
                  "model_transport": "disabled", "publish": "unavailable", "writer_language": policy["language"],
                  "missing_dependencies": missing, "features": policy["features"],
                  "media_tools_ready": tools_ready, "multimodal_model_ready": model_ready,
                  "speech_ready": speech_ready, "speech_error": speech_error,
                  "speech_verification": "assets_only_not_transcription" if speech_ready else None,
                  "embedding_device": multimodal_runtime(root, policy)["device"] if model_ready else None}
    else:
        from pipeline import editorial_current_conversation as current
        if args.command == "prepare":
            result = current.prepare(prepare_spec(root, work, policy, args.spec))
            result["writer_preferences"] = {"language": policy["language"]}
        else:
            job = contained(root, args.job_root)
            if not job.is_relative_to(work):
                raise ValueError("loki_job_outside_work")
            if args.command == "memory":
                result = current.memory(job, expected_revision=args.expected_revision, query=args.query)
            elif args.command == "submit":
                response = contained(root, args.response)
                if response.stat().st_size > policy["max_input_bytes"]:
                    raise ValueError("loki_input_exceeds_policy")
                result = current.submit(job, response, expected_revision=args.expected_revision)
            else:
                field = "action" if args.command == "read" else "note"
                path = contained(root, getattr(args, field))
                if path.stat().st_size > policy["max_input_bytes"]:
                    raise ValueError("loki_input_exceeds_policy")
                value = load_json(path, policy["max_input_bytes"])
                operation = current.multimodal if args.command == "read" and isinstance(value, dict) and value.get("action") == "navigate_multimodal" else getattr(current, args.command)
                result = operation(job, value, expected_revision=args.expected_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
