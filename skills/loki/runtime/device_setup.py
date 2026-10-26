"""Select pinned PyTorch wheels from public GPU hardware before importing torch.

Sources: https://pytorch.org/get-started/previous-versions/
https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html
https://docs.pytorch.org/docs/2.11/notes/mps.html
"""
from __future__ import annotations

import csv
import ctypes
import io
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def torch_requirements(selection):
    suffix = {CUDA_INDEX: "+cu128", CPU_INDEX: "+cpu", None: ""}[selection["index_url"]]
    return ["torch==2.11.0" + suffix, "torchvision==0.26.0" + suffix]


class DeviceSelectionError(RuntimeError):
    def __init__(self, selection):
        self.selection = selection
        super().__init__(selection["reason"] + ": " + selection["action"])


def _hardware_names():
    if sys.platform == "win32":
        from ctypes import wintypes
        class Display(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]
        names = []
        for index in range(32):
            display = Display()
            display.cb = ctypes.sizeof(Display)
            if not ctypes.windll.user32.EnumDisplayDevicesW(None, index, ctypes.byref(display), 0):
                break
            names.append(display.DeviceString)
        return list(dict.fromkeys(names))
    if sys.platform.startswith("linux"):
        names = []
        for path in Path("/sys/bus/pci/devices").glob("*/vendor"):
            try:
                vendor = path.read_text(encoding="ascii").strip()
                device_class = path.with_name("class").read_text(encoding="ascii").strip()
            except OSError:
                continue
            if device_class.startswith("0x03") and vendor in {"0x10de", "0x1002"}:
                names.append("NVIDIA display adapter" if vendor == "0x10de" else "AMD display adapter")
        return list(dict.fromkeys(names))
    return []


def detect_hardware():
    machine = platform.machine().lower()
    names = _hardware_names()
    nvidia = any("nvidia" in name.lower() for name in names)
    result = dict(platform=sys.platform, machine=machine, hardware_names=names,
        nvidia_detected=nvidia, devices=[], probe_status="not_found", reason="no_compatible_gpu_detected",
        mps_candidate=sys.platform == "darwin" and machine in {"arm64", "aarch64"},
        unsupported_gpu=any(any(marker in name.lower() for marker in ("amd", "radeon")) for name in names))
    command = shutil.which("nvidia-smi")
    if not command:
        if nvidia:
            result.update(probe_status="failed", reason="nvidia_driver_tool_missing")
        return result
    try:
        proc = subprocess.run([command, "--query-gpu=name,driver_version,memory.total,compute_cap",
            "--format=csv,noheader,nounits"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        result.update(nvidia_detected=True, probe_status="failed", reason="nvidia_driver_query_failed")
        return result
    if proc.returncode:
        result.update(nvidia_detected=True, probe_status="failed", reason="nvidia_driver_query_failed",
                      probe_exit_code=proc.returncode)
        return result
    try:
        for row in csv.reader(io.StringIO(proc.stdout)):
            if not row:
                continue
            if len(row) != 4:
                raise ValueError("columns")
            name, driver, memory, capability = (part.strip() for part in row)
            if not name or not re.fullmatch(r"\d+(?:\.\d+)+", driver) or not re.fullmatch(r"\d+\.\d+", capability):
                raise ValueError("GPU metadata")
            memory = int(memory)
            if memory <= 0:
                raise ValueError("GPU memory")
            result["devices"].append(dict(name=name, driver_version=driver,
                memory_total_mib=memory, compute_capability=capability))
    except (ValueError, TypeError):
        result.update(nvidia_detected=True, probe_status="failed", reason="nvidia_driver_query_invalid")
        return result
    result.update(nvidia_detected=bool(result["devices"]) or nvidia, probe_status="ready",
                  reason="nvidia_gpu_detected" if result["devices"] else "no_compatible_gpu_detected")
    return result


def select_device(requested="auto", hardware=None):
    if requested not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("installation_device_invalid")
    if requested == "cpu":
        return dict(requested=requested, selected="cpu", backend="cpu", index_url=(
            None if sys.platform == "darwin" else CPU_INDEX), reason="explicit_cpu",
            detection=dict(probe_status="skipped", reason="explicit_cpu", nvidia_detected=False, devices=[]))
    hardware = detect_hardware() if hardware is None else hardware
    def blocked(reason, action):
        raise DeviceSelectionError(dict(requested=requested, selected=None, backend=None,
            index_url=None, reason=reason, action=action, detection=hardware))
    if hardware["nvidia_detected"] or requested == "cuda":
        if hardware.get("probe_status") != "ready" or not hardware.get("devices"):
            blocked(hardware.get("reason", "nvidia_gpu_unavailable"),
                "Make the NVIDIA driver/GPU visible (Docker: --gpus all), or explicitly choose --device cpu.")
        if hardware["platform"] not in {"win32", "linux"}:
            blocked("cuda_platform_unsupported", "Use a supported Linux/Windows NVIDIA environment or --device cpu.")
        minimum = (570, 65) if hardware["platform"] == "win32" else (570, 26)
        driver = tuple(int(part) for part in hardware["devices"][0]["driver_version"].split("."))
        if driver < minimum:
            blocked("cuda128_driver_update_required", "Update the NVIDIA driver for CUDA 12.8, or explicitly choose --device cpu.")
        return dict(requested=requested, selected="cuda", backend="cuda", index_url=CUDA_INDEX,
            reason="nvidia_cuda128_selected", detection=hardware)
    if hardware.get("mps_candidate") or requested == "mps":
        version = platform.mac_ver()[0]
        if hardware["platform"] != "darwin" or not version or tuple(map(int, version.split(".")[:2])) < (12, 3):
            blocked("mps_platform_unavailable", "Use a supported Apple Silicon macOS environment or --device cpu.")
        return dict(requested=requested, selected="mps", backend="mps", index_url=None,
            reason="apple_mps_selected_requires_runtime_verification", detection=hardware)
    if hardware.get("unsupported_gpu"):
        blocked("gpu_backend_not_supported_by_this_installer", "This release does not select a ROCm wheel automatically; explicitly choose --device cpu.")
    return dict(requested=requested, selected="cpu", backend="cpu", index_url=(
        None if hardware["platform"] == "darwin" else CPU_INDEX), reason="no_compatible_gpu_detected", detection=hardware)
