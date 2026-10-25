"""Consume only the existing clip snapshot copied into this author's job."""

def _load_user_clips_for_summary(*, vod, cfg, cookies, work_dir, output_dir, logger, **kwargs):
    from pathlib import Path
    import json
    from ..chzzk_user_clips import _bundle_from_json
    snapshot = Path(work_dir) / "user_clips.json"
    if snapshot.is_file():
        bundle = _bundle_from_json(json.loads(snapshot.read_text(encoding="utf-8-sig")))
        if str(bundle.video_no) != str(vod.video_no):
            raise ValueError("loki_clip_snapshot_wrong_vod")
        return bundle
    return None
