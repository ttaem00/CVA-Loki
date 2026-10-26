"""Media probes use the explicit workspace tool receipt without host installation discovery."""

import subprocess

import sys

import os

from pathlib import Path

def find_bin(name):
    import json, hashlib
    root = os.environ.get('LOKI_WORKSPACE')
    if not root or name not in {'ffmpeg', 'ffprobe'}:
        raise RuntimeError('loki_media_tools_not_configured')
    path = Path(root).resolve() / '.loki-tools.json'
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('loki_media_tools_receipt_missing')
    row = json.loads(path.read_text(encoding='utf-8'))['tools'][name]
    binary = Path(row['path']).resolve(strict=True)
    value = hashlib.sha256()
    with binary.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    if value.hexdigest() != row['sha256']:
        raise RuntimeError('loki_media_tool_changed')
    return str(binary)

FFPROBE = find_bin('ffprobe')

FFMPEG = find_bin('ffmpeg')

def _creationflags() -> int:
    """Windows: CREATE_NO_WINDOW 로 ffmpeg/ffprobe 를 조용히 띄운다.

    긴 VOD 를 다수 파트로 빠르게 연속 처리할 때 Windows 가 child 프로세스
    콘솔 subsystem 초기화를 거부하며 0xC0000142 를 내는 경우가 있어
    예방적으로 적용.
    """
    if sys.platform != 'win32':
        return 0
    return getattr(subprocess, 'CREATE_NO_WINDOW', 134217728)

def get_duration(input_path):
    """Return media duration in seconds via ffprobe (format/container level)."""
    result = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_creationflags())
    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        detail = stderr or stdout or '(ffprobe output unavailable)'
        raise RuntimeError(f'ffprobe failed (returncode={result.returncode}):\n{detail}\ninput={input_path}')
    stdout = (result.stdout or '').strip()
    if not stdout:
        raise RuntimeError(f'ffprobe returned empty duration: {input_path}')
    try:
        duration = float(stdout)
    except ValueError as exc:
        raise RuntimeError(f'ffprobe returned non-numeric duration: {stdout!r} ({input_path})') from exc
    if duration <= 0:
        raise RuntimeError(f'invalid media duration: {duration} ({input_path})')
    return duration

def get_audio_duration(input_path, *, with_source=False):
    """Return audio stream duration in seconds via ffprobe.

    Uses ``-select_streams a:0`` to query the first audio stream's duration,
    which is more reliable than container-level ``format=duration`` for
    HLS-concatenated mp4 files where the container metadata can overstate the
    actual media length.  Falls back to ``get_duration()`` (format level) when
    the audio stream has no explicit duration field.

    If *with_source* is True, returns ``(duration, source)`` where *source* is
    ``"stream"`` (reliable) or ``"format"`` (container-level fallback that may
    overstate for HLS-concatenated files).
    """
    _source = 'format'
    _dur = None
    try:
        result = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_creationflags())
    except OSError:
        _dur = get_duration(input_path)
        return (_dur, 'format') if with_source else _dur
    stdout = (result.stdout or '').strip()
    if result.returncode != 0 or not stdout or stdout.upper() == 'N/A':
        _dur = get_duration(input_path)
        return (_dur, 'format') if with_source else _dur
    try:
        duration = float(stdout)
    except ValueError:
        _dur = get_duration(input_path)
        return (_dur, 'format') if with_source else _dur
    if duration <= 0:
        _dur = get_duration(input_path)
        return (_dur, 'format') if with_source else _dur
    return (duration, 'stream') if with_source else duration

class AudioProbeResult:
    """Tri-state result from audio stream probing.

    - has_audio: ffprobe confirmed audio stream exists
    - no_audio: ffprobe completed successfully, no audio stream found
    - probe_failed: ffprobe timed out, crashed, or couldn't run
    """
    __slots__ = ('state', 'detail')

    def __init__(self, state: str, detail: str=''):
        self.state = state
        self.detail = detail

    def __bool__(self):
        return self.state == 'has_audio'

    def __repr__(self):
        return f'AudioProbeResult({self.state!r}, {self.detail!r})'

def probe_audio_stream(input_path) -> AudioProbeResult:
    'Probe whether *input_path* contains an audio stream (tri-state).\n\n    a:0 고정 — get_audio_duration, _probe_audio_at_offset, extract_audio 와\n    동일 stream 사용.\n    '
    try:
        result = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10, creationflags=_creationflags())
    except subprocess.TimeoutExpired:
        return AudioProbeResult('probe_failed', 'ffprobe timeout (10s)')
    except OSError as e:
        return AudioProbeResult('probe_failed', f'OSError: {e}')
    if result.returncode != 0:
        return AudioProbeResult('probe_failed', f'ffprobe rc={result.returncode}')
    if bool((result.stdout or '').strip()):
        return AudioProbeResult('has_audio')
    return AudioProbeResult('no_audio')
