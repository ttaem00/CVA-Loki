from __future__ import annotations

from dataclasses import dataclass

from difflib import SequenceMatcher

import json

import re

import unicodedata

from pathlib import Path

from typing import Any, Iterable

QUOTE_RE = re.compile(
    r'"([^"\n]{1,120})"|“([^”\n]{1,120})”|‘([^’\n]{1,120})’|「([^」\n]{1,120})」|『([^』\n]{1,120})』'
)

SRT_TIME_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?:[,.](?P<ms>\d{1,3}))?"
)

@dataclass(frozen=True)
class QuoteCue:
    start_sec: float
    end_sec: float
    text: str
    source_kind: str = "subtitle"

def seconds_to_hms(sec: Any) -> str:
    try:
        value = float(sec)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    total = int(value)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def extract_public_evidence_quotes(text: str) -> list[str]:
    quotes: list[str] = []
    seen: set[str] = set()
    for match in QUOTE_RE.finditer(text or ""):
        quote = next((group for group in match.groups() if group), "").strip()
        quote = re.sub(r"\s+", " ", quote)
        if not quote or quote in seen:
            continue
        seen.add(quote)
        quotes.append(quote)
    return quotes

def _parse_time(value: str) -> float | None:
    match = SRT_TIME_RE.search(value or "")
    if not match:
        return None
    h = int(match.group("h"))
    m = int(match.group("m"))
    s = int(match.group("s"))
    ms = int((match.group("ms") or "0").ljust(3, "0")[:3])
    return h * 3600 + m * 60 + s + ms / 1000

def parse_subtitle_cues(path: Path) -> list[QuoteCue]:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return []
    cues: list[QuoteCue] = []
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        timing_index = next((idx for idx, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        left, right = lines[timing_index].split("-->", 1)
        start = _parse_time(left)
        end = _parse_time(right)
        if start is None:
            continue
        text = " ".join(lines[timing_index + 1 :]).strip()
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        cues.append(QuoteCue(start_sec=start, end_sec=end if end is not None else start, text=text))
    return cues

def parse_asr_segment_cues(path: Path) -> list[QuoteCue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    segments = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(segments, list):
        return []
    cues: list[QuoteCue] = []
    for idx, row in enumerate(segments):
        if not isinstance(row, dict):
            continue
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
        if not text:
            continue
        try:
            start = float(row.get("start_sec") if row.get("start_sec") is not None else row.get("start"))
        except (TypeError, ValueError):
            continue
        try:
            end = float(row.get("end_sec") if row.get("end_sec") is not None else row.get("end"))
        except (TypeError, ValueError):
            end = start
        cues.append(QuoteCue(start_sec=start, end_sec=max(start, end), text=text))
    return cues

def load_quote_source_cues(work_dir: Path | None, video_no: str) -> list[QuoteCue]:
    if not work_dir or not video_no:
        return []
    candidates: list[Path] = []
    roots = [work_dir / str(video_no), work_dir]
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        candidates.extend(sorted(root.glob("*.srt")))
        candidates.extend(sorted(root.glob("*.vtt")))
        candidates.extend(sorted(root.glob("*asr_segments*.json")))
        candidates.extend(sorted(root.glob("asr_segments.json")))
    cues: list[QuoteCue] = []
    seen_paths: set[Path] = set()
    for path in candidates:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if path.suffix.lower() == ".json":
            cues.extend(parse_asr_segment_cues(path))
        else:
            cues.extend(parse_subtitle_cues(path))
    cues.sort(key=lambda cue: cue.start_sec)
    return cues

def load_chat_quote_cues(chat_records: Iterable[dict[str, Any]] | None) -> list[QuoteCue]:
    """Build public-safe, timestamped chat cues without user identifiers."""

    cues: list[QuoteCue] = []
    for row in chat_records or []:
        if not isinstance(row, dict):
            continue
        text = re.sub(r"\s+", " ", str(row.get("msg") or row.get("message") or "")).strip()
        if not text:
            continue
        raw_sec: Any = row.get("sec")
        if raw_sec is None:
            raw_sec = row.get("time_sec")
        if raw_sec is None and row.get("ms") is not None:
            try:
                raw_sec = float(row["ms"]) / 1000.0
            except (TypeError, ValueError):
                raw_sec = None
        try:
            start = float(raw_sec)
        except (TypeError, ValueError):
            continue
        if start < 0:
            continue
        cues.append(
            QuoteCue(
                start_sec=start,
                end_sec=start,
                text=text[:280],
                source_kind="chat",
            )
        )
    cues.sort(key=lambda cue: cue.start_sec)
    return cues

def _normalize_match_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in normalized if ch.isalnum() or "\uac00" <= ch <= "\ud7a3")

def _window_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle):
        return SequenceMatcher(None, needle, haystack).ratio()
    best = 0.0
    window = max(len(needle), min(len(haystack), int(len(needle) * 1.8)))
    step = max(1, len(needle) // 3)
    for start in range(0, max(1, len(haystack) - window + 1), step):
        best = max(best, SequenceMatcher(None, needle, haystack[start : start + window]).ratio())
    longest = SequenceMatcher(None, needle, haystack).find_longest_match(0, len(needle), 0, len(haystack)).size
    return max(best, longest / max(1, len(needle)))

def _quote_position_time_in_cue(quote: str, cue: QuoteCue) -> tuple[float, str]:
    quote_norm = _normalize_match_text(quote)
    cue_norm = _normalize_match_text(cue.text)
    if not quote_norm or not cue_norm:
        return cue.start_sec, f"{cue.source_kind}_match"
    idx = cue_norm.find(quote_norm)
    if idx < 0:
        return cue.start_sec, f"{cue.source_kind}_match"
    duration = max(0.0, cue.end_sec - cue.start_sec)
    if duration <= 0 or len(cue_norm) <= 1:
        return cue.start_sec, f"{cue.source_kind}_exact"
    ratio = min(1.0, max(0.0, idx / max(1, len(cue_norm))))
    return cue.start_sec + duration * ratio, f"{cue.source_kind}_text_position"

def _best_cue_for_quote(
    quote: str,
    cues: Iterable[QuoteCue],
    anchor_sec: float,
    *,
    window_sec: float = 360.0,
) -> tuple[QuoteCue | None, float]:
    quote_norm = _normalize_match_text(quote)
    if not quote_norm:
        return None, 0.0
    best: tuple[QuoteCue | None, float, float] = (None, 0.0, float("inf"))
    for cue in cues:
        if abs(cue.start_sec - anchor_sec) > window_sec:
            continue
        cue_norm = _normalize_match_text(cue.text)
        if cue.source_kind == "chat" and quote_norm not in cue_norm and cue_norm not in quote_norm:
            continue
        lexical = _window_ratio(quote_norm, cue_norm)
        proximity = abs(cue.start_sec - anchor_sec)
        adjusted = lexical - min(0.08, (proximity / max(1.0, window_sec)) * 0.08)
        if adjusted > best[1] or (adjusted == best[1] and proximity < best[2]):
            best = (cue, adjusted, proximity)
    return best[0], best[1]

def _matched_source_excerpt(quote: str, cue: QuoteCue) -> str:
    text = re.sub(r"\s+", " ", cue.text).strip()
    if cue.source_kind == "chat":
        return text[:280]
    parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s*", text) if part.strip()]
    if len(parts) <= 1:
        return text[:280]
    quote_norm = _normalize_match_text(quote)
    best = max(parts, key=lambda part: _window_ratio(quote_norm, _normalize_match_text(part)))
    return best[:280]

def build_quote_citations_for_evidence(
    evidence_text: str,
    *,
    cues: Iterable[QuoteCue],
    anchor_sec: Any,
    window_sec: float = 90.0,
) -> list[dict[str, Any]]:
    quotes = extract_public_evidence_quotes(evidence_text)
    if not quotes:
        return []
    try:
        anchor = float(anchor_sec)
    except (TypeError, ValueError):
        anchor = 0.0
    cue_list = list(cues)
    citations: list[dict[str, Any]] = []
    for quote in quotes:
        cue, score = _best_cue_for_quote(quote, cue_list, anchor, window_sec=window_sec)
        quote_norm = _normalize_match_text(quote)
        match_threshold = 0.58 if len(quote_norm) >= 5 else 0.78
        matched = cue is not None and score >= match_threshold
        row: dict[str, Any] = {"quote": quote}
        if matched and cue is not None:
            start, status = _quote_position_time_in_cue(quote, cue)
            row.update(
                {
                    "quote": _matched_source_excerpt(quote, cue),
                    "start_sec": int(max(0, start)),
                    "timecode": seconds_to_hms(start),
                    "match_status": status,
                }
            )
        else:
            row["match_status"] = "unmatched"
        citations.append(row)
    return citations

def build_public_quote_citation_payload(
    *,
    segment_ledger: Iterable[dict[str, Any]],
    work_dir: Path | None,
    video_no: str,
    chat_records: Iterable[dict[str, Any]] | None = None,
    window_sec: float = 90.0,
) -> dict[str, Any]:
    subtitle_cues = load_quote_source_cues(work_dir, video_no)
    chat_cues = load_chat_quote_cues(chat_records)
    cues = sorted([*subtitle_cues, *chat_cues], key=lambda cue: cue.start_sec)
    items: list[dict[str, Any]] = []
    for segment in segment_ledger:
        segment_id = str(segment.get("segment_id") or "").strip()
        if not segment_id:
            continue
        evidence_texts: list[str] = []
        for source in segment.get("sources") or []:
            if not isinstance(source, dict):
                continue
            if source.get("source_type") != "chat_or_text_evidence":
                continue
            text = str(source.get("text") or "").strip()
            if text:
                evidence_texts.append(text)
        if not evidence_texts:
            text = str(segment.get("evidence") or "").strip()
            if text:
                evidence_texts.append(text)
        citations: list[dict[str, Any]] = []
        for text in evidence_texts:
            citations.extend(
                build_quote_citations_for_evidence(
                    text,
                    cues=cues,
                    anchor_sec=segment.get("start_sec") or 0,
                    window_sec=window_sec,
                )
            )
        is_timeline = (
            str(segment.get("kind") or "").strip().lower() == "timeline"
            or str(segment.get("section") or "").strip().lower() == "timeline"
            or bool(evidence_texts)
        )
        if is_timeline:
            items.append(
                {
                    "segment_id": segment_id,
                    "start_sec": int(float(segment.get("start_sec") or 0)),
                    "citations": citations[:8],
                    "evidence_state": (
                        "verified"
                        if any(row.get("start_sec") is not None for row in citations)
                        else "not_verified"
                    ),
                }
            )
    return {
        "schema_version": "public_quote_citations.v2",
        "render_target": "report_html_script#summaryEvidenceQuoteCitationsData",
        "raw_transcript_content_included": False,
        "public_quote_text_included": True,
        "video_no": str(video_no or ""),
        "subtitle_cue_count": len(subtitle_cues),
        "chat_cue_count": len(chat_cues),
        "items": items,
    }
