"""파이프라인 공통 데이터 클래스"""

from dataclasses import dataclass, field

from typing import Optional

@dataclass
class VODInfo:
    video_no: str
    title: str
    channel_id: str
    channel_name: str
    duration: int
    publish_date: str
    thumbnail_url: str = ""
    category: str = ""
    streamer_id: str = ""
    source_platform: str = "chzzk"
    source_id: str = ""
    source_url: str = ""



    broadcast_start_at: str = ""
    replay_publish_at: str = ""

@dataclass
class UserClip:
    '사용자가 만든 Chzzk 클립 1개의 정규화된 표현.'
    clip_uid: str
    title: str
    thumbnail_url: str = ""
    category: str = ""
    duration: int = 0
    created_date: str = ""
    read_count: int = 0
    owner_channel_id: str = ""
    rec_id: str = ""

    video_no: Optional[str] = None
    offset_sec: Optional[int] = None
    offset_status: str = "unknown"

    like_count: Optional[int] = None
    play_count: Optional[int] = None
    engagement_status: str = "pending"
    engagement_fetched_at: Optional[str] = None
    engagement_source: str = "clip_page_dom"




    manual_override: Optional[str] = None





    creation_window_status: str = "in_window"
    source_vod_duration_sec: Optional[int] = None

    popularity_rank: Optional[int] = None
    coverage_bucket_index: Optional[int] = None
    selection_reason: str = ""

@dataclass
class UserClipsBundle:
    'VOD 1개에 대한 사용자 클립 집합 + 메타.'
    video_no: str
    channel_id: str = ""
    fetched_at: str = ""
    filter_type: str = ""
    status: str = "ok"
    total: int = 0
    with_offset: int = 0
    with_engagement: int = 0
    order_type: str = ""
    window_start_at: str = ""
    window_end_at: str = ""
    window_status: str = "unknown"
    raw_clip_count: int = 0
    window_candidate_count: int = 0
    excluded_out_of_window_count: int = 0
    excluded_invalid_date_count: int = 0
    excluded_unverified_count: int = 0
    selection_policy: str = ""
    coverage_bucket_count: int = 0
    coverage_nonempty_bucket_count: int = 0
    coverage_hole_count: int = 0
    clips: list = field(default_factory=list)
    schema_version: int = 4
    signal_version: int = 1
