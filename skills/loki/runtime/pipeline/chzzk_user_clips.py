''

from __future__ import annotations

from typing import Optional

from pipeline.models import UserClip, UserClipsBundle

CACHE_SCHEMA_VERSION = 4

DEFAULT_SIGNAL_VERSION = 1

def _coerce_optional_int(value) -> Optional[int]:
    """JSON round-trip 시 None / 숫자 / 숫자형 문자열 모두 처리. 실패 → None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _bundle_from_json(data: dict) -> Optional[UserClipsBundle]:
    if not isinstance(data, dict):
        return None



    try:
        schema_v = int(data.get("schema_version") or 0)
    except (TypeError, ValueError):
        return None
    if schema_v != CACHE_SCHEMA_VERSION:
        return None
    clips_raw = data.get("clips") or []
    clips: list = []
    for cd in clips_raw:
        if not isinstance(cd, dict):
            continue
        try:
            clips.append(UserClip(
                clip_uid=str(cd.get("clip_uid") or ""),
                title=str(cd.get("title") or ""),
                thumbnail_url=str(cd.get("thumbnail_url") or ""),
                category=str(cd.get("category") or ""),
                duration=int(cd.get("duration") or 0),
                created_date=str(cd.get("created_date") or ""),
                read_count=int(cd.get("read_count") or 0),
                owner_channel_id=str(cd.get("owner_channel_id") or ""),
                rec_id=str(cd.get("rec_id") or ""),
                video_no=cd.get("video_no") if cd.get("video_no") else None,
                offset_sec=_coerce_optional_int(cd.get("offset_sec")),
                offset_status=str(cd.get("offset_status") or "unknown"),
                like_count=_coerce_optional_int(cd.get("like_count")),
                play_count=_coerce_optional_int(cd.get("play_count")),
                engagement_status=str(cd.get("engagement_status") or "pending"),
                engagement_fetched_at=(
                    str(cd["engagement_fetched_at"])
                    if cd.get("engagement_fetched_at") else None
                ),
                engagement_source=str(cd.get("engagement_source") or "clip_page_dom"),
                manual_override=(
                    str(cd["manual_override"])
                    if cd.get("manual_override") else None
                ),
                creation_window_status=str(
                    cd.get("creation_window_status") or "legacy_unverified"
                ),
                source_vod_duration_sec=_coerce_optional_int(
                    cd.get("source_vod_duration_sec")
                ),
                popularity_rank=_coerce_optional_int(cd.get("popularity_rank")),
                coverage_bucket_index=_coerce_optional_int(
                    cd.get("coverage_bucket_index")
                ),
                selection_reason=str(cd.get("selection_reason") or ""),
            ))
        except (TypeError, ValueError):
            continue



    try:
        bundle_total = int(data.get("total") or len(clips))
        bundle_with_offset = int(data.get("with_offset") or 0)
        bundle_with_engagement = int(data.get("with_engagement") or 0)
        bundle_signal_version = int(data.get("signal_version") or DEFAULT_SIGNAL_VERSION)
    except (TypeError, ValueError):
        return None
    return UserClipsBundle(
        video_no=str(data.get("video_no") or ""),
        channel_id=str(data.get("channel_id") or ""),
        fetched_at=str(data.get("fetched_at") or ""),
        filter_type=str(data.get("filter_type") or ""),
        status=str(data.get("status") or "ok"),
        total=bundle_total,
        with_offset=bundle_with_offset,
        with_engagement=bundle_with_engagement,
        order_type=str(data.get("order_type") or ""),
        window_start_at=str(data.get("window_start_at") or ""),
        window_end_at=str(data.get("window_end_at") or ""),
        window_status=str(data.get("window_status") or "unknown"),
        raw_clip_count=_coerce_optional_int(data.get("raw_clip_count")) or 0,
        window_candidate_count=(
            _coerce_optional_int(data.get("window_candidate_count")) or 0
        ),
        excluded_out_of_window_count=(
            _coerce_optional_int(data.get("excluded_out_of_window_count")) or 0
        ),
        excluded_invalid_date_count=(
            _coerce_optional_int(data.get("excluded_invalid_date_count")) or 0
        ),
        excluded_unverified_count=(
            _coerce_optional_int(data.get("excluded_unverified_count")) or 0
        ),
        selection_policy=str(data.get("selection_policy") or ""),
        coverage_bucket_count=(
            _coerce_optional_int(data.get("coverage_bucket_count")) or 0
        ),
        coverage_nonempty_bucket_count=(
            _coerce_optional_int(data.get("coverage_nonempty_bucket_count")) or 0
        ),
        coverage_hole_count=(
            _coerce_optional_int(data.get("coverage_hole_count")) or 0
        ),
        clips=clips,
        schema_version=CACHE_SCHEMA_VERSION,
        signal_version=bundle_signal_version,
    )
