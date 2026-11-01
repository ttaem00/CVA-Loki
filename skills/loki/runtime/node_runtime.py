"""Workspace-local official Node pins; authoring only reads the verified result."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess
import sysconfig
import tarfile
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

VERSION = "24.22.1"
SCHEMA = "loki.node-runtime.v1"
RECEIPT = ".loki-node-runtime.json"

PINS = {
    "win-x64": ("zip", "d2046d28c2c3dcd211ac5145c0bf30e2047ef9dfb306693c8c6e2eccd8b95fa5"),
    "win-arm64": ("zip", "a0c807319fede24c4a0d8f84564c82a619bd313b16b10c007c383c2609e99171"),
    "darwin-x64": ("tar.gz", "d132557e7ee2b85af08b0c8c34338e65c6430f2238d01f727728ef42dc83b035"),
    "darwin-arm64": ("tar.gz", "89c682593d76cd44233fa53b1563264e7de9cbb189cad169135c5b4fadfbc0a7"),
    "linux-x64": ("tar.xz", "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6"),
    "linux-arm64": ("tar.xz", "dbabf7a1cbf947e6059ffad2c27b80041db97dea1e553b62afdee7f13fca336e"),
    "linux-x64-musl": ("tar.xz", "b83b6d679e4bf1128c8e9e982f3e64cd8badaddf1e38613aa36f93186395f9fa"),
}
MAX_ARCHIVE = 96 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
NODE_PROBE = r'''const z=require('node:zlib');
if(process.version!=='v24.22.1'||z.constants.BROTLI_DECODER_ERROR_FORMAT_BLOCK_SWITCH!==-17)throw Error('node_security_runtime_invalid');
const raw=Buffer.from('Loki runtime security canary');
if(!z.brotliDecompressSync(z.brotliCompressSync(raw)).equals(raw))throw Error('node_brotli_roundtrip');
console.log(JSON.stringify({node:process.version,brotli:process.versions.brotli,blockSwitchError:-17,roundtrip:true}));
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def owned(root, path):
    root, path = Path(root).resolve(strict=True), Path(path).absolute()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("node_runtime_outside_workspace")
    cursor = root
    for part in path.relative_to(root).parts:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("node_runtime_link_forbidden")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError("node_runtime_hardlink_forbidden")
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("node_runtime_outside_workspace")
    return resolved


def platform_key():
    system, machine = platform.system().lower(), platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    system = {"windows": "win", "darwin": "darwin", "linux": "linux"}.get(system)
    key = f"{system}-{arch}"
    if system == "linux":
        libc = platform.libc_ver()[0].lower()
        multiarch = str(sysconfig.get_config_var("MULTIARCH") or "").lower()
        if libc == "musl" or "musl" in multiarch:
            key += "-musl"
        elif libc != "glibc" and "linux-gnu" not in multiarch:
            raise ValueError("node_runtime_libc_unverified")
    if key not in PINS:
        raise ValueError("node_runtime_platform_unsupported")
    return key


def pin(key):
    if key not in PINS:
        raise ValueError("node_runtime_platform_unsupported")
    extension, checksum = PINS[key]
    prefix = f"node-v{VERSION}-{key}"
    windows = key.startswith("win-")
    return dict(platform=key, version=VERSION, prefix=prefix,
        url=f"https://nodejs.org/dist/v{VERSION}/{prefix}.{extension}", sha256=checksum,
        node="node.exe" if windows else "bin/node",
        npm="node_modules/npm/bin/npm-cli.js" if windows else "lib/node_modules/npm/bin/npm-cli.js")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("node_runtime_redirect_forbidden")


def download(selected):
    if selected != pin(selected.get("platform")):
        raise ValueError("node_runtime_download_pin_invalid")
    request = Request(selected["url"], headers={"Accept-Encoding": "identity", "User-Agent": "Loki-Node-Installer/1.0"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=60) as response:
        if response.status != 200 or response.url != selected["url"]:
            raise ValueError("node_runtime_download_failed")
        raw = response.read(MAX_ARCHIVE + 1)
    if not 1 <= len(raw) <= MAX_ARCHIVE or sha(raw) != selected["sha256"]:
        raise ValueError("node_runtime_archive_digest")
    return raw


def material(raw, selected):
    """Read only Node, npm regular files and license; never extract archive links."""
    if not 1 <= len(raw) <= MAX_ARCHIVE or sha(raw) != selected["sha256"]:
        raise ValueError("node_runtime_archive_digest")
    result, seen, total = {}, set(), 0
    npm_prefix = selected["npm"].removesuffix("bin/npm-cli.js")

    def member(name, size, regular, directory, read):
        nonlocal total
        path = PurePosixPath(name.rstrip("/"))
        if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or str(path) != name.rstrip("/") or path.parts[0] != selected["prefix"]):
            raise ValueError("node_runtime_archive_member")
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        key = str(path).casefold()
        if key in seen or len(seen) >= 20000 or not 0 <= size <= MAX_EXPANDED:
            raise ValueError("node_runtime_archive_member")
        seen.add(key)
        total += size
        if total > MAX_EXPANDED:
            raise ValueError("node_runtime_archive_size")
        if directory or relative not in {selected["node"], "LICENSE"} and not relative.startswith(npm_prefix):
            return
        if not regular or any(part.rstrip(". ").split(".")[0].upper() in
                {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                or part.endswith((".", " ")) for part in path.parts):
            raise ValueError("node_runtime_archive_member")
        value = read(size + 1)
        if len(value) != size:
            raise ValueError("node_runtime_archive_size")
        result[relative] = value

    if selected["url"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for row in archive.infolist():
                mode = row.external_attr >> 16
                with archive.open(row) as stream:
                    member(row.filename, row.file_size, stat.S_IFMT(mode) in {0, stat.S_IFREG} and not row.is_dir(),
                           row.is_dir(), stream.read)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            for row in archive:
                stream = archive.extractfile(row) if row.isfile() else None
                member(row.name, row.size, row.isfile(), row.isdir(), stream.read if stream else lambda n: b"")
                if stream:
                    stream.close()
    if not {selected["node"], selected["npm"], "LICENSE"} <= result.keys():
        raise ValueError("node_runtime_archive_incomplete")
    return result


def probe(node):
    environment = {k: v for k, v in os.environ.items() if k.upper() not in {"NODE_OPTIONS", "NODE_PATH"}}
    value = subprocess.run([str(node), "-e", NODE_PROBE], capture_output=True, text=True,
                           encoding="utf-8", timeout=30, check=True, env=environment)
    result = json.loads(value.stdout)
    if (not isinstance(result, dict) or result.get("node") != "v" + VERSION
            or result.get("blockSwitchError") != -17 or result.get("roundtrip") is not True
            or not isinstance(result.get("brotli"), str)):
        raise ValueError("node_security_runtime_invalid")
    return result


def _save(path, value):
    temporary = owned(path.parent, path.with_name(path.name + ".part"))
    if temporary.exists():
        raise ValueError("node_runtime_partial_receipt_preserved")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def _identity(root, selected, files):
    target = owned(root, root / ".tools" / selected["prefix"])
    archive = owned(root, root / ".tools/node-archives" / selected["url"].rsplit("/", 1)[1])
    return dict(schema_version=SCHEMA, platform=selected["platform"], version=VERSION,
        archive=dict(path=str(archive), url=selected["url"], sha256=selected["sha256"]),
        node=dict(path=str(target / selected["node"]), sha256=sha(files[selected["node"]])),
        npm=dict(path=str(target / selected["npm"]), sha256=sha(files[selected["npm"]])))


def _read_archive(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE + 1)
    if not 1 <= len(raw) <= MAX_ARCHIVE:
        raise ValueError("node_runtime_archive_size")
    return raw


def load(workspace, *, verify_probe=True):
    root = Path(workspace).resolve(strict=True)
    receipt = owned(root, root / RECEIPT)
    if not receipt.is_file():
        return None
    value = json.loads(receipt.read_text(encoding="utf-8"))
    selected = pin(value.get("platform"))
    archive = owned(root, root / ".tools/node-archives" / selected["url"].rsplit("/", 1)[1])
    files = material(_read_archive(archive), selected)
    identity = _identity(root, selected, files)
    if ({k: v for k, v in value.items() if k not in {"status", "verification"}} != identity
            or value.get("status") != "ready"):
        raise ValueError("node_runtime_receipt_invalid")
    for name in ("node", "npm"):
        path = owned(root, Path(identity[name]["path"]))
        if (not path.is_file() or path.stat().st_size != len(files[selected[name]])
                or sha(path.read_bytes()) != identity[name]["sha256"]):
            raise ValueError("node_runtime_binary_changed")
    verification = probe(identity["node"]["path"]) if verify_probe else value.get("verification")
    if not isinstance(verification, dict) or verification.get("node") != "v" + VERSION or verification.get("blockSwitchError") != -17 or verification.get("roundtrip") is not True:
        raise ValueError("node_runtime_receipt_invalid")
    return dict(node=identity["node"]["path"], npm=identity["npm"]["path"],
        node_sha256=identity["node"]["sha256"], archive_sha256=selected["sha256"],
        version=VERSION, verification=verification)


def install(workspace):
    root = Path(workspace).resolve(strict=True)
    selected = pin(platform_key())
    target = owned(root, root / ".tools" / selected["prefix"])
    receipt = owned(root, root / RECEIPT)
    if target.exists() and not receipt.is_file():
        raise ValueError("node_runtime_unowned_directory_preserved")
    archive = owned(root, root / ".tools/node-archives" / selected["url"].rsplit("/", 1)[1])
    raw = _read_archive(archive) if archive.is_file() else download(selected)
    files = material(raw, selected)
    identity = _identity(root, selected, files)
    if receipt.is_file():
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if ({k: v for k, v in saved.items() if k not in {"status", "verification"}} != identity
                or saved.get("status") not in {"preparing", "ready"}):
            raise ValueError("node_runtime_owner_changed_preserved")
    if target.exists():
        for path in target.rglob("*"):
            path = owned(root, path)
            if path.is_file() and (path.relative_to(target).as_posix() not in files
                    or path.stat().st_size != len(files[path.relative_to(target).as_posix()])
                    or path.read_bytes() != files[path.relative_to(target).as_posix()]):
                raise ValueError("node_runtime_existing_files_changed_preserved")
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("xb") as stream:
            stream.write(raw)
    _save(receipt, dict(identity, status="preparing"))
    for relative, raw_file in files.items():
        destination = owned(root, target / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(raw_file)
        if relative == selected["node"] and os.name != "nt":
            destination.chmod(0o755)
    verification = probe(identity["node"]["path"])
    _save(receipt, dict(identity, status="ready", verification=verification))
    return load(root, verify_probe=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.workspace)))
