"""Pure Markdown summary parsing helpers.

This module owns summary-section parsing only; the legacy orchestration module
re-exports these names temporarily so existing report/admin consumers remain unchanged.
"""

from __future__ import annotations

import re

from typing import Any

_INJECTED_LABEL_RE = re.compile(r"\[INJECTED(?:\s*·\s*[^\]]*)?\]")

_SRT_XVAL_THRESHOLD_SEC = 180

_SRT_ACTUAL_TIME_RE = re.compile(r"\[실제 시점:\s*(\d{2}:\d{2}:\d{2})\]")

_TIMELINE_APPENDIX_LINE_RE = re.compile(
    r"^\s{0,3}(?:#{4,6}\s+\S|\|.*\|\s*$)",
)

def _strip_injected_label(s: str) -> tuple[str, bool, str]:
    """`[INJECTED · ...]` 라벨 추출. (cleaned_text, is_injected, label_text)."""
    if not s:
        return s, False, ""
    m = _INJECTED_LABEL_RE.search(s)
    if not m:
        return s, False, ""
    cleaned = (s[:m.start()] + s[m.end():]).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned, True, m.group(0)

def _clean_timeline_evidence_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return ""
    if compact.strip("\"'“”‘’` ") == "":
        return ""
    if compact.lower() in {"n/a", "na", "none", "null"}:
        return ""
    if compact in {"-", "없음", "근거 없음", "(없음)"}:
        return ""
    return compact

def _parse_timeline_entry_header(line: str, vod_tag_pattern: str) -> tuple[str, str, str] | None:
    time_pattern = (
        vod_tag_pattern
        + r"\[?\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?\]?"
    )
    match = re.match(
        r"\s{0,4}[-*]\s*\*{0,4}\s*("
        + time_pattern
        + r")(?:\([^)]*\))?\s*(.*?)\s*$",
        line,
    )
    if not match:
        return None
    tc_raw = match.group(1)
    title = (match.group(2) or "").strip()
    mood_raw = ""
    mood_match = re.search(r"\(\s*(?:분위기\s*[:：]\s*)?([^()]*)\)\s*$", title)
    if mood_match:
        candidate = mood_match.group(1).strip()
        if "분위기" in mood_match.group(0) or any(
            token in candidate for token in ("🔥", "😂", "🎮", "💬", "💖", "😬", "🌶", "💤")
        ):
            mood_raw = candidate
            title = title[: mood_match.start()].strip()
    title = title.strip(" *`").strip()
    return tc_raw, title, mood_raw

def _parse_highlight_entry_header(line: str, vod_tag_pattern: str) -> tuple[str, str] | None:
    time_pattern = (
        vod_tag_pattern
        + r"\[?\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?\]?"
    )
    match = re.match(
        r"\s*\d+\.\s*\*{0,4}\s*("
        + time_pattern
        + r")(?:\([^)]*\))?\s*\*{0,4}\s*(.*?)\s*$",
        line,
    )
    if not match:
        return None
    return match.group(1), (match.group(2) or "").strip(" *").strip()

def _hms_to_sec(hms: str) -> int:
    parts = hms.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

def _first_timecode_sec(tc_text: str) -> int | None:
    text = str(tc_text or "")
    text = re.sub(r"^\[VOD\s+\d+\]\s*", "", text).strip("[] ")
    if "~" in text:
        text = text.split("~", 1)[0].strip()
    match = re.search(r"\d{2}:\d{2}:\d{2}", text)
    if not match:
        return None
    try:
        return _hms_to_sec(match.group(0))
    except (TypeError, ValueError):
        return None

def _timestamp_confidence_from_texts(
    claimed_tc: str,
    *texts: str,
) -> dict[str, Any]:
    """Return manager-visible timestamp confidence from SRT correction markers.

    The marker can be inserted in a title, summary, evidence, or reason line.
    Treat it as a visible quality state rather than leaving it as hidden prose.
    """
    blob = "\n".join(str(text or "") for text in texts if text is not None)
    match = _SRT_ACTUAL_TIME_RE.search(blob)
    claimed_sec = _first_timecode_sec(claimed_tc)
    if not match:
        return {"status": "verified"}

    actual_tc = match.group(1)
    actual_sec = _first_timecode_sec(actual_tc)
    gap_sec = abs(actual_sec - claimed_sec) if actual_sec is not None and claimed_sec is not None else None
    if gap_sec is None or gap_sec >= _SRT_XVAL_THRESHOLD_SEC:
        return {
            "status": "mismatch",
            "claimed_tc": str(claimed_tc or ""),
            "actual_tc": actual_tc,
            "gap_sec": gap_sec,
            "manager_label": "시각 확인 필요",
        }
    return {
        "status": "verified_with_correction",
        "claimed_tc": str(claimed_tc or ""),
        "actual_tc": actual_tc,
        "gap_sec": gap_sec,
        "manager_label": "시각 보정됨",
    }

def _strip_timestamp_confidence_markers(text: str) -> str:
    cleaned = _SRT_ACTUAL_TIME_RE.sub("", str(text or ""))
    cleaned = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", cleaned)
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.M)
    return cleaned.strip()

def _is_timeline_format_appendix_line(line: str) -> bool:
    return bool(_TIMELINE_APPENDIX_LINE_RE.match(line or ""))

def _parse_bundle_timeline_vod_tag(tc_text: str) -> tuple[int, str]:
    """타임라인의 VOD 태그에서 VOD 인덱스(0-based)와 순수 tc 추출.

    Returns (vod_idx, clean_tc). 태그 없으면 (0, tc_text).
    """
    text = str(tc_text or "").strip()
    m = re.match(r"\[VOD\s+(\d+)\]\s*(.*)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) - 1, m.group(2).strip().strip("[]")
    m = re.match(
        r"\[?VOD\s+(\d+)\s*[\]·:|\-–—]?\s*"
        r"(\d{1,2}:\d{2}:\d{2}(?:\s*~\s*\d{1,2}:\d{2}:\d{2})?)\]?",
        text,
        re.IGNORECASE,
    )
    if m:
        clean_tc = re.sub(r"\s*~\s*", "~", m.group(2).strip())
        return int(m.group(1)) - 1, clean_tc
    return 0, text.strip("[] ")

def _parse_summary_sections(md: str) -> dict:
    'Claude 요약 Markdown을 섹션별로 구조화.\n\n    반환: {\n      \'title\': str, \'hashtags\': [str], \'pull_quote\': str,\n      \'timeline\': [{\'tc\': \'00:26:06\', \'title\': \'...\', \'mood\': \'hot|funny|game|chat|warm|tense|spicy|chill\',\n                    \'summary\': \'...\', \'evidence\': \'...\', \'is_injected\': bool,\n                    \'injected_label\': str}],\n      \'highlights\': [{\'tc_range\': \'...\', \'title\': \'...\', \'reason\': \'...\',\n                      \'is_injected\': bool, \'injected_label\': str}],\n      \'editor_notes\': [paragraphs],\n      \'editor_brief_points\': [{\'tc_range\': \'00:12:34~00:13:45\', \'title\': \'...\',\n                               \'cut_reason\': \'...\', \'context_warning\': \'...\'}],\n      \'injection_warnings\': [str],\n      \'raw_fallback\': full md (파싱 실패 시 원본 렌더링용)\n    }\n\n    `editor_brief_points` 는 chunk 통합 프롬프트에 추가된\n    `### 🎬 편집 포인트` 섹션을 별도 dict 키로 분리해 보관한다. 시청자 HTML/MD\n    렌더러는 이 키를 절대 노출하지 않으며, `_strip_editor_brief_section` 이\n    summary 본문에서 섹션 자체를 제거한 뒤 viewer surface 로 흐르도록 한다.\n    편집자 브리프 페이지(`/editor` 라우트)는 본 키만 소비한다.\n\n    `is_injected` 는  의 결정론 inject anchor 가 LLM 본문이 아니라\n    parser-side 후처리로 들어왔음을 HTML/Cafe 렌더러가 인지하도록 표기. label\n    text 는 사용자 가시 badge 용 (예: "시청자 클립 19/2471").\n    '
    import re

    out = {
        "title": "", "hashtags": [], "pull_quote": "",
        "timeline": [], "highlights": [], "editor_notes": [],
        "chzzk_comment_timeline": [],
        "editor_brief_points": [],
        "injection_warnings": [],
        "raw_fallback": md,
    }



    warn_match = re.search(r"<!--\s*T4_INJECT_WARNINGS:\s*(.+?)\s*-->", md, flags=re.S)
    if warn_match:
        out["injection_warnings"] = [
            ln.strip() for ln in warn_match.group(1).splitlines() if ln.strip()
        ]


    m = re.search(r"^##\s+.*?방송 분석 리포트\s*:\s*(.+)$", md, flags=re.M)
    if m:
        out["title"] = m.group(1).strip()


    m = re.search(r"핵심 요약[:\*\s]*(.+)", md)
    if m:
        tags = re.findall(r"[`#]+([\w가-힣]+)", m.group(1))
        seen = set()
        out["hashtags"] = [t for t in tags if not (t in seen or seen.add(t))]


    m = re.search(r'^\s*>\s*"?(.+?)"?\s*$', md, flags=re.M)
    if m:
        out["pull_quote"] = m.group(1).strip().strip('"')



    tl_match = re.search(
        r"###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n(.+?)(?=\n###\s|\Z)",
        md, flags=re.S
    )
    if tl_match:
        tl_body = tl_match.group(1)






        _VOD_TAG_OPT = r"(?:\[VOD\s+\d+\]\s*|\[VOD\s+\d+\s+)?"
        entries = re.split(
            r"\n(?=\s{0,4}[-*]\s*\*{0,4}\s*" + _VOD_TAG_OPT + r"\[?\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?\]?(?:\([^)]*\))?)",
            tl_body,
        )
        for ent in entries:

            _ast_norm = r"^(\s{0,4}[-*]\s*)\*{1,4}(\s*" + _VOD_TAG_OPT + r"\[?\d{2}:\d{2}:\d{2})"
            normalized = re.sub(_ast_norm, r"\1**\2", ent)
            if normalized != ent:
                normalized = re.sub(r"\*{1,4}\s*(\(분위기:|\Z|\n)", r"**\1", normalized, count=1)
            first_line = normalized.strip().splitlines()[0] if normalized.strip() else ""
            parsed_header = _parse_timeline_entry_header(first_line, _VOD_TAG_OPT)
            if not parsed_header:
                continue
            tc_raw, title, mood_raw = parsed_header
            _, tc = _parse_bundle_timeline_vod_tag(tc_raw)


            mood = "chill"
            if "\U0001F336" in mood_raw:
                mood = "spicy"
            elif "😬" in mood_raw:
                mood = "tense"
            elif "🎮" in mood_raw:
                mood = "game"
            elif "😂" in mood_raw:
                mood = "funny"
            elif "💖" in mood_raw:
                mood = "warm"
            elif "🔥🔥" in mood_raw:
                mood = "hot"
            elif "🔥" in mood_raw:
                mood = "hot"
            elif "💬" in mood_raw:
                mood = "chat"
            elif "💤" in mood_raw:
                mood = "chill"

            summary_lines: list[str] = []
            evidence_lines: list[str] = []
            for raw_line in ent.splitlines()[1:]:
                if _is_timeline_format_appendix_line(raw_line):
                    break
                line = raw_line.strip()
                if not line:
                    continue
                line = re.sub(r"^[-*]\s*", "", line).strip()
                if not line:
                    continue
                summary_match = re.match(r"내용\s*[:：]\s*(.+)", line)
                evidence_match = re.match(r"근거\s*[:：]\s*(.+)", line)
                if summary_match:
                    summary_lines.append(summary_match.group(1).strip())
                elif evidence_match:
                    evidence_lines.append(evidence_match.group(1).strip())
                else:
                    summary_lines.append(line)
            summary_text = " ".join(s.strip() for s in summary_lines) or ""
            evidence_text = _clean_timeline_evidence_text(" ".join(e.strip() for e in evidence_lines))

            title = title.rstrip(" *`").strip()
            title, is_injected, injected_label = _strip_injected_label(title)

            out["timeline"].append({
                "tc": tc,
                "tc_raw": tc_raw,
                "title": title,
                "mood": mood,
                "mood_raw": mood_raw,
                "summary": summary_text,
                "evidence": evidence_text,
                "timestamp_confidence": _timestamp_confidence_from_texts(
                    tc,
                    title,
                    summary_text,
                    evidence_text,
                ),
                "is_injected": is_injected,
                "injected_label": injected_label,
            })






    hl_match = re.search(
        r"###\s*(?:🎬\s*)?[^\n]*하이라이트[^\n]*\n(.+?)(?=\n###\s|\Z)",
        md, flags=re.S
    )
    if hl_match:
        hl_body = hl_match.group(1)
        _HL_VOD_TAG = r"(?:\[VOD\s+\d+\]\s*)?"
        entries = re.split(r"\n(?=\s*\d+\.\s*\*{0,4}" + _HL_VOD_TAG + r"\[?\d{2}:\d{2}:\d{2})", hl_body)
        reason_re = re.compile(r"추천 이유\s*[:：]\s*(.+?)(?:\s*\)\s*)?$", re.M)
        for entry in entries:
            lines = entry.strip().splitlines()
            if not lines:
                continue
            parsed_header = _parse_highlight_entry_header(lines[0], _HL_VOD_TAG)
            if not parsed_header:
                continue
            tc_range_raw, header_tail = parsed_header
            tc_range = re.sub(r"^\[VOD\s+\d+\]\s*", "", tc_range_raw).strip("[] ")

            title, reason = header_tail, ""
            rm_inline = re.match(r"(.+?)\s*\(\s*추천 이유\s*[:：]\s*(.+?)\)\s*$", header_tail)
            if rm_inline:
                title = rm_inline.group(1).strip().rstrip("*").strip()
                reason = rm_inline.group(2).strip().rstrip(")").strip()
            else:

                for ln in lines[1:]:
                    rm2 = reason_re.search(ln)
                    if rm2:
                        reason = rm2.group(1).strip().rstrip(")").strip()
                        break
            title = title.strip(" *")
            title, is_injected, injected_label = _strip_injected_label(title)
            out["highlights"].append({
                "tc_range": tc_range,
                "tc_range_raw": tc_range_raw,
                "title": title,
                "reason": reason,
                "timestamp_confidence": _timestamp_confidence_from_texts(
                    tc_range,
                    title,
                    reason,
                ),
                "is_injected": is_injected,
                "injected_label": injected_label,
            })



    ed_match = re.search(
        r"^###[ \t]+(?:📝[ \t]*)?"
        r"(?:에디터(?:의)?[ \t]*(?:방송[ \t]*)?후기|(?:방송[ \t]*)?후기)"
        r"[ \t]*\r?\n(.+?)(?=^#{1,3}(?!#)[ \t]|\Z)",
        md, flags=re.S | re.M
    )
    if ed_match:
        body = ed_match.group(1).strip()
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        out["editor_notes"] = paragraphs







    eb_match = re.search(
        r"###\s*(?:🎬\s*)?[^\n]*편집 포인트[^\n]*\n(.+?)(?=\n###\s|\Z)",
        md, flags=re.S
    )
    if eb_match:
        eb_body = eb_match.group(1)


        eb_entries = re.split(
            r"\n(?=\s*\d+\.\s*\*\*\[?\d{2}:\d{2}:\d{2})",
            eb_body,
        )
        eb_header_re = re.compile(
            r"\s*\d+\.\s*\*\*\[?(\d{2}:\d{2}:\d{2}(?:~\d{2}:\d{2}:\d{2})?)\]?\*\*\s*(.+)",
            re.S,
        )
        for entry in eb_entries:
            entry_text = entry.strip()
            if not entry_text:
                continue
            hm = eb_header_re.match(entry_text)
            if not hm:
                continue
            tc_range = hm.group(1).strip()
            tail = hm.group(2).strip()


            title = tail
            cut_reason = ""
            context_warning = ""
            warn_match = re.search(r"(?:⚠\s*)?맥락\s*경고\s*[:：]\s*(.+)", tail, flags=re.S)
            if warn_match:
                context_warning = warn_match.group(1).strip().rstrip(".。 ")
                tail_before_warn = tail[: warn_match.start()].rstrip(" .。⚠")
            else:
                tail_before_warn = tail
            split_m = re.match(r"^(.+?)\s*[—\-–]\s*(.+)$", tail_before_warn, flags=re.S)
            if split_m:
                title = split_m.group(1).strip().rstrip(" *`")
                cut_reason = split_m.group(2).strip().rstrip(" *`.。")
            else:
                title = tail_before_warn.strip().rstrip(" *`")

            title = " ".join(title.split())
            cut_reason = " ".join(cut_reason.split())
            context_warning = " ".join(context_warning.split())
            if not title:
                continue
            out["editor_brief_points"].append({
                "tc_range": tc_range,
                "title": title,
                "cut_reason": cut_reason,
                "context_warning": context_warning,
            })

    def _parse_chzzk_comment_timeline_line(line: str) -> dict | None:
        body = re.sub(r"^\s*[-*]\s*", "", line or "").strip()
        if not body:
            return None
        patterns = [
            re.compile(
                r"^\[VOD\s+(\d+)\]\s*\[(\d{2}:\d{2}:\d{2})\](?:\([^)]*\))?"
                r"(?:\s*~\s*\[?(\d{2}:\d{2}:\d{2})\]?(?:\([^)]*\))?)?\s+(.+)$"
            ),
            re.compile(
                r"^\[VOD\s+(\d+)\s+(\d{2}:\d{2}:\d{2})\](?:\([^)]*\))?"
                r"(?:\s*~\s*\[?(\d{2}:\d{2}:\d{2})\]?(?:\([^)]*\))?)?\s+(.+)$"
            ),
            re.compile(
                r"^\[(\d{2}:\d{2}:\d{2})\](?:\([^)]*\))?"
                r"(?:\s*~\s*\[?(\d{2}:\d{2}:\d{2})\]?(?:\([^)]*\))?)?\s+(.+)$"
            ),
        ]
        for index, pattern in enumerate(patterns):
            cm = pattern.match(body)
            if not cm:
                continue
            if index < 2:
                vod_index = cm.group(1)
                tc = cm.group(2)
                tc_end = cm.group(3)
                title = cm.group(4)
                entry: dict = {
                    "tc": tc,
                    "tc_raw": f"[VOD {vod_index}] [{tc}]",
                    "title": title.strip(),
                }
            else:
                tc = cm.group(1)
                tc_end = cm.group(2)
                title = cm.group(3)
                entry = {"tc": tc, "title": title.strip()}
            if tc_end:
                entry["tc_end"] = tc_end
            return entry
        return None


    ct_match = re.search(
        r"###\s*치지직 댓글 타임라인\s*\n(.+?)(?=\n###\s|\Z)",
        md, flags=re.S
    )
    if ct_match:
        ct_body = ct_match.group(1)
        for line in ct_body.splitlines():
            entry = _parse_chzzk_comment_timeline_line(line)
            if entry:
                out["chzzk_comment_timeline"].append(entry)

    return out
