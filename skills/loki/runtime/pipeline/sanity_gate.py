'LLM 응답 silent truncation/format violation 차단 sanity gate.'

from __future__ import annotations

import logging

import re

from typing import Optional

from .claude_cli import STOP_REASON_FAIL, SanityGateError

logger = logging.getLogger("pipeline")

_MERGE_REQUIRED_HEADINGS = (
    re.compile(r"^##\s+.*방송\s*분석\s*리포트", re.MULTILINE),
    re.compile(r"^###\s+.*타임라인\s*상세\s*요약", re.MULTILINE),
    re.compile(r"^###\s+.*하이라이트\s*추천\s*구간", re.MULTILINE),
)

_MERGE_TIMELINE_BULLET_RE = re.compile(
    r"^\s{0,4}[-*]\s*\*{0,4}\s*(?:\[VOD\s+\d+\]\s*)?\[?\d{1,2}:\d{2}:\d{2}",
    re.MULTILINE,
)

_MERGE_FINAL_MINIMUM_VIABLE_BODY_CHARS = 1500

_SUCCESSFUL_STOP_REASONS = {"ok", "end_turn", "completed", "stop"}

def _check_stop_reason(meta: Optional[dict]) -> Optional[str]:
    """meta 의 stop_reason 이 fail trigger 인지 확인. fail 사유 메시지 또는 None."""
    if not meta:
        return None
    sr = meta.get("stop_reason")
    if sr in STOP_REASON_FAIL:
        return f"stop_reason={sr}"


    subtype = meta.get("subtype")
    if subtype is not None and subtype != "success":
        return f"subtype={subtype}"
    is_error = meta.get("is_error")
    if is_error:
        return f"is_error={is_error}"
    return None

def _body_chars(text: str, meta: Optional[dict]) -> int:
    """meta 에 body_chars 가 있으면 사용, 없으면 text 길이로 fallback."""
    if meta is not None:
        bc = meta.get("body_chars")
        if isinstance(bc, int):
            return bc
    return len(text or "")

_HMS_RANGE_RE = re.compile(
    r"\[(\d{1,2}):(\d{2}):(\d{2})(?:~(\d{1,2}):(\d{2}):(\d{2}))?\]"
)

_TIMELINE_SECTION_RE = re.compile(
    r"###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n(.+?)(?=\n###\s|\Z)",
    re.S,
)

_HIGHLIGHT_SECTION_RE = re.compile(
    r"###\s*(?:🎬\s*)?[^\n]*하이라이트[^\n]*\n(.+?)(?=\n###\s|\Z)",
    re.S,
)

_TIMELINE_ENTRY_START_RE = re.compile(
    r"^\s{0,4}[-*]\s*\*{0,4}\s*(?:\[VOD\s+\d+\]\s*)?\[?\d{1,2}:\d{2}:\d{2}",
)

_TIMELINE_APPENDIX_LINE_RE = re.compile(
    r"^\s{0,3}(?:#{4,6}\s+\S|\|.*\|\s*$)",
)

_DEFAULT_MAX_TIMELINE_TAIL_GAP_SEC = 7200

_TAIL_COVERAGE_MIN_DURATION_SEC = 7200

def _hms_to_sec(h: str, m: str, s: str) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)

def _extract_ranges_in_section(body: str, section_re: re.Pattern) -> list[tuple[int, int]]:
    '`[HH:MM:SS]` (point=start==end) 또는 `[HH:MM:SS~HH:MM:SS]` (range) 양쪽'
    sec = section_re.search(body)
    if not sec:
        return []
    out: list[tuple[int, int]] = []
    for m in _HMS_RANGE_RE.finditer(sec.group(1)):
        start = _hms_to_sec(m.group(1), m.group(2), m.group(3))
        if m.group(4):
            end = _hms_to_sec(m.group(4), m.group(5), m.group(6))
        else:
            end = start
        if end < start:
            start, end = end, start
        out.append((start, end))
    return out

def _extract_timeline_ranges(body: str) -> list[tuple[int, int]]:
    return _extract_ranges_in_section(body, _TIMELINE_SECTION_RE)

def _extract_highlight_ranges(body: str) -> list[tuple[int, int]]:
    return _extract_ranges_in_section(body, _HIGHLIGHT_SECTION_RE)

def _find_timeline_format_appendix(body: str) -> str:
    """Detect non-report appendix text accidentally emitted inside timeline."""
    sec = _TIMELINE_SECTION_RE.search(body or "")
    if not sec:
        return ""
    seen_entry = False
    for raw_line in sec.group(1).splitlines():
        if _TIMELINE_ENTRY_START_RE.match(raw_line):
            seen_entry = True
            continue
        if seen_entry and _TIMELINE_APPENDIX_LINE_RE.match(raw_line):
            return raw_line.strip()[:100]
    return ""

def find_missing_promoted_anchors(
    text: str,
    promoted_anchors: list,
    *,
    tolerance_sec: int = 60,
) -> list[dict]:
    'must-include anchor 의 timeline / highlight 양쪽 커버리지 검사.\n\n    각 anchor 는 dict 로 `offset_sec` (int) 와 추가 메타 (clip_uid/title/like/play 등)\n    를 가진다. 본 함수는 양쪽 검사:\n      - timeline 섹션에 `[HH:MM:SS]` 가 anchor offset ±tolerance_sec 안에 존재하는가\n      - highlight 섹션의 어떤 range 라도 [start-tolerance, end+tolerance] 가 anchor 를 덮는가\n\n    AND-coverage: 양쪽 모두 커버돼야 "이미 LLM 이 다뤘다" 로 간주. 한쪽이라도\n    빠지면 missing 으로 보고하되 `_missing_in_timeline` / `_missing_in_highlight`\n    flag 로 어느 쪽이 빈지 표시 — caller 가 빈 surface 에만 inject 해 중복 방지.\n    '
    if not promoted_anchors:
        return []
    body = text or ""
    timeline_ranges = _extract_timeline_ranges(body)
    highlight_ranges = _extract_highlight_ranges(body)

    missing: list[dict] = []
    for anchor in promoted_anchors:
        try:
            offset = int(anchor.get("offset_sec"))
        except (TypeError, ValueError, AttributeError):
            continue
        in_timeline = any(
            (start - tolerance_sec) <= offset <= (end + tolerance_sec)
            for start, end in timeline_ranges
        )
        in_highlight = any(
            (start - tolerance_sec) <= offset <= (end + tolerance_sec)
            for start, end in highlight_ranges
        )
        if in_timeline and in_highlight:
            continue
        missing.append({
            **anchor,
            "_missing_in_timeline": not in_timeline,
            "_missing_in_highlight": not in_highlight,
        })
    return missing

def adaptive_min_body_chars(duration_sec: int) -> int:
    """VOD 길이에 따른 merge 최소 글자수 계산.

    30분 미만 짧은 VOD는 입력(자막/채팅) 자체가 적어 LLM이 3000자 이상
    생성할 수 없다. duration 비례로 floor를 조정한다.
    - ≤15분: 1500자
    - 15~60분: 1500 + (duration-900) * (1500/2700) ≈ 선형 보간
    - ≥60분: 3000자 (기존 기본값)
    """
    if duration_sec <= 0:
        return 3000
    if duration_sec >= 3600:
        return 3000
    if duration_sec <= 900:
        return 1500
    return int(1500 + (duration_sec - 900) * (1500 / 2700))

def validate_merge_response(
    text: str,
    meta: Optional[dict] = None,
    *,
    min_body_chars: int = 3000,
    min_timeline_items: int = 5,
    require_full_headings: bool = True,
    promoted_anchors: Optional[list] = None,
    expected_duration_sec: Optional[int] = None,
    max_timeline_tail_gap_sec: int = _DEFAULT_MAX_TIMELINE_TAIL_GAP_SEC,
) -> Optional[dict]:
    '통합/병합 단계 응답 검증. fail 시 SanityGateError 발생.\n\n    임계값:\n    - body_chars >= min_body_chars (final report 평소 5K-10K)\n    - stop_reason / subtype 정상\n    - require_full_headings=True 시 3 required headings 모두 존재 (final merge)\n    - require_full_headings=False 시 timeline bullet 만 검사 (round1 mid-merge)\n    - timeline bullet >= min_timeline_items\n    - expected_duration_sec 가 긴 VOD이면 본문 타임라인 첫/마지막 항목이\n      VOD 앞/끝을 과도하게 비우지 않아야 함\n\n    `promoted_anchors` 가 주어지면 must-include 검사 결과를\n    `{"missing_promoted_anchors": [...]}` 로 반환한다 (). raise 하지 않는다 —\n    누락은 caller (parser injection) 가 결정론으로 inject. promoted_anchors=None 은\n    기존 동작과 100% 호환 (반환 None).\n    '
    body_chars = _body_chars(text, meta)

    sr_fail = _check_stop_reason(meta)
    if sr_fail:
        raise SanityGateError(
            f"sanity gate FAIL (merge): {sr_fail} (body_chars={body_chars})"
        )

    stop_reason = meta.get("stop_reason") if meta else None
    minimum_viable_floor = min(
        min_body_chars,
        _MERGE_FINAL_MINIMUM_VIABLE_BODY_CHARS,
    )
    structured_final_below_target = (
        require_full_headings
        and stop_reason in _SUCCESSFUL_STOP_REASONS
        and minimum_viable_floor <= body_chars < min_body_chars
    )
    if body_chars < min_body_chars and not structured_final_below_target:
        raise SanityGateError(
            f"sanity gate FAIL (merge): body_chars={body_chars} < {min_body_chars} "
            f"(stop_reason={meta and meta.get('stop_reason')})"
        )

    body = text or ""
    if require_full_headings:
        missing_headings = [
            rx.pattern for rx in _MERGE_REQUIRED_HEADINGS if not rx.search(body)
        ]
        if missing_headings:
            raise SanityGateError(
                f"sanity gate FAIL (merge): 필수 헤딩 누락 "
                f"{len(missing_headings)}/{len(_MERGE_REQUIRED_HEADINGS)} "
                f"({missing_headings[:2]}...) (body_chars={body_chars})"
            )

    timeline_count = len(_MERGE_TIMELINE_BULLET_RE.findall(body))
    if timeline_count < min_timeline_items:
        raise SanityGateError(
            f"sanity gate FAIL (merge): timeline bullet {timeline_count} < {min_timeline_items} "
            f"(body_chars={body_chars})"
        )

    appendix = _find_timeline_format_appendix(body)
    if require_full_headings and appendix:
        raise SanityGateError(
            "sanity gate FAIL (merge): invalid timeline appendix after timeline entries "
            f"({appendix})"
        )

    if (
        require_full_headings
        and expected_duration_sec is not None
        and expected_duration_sec >= _TAIL_COVERAGE_MIN_DURATION_SEC
    ):
        timeline_ranges = _extract_timeline_ranges(body)
        earliest_timeline_sec = min((start for start, _ in timeline_ranges), default=None)
        latest_timeline_sec = max((end for _, end in timeline_ranges), default=None)
        if earliest_timeline_sec is None or latest_timeline_sec is None:
            raise SanityGateError(
                "sanity gate FAIL (merge): timeline coverage unavailable "
                f"(expected_duration_sec={expected_duration_sec})"
            )
        allowed_gap = min(
            max_timeline_tail_gap_sec,
            max(1800, int(expected_duration_sec * 0.25)),
        )
        head_gap = earliest_timeline_sec
        if head_gap > allowed_gap:
            raise SanityGateError(
                "sanity gate FAIL (merge): timeline head coverage gap "
                f"{head_gap}s > {allowed_gap}s "
                f"(earliest_timeline_sec={earliest_timeline_sec}, "
                f"expected_duration_sec={expected_duration_sec})"
            )
        tail_gap = int(expected_duration_sec) - latest_timeline_sec
        if tail_gap > allowed_gap:
            raise SanityGateError(
                "sanity gate FAIL (merge): timeline tail coverage gap "
                f"{tail_gap}s > {allowed_gap}s "
                f"(latest_timeline_sec={latest_timeline_sec}, "
                f"expected_duration_sec={expected_duration_sec})"
            )

    hr_count = len(re.findall(r"\n---\n", body))
    if hr_count:
        logger.warning(
            'sanity gate WARN (merge): stray HR (---) found %d times — post-processor will strip', hr_count,
        )

    dup_title = len(re.findall(r"^##\s+.*방송\s*분석\s*리포트", body, flags=re.MULTILINE))
    if dup_title > 1:
        logger.warning(
            'sanity gate WARN (merge): duplicate report title heading found %d times',
            dup_title,
        )

    if promoted_anchors:
        missing = find_missing_promoted_anchors(body, promoted_anchors)
        return {"missing_promoted_anchors": missing}
    if structured_final_below_target:
        logger.warning(
            "sanity gate WARN (merge): final body below heuristic target accepted "
            "after structure/coverage validation "
            "(body_chars=%s target=%s minimum_viable=%s)",
            body_chars,
            min_body_chars,
            minimum_viable_floor,
        )
    return None
