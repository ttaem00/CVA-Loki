"""Consume only the existing support snapshots copied into this author's job."""

def _prepare_current_conversation_sources(*, vod, spec, source_dir, root, work, selected_pipeline):
    """Use the installed feature's local inputs without acquiring missing sources."""
    import logging
    import os
    from pathlib import Path
    import shutil
    from loki import configuration
    from ..chunker import chunk_srt
    from ..editorial_source_timing import prepare_source_timing
    from ..evidence_world.contracts import native_path
    from ..chat_collector import has_complete_chat_cache, load_chat_log_json
    from ..live_vod_chat import is_bound_chat, descriptor_status

    _, configured_work, policy = configuration(Path(os.environ["LOKI_CONFIG_PATH"]))
    if native_path(configured_work).resolve() != work:
        raise ValueError("loki_current_source_workspace_mismatch")
    core = policy["features"] == "core"
    video = str(vod.video_no)
    prepared_media_path = spec.get("media_path")
    if core:
        if prepared_media_path:
            raise ValueError("loki_media_feature_not_installed")
    else:
        if not prepared_media_path or not native_path(prepared_media_path).is_file():
            raise ValueError("source_unavailable_before_summary:media")
        if not native_path(prepared_media_path).resolve().is_relative_to(source_dir):
            raise ValueError("current_conversation_media_outside_source")
        from ..downloader import _is_valid_media_file
        if not _is_valid_media_file(str(prepared_media_path), expected_duration_sec=vod.duration):
            raise ValueError("source_unavailable_before_summary:media")
    chunks = chunk_srt(str(native_path(spec["srt_path"])))
    source = prepare_source_timing(video_no=video, duration_sec=vod.duration,
        work_dir=source_dir, chunks=chunks, srt_path=spec["srt_path"])
    if source["asset"].get("clock_authority", {}).get("kind") == "live":
        raise ValueError("current_conversation_requires_confirmed_vod_clock")
    chat_path = source_dir / f"{video}_chat.log.json"
    if chat_path.is_file() and is_bound_chat(chat_path):
        descriptor_status(chat_path)
    if not core and (not has_complete_chat_cache(video, str(source_dir))
            or load_chat_log_json(video, str(source_dir), expected_duration_sec=vod.duration) is None):
        raise ValueError("source_unavailable_before_summary:chat")
    chat_source_path = chat_path if chat_path.is_file() else None
    comment_dir = root / "comment-source"
    clip_dir = root / "clip-source"
    for directory, filename in ((comment_dir, "timeline_comments.json"), (clip_dir, "user_clips.json")):
        directory.mkdir(parents=True, exist_ok=True)
        previous = source_dir / filename
        if previous.is_file() and not (directory / filename).exists():
            shutil.copyfile(previous, directory / filename)
    context = {}
    _load_timeline_comments_for_summary(vod=vod, cfg={}, cookies={}, work_dir=str(comment_dir),
        logger=logging.getLogger(__name__), context_sink=context)
    try:
        clips = _load_user_clips_for_summary(vod=vod, cfg={}, cookies=None, work_dir=str(clip_dir),
            output_dir=str(root), logger=logging.getLogger(__name__))
    except (OSError, ValueError) as error:
        raise ValueError("source_unavailable_before_summary:clips") from error
    if not core:
        missing = _summary_support_missing(clips, context)
        if missing:
            raise ValueError("source_unavailable_before_summary:" + ",".join(missing))
    return chunks, source, prepared_media_path, chat_source_path, comment_dir, clip_dir / "user_clips.json"

def _load_timeline_comments_for_summary(*, vod, cfg, cookies, work_dir, logger,
                                        slice_mode=False, context_sink=None):
    from ..timeline_comment_collector import (
        build_private_comment_semantic_context, build_private_timeline_anchors_for_bundle,
        default_source_url, format_timeline_comment_bundle_for_prompt, load_timeline_comment_bundle,
    )
    from ..timeline_comment_anchors import build_timeline_comment_context
    expected = default_source_url(str(vod.video_no), cfg.get("timeline_comments_source_url"))
    try:
        bundle = load_timeline_comment_bundle(str(vod.video_no), work_dir,
            cache_ttl_hours=0, expected_source_url=expected)
    except (OSError, ValueError, TypeError):
        bundle = None
    usable = bundle is not None and bundle.status in {"ok", "no_comments", "no_timeline_comments"}
    context = build_timeline_comment_context(
        build_private_timeline_anchors_for_bundle(bundle) if usable else [],
        source_status=bundle.status if bundle is not None else "unavailable",
        selected_comment_count=bundle.selected_count if usable else 0,
        source_kind=f"{bundle.platform}_automatic" if bundle is not None else "unknown",
        duration_sec=vod.duration)
    if usable:
        semantic = build_private_comment_semantic_context(bundle)
        if semantic:
            context["private_comment_semantic_context"] = semantic
    if isinstance(context_sink, dict):
        context_sink.clear()
        context_sink.update(context)
    return (format_timeline_comment_bundle_for_prompt(bundle) or None) if usable else None

def _summary_support_missing(user_clips, timeline_context):
    missing = []
    if user_clips is None or getattr(user_clips, "status", "") not in {"ok", "no_clips"}:
        missing.append("clips")
    if timeline_context.get("source_status") not in {"ok", "no_comments", "no_timeline_comments"}:
        missing.append("comments")
    return missing

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
