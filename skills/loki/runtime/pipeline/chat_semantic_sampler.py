"""Deterministic, privacy-minimal replay-chat sampling for Pipeline 2.5.

The Pipeline 2.5 chunk path calls this module after each chunk's time slice and
before prompt serialization.  Applying the global 60-row budget before that
slice would erase coverage from later chunks and is therefore outside this
module's integration contract.  The same functions remain usable by raw-free
shadow evaluation.  User identifiers are in-memory diversity keys and are
never returned in rows or statistics.
"""

from __future__ import annotations

import math

import re

import unicodedata

from collections import Counter, defaultdict, deque

from dataclasses import dataclass, field

from difflib import SequenceMatcher

from typing import Any, Mapping, Sequence

DEFAULT_CHAT_ROW_LIMIT = 60

DEFAULT_MESSAGE_CHAR_LIMIT = 240

DEFAULT_BURST_WINDOW_MS = 10_000

_GRAM_CLUSTER_CANDIDATE_LIMIT = 128

_URL_PATTERN = re.compile(
    r"(?:https?://|www\.|(?:discord(?:app)?\.com|open\.kakao\.com|t\.me)/)",
    re.IGNORECASE,
)

_PROMOTION_PATTERN = re.compile(
    r"(?:홍보|광고|구독|팔로우|무료|이벤트|오픈채팅|디스코드|입장|가입)",
    re.IGNORECASE,
)

_LAUGHTER_PATTERN = re.compile(r"(?:ㅋ{2,}|ㅎ{2,}|(?:lol|lmao)+)", re.IGNORECASE)

_SURPRISE_PATTERN = re.compile(
    r"(?:헐+|헉+|ㄷ{2,}|와{2,}|우와+|대박|미쳤|레전드|wow|wtf|omg)",
    re.IGNORECASE,
)

_QUESTION_PATTERN = re.compile(r"[?？]|(?:뭐야|뭐지|왜|어떻게|어케)")

_REPEATED_CHAR_PATTERN = re.compile(r"(.)\1{2,}")

_DIGIT_PATTERN = re.compile(r"\d+")

@dataclass(frozen=True)
class SemanticChatSample:
    """Selected prompt rows plus aggregate-only, identifier-free diagnostics."""

    rows: list[dict[str, Any]]
    stats: dict[str, Any]

@dataclass
class _Row:
    index: int
    ms: int
    msg: str
    normalized: str
    user_key: str
    known_user: bool
    families: frozenset[str]
    internal_repeat_ratio: float = 0.0
    content_richness: float = 0.0
    caption_support: float = 0.0
    context_term_rarity: float = 0.0
    low_information_repeat: bool = False
    cluster_id: int = -1
    burst_bucket: int = 0
    time_bin: int = -1

@dataclass
class _Cluster:
    cluster_id: int
    normalized: str
    grams: frozenset[str]
    rows: list[_Row] = field(default_factory=list)
    known_users: set[str] = field(default_factory=set)
    consensus_users: set[str] = field(default_factory=set)
    families: set[str] = field(default_factory=set)

_CONTEXT_TERM_PATTERN = re.compile(r"[0-9a-z가-힣]+", re.IGNORECASE)

def _safe_ms(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, int(number))

def _user_key(row: Mapping[str, Any], index: int) -> tuple[str, bool]:
    for field_name in ("uid", "user", "nick"):
        value = str(row.get(field_name) or "").strip()
        if value:
            return f"known:{field_name}:{value}", True
    return f"unknown-row:{index}", False

def _reaction_families(text: str) -> frozenset[str]:
    families: set[str] = set()
    if _LAUGHTER_PATTERN.search(text):
        families.add("laughter")
    if _SURPRISE_PATTERN.search(text):
        families.add("surprise")
    if _QUESTION_PATTERN.search(text):
        families.add("question")
    if any(unicodedata.category(char).startswith("S") for char in text):
        families.add("emoji")
    return frozenset(families)

def _normalize_text(text: str, families: frozenset[str]) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"ㅋ{2,}", "ㅋㅋ", normalized)
    normalized = re.sub(r"ㅎ{2,}", "ㅎㅎ", normalized)
    normalized = _REPEATED_CHAR_PATTERN.sub(r"\1\1", normalized)
    kept = []
    for char in normalized:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("S"):
            kept.append(char)
    compact = "".join(kept)
    if compact:
        return compact
    if families:
        return "<" + "+".join(sorted(families)) + ">"
    return ""

def _collapse_full_message_repetition(text: str) -> tuple[str, float]:
    """Collapse exact whole-message repeats for scoring and clustering only.

    A repeated greeting and a repeated long copypasta should not receive extra
    value merely because the same block was pasted more than once.  The caller
    still emits the untouched original message.  Single-character reaction
    runs are already normalized separately and are intentionally not collapsed
    here.
    """

    length = len(text)
    if length < 4:
        return text, 0.0
    for block_length in range(2, length // 2 + 1):
        if length % block_length:
            continue
        block = text[:block_length]
        copies = length // block_length
        if copies >= 2 and block * copies == text:
            return block, 1.0 - (block_length / length)
    return text, 0.0

def _content_richness(text: str) -> float:
    """Return a bounded structural information score without a language model."""

    if not text:
        return 0.0
    grams = _char_bigrams(text)
    diversity = len(grams) / max(1, len(text) - 1)
    length_value = min(1.0, len(text) / 24.0)
    return min(1.0, 0.65 * length_value + 0.35 * diversity)

def _context_terms(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return frozenset(
        term
        for term in _CONTEXT_TERM_PATTERN.findall(normalized)
        if len(term) >= 2 or term.isdigit()
    )

def _caption_support(
    message: str,
    scoring_text: str,
    *,
    context_terms: frozenset[str],
    context_grams: frozenset[str],
) -> float:
    """Measure lexical subtitle linkage and whether the chat adds context."""

    if not scoring_text or not context_terms or not context_grams:
        return 0.0
    message_terms = _context_terms(message)
    token_hits = len(message_terms & context_terms)
    message_grams = _char_bigrams(scoring_text)
    gram_hits = len(message_grams & context_grams)
    if token_hits == 0 and gram_hits < 2:
        return 0.0
    token_signal = min(1.0, token_hits / 2.0)
    gram_signal = min(1.0, gram_hits / max(2, min(len(message_grams), 8)))
    support = 0.6 * token_signal + 0.4 * gram_signal
    if support > 0 and message_terms - context_terms:
        support += 0.15
    return min(1.0, support)

def _is_link_advertisement(text: str) -> bool:
    match = _URL_PATTERN.search(text)
    if not match:
        return False
    without_urls = _URL_PATTERN.sub("", text)
    alnum_count = sum(char.isalnum() for char in without_urls)
    return alnum_count <= 16 or bool(_PROMOTION_PATTERN.search(text))

def _char_bigrams(text: str) -> frozenset[str]:
    if len(text) < 2:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[index : index + 2] for index in range(len(text) - 1))

def _near_duplicate(left: str, right: str, left_grams: frozenset[str], right_grams: frozenset[str]) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 4 or max(len(left), len(right)) > 80:
        return False
    if _DIGIT_PATTERN.findall(left) != _DIGIT_PATTERN.findall(right):
        return False
    shorter, longer = sorted((left, right), key=len)
    containment = len(shorter) / max(1, len(longer)) if shorter in longer else 0.0
    intersection = len(left_grams & right_grams)
    union = len(left_grams | right_grams)
    jaccard = intersection / union if union else 0.0
    if containment >= 0.72:
        return True
    if jaccard < 0.58:
        return False
    return SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.86

def _legacy_first_middle_last(chats: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
    if len(chats) <= limit:
        return list(chats)
    third = limit // 3
    sizes = (third, third, limit - 2 * third)
    middle = len(chats) // 2
    half = sizes[1] // 2
    return list(chats[: sizes[0]]) + list(chats[middle - half : middle - half + sizes[1]]) + list(chats[-sizes[2] :])

def _build_clusters(
    rows: list[_Row], *, cross_user_window_ms: int
) -> tuple[list[_Cluster], Counter[str]]:
    clusters: list[_Cluster] = []
    exact_index: dict[str, int] = {}
    gram_index: dict[str, deque[int]] = defaultdict(
        lambda: deque(maxlen=_GRAM_CLUSTER_CANDIDATE_LIMIT)
    )
    match_counts: Counter[str] = Counter()

    for row in rows:
        cluster_id = exact_index.get(row.normalized)
        match_kind = "exact_repeat" if cluster_id is not None else "new"
        grams = _char_bigrams(row.normalized)
        if cluster_id is None and grams:
            overlap_counts: Counter[int] = Counter()
            for gram in grams:
                overlap_counts.update(gram_index.get(gram, ()))
            for candidate_id, _ in sorted(overlap_counts.items(), key=lambda item: (-item[1], item[0]))[:32]:
                candidate = clusters[candidate_id]
                same_known_user = row.known_user and row.user_key in candidate.known_users
                shared_reaction = bool(row.families & candidate.families)
                close_distinct_users = (
                    row.known_user
                    and bool(candidate.known_users)
                    and row.user_key not in candidate.known_users
                    and bool(candidate.rows)
                    and row.ms - candidate.rows[-1].ms <= cross_user_window_ms
                )
                candidate_caption_support = max(
                    (candidate_row.caption_support for candidate_row in candidate.rows),
                    default=0.0,
                )
                candidate_richness = max(
                    (candidate_row.content_richness for candidate_row in candidate.rows),
                    default=0.0,
                )
                explanatory_followup = close_distinct_users and (
                    (
                        row.caption_support >= 0.25
                        and candidate_caption_support >= 0.25
                    )
                    or (
                        row.content_richness >= 0.65
                        and candidate_richness >= 0.65
                    )
                )
                if not (same_known_user or shared_reaction or explanatory_followup):
                    continue
                if _near_duplicate(row.normalized, candidate.normalized, grams, candidate.grams):
                    cluster_id = candidate_id
                    match_kind = "near_repeat"



                    exact_index[row.normalized] = cluster_id
                    break
        if cluster_id is None:
            cluster_id = len(clusters)
            cluster = _Cluster(cluster_id=cluster_id, normalized=row.normalized, grams=grams)
            clusters.append(cluster)
            exact_index[row.normalized] = cluster_id
            for gram in grams:
                gram_index[gram].append(cluster_id)
        cluster = clusters[cluster_id]
        row.cluster_id = cluster_id
        cluster.rows.append(row)
        if row.known_user:
            cluster.known_users.add(row.user_key)
        cluster.families.update(row.families)
        if match_kind != "new":
            match_counts[match_kind] += 1
    return clusters, match_counts

def _local_consensus_rows(cluster: _Cluster, window_ms: int) -> list[_Row]:
    """Return distinct-user rows from the densest local time window."""

    ordered = sorted((row for row in cluster.rows if row.known_user), key=lambda row: (row.ms, row.index))
    if len(ordered) < 2:
        return []
    left = 0
    users: Counter[str] = Counter()
    best_bounds = (0, 0)
    best_distinct = 0
    for right, row in enumerate(ordered):
        users[row.user_key] += 1
        while ordered[right].ms - ordered[left].ms > window_ms:
            left_user = ordered[left].user_key
            users[left_user] -= 1
            if users[left_user] <= 0:
                del users[left_user]
            left += 1
        if len(users) > best_distinct:
            best_distinct = len(users)
            best_bounds = (left, right)
    if best_distinct < 2:
        return []
    first_by_user: dict[str, _Row] = {}
    for row in ordered[best_bounds[0] : best_bounds[1] + 1]:
        first_by_user.setdefault(row.user_key, row)
    return list(first_by_user.values())

def _cluster_representatives(
    clusters: list[_Cluster], consensus_window_ms: int
) -> tuple[list[_Row], int, int]:
    candidates: list[_Row] = []
    one_user_repeat_removed = 0
    low_information_echo_removed = 0
    for cluster in clusters:
        first_by_user: dict[str, _Row] = {}

        def representative_quality(row: _Row) -> tuple[float, float, float, float, int, int]:
            return (
                row.context_term_rarity,
                row.caption_support,
                row.content_richness,
                -row.internal_repeat_ratio,
                -row.ms,
                -row.index,
            )

        for row in cluster.rows:
            if row.known_user and row.user_key in first_by_user:
                one_user_repeat_removed += 1
                if representative_quality(row) > representative_quality(first_by_user[row.user_key]):
                    first_by_user[row.user_key] = row
                continue
            first_by_user.setdefault(row.user_key, row)

        known_rows = [row for row in first_by_user.values() if row.known_user]
        unknown_rows = [row for row in first_by_user.values() if not row.known_user]
        local_consensus = _local_consensus_rows(cluster, consensus_window_ms)
        cluster.consensus_users = {row.user_key for row in local_consensus}
        if (
            len(cluster.consensus_users) >= 2
            and local_consensus
            and all(row.low_information_repeat for row in local_consensus)
        ):
            low_information_echo_removed += len(cluster.rows)
            continue
        if len(cluster.consensus_users) >= 2:
            distinct_variants = {row.normalized for row in local_consensus}
            caption_linked_followup = (
                len(distinct_variants) >= 2
                and max(row.caption_support for row in local_consensus) >= 0.25
            )
            long_copypasta = (
                len(distinct_variants) == 1
                and max(len(row.normalized) for row in local_consensus) >= 24
            )
            if long_copypasta:
                representative_limit = 1
            elif caption_linked_followup and not cluster.families:
                representative_limit = min(2, len(cluster.consensus_users))
            else:


                representative_limit = min(3, len(cluster.consensus_users))
            representatives = local_consensus[:representative_limit]
        elif known_rows or unknown_rows:




            representatives = [
                min(
                    known_rows + unknown_rows,
                    key=lambda row: (
                        -row.caption_support,
                        -row.content_richness,
                        row.internal_repeat_ratio,
                        row.ms,
                        row.index,
                    ),
                )
            ]
        else:
            representatives = []
        candidates.extend(representatives)
    return candidates, one_user_repeat_removed, low_information_echo_removed

def _user_cap(limit: int, known_user_count: int) -> int:
    if limit <= 0:
        return 0
    if known_user_count <= 0:
        return limit
    return min(6, max(2, math.ceil(limit / min(known_user_count, 30))))

def sample_semantic_chat_rows_with_stats(
    chats: Sequence[Mapping[str, Any]],
    *,
    limit: int = DEFAULT_CHAT_ROW_LIMIT,
    max_message_chars: int = DEFAULT_MESSAGE_CHAR_LIMIT,
    context_text: str = "",
    focus_start_ms: int | None = None,
    focus_end_ms: int | None = None,
    burst_window_ms: int = DEFAULT_BURST_WINDOW_MS,
    live_context_small_budget: bool = False,
    include_source_index: bool = False,
) -> SemanticChatSample:
    """Return a diverse bounded sample and raw-free diagnostics.

    ``context_text`` is the already-available subtitle text for the actual
    segment. It affects deterministic relevance scoring only and is never
    returned or copied into diagnostics. ``focus_start_ms``/``focus_end_ms``
    describe the segment when the input also contains caller-provided overlap
    margins. Rows immediately before and after those boundaries get explicit
    representation opportunities.
    """

    safe_limit = max(0, int(limit))
    safe_char_limit = max(1, int(max_message_chars))
    safe_burst_ms = max(1, int(burst_window_ms))
    normalized_context = _normalize_text(str(context_text or ""), frozenset())
    context_terms = _context_terms(str(context_text or ""))
    context_grams = _char_bigrams(normalized_context)
    dropped_empty = 0
    dropped_link_advertisement = 0
    internal_repeat_collapsed = 0
    prepared: list[_Row] = []

    for index, raw in enumerate(chats or ()):
        if not isinstance(raw, Mapping):
            dropped_empty += 1
            continue
        message = str(raw.get("msg") or "").strip()
        if not message:
            dropped_empty += 1
            continue
        if _is_link_advertisement(message):
            dropped_link_advertisement += 1
            continue
        message = message[:safe_char_limit]
        families = _reaction_families(message)
        normalized = _normalize_text(message, families)
        if not normalized:
            dropped_empty += 1
            continue
        scoring_text, internal_repeat_ratio = _collapse_full_message_repetition(normalized)
        if internal_repeat_ratio > 0:
            internal_repeat_collapsed += 1
        richness = _content_richness(scoring_text)
        caption_support = _caption_support(
            message,
            scoring_text,
            context_terms=context_terms,
            context_grams=context_grams,
        )
        low_information_repeat = (
            internal_repeat_ratio >= 0.34
            and len(scoring_text) <= 12
            and not families
            and caption_support < 0.2
        )
        user_key, known_user = _user_key(raw, index)
        ms = _safe_ms(raw.get("ms"))
        prepared.append(
            _Row(
                index=index,
                ms=ms,
                msg=message,
                normalized=scoring_text,
                user_key=user_key,
                known_user=known_user,
                families=families,
                internal_repeat_ratio=internal_repeat_ratio,
                content_richness=richness,
                caption_support=caption_support,
                low_information_repeat=low_information_repeat,
                burst_bucket=ms // safe_burst_ms,
            )
        )

    prepared.sort(key=lambda row: (row.ms, row.index))
    context_term_frequency: Counter[str] = Counter()
    for row in prepared:
        context_term_frequency.update(_context_terms(row.msg) & context_terms)
    for row in prepared:
        matching_terms = _context_terms(row.msg) & context_terms
        if matching_terms:
            row.context_term_rarity = 1.0 / min(
                context_term_frequency[term] for term in matching_terms
            )
    clusters, repeat_matches = _build_clusters(
        prepared,
        cross_user_window_ms=safe_burst_ms,
    )
    candidates, one_user_repeat_removed, low_information_echo_removed = (
        _cluster_representatives(clusters, safe_burst_ms)
    )

    known_users = {row.user_key for row in prepared if row.known_user}
    per_user_limit = _user_cap(safe_limit, len(known_users))
    bucket_users: dict[int, set[str]] = defaultdict(set)
    bucket_has_unknown: set[int] = set()
    bucket_rows: Counter[int] = Counter()
    for row in prepared:
        bucket_rows[row.burst_bucket] += 1
        if row.known_user:
            bucket_users[row.burst_bucket].add(row.user_key)
        else:
            bucket_has_unknown.add(row.burst_bucket)

    def bucket_distinct_users(bucket: int) -> int:
        return len(bucket_users.get(bucket, ())) + (
            0 if live_context_small_budget else 1 if bucket in bucket_has_unknown else 0
        )

    if prepared:
        overall_start = prepared[0].ms
        overall_end = prepared[-1].ms
    else:
        overall_start = overall_end = 0
    coverage_start = _safe_ms(focus_start_ms) if focus_start_ms is not None else overall_start
    coverage_end = _safe_ms(focus_end_ms) if focus_end_ms is not None else overall_end
    if coverage_end < coverage_start:
        coverage_start, coverage_end = coverage_end, coverage_start
    requested_bins = min(5, safe_limit) if live_context_small_budget else max(1, safe_limit // 5) if safe_limit else 0
    time_bin_total = min(12, requested_bins, max(1, len(candidates))) if candidates and safe_limit else 0
    span = max(1, coverage_end - coverage_start + 1)
    for row in candidates:
        if time_bin_total and coverage_start <= row.ms <= coverage_end:
            row.time_bin = min(time_bin_total - 1, int((row.ms - coverage_start) * time_bin_total / span))

    def cluster_for(row: _Row) -> _Cluster:
        return clusters[row.cluster_id]

    def row_score(row: _Row) -> float:
        cluster = cluster_for(row)
        consensus = len(cluster.consensus_users)
        distinct_in_bucket = bucket_distinct_users(row.burst_bucket)
        semantic_length = len(row.normalized)
        content_score = 2.0 * row.content_richness
        if row.families and semantic_length <= 6:
            content_score *= 0.35
        distinctive_context = (
            0.5
            if (not row.families or semantic_length >= 24)
            and (row.content_richness >= 0.65 or semantic_length >= 24)
            and row.caption_support < 0.25
            else 0.0
        )
        return (
            content_score
            + 3.0 * row.caption_support
            + 1.7 * math.log2(1 + consensus)


            + 0.25 * math.log2(1 + distinct_in_bucket)
            + distinctive_context
            + (0.2 if len(row.families) >= 2 else 0.0)
        )

    selected: list[_Row] = []
    selected_indexes: set[int] = set()
    selected_per_user: Counter[str] = Counter()

    def try_select(row: _Row) -> bool:
        if len(selected) >= safe_limit or row.index in selected_indexes:
            return False
        if row.known_user and selected_per_user[row.user_key] >= per_user_limit:
            return False
        selected.append(row)
        selected_indexes.add(row.index)
        if row.known_user:
            selected_per_user[row.user_key] += 1
        return True


    by_time_bin: dict[int, list[_Row]] = defaultdict(list)
    for row in candidates:
        if row.time_bin >= 0:
            by_time_bin[row.time_bin].append(row)
    for bin_index in range(time_bin_total):
        for row in sorted(by_time_bin.get(bin_index, ()), key=lambda item: (-row_score(item), item.ms, item.index)):
            if try_select(row):
                break


    boundary_selected = {"before": 0, "after": 0}
    if focus_start_ms is not None:
        before = sorted(
            (row for row in candidates if row.ms < coverage_start),
            key=lambda item: (coverage_start - item.ms, -row_score(item), item.index),
        )
        if before and try_select(before[0]):
            boundary_selected["before"] = 1
    if focus_end_ms is not None:
        after = sorted(
            (row for row in candidates if row.ms > coverage_end),
            key=lambda item: (item.ms - coverage_end, -row_score(item), item.index),
        )
        if after and try_select(after[0]):
            boundary_selected["after"] = 1


    burst_budget = min(2, max(0, safe_limit - time_bin_total)) if live_context_small_budget else min(4, max(0, safe_limit // 12))
    burst_buckets = sorted(
        (bucket for bucket in bucket_rows if bucket_distinct_users(bucket) >= 2),
        key=lambda bucket: (-bucket_distinct_users(bucket), -min(bucket_rows[bucket], bucket_distinct_users(bucket) * 3), bucket),
    )[:burst_budget]
    for bucket in burst_buckets:
        for row in sorted(
            (candidate for candidate in candidates if candidate.burst_bucket == bucket),
            key=lambda item: (-row_score(item), item.ms, item.index),
        ):
            if try_select(row):
                break







    consensus_cluster_budget = 0 if live_context_small_budget else min(12, max(0, safe_limit // 5))
    consensus_clusters = sorted(
        (cluster for cluster in clusters if len(cluster.consensus_users) >= 2),
        key=lambda cluster: (-len(cluster.consensus_users), cluster.rows[0].ms, cluster.cluster_id),
    )[:consensus_cluster_budget]
    for cluster in consensus_clusters:
        representatives = sorted(
            (candidate for candidate in candidates if candidate.cluster_id == cluster.cluster_id),
            key=lambda item: (item.ms, item.index),
        )
        for row in representatives[:2]:
            try_select(row)


    for row in sorted(candidates, key=lambda item: (-row_score(item), item.ms, item.index)):
        if len(selected) >= safe_limit:
            break
        try_select(row)

    selected.sort(key=lambda row: (row.ms, row.index))
    output_rows = [{"ms": row.ms, "msg": row.msg,
                    **({"source_index": row.index} if include_source_index else {})}
                   for row in selected]
    selected_clusters = Counter(row.cluster_id for row in selected)
    selected_consensus_cluster_count = sum(
        1
        for cluster_id in selected_clusters
        if len(clusters[cluster_id].consensus_users) >= 2
    )
    selected_visible_consensus_cluster_count = sum(
        1
        for cluster_id, count in selected_clusters.items()
        if count >= 2 and len(clusters[cluster_id].consensus_users) >= 2
    )
    selected_consensus_repeat_rows = sum(
        max(0, count - 1)
        for cluster_id, count in selected_clusters.items()
        if len(clusters[cluster_id].consensus_users) >= 2
    )
    selected_known_user_counts = Counter(row.user_key for row in selected if row.known_user)
    selected_known_total = sum(selected_known_user_counts.values())
    max_known_user_share = (
        max(selected_known_user_counts.values()) / selected_known_total
        if selected_known_total and selected_known_user_counts
        else 0.0
    )
    covered_bins = {row.time_bin for row in selected if row.time_bin >= 0}
    legacy_rows = _legacy_first_middle_last(chats, safe_limit) if safe_limit else []

    stats: dict[str, Any] = {
        "schema_version": "chat_semantic_sampler.shadow_stats.v1",
        "default_output_changed": False,
        "live_context_small_budget": live_context_small_budget,
        "input_count": len(chats or ()),
        "eligible_count": len(prepared),
        "candidate_count": len(candidates),
        "selected_count": len(output_rows),
        "limit": safe_limit,
        "dropped_empty_count": dropped_empty,
        "dropped_link_advertisement_count": dropped_link_advertisement,
        "internal_repeat_collapsed_count": internal_repeat_collapsed,
        "exact_repeat_match_count": int(repeat_matches["exact_repeat"]),
        "near_repeat_match_count": int(repeat_matches["near_repeat"]),
        "one_user_repeat_removed_count": one_user_repeat_removed,
        "low_information_echo_removed_count": low_information_echo_removed,
        "context_text_used": bool(normalized_context),
        "caption_linked_candidate_count": sum(
            1 for row in candidates if row.caption_support >= 0.25
        ),
        "selected_caption_linked_count": sum(
            1 for row in selected if row.caption_support >= 0.25
        ),
        "selected_caption_linked_reaction_count": sum(
            1 for row in selected if row.families and row.caption_support >= 0.25
        ),
        "selected_distinctive_context_count": sum(
            1
            for row in selected
            if (not row.families or len(row.normalized) >= 24)
            and (row.content_richness >= 0.65 or len(row.normalized) >= 24)
            and row.caption_support < 0.25
        ),
        "multi_user_consensus_cluster_count": sum(1 for cluster in clusters if len(cluster.consensus_users) >= 2),
        "selected_consensus_cluster_count": selected_consensus_cluster_count,
        "selected_visible_consensus_cluster_count": selected_visible_consensus_cluster_count,
        "selected_consensus_repeat_row_count": selected_consensus_repeat_rows,
        "known_user_count": len(known_users),
        "per_known_user_cap": per_user_limit,
        "max_selected_known_user_share": round(max_known_user_share, 6),
        "time_bin_count": time_bin_total,
        "time_bins_covered": len(covered_bins),
        "boundary_before_selected": boundary_selected["before"],
        "boundary_after_selected": boundary_selected["after"],
        "multi_user_burst_bucket_count": sum(1 for bucket in bucket_rows if bucket_distinct_users(bucket) >= 2),
        "selected_multi_user_burst_bucket_count": len({row.burst_bucket for row in selected if bucket_distinct_users(row.burst_bucket) >= 2}),
        "legacy_selected_count": len(legacy_rows),
        "estimated_request_chars": sum(len(row["msg"]) for row in output_rows),
        "identifiers_emitted": False,
        "network_calls": 0,
        "llm_calls": 0,
    }
    return SemanticChatSample(rows=output_rows, stats=stats)
