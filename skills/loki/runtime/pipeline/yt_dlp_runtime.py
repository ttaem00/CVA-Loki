"""Safe yt-dlp freshness and managed-runtime promotion primitives.

The shared public-video acquisition consumer owns the network boundary. This
module pins the official channel, validates release metadata and checksums, and
promotes only a candidate already staged below the repo-managed root. Importing
or calling the local inspection helpers never performs network I/O or runs a
package manager.
"""

from __future__ import annotations

WINDOWS_SAFE_TRIM_FILENAME_LENGTH = 120

def windows_safe_filename_args(
    *,
    trim_length: int = WINDOWS_SAFE_TRIM_FILENAME_LENGTH,
) -> tuple[str, ...]:
    """Return the shared fail-closed yt-dlp filename/config boundary."""

    bounded = int(trim_length)
    if bounded < 32 or bounded > WINDOWS_SAFE_TRIM_FILENAME_LENGTH:
        raise ValueError("yt-dlp trim length must be between 32 and 120")
    return (
        "--ignore-config",
        "--windows-filenames",
        "--trim-filenames",
        str(bounded),
    )
