"""Bounded public CHZZK recording and durable bridge-event ingestion.

The local manager owns only processes it creates. Stream resolution never uses
cookies or browser state, and signed playback URLs never reach manifests/logs.
Chat bridges submit events explicitly; starting a recording does not imply that
ManagerCue's IndexedDB or command-bot messages are being collected automatically.
"""

from __future__ import annotations

import subprocess

from functools import lru_cache

@lru_cache(maxsize=4)
def _hls_demuxer_args(ffmpeg: str) -> tuple[str, ...]:
    """Accept CHZZK fMP4 segments named .ts without relaxing network protocols.

    Recent ffmpeg versions added an extension/content equality check; CHZZK's
    valid public fMP4 segments can have a .ts URL. Older versions lack the
    option and already accept these segments, so probe the local demuxer once.
    Keep the default extension allowlist and the separate protocol allowlist.
    """
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-h", "demuxer=hls"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=2, check=False)
        if result.returncode == 0 and "-extension_picky " in (result.stdout or ""):
            return ("-extension_picky", "0")
    except (OSError, subprocess.SubprocessError):
        pass
    return ()
