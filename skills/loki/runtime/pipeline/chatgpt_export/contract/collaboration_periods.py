"""Optional, explicit VOD-relative collaborator intervals; no identity inference."""

from copy import deepcopy

import math

import re

import unicodedata

import json

OUTLINE_COLLABORATION_HEADING = "[시점별 합방 멤버]"

MAX_PERIOD_LINKS = 4096

def parse_outline_collaboration_periods(text, duration_sec=None, *, allowed_evidence_refs=None,
                                        recover=False, diagnostics=None):
    """Read the optional native finalizer lane using the existing wire contract.

    Missing/empty means unknown, never a whole-VOD declaration. Evidence refs
    address the preserved observations delivered to the finalizer; they are
    not a claim of independently verified attendance or watched media.
    """
    if OUTLINE_COLLABORATION_HEADING not in text:
        return None
    def invalid_section(code):
        if not recover:
            raise ValueError(code)
        if diagnostics is not None:
            diagnostics.append({"code": "period_invalid"})
        return []
    if text.count(OUTLINE_COLLABORATION_HEADING) != 1:
        return invalid_section("collaboration_outline_heading_duplicate")
    start = text.index(OUTLINE_COLLABORATION_HEADING)
    try:
        in_order = text.index("[불확실하거나 빠진 부분]") < start < text.index("[실제 사용한 정보]")
    except ValueError:
        in_order = False
    if not in_order:
        return invalid_section("collaboration_outline_heading_order")
    body = text[start + len(OUTLINE_COLLABORATION_HEADING):text.index("[실제 사용한 정보]")].strip()
    if body in {"없음", "미확인"}:
        return []
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("collaboration_outline_duplicate_key")
            result[key] = value
        return result
    try:
        rows = json.loads(body, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        if not recover:
            raise
        if diagnostics is not None:
            diagnostics.append({"code": "period_invalid"})
        return []


    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("source_kind") not in (None, "source_evidence") or row.get("support_status") not in (None, "provisional"):
            raise ValueError("collaboration_outline_cannot_claim_manager_or_verified_authority")
        refs = row.get("evidence_refs", [])
        if allowed_evidence_refs is not None and isinstance(refs, list) and any(
                isinstance(ref, str) and ref not in allowed_evidence_refs for ref in refs):
            raise ValueError("collaboration_outline_unknown_observation")
    if recover:
        projection = recover_collaboration_periods(rows, duration_sec)
        rows = projection["periods"]
        if diagnostics is not None:
            diagnostics.extend(projection["diagnostics"])
    else:
        validate_collaboration_periods(rows, duration_sec)
    for row in rows:
        if row["source_kind"] != "source_evidence" or row["support_status"] != "provisional":
            raise ValueError("collaboration_outline_cannot_claim_manager_or_verified_authority")
        if allowed_evidence_refs is not None and not set(row["evidence_refs"]) <= set(allowed_evidence_refs):
            raise ValueError("collaboration_outline_unknown_observation")
    return rows

def validate_collaboration_periods(periods, duration_sec=None, *, require_evidence_refs=True):
    if not isinstance(periods, list) or len(periods) > 128:
        raise ValueError("collaboration_periods_must_be_bounded_array")
    if duration_sec is not None and (
        isinstance(duration_sec, bool) or not isinstance(duration_sec, (int, float))
        or not math.isfinite(duration_sec) or duration_sec < 0
    ):
        raise ValueError("collaboration_duration_invalid")
    seen = set()
    previous_end = 0
    allowed = {"id", "start_sec", "end_sec", "members", "source_kind", "support_status", "evidence_refs", "member_vods"}
    required = allowed - {"evidence_refs", "member_vods"}
    for row in periods:
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - allowed:
            raise ValueError("collaboration_period_shape_invalid")
        key = row["id"]
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", key) or key in seen:
            raise ValueError("collaboration_period_id_invalid")
        seen.add(key)
        start, end = row["start_sec"], row["end_sec"]
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (start, end)):
            raise ValueError("collaboration_period_time_invalid")
        if start < previous_end or end <= start or (duration_sec is not None and end > duration_sec):
            raise ValueError("collaboration_period_range_invalid")
        previous_end = end
        members = row["members"]
        if not isinstance(members, list) or len(members) > 32:
            raise ValueError("collaboration_members_invalid")
        normalized = set()
        for name in members:
            if not isinstance(name, str) or not name or name != name.strip() or len(name) > 64 or any(unicodedata.category(c).startswith("C") for c in name):
                raise ValueError("collaboration_member_name_invalid")
            identity = unicodedata.normalize("NFKC", name).lower()
            if identity in normalized:
                raise ValueError("collaboration_member_name_duplicate")
            normalized.add(identity)
        links = row.get("member_vods", [])
        if not isinstance(links, list) or len(links) > MAX_PERIOD_LINKS:
            raise ValueError("collaboration_member_vods_invalid")
        linked_pairs = set()
        linked_vods = set()
        linked_channels = {}
        for link in links:
            if not isinstance(link, dict) or not {"member_name", "vod_no"} <= link.keys() or link.keys() - {"member_name", "vod_no", "channel_id"}:
                raise ValueError("collaboration_member_vod_shape_invalid")
            name, vod = link["member_name"], link["vod_no"]
            if not isinstance(name, str) or name not in members:
                raise ValueError("collaboration_member_vod_member_invalid")
            if not isinstance(vod, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", vod) or vod in linked_vods:
                raise ValueError("collaboration_member_vod_number_invalid")
            if "channel_id" in link and (not isinstance(link["channel_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", link["channel_id"])):
                raise ValueError("collaboration_member_vod_channel_invalid")
            pair = (name, vod)
            if pair in linked_pairs:
                raise ValueError("collaboration_member_vod_duplicate")
            channel = link.get("channel_id")
            if channel and name in linked_channels and linked_channels[name] != channel:
                raise ValueError("collaboration_member_vod_channel_conflict")
            if channel:
                linked_channels[name] = channel
            linked_pairs.add(pair)
            linked_vods.add(vod)
        if row["source_kind"] not in ("manager_declared", "source_evidence") or row["support_status"] not in ("supported", "provisional"):
            raise ValueError("collaboration_period_provenance_invalid")
        refs = row.get("evidence_refs", [])
        if not isinstance(refs, list) or len(refs) > 128 or any(not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", ref) for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError("collaboration_period_evidence_invalid")
        if require_evidence_refs and row["source_kind"] == "source_evidence" and not refs:
            raise ValueError("collaboration_period_evidence_required")
    return periods

def public_collaboration_periods(periods, duration_sec=None):
    projection = recover_collaboration_periods(periods, duration_sec, require_evidence_refs=False)
    return [{key: deepcopy(value) for key, value in row.items() if key != "evidence_refs"}
            for row in projection["periods"]]

def recover_collaboration_periods(periods, duration_sec=None, *, require_evidence_refs=True):
    """Project an optional lane without changing raw input or unrelated rows.

    Only outer whitespace and safe integer VOD numbers are normalized. Invalid
    links are isolated; contradictory identities and ranges quarantine every
    involved row rather than choosing the first claim. Canonical save/authority
    validation stays strict and operates on this reviewed projection.
    """
    diagnostics = []
    def note(code, index=None, link_index=None):
        item = {"code": code}
        if index is not None:
            item["period_index"] = index
        if link_index is not None:
            item["link_index"] = link_index
        diagnostics.append(item)
    if not isinstance(periods, list) or len(periods) > 128:
        note("period_invalid")
        return {"periods": [], "diagnostics": diagnostics}

    validate_collaboration_periods([], duration_sec, require_evidence_refs=require_evidence_refs)
    candidates = []
    for index, raw in enumerate(periods):
        if not isinstance(raw, dict):
            note("period_invalid", index)
            continue
        row = deepcopy(raw)
        for key in ("id",):
            if isinstance(row.get(key), str):
                row[key] = row[key].strip()
        if isinstance(row.get("members"), list):
            row["members"] = [name.strip() if isinstance(name, str) else name for name in row["members"]]
        links = row.get("member_vods", [])
        valid_links = []
        if not isinstance(links, list) or len(links) > MAX_PERIOD_LINKS:
            note("member_vod_invalid", index)
            links = []
        for link_index, original in enumerate(links):
            if not isinstance(original, dict):
                note("member_vod_invalid", index, link_index)
                continue
            link = deepcopy(original)
            for key in ("member_name", "vod_no", "channel_id"):
                if isinstance(link.get(key), str):
                    link[key] = link[key].strip()
            vod = link.get("vod_no")
            if (isinstance(vod, (int, float)) and not isinstance(vod, bool)
                    and 0 < vod <= 9007199254740991 and math.isfinite(vod) and vod == int(vod)):
                link["vod_no"] = str(int(vod))
            try:


                probe = {"id": "probe", "start_sec": 0, "end_sec": 1,
                         "members": row.get("members"), "source_kind": "manager_declared",
                         "support_status": "provisional", "member_vods": [link]}
                validate_collaboration_periods([probe])
            except (ValueError, TypeError):
                note("member_vod_invalid", index, link_index)
                continue
            if link != original:
                note("normalization_applied", index, link_index)
            valid_links.append((link_index, link))
        duplicates, seen_links, vod_claims, member_channels = set(), {}, {}, {}
        for pos, (_, link) in enumerate(valid_links):
            signature = tuple(sorted(link.items()))
            if signature in seen_links:
                duplicates.add(pos)
            else:
                seen_links[signature] = pos
            vod_claims.setdefault(link["vod_no"], set()).add(signature)
            if link.get("channel_id"):
                member_channels.setdefault(link["member_name"], set()).add(link["channel_id"])

        bad_members = {name for name, channels in member_channels.items() if len(channels) > 1}
        bad_vods = {vod for vod, claims in vod_claims.items() if len(claims) > 1}
        for pos, (link_index, link) in enumerate(valid_links):
            if link["member_name"] in bad_members or link["vod_no"] in bad_vods:
                note("member_vod_conflict", index, link_index)
            elif pos in duplicates:
                note("member_vod_duplicate", index, link_index)
        row["member_vods"] = [link for pos, (_, link) in enumerate(valid_links)
                              if pos not in duplicates and link["member_name"] not in bad_members
                              and link["vod_no"] not in bad_vods]
        if "member_vods" not in raw:
            row.pop("member_vods", None)
        if any(row.get(key) != raw.get(key) for key in ("id", "members")):
            note("normalization_applied", index)
        try:
            validate_collaboration_periods([row], duration_sec, require_evidence_refs=require_evidence_refs)
        except (ValueError, TypeError):
            note("period_invalid", index)
            continue
        candidates.append((index, row))
    conflicts, duplicates = set(), set()
    for pos, (index, row) in enumerate(candidates):
        for other_pos, (_, other) in enumerate(candidates[:pos]):
            if row == other:
                duplicates.add(pos)
            elif row["id"] == other["id"] or max(row["start_sec"], other["start_sec"]) < min(row["end_sec"], other["end_sec"]):
                conflicts.update((pos, other_pos))
    for pos, (index, _) in enumerate(candidates):
        if pos in conflicts:
            note("period_conflict", index)
        elif pos in duplicates:
            note("period_duplicate", index)
    accepted = [row for pos, (_, row) in enumerate(candidates) if pos not in conflicts | duplicates]
    ordered = sorted(accepted, key=lambda row: (row["start_sec"], row["end_sec"], row["id"]))
    if ordered != accepted:
        note("normalization_applied")
    return {"periods": ordered, "diagnostics": diagnostics}
