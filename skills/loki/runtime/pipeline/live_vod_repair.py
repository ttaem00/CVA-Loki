"""Bounded public VOD repair with two independent, hash-bound audio anchors.

This helper never changes STOP, runs a model, or edits the source database.
Only the existing host engine can adopt the returned fragments.
"""

from __future__ import annotations

import hashlib

from contextlib import closing

import json

import math

import os

from pathlib import Path

import re

import sqlite3

import subprocess

import wave

from urllib.parse import urlsplit

import numpy as np

from pipeline.media_tools.chzzk import resolve_chzzk_source, same_chzzk_source

from pipeline.media_tools.executor import _tool_paths, _remote_input_args

from pipeline.evidence_world.contracts import EvidenceError

from pipeline.evidence_world.temporal_source import resolve_session

from scripts.admin.live_monitor_vod import public_vod_metadata, verify_live_vod

from scripts.admin.report_playback import ReportPlaybackService, read_terminal_hls_metadata

CLOCK_PROOF_VERSION = 'pcm-lowpass-v4-interior-16khz'

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

def pcm(path, *, sample_rate=4000):
    with wave.open(str(path), 'rb') as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != 16000:
            raise ValueError('repair_audio_format_invalid')
        raw = np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2').astype(np.float64)


        positions = np.arange(-32, 33, dtype=np.float64)
        kernel = np.sinc(2 * 1900 / 16000 * positions) * np.hanning(65)
        kernel /= kernel.sum()
        return np.convolve(raw, kernel, mode='same')[::16000 // sample_rate]

def locate_anchor(reference, target, *, sample_rate=4000):
    if len(reference) < 4 * sample_rate or len(target) < len(reference):
        raise ValueError('repair_clock_unverified')
    reference = reference - reference.mean()
    energy = np.sum(reference ** 2)
    if energy / len(reference) < 100:
        raise ValueError('repair_clock_unverified')
    size = len(reference)
    fft_size = 1 << (len(target) + size - 2).bit_length()
    convolution = np.fft.irfft(np.fft.rfft(target, fft_size) *
                               np.fft.rfft(reference[::-1], fft_size), fft_size)
    dot = convolution[size - 1:len(target)]
    prefix = np.concatenate(([0.0], np.cumsum(target)))
    squared = np.concatenate(([0.0], np.cumsum(target ** 2)))
    local_energy = squared[size:] - squared[:-size] - (prefix[size:] - prefix[:-size]) ** 2 / size
    scores = dot / np.sqrt(np.maximum(local_energy, 1) * energy)
    position = int(np.argmax(scores))
    score = float(scores[position])

    competitors = scores.copy()
    radius = sample_rate // 5
    competitors[max(0, position - radius):position + radius + 1] = -1
    if score < .92 or score - float(competitors.max(initial=-1)) < .08:
        raise ValueError('repair_clock_unverified')
    return position / sample_rate, score

def select_anchors(eligible, folder, *, not_before_ms=None):
    """Skip incomplete replay edges and silence before any audio download."""
    if len(eligible) < 2:
        raise ValueError('repair_clock_unverified')
    def usable(row):
        if not_before_ms is not None and row['start_at_ms'] < not_before_ms:
            return False
        path = local_file(folder, row['relative_path'])
        if sha(path) != row['sha256']:
            raise ValueError('repair_anchor_changed')
        samples = pcm(path)
        return len(samples) >= 16000 and float(np.var(samples)) >= 100


    early = next((row for row in eligible[:64] if usable(row)), None)
    late = next((row for row in reversed(eligible[-64:]) if usable(row)), None)
    if not early or not late or late['start_at_ms'] - early['end_at_ms'] < 1000:
        raise ValueError('repair_anchor_unavailable')
    revision = hashlib.sha256(json.dumps([CLOCK_PROOF_VERSION, [[r['audio_id'], r['sha256']] for r in [early, late]]]).encode()).hexdigest()
    changed_silent_edge = (early['audio_id'] != eligible[0]['audio_id'] or late['audio_id'] != eligible[-1]['audio_id'])
    return [early, late], revision, changed_silent_edge

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

def run_ffmpeg(arguments, timeout=180, cwd=None, input_data=None):
    result = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            input=input_data, timeout=timeout, cwd=cwd,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('repair_media_failed')

def repair(request, *, runtime_root=None, cold_archive_root=None,
           resolve_source=resolve_chzzk_source, metadata=public_vod_metadata,
           alignment_output_root=None):
    archive = request.get('archive', '')
    if not isinstance(archive, str) or not re.fullmatch(r'[a-f0-9]{32}-[1-9]\d{0,15}', archive):
        raise ValueError('repair_archive_invalid')
    if alignment_output_root is not None and (
            request.get('action') != 'align' or not Path(alignment_output_root).is_absolute()):
        raise ValueError('repair_alignment_output_invalid')


    _, database, _ = resolve_session(
        generation_root=runtime_root or os.environ['CHZZ_LIVE_RUNTIME_ROOT'],
        channel_uid=archive[:32],live_id=archive[33:],
        cold_archive_root=cold_archive_root or os.environ.get('CHZZ_COLD_ARCHIVE_ROOT') or None)
    folder = database.parent
    if alignment_output_root is not None:
        alignment_output_root = Path(alignment_output_root)
        output_root = alignment_output_root.resolve()
        recovery_root = (folder / 'vod-recovery').resolve()
        if (folder.resolve().is_relative_to(output_root)
                or output_root.is_relative_to(recovery_root)
                or recovery_root.is_relative_to(output_root)):
            raise ValueError('repair_alignment_output_invalid')
    video = str(request.get('videoNo', ''))
    start, end = request.get('fromMs'), request.get('untilMs')
    aligning = request.get('action') == 'align'
    inspecting = request.get('action') == 'anchors'
    locating = request.get('action') == 'registered_location'
    observe_terminal = aligning and request.get('observeTerminal') is True
    if (not video.isdecimal() or not (aligning or inspecting or locating) and (type(start) is not int or type(end) is not int
            or not 500 <= end - start <= 120000)):
        raise ValueError('repair_range_invalid')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        broadcast = db.execute('SELECT * FROM broadcasts').fetchall()
        if (len(broadcast) != 1 or broadcast[0]['broadcast_id'] != 'chzzk:' + archive.replace('-', ':')
                or broadcast[0]['state'] != 'ended'):
            raise ValueError('repair_broadcast_not_ended')
        broadcast = dict(broadcast[0])
        if locating:

            return {'status': 'source_checked', 'archive': archive, 'videoNo': video,
                    'broadcastId': broadcast['broadcast_id'], 'database': str(database),
                    'locationReadVersion': 'sqlite-busy-1s-v1'}
        saved = request.get('mapping')
        if (not aligning and not inspecting and isinstance(saved, dict)
                and type(saved.get('durationMs')) is int and saved['durationMs'] > 0):




            verified = {'verified': True, 'durationSec': saved['durationMs'] / 1000}
        else:
            verified = verify_live_vod(archive, broadcast['open_at_utc_ms'], broadcast['open_at_confidence'], video,
                                       metadata(video))


        rows = db.execute("SELECT * FROM audio_chunks WHERE broadcast_id=? AND status='final' "
                          "AND clock_accuracy='provider_program_date_time' ORDER BY start_at_ms", (broadcast['broadcast_id'],)).fetchall()
        eligible = [dict(row) for row in rows if row['end_at_ms'] - row['start_at_ms'] >= 4000
                    and row['end_at_ms'] < broadcast['open_at_utc_ms'] + verified['durationSec'] * 1000 - ANCHOR_MARGIN_SEC * 1000]
    mapping = request.get('mapping')


    interior = aligning or inspecting or isinstance(mapping, dict) and mapping.get('clockProofVersion') == CLOCK_PROOF_VERSION
    anchors, anchor_revision, changed_silent_edge = select_anchors(eligible, folder,
        not_before_ms=broadcast['open_at_utc_ms'] + ANCHOR_MARGIN_SEC * 1000 if interior else None)
    if inspecting:
        return {'status': 'anchors_checked', 'anchorRevision': anchor_revision,
            'clockProofVersion': CLOCK_PROOF_VERSION,
            'changedSilentEdge': changed_silent_edge,
            'anchors': [{'audioId': a['audio_id'], 'sha256': a['sha256']} for a in anchors]}
    source = resolve_source(video, target_height=360, public_only=True) if observe_terminal else resolve_source(video, target_height=360)
    if (not re.fullmatch(r'[a-f0-9]{64}', source.source_revision)
            or abs(source.duration_sec - verified['durationSec']) > 2):
        raise ValueError('repair_source_changed')
    ffmpeg, _ = _tool_paths()

    scratch_revision = (mapping['sourceRevision'] if not aligning and isinstance(mapping, dict)
                        and isinstance(mapping.get('sourceRevision'), str) else source.source_revision)
    identity = hashlib.sha256(json.dumps([CLOCK_PROOF_VERSION, video, scratch_revision, start, end,
                                          [a['sha256'] for a in anchors]]).encode()).hexdigest()


    output = (local_file(alignment_output_root, identity) if alignment_output_root is not None
              else local_file(folder, 'vod-recovery/' + identity))
    output.mkdir(parents=True, exist_ok=True)
    snapshot = None
    if urlsplit(source.media_url).path.lower().endswith('.m3u8'):
        from pipeline.downloader import _load_m3u8_media_snapshot
        snapshot = _load_m3u8_media_snapshot(source.media_url)

    def media_input(offset, duration):
        if snapshot is None:
            return ['-ss', str(offset), *_remote_input_args(ffmpeg, source.media_url), '-i', source.media_url], None
        playlist, trim = hls_window(*snapshot, offset, duration)
        arguments = _remote_input_args(ffmpeg, source.media_url)
        arguments[arguments.index('-protocol_whitelist') + 1] += ',pipe'
        return [*arguments, '-f', 'hls', '-i', 'pipe:0', '-ss', str(trim)], playlist.encode('utf-8')

    proof = []
    for anchor in anchors:
        path = local_file(folder, anchor['relative_path'])
        if sha(path) != anchor['sha256']:
            raise ValueError('repair_anchor_changed')
    if aligning:
        origins = []
        for index, anchor in enumerate(anchors):
            offset = max(0, (anchor['start_at_ms'] - broadcast['open_at_utc_ms']) / 1000 - ANCHOR_MARGIN_SEC)
            duration = (anchor['end_at_ms'] - anchor['start_at_ms']) / 1000 + 2 * ANCHOR_MARGIN_SEC
            target_wav = output / f'anchor-{index}.wav'


            arguments, playlist = media_input(offset, duration)
            run_ffmpeg([ffmpeg, '-nostdin', '-y', '-v', 'error', *arguments,
                '-t', str(duration), '-vn', '-ar', '16000', '-ac', '1', str(target_wav)], input_data=playlist)
            try:


                position, score = locate_anchor(
                    pcm(local_file(folder, anchor['relative_path']), sample_rate=16000),
                    pcm(target_wav, sample_rate=16000), sample_rate=16000)
            except ValueError as error:
                if str(error) != 'repair_clock_unverified':
                    raise
                return {'status': 'held', 'reason': str(error), 'anchorRevision': anchor_revision,
                    'clockProofVersion': CLOCK_PROOF_VERSION}
            origins.append(anchor['start_at_ms'] - round((offset + position) * 1000))
            proof.append({'audioId': anchor['audio_id'], 'sha256': anchor['sha256'], 'score': round(score, 6)})
        if abs(origins[0] - origins[1]) > 100:
            raise ValueError('repair_clock_drift')
        aligned = {'status': 'aligned', 'archive': archive, 'videoNo': video, 'anchors': proof,
            'clockProofVersion': CLOCK_PROOF_VERSION,
            'originMs': round(sum(origins) / 2), 'clockToleranceMs': abs(origins[0] - origins[1]),
            'sourceRevision': source.source_revision, 'durationMs': round(source.duration_sec * 1000)}
        if getattr(source, 'identity_revision', ''):
            aligned['sourceIdentity'] = source.identity_revision
        if observe_terminal:
            try:
                observed = read_terminal_hls_metadata(snapshot[1]) if snapshot is not None else \
                    ReportPlaybackService().terminal_metadata(video, requested_height=360, public_only=True)
                if (abs(observed['totalDurationMs'] - round(verified['durationSec'] * 1000)) > 2000
                        or abs(observed['totalDurationMs'] - aligned['durationMs']) > 2000):
                    raise ValueError('repair_terminal_duration_mismatch')
                aligned['terminalSource'] = {'status': 'terminal_verified', 'archive': archive,
                    'broadcastId': broadcast['broadcast_id'], 'videoNo': video, 'sourceRevision': source.source_revision,
                    'clockProofVersion': CLOCK_PROOF_VERSION, 'originMs': aligned['originMs'],
                    'clockToleranceMs': aligned['clockToleranceMs'], 'durationMs': aligned['durationMs'],
                    'anchors': [{'audioId': a['audio_id'], 'sha256': a['sha256']} for a in anchors],
                    'terminal': observed, 'sourceEndMs': aligned['originMs'] + (aligned['clockToleranceMs'] + 1) // 2
                    + max(aligned['durationMs'], observed['totalDurationMs'])}
            except Exception as error:
                aligned['terminalSource'] = failure_result(error)
        return aligned
    if (not isinstance(mapping, dict) or mapping.get('status') != 'aligned'
            or mapping.get('archive') != archive or mapping.get('videoNo') != video
            or not same_chzzk_source(source, mapping.get('sourceRevision'), mapping.get('sourceIdentity'))
            or mapping.get('durationMs') != round(source.duration_sec * 1000)
            or type(mapping.get('originMs')) is not int or type(mapping.get('clockToleranceMs')) is not int
            or not 0 <= mapping['clockToleranceMs'] <= 100
            or [(a.get('audioId'), a.get('sha256')) for a in mapping.get('anchors', [])] !=
               [(a['audio_id'], a['sha256']) for a in anchors]):
        raise ValueError('repair_mapping_changed')
    offset = (start - mapping['originMs']) / 1000
    if offset < 0 or (end - mapping['originMs']) / 1000 > source.duration_sec:
        raise ValueError('repair_outside_vod')
    fragments = output / 'fragments'
    fragments.mkdir(exist_ok=True)
    arguments, playlist = media_input(offset, (end-start)/1000)
    run_ffmpeg([ffmpeg, '-nostdin', '-y', '-v', 'error', *arguments, '-t', str((end-start)/1000),
                '-map', '0:v:0', '-map', '0:a:0', '-vf', 'scale=-2:360', '-c:v', 'libx264', '-preset', 'fast',
                '-threads', '2', '-g', '100', '-sc_threshold', '0', '-c:a', 'aac', '-ar', '48000', '-f', 'hls', '-hls_time', '4',
                '-hls_segment_type', 'fmp4', '-hls_playlist_type', 'vod', '-hls_segment_filename',
                '%05d.m4s', 'index.m3u8'], cwd=fragments, input_data=playlist)
    return {'status': 'verified', 'archive': archive, 'videoNo': video, 'fromMs': start, 'untilMs': end,
            'playlist': (fragments / 'index.m3u8').relative_to(folder).as_posix(),
            'anchors': mapping['anchors'], 'clockToleranceMs': mapping['clockToleranceMs'],
            'sourceRevision': mapping['sourceRevision'], 'originMs': mapping['originMs']}

def failure_result(error):


    code = getattr(error, 'code', None)
    if not isinstance(code, str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,80}', code):
        code = str(error)
    if isinstance(error, EvidenceError):
        return {'status': 'held', 'reason': 'repair_source_location_held',
                'sourceReason': code if re.fullmatch(r'[a-z][a-z0-9_]{1,80}', code)
                else 'temporal_database_unavailable'}
    return {'status': 'held',
            'reason': code if re.fullmatch(r'repair_[a-z_]+', code) else 'repair_unavailable',
            **({'sourceReason': code} if re.fullmatch(r'[a-z][a-z0-9_]{2,80}', code)
               and not code.startswith('repair_') else {}),
            'failureType': type(error).__name__}
