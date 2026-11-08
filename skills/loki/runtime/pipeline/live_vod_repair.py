"""Bounded public VOD repair with two independent, hash-bound audio anchors.

This helper never changes STOP, runs a model, or edits the source database.
Only the existing host engine can adopt the returned fragments.
"""

from __future__ import annotations

import hashlib

import math

from pathlib import Path

ANCHOR_MARGIN_SEC = 5

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def local_file(folder, relative):
    if not isinstance(relative, str) or ':' in relative or '\\' in relative or any(part in {'', '.', '..'} for part in relative.split('/')):
        raise ValueError('repair_path_invalid')
    path = folder / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('repair_path_invalid')
    for part in [path, *path.parents]:
        if part == folder.parent:
            break
        if part.is_symlink():
            raise ValueError('repair_path_invalid')
    path.resolve().relative_to(folder.resolve())
    return path

def hls_window(playlist_url, playlist_text, start, duration):
    """Keep the requested HLS fragments and their own decoder initialization.

    fMP4 input seeking can skip packet/initialization boundaries. As with the
    collected-media reader, decode a short playlist, then trim its local clock.
    """
    from pipeline.downloader import _parse_m3u8_attrs, _resolve_m3u8_child_url

    def unavailable():
        raise ValueError('repair_hls_window_unavailable')

    lines = [line.strip() for line in playlist_text.splitlines() if line.strip()]
    if not lines or lines[0] != '#EXTM3U':
        unavailable()
    rows, init, pending, discontinuity, cursor, sequence = [], None, None, False, 0.0, 0
    encrypted, init_range, byte_range = False, False, False
    for line in lines[1:]:
        if line.startswith('#EXT-X-BYTERANGE:'):
            byte_range = True
        elif line.startswith('#EXT-X-KEY:'):
            encrypted = _parse_m3u8_attrs(line.split(':', 1)[1]).get('METHOD') != 'NONE'
        elif line.startswith('#EXT-X-MAP:'):
            attrs = _parse_m3u8_attrs(line.split(':', 1)[1])
            init, init_range = attrs.get('URI'), 'BYTERANGE' in attrs or not attrs.get('URI')
        elif line == '#EXT-X-DISCONTINUITY':
            discontinuity = True
        elif line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            try:
                sequence = int(line.split(':', 1)[1])
            except ValueError:
                unavailable()
        elif line.startswith('#EXTINF:'):
            try:
                pending = float(line.split(':', 1)[1].split(',', 1)[0])
            except ValueError:
                unavailable()
            if not math.isfinite(pending) or pending <= 0:
                unavailable()
        elif not line.startswith('#'):
            if pending is None:
                unavailable()
            rows.append((cursor, cursor + pending, line, init, discontinuity,
                         encrypted or init_range or byte_range))
            cursor += pending
            pending, discontinuity, byte_range = None, False, False
            if cursor >= start + duration:
                break
    if pending is not None or start + duration > cursor + 1e-6:
        unavailable()
    selected = [(index, row) for index, row in enumerate(rows)
                if row[0] < start + duration and row[1] > max(0, start - ANCHOR_MARGIN_SEC)]
    output = ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-PLAYLIST-TYPE:VOD',
              '#EXT-X-TARGETDURATION:' + str(math.ceil(max(row[1] - row[0] for _, row in selected))),
              '#EXT-X-MEDIA-SEQUENCE:' + str(sequence + selected[0][0])]
    prior_init = None
    for index, (_, row) in enumerate(selected):
        a, b, uri, current_init, changed, unsupported = row


        if unsupported:
            raise ValueError('repair_hls_unsupported')
        if changed or index and current_init != prior_init:
            output.append('#EXT-X-DISCONTINUITY')
        if current_init and (index == 0 or current_init != prior_init or changed):
            output.append('#EXT-X-MAP:URI="' + _resolve_m3u8_child_url(playlist_url, current_init) + '"')
        output.extend([f'#EXTINF:{b-a:.9f},', _resolve_m3u8_child_url(playlist_url, uri)])
        prior_init = current_init
    output.append('#EXT-X-ENDLIST')
    return '\n'.join(output) + '\n', start - selected[0][1][0]
