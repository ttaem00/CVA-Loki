"""Pinned Sherpa F2 assets in the explicit Loki workspace; downloads belong only to source setup."""

from __future__ import annotations

import hashlib

import os

import tarfile

import tempfile

from pathlib import Path

from urllib.request import Request

from pipeline.runtime_root import runtime_data_path

SEGMENTATION_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2'

SEGMENTATION_ARCHIVE_SHA256 = '24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488'

SEGMENTATION_ARCHIVE_BYTES = 6958444

SEGMENTATION_MODEL_SHA256 = '220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079'

EMBEDDING_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx'

EMBEDDING_MODEL_SHA256 = 'ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e'

EMBEDDING_MODEL_BYTES = 40257283

class SpeakerTurnRuntimeUnavailable(RuntimeError):
    """The optional managed speaker-turn runtime could not be prepared."""

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def _verified(path: Path, expected: str) -> bool:
    return path.is_file() and _sha256(path) == expected

def _download(url, destination, expected_bytes):
    from urllib.request import build_opener, ProxyHandler
    from urllib.parse import urlsplit
    if urlsplit(url).scheme != 'https' or not url.startswith('https://github.com/k2-fsa/sherpa-onnx/releases/download/'):
        raise SpeakerTurnRuntimeUnavailable('unapproved speaker-turn asset URL')
    request = Request(url, headers={'User-Agent': 'Loki-speech-setup/1'})
    with build_opener(ProxyHandler({})).open(request, timeout=120) as response, destination.open('wb') as stream:
        written = 0
        while (block := response.read(1048576)):
            written += len(block)
            if written > expected_bytes:
                raise SpeakerTurnRuntimeUnavailable('speaker-turn asset exceeds pinned size')
            stream.write(block)
    if written != expected_bytes:
        raise SpeakerTurnRuntimeUnavailable('speaker-turn asset is incomplete')

def _extract_segmentation_model(archive: Path, destination: Path) -> None:
    wanted = 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx'
    with tarfile.open(archive, 'r:bz2') as bundle:
        member = next((row for row in bundle.getmembers() if row.name == wanted), None)
        if member is None or not member.isfile():
            raise SpeakerTurnRuntimeUnavailable('segmentation archive has no model.onnx')
        source = bundle.extractfile(member)
        if source is None:
            raise SpeakerTurnRuntimeUnavailable('segmentation model cannot be extracted')
        with destination.open('wb') as stream:
            while (block := source.read(1024 * 1024)):
                stream.write(block)

def resolve_sherpa_model_paths():
    root = runtime_data_path('.models', 'speaker-turn', 'sherpa-v1')
    current = runtime_data_path()
    for part in root.relative_to(current).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise SpeakerTurnRuntimeUnavailable('speaker-turn asset path cannot be a link')
    paths = (root / 'segmentation.onnx', root / 'embedding.onnx')
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise SpeakerTurnRuntimeUnavailable('speaker-turn asset cannot be a link')
    return paths

def ensure_sherpa_models(*, allow_download: bool=True) -> tuple[Path, Path]:
    """Return verified shared assets, downloading each official asset at most once."""
    segmentation, embedding = resolve_sherpa_model_paths()
    if _verified(segmentation, SEGMENTATION_MODEL_SHA256) and _verified(embedding, EMBEDDING_MODEL_SHA256):
        return (segmentation, embedding)
    if not allow_download:
        raise SpeakerTurnRuntimeUnavailable('verified sherpa speaker-turn models are missing')
    if allow_download and os.environ.get('LOKI_SOURCE_PREPARATION') != str(runtime_data_path().resolve()):
        raise SpeakerTurnRuntimeUnavailable('Run speech_setup before offline transcription')
    root = segmentation.parent
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='speaker-turn-', dir=root) as temp_name:
            temp = Path(temp_name)
            archive = temp / 'segmentation.tar.bz2'
            extracted = temp / 'segmentation.onnx'
            embedding_download = temp / 'embedding.onnx'
            if not _verified(segmentation, SEGMENTATION_MODEL_SHA256):
                _download(SEGMENTATION_URL, archive, SEGMENTATION_ARCHIVE_BYTES)
                if _sha256(archive) != SEGMENTATION_ARCHIVE_SHA256:
                    raise SpeakerTurnRuntimeUnavailable('segmentation archive checksum mismatch')
                _extract_segmentation_model(archive, extracted)
                if _sha256(extracted) != SEGMENTATION_MODEL_SHA256:
                    raise SpeakerTurnRuntimeUnavailable('segmentation model checksum mismatch')
                os.replace(extracted, segmentation)
            if not _verified(embedding, EMBEDDING_MODEL_SHA256):
                _download(EMBEDDING_URL, embedding_download, EMBEDDING_MODEL_BYTES)
                if _sha256(embedding_download) != EMBEDDING_MODEL_SHA256:
                    raise SpeakerTurnRuntimeUnavailable('embedding model checksum mismatch')
                os.replace(embedding_download, embedding)
    except (OSError, tarfile.TarError) as exc:
        raise SpeakerTurnRuntimeUnavailable(f'speaker-turn model setup failed: {exc}') from exc
    if not _verified(segmentation, SEGMENTATION_MODEL_SHA256) or not _verified(embedding, EMBEDDING_MODEL_SHA256):
        raise SpeakerTurnRuntimeUnavailable('speaker-turn model verification failed')
    return (segmentation, embedding)
