"""Isolated CPU worker for sherpa-onnx speaker diarization."""

from __future__ import annotations

import argparse

import json

import wave

from pathlib import Path

import numpy as np

import sherpa_onnx

def _read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError("expected mono PCM16 WAV")
        rate = reader.getframerate()
        samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype=np.int16)
    return samples.astype(np.float32) / 32768.0, rate

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--segmentation-model", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    audio, sample_rate = _read_pcm16_mono(args.wav)
    segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
            model=str(args.segmentation_model.resolve()), window_shift_ratio=0.1
        ),
        num_threads=1,
        debug=False,
        provider="cpu",
    )
    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(args.embedding_model.resolve()),
        num_threads=1,
        debug=False,
        provider="cpu",
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=segmentation,
        embedding=embedding,
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("invalid sherpa-onnx speaker diarization config")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    if sample_rate != diarizer.sample_rate:
        raise RuntimeError(
            f"sample rate mismatch: input={sample_rate}, model={diarizer.sample_rate}"
        )
    rows = diarizer.process(audio).sort_by_start_time()
    payload = {
        "segments": [
            {"start": float(row.start), "end": float(row.end), "speaker": int(row.speaker)}
            for row in rows
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
