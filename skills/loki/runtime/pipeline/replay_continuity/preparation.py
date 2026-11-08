"""Prepare a CHZZK VOD/source pair and existing reactions without summaries.

Providers run in an owned subprocess: cancellation closes its process tree.
CHZZK media uses Loki's configured login through the existing private helper.
Network adaptations never change the Report Admin's network/browser state.
YouTube authentication is available only through an explicit private auth choice.
The CHZZK VOD playback clock is this preparation's canonical broadcast clock.
Legacy first-message-relative chat is retained unplaced when it cannot be refreshed.
"""

from __future__ import annotations

from contextlib import contextmanager

import os

import signal

@contextmanager
def _owned_process_tree(process):
    """Own descendants as well as Python (ffmpeg and browser workers)."""
    job = None
    kernel = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_mem", ctypes.c_size_t), ("job_mem", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, int(process._handle)):
            if job:
                kernel.CloseHandle(job)
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("준비 작업의 하위 프로세스 보호를 설정하지 못했습니다.")
    try:
        yield
    finally:
        if job:
            kernel.CloseHandle(job)
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
