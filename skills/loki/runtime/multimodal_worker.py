"""Run the bundled local retrieval encoder under the workspace execution policy."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    here = Path(__file__).resolve().parent
    loader = importlib.util.spec_from_file_location("loki_worker_runtime", here / "loki.py")
    runtime = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(runtime)
    runtime.verify_release()
    root = Path(args.workspace).resolve(strict=True)
    config_path = runtime.contained(root, os.environ.get("LOKI_CONFIG_PATH") or root / "loki.json")
    config_root, work, policy = runtime.configuration(config_path)
    if config_root != root:
        raise ValueError("loki_multimodal_config_outside_workspace")
    spec_path = runtime.contained(root, args.spec)
    if not spec_path.is_relative_to(work):
        raise ValueError("loki_multimodal_spec_outside_work")
    runtime.isolate(root, config_path)
    spec = runtime.load_json(spec_path, policy["max_input_bytes"])
    expected = runtime.multimodal_runtime(root, policy)
    if (expected is None or spec.get("model_ref") != expected["model_ref"]
            or spec.get("sdk_root") != expected["sdk_root"] or spec.get("device") != expected["device"]):
        raise ValueError("loki_multimodal_worker_assets_changed")
    runtime.contained(root, spec["output_root"])
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                      HF_HUB_DISABLE_TELEMETRY="1", TRANSFORMERS_OFFLINE="1", DO_NOT_TRACK="1")
    from pipeline.editorial_multimodal_navigation import main as encode
    return encode(["--spec", str(spec_path), "--worker"])


if __name__ == "__main__":
    raise SystemExit(main())
