"""Install and admit only Loki's local media tools and bounded embedding worker."""
from __future__ import annotations

import argparse
import contextlib
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

SCHEMA = "loki.local-tools.v1"
PROTOCOL = ["-protocol_whitelist", "file,pipe"]
FORMAT = ["-format_whitelist", "mov,matroska,webm"]
MEDIA = {".mp4", ".mkv", ".webm", ".mov"}


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _plain(root, path):
    root = Path(root).resolve(strict=True)
    value = str(path)
    if os.name == "nt" and value.startswith("\\\\?\\") and re.match(r"^[A-Za-z]:[\\/]", value[4:]):
        value = value[4:]
    if value.startswith(("\\\\", "//")) or (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
            and not re.match(r"^[A-Za-z]:[\\/]", value)):
        raise PermissionError("loki_local_tool_url_forbidden")
    path = Path(value)
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise PermissionError("loki_local_tool_path_outside_workspace")
    current = root
    for part in path.absolute().relative_to(root).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("loki_local_tool_link_forbidden")
    return resolved


def _owned(root, path, *, media=False, exists=False):
    path = _plain(root, path)
    root = Path(root).resolve()
    configured = os.environ.get("LOKI_WORK_DIR") if os.environ.get("LOKI_WORKSPACE") == str(root) else None
    work = _plain(root, configured or root / "work")
    if not path.is_relative_to(work) and path.relative_to(root).parts[0] not in {".models", "verification"}:
        raise PermissionError("loki_local_tool_path_not_owned")
    if media and path.suffix.lower() not in MEDIA:
        raise PermissionError("loki_local_tool_media_type")
    if exists and not path.is_file():
        raise PermissionError("loki_local_tool_input_missing")
    return path


def _node_runtime():
    spec = importlib.util.spec_from_file_location("loki_node_runtime", Path(__file__).with_name("node_runtime.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _node_tool(root, preferred=None):
    helper = _node_runtime()
    managed = helper.load(root)
    if managed:
        return dict(path=managed["node"], sha256=managed["node_sha256"])
    node = preferred["path"] if preferred else shutil.which("node")
    if node:
        node = Path(node).resolve(strict=True)
        if preferred and _sha(node) != preferred["sha256"]:
            raise PermissionError("loki_local_node_binary_changed")
        helper.probe(node)
        return dict(path=str(node), sha256=_sha(node))
    return None


def setup(workspace):
    root = Path(workspace).resolve(strict=True)
    receipt = _plain(root, root / ".loki-tools.json")
    paths = [shutil.which(name) for name in ("ffmpeg", "ffprobe")]
    source = "path"
    if not all(paths):
        from static_ffmpeg import run
        folder = Path(run.__file__).resolve().parent
        if sys.prefix == sys.base_prefix or not folder.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError("loki_static_ffmpeg_requires_running_venv")
        with contextlib.redirect_stdout(sys.stderr):
            paths = run.get_or_fetch_platform_executables_else_raise()
        if any(not Path(path).resolve().is_relative_to(folder) for path in paths):
            raise ValueError("loki_static_ffmpeg_outside_package")
        source = "venv_static_ffmpeg"
    tools = {}
    for name, path in zip(("ffmpeg", "ffprobe"), paths):
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("loki_local_tool_binary_missing")
        tools[name] = dict(path=str(path), sha256=_sha(path))
    node = _node_tool(root)
    if node:
        tools["node"] = node
    result = dict(schema_version=SCHEMA, status="ready", source=source, tools=tools)
    temporary = _plain(root, root / ".loki-tools.json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, receipt)
    return result


def _tools(root):
    path = _plain(root, Path(root) / ".loki-tools.json")
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA
            or value.get("status") != "ready" or not isinstance(value.get("tools"), dict)
            or not {"ffmpeg", "ffprobe"} <= set(value["tools"]) <= {"ffmpeg", "ffprobe", "node"}):
        raise ValueError("loki_local_tools_receipt_invalid")
    tools = value["tools"]
    for row in tools.values():
        if (not isinstance(row, dict) or set(row) != {"path", "sha256"}
                or not isinstance(row["path"], str) or not Path(row["path"]).is_absolute()
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
            raise ValueError("loki_local_tools_receipt_invalid")
    return tools


def _number(value, maximum=1_000_000_000):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise PermissionError("loki_local_tool_number") from None
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise PermissionError("loki_local_tool_number")
    return number


def _binary(executable):
    resolved = shutil.which(str(executable)) if not Path(str(executable)).is_absolute() else executable
    return Path(resolved).resolve() if resolved else None


def validate_command(workspace, executable, argv, tools=None, *, runtime=None):
    root = Path(workspace).resolve(strict=True)
    runtime = Path(runtime or Path(__file__).parent).resolve()
    if not isinstance(argv, (tuple, list)) or not argv or not all(isinstance(item, str) for item in argv):
        raise PermissionError("loki_local_tool_argv")
    argv = list(argv)
    binary = _binary(executable)
    if binary != _binary(argv[0]):
        raise PermissionError("loki_local_tool_executable_mismatch")
    tools = _tools(root) if tools is None else tools
    known_node = tools.get("node")
    if binary and (binary.name.lower() in {"node", "node.exe"}
                   or known_node and binary == Path(known_node["path"]).resolve()):
        if any(key.upper() in {"NODE_OPTIONS", "NODE_PATH"} for key in os.environ):
            raise PermissionError("loki_local_node_process_override")
        if (not known_node or binary != Path(known_node["path"]).resolve()
                      or _sha(binary) != known_node["sha256"]):
            raise PermissionError("loki_local_node_binary_changed")
        allowed = {runtime / "tools/parse_editor_comments.js", runtime / "tools/project_comment_collaboration.js"}
        if len(argv) == 2 and Path(argv[1]).resolve() in allowed:
            return "node"
        raise PermissionError("loki_unadmitted_node_helper")
    if {"ffmpeg", "ffprobe"} <= tools.keys() and binary == Path(sys.executable).resolve():
        if (len(argv) == 7 and argv[1:3] == ["-B", str(runtime / "multimodal_worker.py")]
                and argv[3] == "--workspace" and Path(argv[4]).resolve() == root and argv[5] == "--spec"):
            spec = _owned(root, argv[6], exists=True)
            if spec.suffix == ".json":
                return "mm_worker"
        raise PermissionError("loki_unadmitted_python_worker")
    kind = next((name for name, row in tools.items() if binary == Path(row["path"]).resolve()), None)
    if kind is None:
        raise PermissionError("loki_current_codex_author_required: subprocess denied")
    if _sha(binary) != tools[kind]["sha256"]:
        raise PermissionError("loki_local_tool_binary_changed")
    args = list(argv[1:])
    if args[:2] != PROTOCOL:
        raise PermissionError("loki_local_tool_protocol_policy")
    args = args[2:]
    if args[:2] != FORMAT:
        raise PermissionError("loki_local_tool_format_policy")
    args = args[2:]
    if args == ["-version"]:
        return kind
    if kind == "ffprobe":
        if args[:-1] not in (["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"],
                            ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "json"]):
            raise PermissionError("loki_unadmitted_ffprobe")
        _owned(root, args[-1], media=True, exists=True)
        return kind
    if len(args) == 15 and args[:5] == ["-hide_banner", "-loglevel", "error", "-y", "-ss"]:
        _number(args[5])
        if args[6] != "-i" or args[8] != "-vf" or args[10:14] != ["-frames:v", "9", "-q:v", "3"]:
            raise PermissionError("loki_unadmitted_frame_decode")
        match = re.fullmatch(r"fps=1/([0-9.]+),scale=-2:([0-9]+)", args[9])
        if not match or not 0 < _number(match[1], 120) or not 2 <= int(match[2]) <= 4096:
            raise PermissionError("loki_unadmitted_frame_filter")
        _owned(root, args[7], media=True, exists=True)
        output = _owned(root, args[14])
        if output.name != "frame-%02d.jpg":
            raise PermissionError("loki_unadmitted_frame_output")
        return kind
    if args[:5] == ["-hide_banner", "-loglevel", "error", "-threads", "2"] and len(args) >= 18:
        seek = args[5:9]
        if seek[0] == "-ss" and seek[2] == "-i":
            _number(seek[1]); _owned(root, seek[3], media=True, exists=True)
        elif seek[0] == "-i" and seek[2] == "-ss":
            _owned(root, seek[1], media=True, exists=True); _number(seek[3])
        else:
            raise PermissionError("loki_unadmitted_multimodal_seek")
        if args[9] != "-t" or args[11:13] != ["-threads", "2"]:
            raise PermissionError("loki_unadmitted_multimodal_scope")
        duration = _number(args[10], 30)
        suffix = args[13:]
        audio = ["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]
        video = ["-an", "-vf", "fps=1,scale=256:144", "-frames:v", str(math.ceil(duration)),
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        if duration > 0 and suffix in (audio, video):
            return kind
    raise PermissionError("loki_unadmitted_ffmpeg")


def install(workspace, *, runtime=None):
    root = Path(workspace).resolve(strict=True)
    tools = _tools(root)


    for key in list(os.environ):
        if key.upper() in {"NODE_OPTIONS", "NODE_PATH"}:
            os.environ.pop(key)
    node = _node_tool(root, tools.get("node"))
    if node:
        if tools.get("node") is not None and tools["node"] != node:
            raise PermissionError("loki_local_node_binary_changed")
        tools["node"] = node
    directories = []
    for name in ("node", "ffmpeg", "ffprobe"):
        if name not in tools:
            continue
        row = tools[name]
        path = Path(row["path"]).resolve(strict=True)
        if not path.is_file() or _sha(path) != row["sha256"]:
            raise PermissionError("loki_local_tool_binary_changed")
        directory = str(path.parent)
        if directory not in directories:
            directories.append(directory)
    if directories:
        os.environ["PATH"] = os.pathsep.join([*directories, os.environ.get("PATH", "")])
    original_run = subprocess.run
    admitted_run = ContextVar("loki_admitted_run", default=None)
    def run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv:
            binary = _binary(argv[0])
            if binary is not None:
                argv = list(argv)
                argv[0] = str(binary)
            if any(binary == Path(row["path"]).resolve() for name, row in tools.items()
                   if name in {"ffmpeg", "ffprobe"}):
                if argv[1:3] != PROTOCOL:
                    argv[1:1] = PROTOCOL
                if argv[3:5] != FORMAT:
                    argv[3:3] = FORMAT
        if kwargs.get("shell") or kwargs.get("executable") or kwargs.get("env") is not None:
            raise PermissionError("loki_local_tool_process_override")
        validate_command(root, argv[0] if isinstance(argv, (list, tuple)) and argv else "", argv,
                         tools, runtime=runtime)
        token = admitted_run.set(tuple(argv))
        try:
            return original_run(argv, *args, **kwargs)
        finally:
            admitted_run.reset(token)
    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "os.system"}:
            raise PermissionError("loki_offline_policy: " + event)
        if event == "subprocess.Popen":
            if args[3] is not None:
                raise PermissionError("loki_local_tool_process_override")
            executable, command = args[0], args[1]
            if isinstance(command, str):
                bound = admitted_run.get()
                if os.name != "nt" or bound is None or command != subprocess.list2cmdline(bound):
                    raise PermissionError("loki_local_tool_argv")
                command = bound
                if executable is None:
                    executable = bound[0]
            validate_command(root, executable, command, tools, runtime=runtime)
    sys.addaudithook(audit)
    subprocess.run = run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup"])
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    try:
        result = setup(args.workspace)
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps(dict(status="FAIL", error=str(error))))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
