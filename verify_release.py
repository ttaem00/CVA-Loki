"""Validate the exact public release payload before archiving or publication."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

DENIED_PARTS = {".git", ".env", ".claude", ".runtime", "work", "output", "site", "data", "logs", "secrets", "credentials"}
DENIED_MODULES = {"codex_cli.py", "cookie_refresh.py", "openai_responses_free.py", "chzzk_browser_login.py", "daemon.py", "dashboard.py"}
REJECTED_UI = {"skills/loki/runtime/ui/" + name for name in ("index.html", "review.js", "review.css")}
SECRET = re.compile(r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)")


def validate(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "loki.release.v1":
        raise ValueError("release_manifest_schema")
    expected = {"release-manifest.json"}
    for row in manifest["files"]:
        name = row["path"]
        parts = PurePosixPath(name).parts
        if (not parts or PurePosixPath(name).is_absolute() or Path(name).is_absolute() or ":" in name or ".." in parts or "\\" in name
                or any(p.lower() in DENIED_PARTS or p.lower().startswith(".env") for p in parts)
                or parts[-1] in DENIED_MODULES or name in REJECTED_UI or name in expected):
            raise ValueError("release_payload_path_forbidden: " + name)
        target = root / name
        if not target.resolve().is_relative_to(root):
            raise ValueError("release_payload_outside_root: " + name)
        current = root
        for part in parts:
            current /= part
            if current.is_symlink() or (current.exists() and getattr(current.stat(), "st_file_attributes", 0) & 0x400):
                raise ValueError("release_payload_link: " + name)
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"] or len(raw) != row["bytes"]:
            raise ValueError("release_payload_changed: " + name)
        if SECRET.search(raw.decode("utf-8", errors="ignore")):
            raise ValueError("release_secret_pattern: " + name)
        if name.endswith(".py"):
            compile(raw.decode("utf-8-sig"), name, "exec")
        expected.add(name)
    directories = {parent.as_posix() for name in expected for parent in PurePosixPath(name).parents
                   if parent != PurePosixPath(".")}
    actual = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            name = child.relative_to(root).as_posix()
            if child.is_symlink() or getattr(child.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("release_payload_link: " + name)
            if directory == root and child.name == ".git" and (child.is_dir() or child.is_file()):
                continue
            if child.is_dir():
                if name not in directories:
                    raise ValueError("release_unmanifested_payload: " + name)
                pending.append(child)
            elif child.is_file():
                actual.add(name)
            else:
                raise ValueError("release_unmanifested_payload: " + name)
    if expected != actual:
        raise ValueError("release_unmanifested_payload: " + ",".join(sorted(expected ^ actual)))
    plugin = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if plugin["name"] != "loki" or plugin["skills"].rstrip("/") != "./skills":
        raise ValueError("release_plugin_contract")
    skill = root / "skills/loki"
    for required in ("install.py", "verify_release.py"):
        if not (root / required).is_file():
            raise ValueError("release_installer_incomplete: " + required)
    for required in ("SKILL.md", "runtime/loki.py", "runtime/source-manifest.json", "runtime/install.py",
                     "runtime/selftest.py", "runtime/requirements.txt", "runtime/local_tools.py",
                     "runtime/device_setup.py", "runtime/multimodal_setup.py", "runtime/multimodal_worker.py",
                     "runtime/media_selftest.py", "runtime/review.py", "runtime/review_store.py",
                     "runtime/public_ui.py", "runtime/speech_setup.py", "runtime/transcribe_source.py", "runtime/source_acquire.py",
                     "runtime/scripts/admin/static/report_workspace_state.js", "runtime/scripts/admin/static/report_workspace_history.js",
                     "runtime/requirements-multimodal.txt", "runtime/requirements-speech.txt", "runtime/requirements-source.txt", "LICENSE"):
        if not (skill / required).is_file():
            raise ValueError("release_standalone_skill_incomplete: " + required)
    ui = ast.parse((skill / "runtime/public_ui.py").read_text(encoding="utf-8"))
    assignment = next((node for node in ui.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "UI_ASSETS" for target in node.targets)), None)
    names = ast.literal_eval(assignment.value) if assignment else ()
    canonical_paths = {name.removeprefix("skills/loki/runtime/") for name in expected
                       if name.startswith("skills/loki/runtime/")}
    if (not isinstance(names, tuple) or len(names) != 28
            or any(not isinstance(name, str) or name not in canonical_paths for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("release_canonical_ui_incomplete")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    manifest = validate(args.path)
    print(json.dumps({"status": "PASS", "files": len(manifest["files"]), "version": manifest["version"]}))
