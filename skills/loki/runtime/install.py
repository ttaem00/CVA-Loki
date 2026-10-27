"""One-command local Loki setup. Semantic writing stays with the current Codex."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import venv

sys.dont_write_bytecode = True
OWNER_SCHEMA = "loki.install-owner.v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = plain_path(path.with_name(path.name + ".part"))
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def plain_path(path):
    absolute = Path(path).absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("installation_path_link_forbidden")
    return absolute.resolve()


def same_payload(source, installed):
    """Check the installed skill payload before executing its local entrypoint."""
    source, installed = Path(source), Path(installed)
    expected = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()}
    actual = {p.relative_to(installed) for p in installed.rglob("*") if p.is_file()}
    if actual != set(expected):
        raise ValueError("installed_skill_payload_inventory_changed")
    for name, original in expected.items():
        target = plain_path(installed / name)
        if not target.is_file() or digest(original) != digest(target):
            raise ValueError("installed_skill_payload_changed: " + name.as_posix())
    return len(expected)


def npm_environment(cache, user_config, global_config, node):
    environment = {key: value for key, value in os.environ.items()
        if not key.lower().startswith("npm_config_") and key.upper() not in
        {"NPM_TOKEN", "NODE_AUTH_TOKEN", "NODE_OPTIONS", "NODE_PATH"}}
    environment.update(npm_config_cache=str(cache), npm_config_userconfig=str(user_config),
        npm_config_globalconfig=str(global_config),
        PATH=str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""))
    return environment


class Installer:
    def __init__(self, workspace):
        self.workspace = workspace
        self.stage = "verify_release"
        self.log_dir = None
        self.commands = []
        self.device_selection = None

    def command(self, stage, argv, *, json_output=False, timeout=600, env=None, cwd=None):
        self.stage = stage
        print("Loki installation: " + stage, file=sys.stderr, flush=True)
        started = time.monotonic()
        proc = subprocess.run([str(a) for a in argv], text=True, encoding="utf-8",
                              errors="replace", capture_output=True, timeout=timeout, env=env, cwd=cwd)
        self.commands.append({"stage": stage, "argv": [str(a) for a in argv],
                              "exit_code": proc.returncode, "seconds": round(time.monotonic() - started, 3)})
        if self.log_dir is not None:
            (self.log_dir / (stage + ".stdout.txt")).write_text(proc.stdout, encoding="utf-8")
            (self.log_dir / (stage + ".stderr.txt")).write_text(proc.stderr, encoding="utf-8")
        if proc.returncode:
            detail = proc.stderr if proc.stderr.strip() else proc.stdout
            raise RuntimeError(stage + " failed; " + detail[-3000:])
        if not json_output:
            return proc.stdout.strip()
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise ValueError(stage + " did not return JSON") from exc

    def install(self, mode, *, features="multimodal", model_source=None, device="auto"):
        here = Path(__file__).resolve().parent
        bundle = here if (here / "release-manifest.json").is_file() else None
        runtime = here / "skills/loki/runtime" if bundle else here
        if not (runtime / "source-manifest.json").is_file():
            raise ValueError("run_the_installer_from_a_complete_Loki_release_or_skill")
        if bundle:
            release = module(bundle / "verify_release.py", "loki_release_check").validate(bundle)
            source_identity = digest(bundle / "release-manifest.json")
        else:
            if mode != "setup":
                raise ValueError("plugin_or_skill_install_requires_the_complete_release_bundle")
            release = None
            source_identity = digest(runtime / "source-manifest.json")
        source_manifest = module(runtime / "loki.py", "loki_installer_runtime").verify_release()
        if release is None:
            release = {"version": source_manifest["version"]}
        if release["version"] != source_manifest["version"]:
            raise ValueError("installation_release_version_mismatch")
        if self.workspace == here or self.workspace.is_relative_to(here):
            raise ValueError("workspace_must_be_separate_from_installed_files")

        self.stage = "workspace"
        owner_path = plain_path(self.workspace / ".loki-install-owner.json")
        env_root = plain_path(self.workspace / ".venv")
        config = plain_path(self.workspace / "loki.json")
        owner = {"schema_version": OWNER_SCHEMA, "workspace": str(self.workspace),
                 "environment": str(env_root), "source_identity": source_identity}
        if owner_path.exists() and read_json(owner_path) != owner:
            raise ValueError("installation_owner_or_release_changed_use_a_new_workspace")
        if env_root.exists() and not owner_path.is_file():
            raise ValueError("unowned_virtual_environment_preserved")
        if not config.is_file():
            self.command("initialize_workspace", [sys.executable, "-B", runtime / "loki.py", "init",
                                                  "--directory", self.workspace])
        module(runtime / "loki.py", "loki_installer_config").configuration(config)
        save(owner_path, owner)
        self.log_dir = plain_path(self.workspace / "installation-logs" / uuid.uuid4().hex[:12])
        self.log_dir.mkdir(parents=True)
        self.stage = "create_environment"
        python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file() or not (env_root / "pyvenv.cfg").is_file():


            venv.EnvBuilder(with_pip=True).create(env_root)
        if not python.is_file():
            raise ValueError("owned_environment_incomplete_preserved")
        prefix = self.command("check_environment", [python, "-c",
            "import importlib.util,json,sys;print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'pip':importlib.util.find_spec('pip') is not None}))"], json_output=True)
        if Path(prefix["prefix"]).resolve() != env_root.resolve() or prefix["prefix"] == prefix["base_prefix"]:
            raise ValueError("installer_refuses_system_interpreter")
        if prefix.get("pip") is False:
            self.command("repair_pip", [python, "-m", "ensurepip", "--upgrade"])
        pip_cache = plain_path(self.workspace / ".cache/pip")
        self.command("install_dependencies", [python, "-m", "pip", "--isolated", "--disable-pip-version-check",
            "--cache-dir", pip_cache, "install", "-r", runtime / "requirements.txt"])
        node_info, node = None, None
        if features != "core" or mode == "plugin":
            node_info = self.command("install_node", [python, "-B", runtime / "node_runtime.py",
                "--workspace", self.workspace], json_output=True)
            node = plain_path(node_info["node"])
            if (not node.is_relative_to(self.workspace) or not plain_path(node_info["npm"]).is_relative_to(self.workspace)
                    or node_info.get("version") != "24.22.1"
                    or node_info.get("verification", {}).get("node") != "v24.22.1"
                    or node_info["verification"].get("blockSwitchError") != -17
                    or node_info["verification"].get("roundtrip") is not True):
                raise ValueError("node_security_runtime_not_verified")
        if features in {"media", "multimodal"}:
            self.command("install_media_dependencies", [python, "-m", "pip", "--isolated", "--disable-pip-version-check",
                "--cache-dir", pip_cache, "install", "static-ffmpeg==3.0"])
        if features == "multimodal":
            self.stage = "detect_device"
            selector = module(runtime / "device_setup.py", "loki_device_setup")
            try:
                self.device_selection = selector.select_device(device)
            except selector.DeviceSelectionError as error:
                self.device_selection = error.selection
                save(self.log_dir / "device-selection.json", self.device_selection)
                raise
            save(self.log_dir / "device-selection.json", self.device_selection)
            torch_args = [python, "-m", "pip", "--isolated", "--disable-pip-version-check", "--cache-dir", pip_cache,
                "install", "--only-binary=:all:", *selector.torch_requirements(self.device_selection)]
            if self.device_selection["index_url"]:
                torch_args += ["--index-url", self.device_selection["index_url"]]
            self.command("install_torch", torch_args, timeout=1800)
            self.command("install_multimodal_dependencies", [python, "-m", "pip", "--isolated", "--disable-pip-version-check",
                "--cache-dir", pip_cache, "install", "-r", runtime / "requirements-multimodal.txt"], timeout=1800)
            self.command("install_speech_dependencies", [python, "-m", "pip", "--isolated", "--disable-pip-version-check",
                "--cache-dir", pip_cache, "install", "-r", runtime / "requirements-speech.txt",
                "-r", runtime / "requirements-source.txt"], timeout=1800)

        native = None
        if mode == "skill":
            self.stage = "install_skill"
            source = bundle / "skills/loki"
            target = plain_path(self.workspace / ".agents/skills/loki")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
            same_payload(source, target)
            runtime = target / "runtime"
        elif mode == "plugin":
            codex = shutil.which("codex")
            if not codex:
                prefix = plain_path(self.workspace / ".tools/codex")
                prefix.mkdir(parents=True, exist_ok=True)
                user_config = plain_path(prefix / "user.npmrc")
                global_config = plain_path(prefix / "global.npmrc")
                for path in (user_config, global_config):
                    if not path.exists():
                        path.write_text("", encoding="utf-8")
                npm_env = npm_environment(plain_path(self.workspace / ".cache/npm"),
                                          user_config, global_config, node)
                self.command("install_codex", [node, node_info["npm"], "install", "--prefix", prefix,
                    "--no-save", "--no-audit", "--no-fund", "--omit=dev", "@openai/codex@0.164.0"],
                    env=npm_env, cwd=prefix, timeout=900)
                codex_command = [node, prefix / "node_modules/@openai/codex/bin/codex.js"]
            else:
                codex_command = [codex]
            registered = self.command("register_marketplace", [*codex_command, "plugin", "marketplace", "add", bundle, "--json"],
                                      json_output=True)
            added = self.command("install_plugin", [*codex_command, "plugin", "add", "loki@cva-loki", "--json"], json_output=True)
            installed = added.get("installedPath")
            if not isinstance(installed, str) or not Path(installed).is_absolute():
                raise ValueError("native_plugin_install_path_missing")
            cached = Path(installed)
            same_payload(bundle / "skills/loki", cached / "skills/loki")
            runtime = cached / "skills/loki/runtime"
            listed = self.command("verify_plugin_discovery", [*codex_command, "plugin", "list", "--marketplace", "cva-loki", "--json"],
                                  json_output=True)

            def installed_loki(value, in_installed=False):
                if isinstance(value, list):
                    return any(installed_loki(v, in_installed) for v in value)
                if not isinstance(value, dict):
                    return False
                identifier = value.get("pluginId") or value.get("id") or value.get("name")
                if identifier in {"loki", "loki@cva-loki"} and (in_installed or value.get("installed") is True):
                    return value.get("enabled") is not False
                return any(installed_loki(v, in_installed or k == "installed") for k, v in value.items())
            if not installed_loki(listed):
                raise ValueError("installed_plugin_not_discovered_or_enabled")
            native = {"registration": registered, "install": added, "discovery": listed,
                      "CLI_version": self.command("codex_version", [*codex_command, "--version"])}

        tools = None
        if features in {"media", "multimodal"}:
            tools = self.command("setup_media_tools", [python, "-B", runtime / "local_tools.py", "setup",
                "--workspace", self.workspace], json_output=True, timeout=900)
        model, speech = None, None
        if features == "multimodal":
            model_args = [python, "-B", runtime / "multimodal_setup.py", "--workspace", self.workspace,
                          "--device", self.device_selection["selected"], "--verify"]
            if model_source:
                model_args += ["--model-source", model_source]
            model = self.command("setup_multimodal", model_args, json_output=True, timeout=1800)
            if model.get("status") != "ready" or model.get("verification", {}).get("status") != "passed":
                raise ValueError("multimodal_model_execution_not_verified")
            actual_device = str(model.get("verification", {}).get("device", ""))
            if actual_device.split(":", 1)[0] != self.device_selection["selected"]:
                raise ValueError("multimodal_device_verification_mismatch")
            self.device_selection["actual"] = actual_device
            save(self.log_dir / "device-selection.json", self.device_selection)
            speech = self.command("setup_speech", [python, "-B", runtime / "speech_setup.py",
                "--workspace", self.workspace, "--device", self.device_selection["selected"]],
                json_output=True, timeout=1800)
            if (speech.get("status") != "ready" or speech.get("device") != self.device_selection["selected"]
                    or speech.get("verification") != "assets_only_not_transcription"
                    or not isinstance(speech.get("runtime_ref"), dict)):
                raise ValueError("speech_assets_not_ready")
            speech["assets_only"] = True
        settings = read_json(config)
        settings["features"] = features
        settings["multimodal_runtime"] = model["runtime_ref"] if model else None
        save(config, settings)

        doctor = self.command("doctor", [python, "-B", runtime / "loki.py", "--config", config, "doctor"], json_output=True)
        if doctor.get("status") != "ready" or doctor.get("missing_dependencies") != []:
            raise ValueError("runtime_doctor_not_ready")
        if doctor.get("version") != release["version"]:
            raise ValueError("runtime_doctor_version_mismatch")
        verification = plain_path(self.workspace / "verification" / uuid.uuid4().hex[:12])
        verification.parent.mkdir(exist_ok=True)
        check = self.command("selftest", [python, "-B", runtime / "selftest.py", "--workspace", verification],
                             json_output=True)
        if check.get("status") != "PASS" or check.get("kind") != "synthetic_contract_selftest":
            raise ValueError("selftest_not_passed")
        media_check = None
        if features in {"media", "multimodal"}:
            media_check = self.command("media_selftest", [python, "-B", runtime / "media_selftest.py",
                "--workspace", self.workspace], json_output=True, timeout=600)
            if media_check.get("status") != "PASS" or media_check.get("kind") != "synthetic_media_contract_selftest":
                raise ValueError("media_selftest_not_passed")
        dependencies = self.command("dependency_versions", [python, "-m", "pip", "--disable-pip-version-check", "list", "--format=json"],
                                    json_output=True)
        result = {"schema_version": "loki.installation-result.v1", "status": "PASS", "kind": "loki_installation",
                  "mode": mode, "version": release["version"], "source_identity": source_identity,
                  "workspace": str(self.workspace), "config": str(config), "python": str(python),
                  "installed_runtime": str(runtime), "skill": str(runtime.parent / "SKILL.md"),
                  "doctor": doctor, "selftest": check, "native_plugin": native,
                  "features": features, "local_tools": tools, "multimodal": model,
                  "node_runtime": node_info,
                  "speech": speech,
                  "device_selection": self.device_selection,
                  "media_selftest": media_check,
                  "review": {"command": [str(python), str(runtime / "review.py"), "--config", str(config)],
                             "url": "http://127.0.0.1:8787/", "status": "available_not_started"},
                  "dependencies": dependencies, "commands": self.commands, "logs": str(self.log_dir),
                  "attempt_id": self.log_dir.name,
                  "semantic_author": "current_Codex_required_for_real_sources",
                  "evidence_scope": "installation_and_synthetic_contract; not_model_generation_or_broadcast_quality"}
        save(self.log_dir / "result.json", result)
        save(self.workspace / "installation-result.json", result)
        return result


def main(argv=None):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="New/owned user workspace; installation files remain read-only")
    parser.add_argument("--mode", choices=("skill", "plugin", "setup"), default=None)
    parser.add_argument("--features", choices=("core", "media", "multimodal"), default="multimodal")
    parser.add_argument("--model-source", help="Optional existing directory containing the pinned public model files")
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto",
                        help="Detect a supported GPU before installing PyTorch; cpu explicitly disables GPU use")
    args = parser.parse_args(argv)
    installer = None
    try:
        if sys.version_info < (3, 11):
            raise ValueError("Python_3.11_or_newer_is_required")
        workspace = plain_path(args.workspace)
        installer = Installer(workspace)
        bundled = (Path(__file__).parent / "release-manifest.json").is_file()
        result = installer.install(args.mode or ("skill" if bundled else "setup"),
                                   features=args.features, model_source=args.model_source, device=args.device)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        failure = {"schema_version": "loki.installation-result.v1", "status": "FAIL",
                   "stage": installer.stage if installer else "workspace_path", "error": str(exc),
                   "commands": installer.commands if installer else [],
                   "device_selection": installer.device_selection if installer else None,
                   "existing_data_preserved": True,
                   "logs": str(installer.log_dir) if installer and installer.log_dir else None,
                   "attempt_id": installer.log_dir.name if installer and installer.log_dir else None}
        if installer and installer.log_dir is not None:
            save(installer.log_dir / "failure.json", failure)
            save(installer.workspace / "installation-result.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
