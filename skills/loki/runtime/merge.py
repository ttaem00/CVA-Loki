"""
merge.py — 분할된 파일을 손실 없이 하나로 합치기

ffmpeg concat demuxer 사용:
  - 오디오/비디오 재인코딩 없음 (-c copy)
  - 스트림 그대로 이어 붙임
"""

import subprocess

import os

import tempfile

from split_video import FFMPEG

def merge_files(file_paths: list, output_path: str, log_func=print) -> str:
    """
    file_paths 순서대로 손실 없이 output_path 하나로 합친다.

    file_paths: 정렬된 파일 경로 목록
    output_path: 출력 파일 경로 (이미 존재하면 덮어씀)
    log_func: 로그 콜백
    반환: output_path
    """
    if not file_paths:
        raise ValueError("합칠 파일이 없습니다.")


    if len(file_paths) == 1:
        import shutil
        log_func(f"  파일 1개 — 복사: {os.path.basename(output_path)}")
        shutil.copy2(file_paths[0], output_path)
        return output_path

    log_func(f"  {len(file_paths)}개 파일 합치는 중...")
    for i, p in enumerate(file_paths, 1):
        log_func(f"    {i:02d}. {os.path.basename(p)}")


    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for fp in file_paths:

                fp_norm = fp.replace("\\", "/").replace("'", "\\'")
                f.write(f"file '{fp_norm}'\n")

        cmd = [
            FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat 실패 (returncode={result.returncode}):\n"
                + result.stderr[-800:]
            )

    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass

    size_mb = os.path.getsize(output_path) / (1024 ** 2)
    log_func(f"  합치기 완료: {os.path.basename(output_path)} ({size_mb:.1f} MB)")
    return output_path
