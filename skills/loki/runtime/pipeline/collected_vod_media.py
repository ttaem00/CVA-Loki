"""Standalone inputs use confirmed VOD clocks; live-finalizer archives are not bundled."""

def collected_clock(srt_path, video_no, duration_sec):
    return None

def assert_collected_media_fresh(*args, **kwargs):
    from pathlib import Path
    path = Path(args[0]).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"}:
        raise ValueError("loki_confirmed_local_video_required")
    return False

def collected_media_window(*args, **kwargs):
    from pathlib import Path
    assert_collected_media_fresh(args[0])
    return Path(args[0]), args[1]["start"]
