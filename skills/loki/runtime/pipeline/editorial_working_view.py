"""Optional working projection from the existing T2 merge, not a new owner/pass."""

from copy import deepcopy

from .evidence_world.contracts import digest

PROFILE = "broadcast_portfolio_v1"

CANDIDATE_OWNER_PROFILE = "candidate_owner_vn1"

def owner_request_view(view, *, lead_windows=None, owner_refs=None):
    """Project one compact semantic work card per existing hypothesis.

    The full working view remains the source-bound saved evidence.  This is the
    only hypothesis representation sent to VN+1 Story/discovery/author calls.
    It copies meaning and refs; it never fills missing meaning in code.
    """

    validate_view(view)
    scoped = request_view(view, lead_windows=lead_windows)
    lead_index = {
        row["lead_ref"]: row for row in scoped.get("local_leads", [])
        if isinstance(row, dict) and isinstance(row.get("lead_ref"), str)
    }
    selected_refs = None if owner_refs is None else set(owner_refs)
    cards = []
    for hypothesis in scoped.get("hypotheses", []):
        if selected_refs is not None and hypothesis["hypothesis_ref"] not in selected_refs:
            continue
        lead_refs = list(dict.fromkeys(hypothesis.get("editorial_lead_refs", [])))
        time_ranges = []
        next_read = []
        for ref in lead_refs:
            lead = lead_index.get(ref) or {}
            if isinstance(lead.get("time_range"), dict):
                time_ranges.append(deepcopy(lead["time_range"]))
            needed = lead.get("needed_context")
            if isinstance(needed, str) and needed.strip():
                next_read.append(needed.strip())
        for value in hypothesis.get("necessary_beats", []):
            if isinstance(value, str) and value.strip():
                next_read.append(value.strip())
        agenda_refs = [
            row["agenda_ref"] for row in scoped.get("attention_decisions", [])
            if isinstance(row, dict)
            and hypothesis["hypothesis_ref"] in row.get("editorial_hypothesis_refs", [])
            and isinstance(row.get("agenda_ref"), str)
        ]
        locations = {
            "observation_refs": list(hypothesis.get("source_refs", [])),
            "lead_refs": lead_refs,
            "attention_refs": list(hypothesis.get("attention_refs", [])),
            "agenda_refs": list(dict.fromkeys(agenda_refs)),
            "thread_refs": list(hypothesis.get("thread_refs", [])),
            "time_ranges": time_ranges,
        }
        card = {
            "owner_ref": hypothesis["hypothesis_ref"],
            "source_locations": locations,
            "people_goal_rules_relationship_knowledge": str(
                hypothesis.get("actor_goal_rule_relationship_knowledge") or ""
            ),
            "viewer_value_hypothesis": str(
                hypothesis.get("why_watch") or hypothesis.get("viewing_promise") or ""
            ),
            "uncertainty_and_counterevidence": {
                "missing": str(hypothesis.get("missing") or ""),
                "counterevidence": str(hypothesis.get("counter_evidence") or ""),
            },
            "next_read": list(dict.fromkeys(next_read)),
            "ordered_beats": deepcopy(hypothesis.get("ordered_beats", [])),
            "possible_forms": list(hypothesis.get("possible_forms", [])),
            "composition_decision": {
                "action": "open",
                "reason": "Requires the candidate owner to test originals",
                "evidence_refs": [],
            },
        }
        cards.append(card)
    body = {
        "profile": CANDIDATE_OWNER_PROFILE,
        "authority": (
            "One candidate editor owns original reads, hypothesis revision or rejection, "
            "ordered spans, title and description. Every claim returns to source identity."
        ),
        "source_identity": {
            "video_no": str(view.get("video_no") or ""),
            "outline_revision": str(view.get("outline_sha256") or ""),
            "observation_revision": str(view.get("observations_sha256") or ""),
        },
        "cards": cards,
    }
    body["revision"] = digest(body)
    return body

def validate_owner_request(value, view):
    refs = {
        row.get("owner_ref") for row in value.get("cards", [])
        if isinstance(row, dict) and isinstance(row.get("owner_ref"), str)
    } if isinstance(value, dict) else set()
    expected = owner_request_view(view, owner_refs=refs)
    if value != expected:
        raise ValueError("candidate_owner_projection_changed")
    return value

def check_refs(refs, known, label):
    if (not isinstance(refs, list) or any(not isinstance(r, str) for r in refs)
            or len(refs) != len(set(refs)) or not set(refs) <= known):
        raise ValueError(label + "_unknown_or_duplicate_refs")

def validate_view(view, *, outline_text=None, observations=None, video_no=None):
    from .editorial_marker_desk import PROFILE as DESK_PROFILE
    if (view.get("profile") not in {PROFILE, DESK_PROFILE} or view.get("sha256") != digest({k:v for k,v in view.items() if k != "sha256"})
            or outline_text is not None and view.get("outline_sha256") != digest(outline_text)
            or observations is not None and view.get("observations_sha256") != digest(observations)
            or video_no is not None and view.get("video_no") != str(video_no)):
        raise ValueError("editorial_working_view_source_or_revision_mismatch")

def request_view(view, *, lead_windows=None):
    validate_view(view)
    from .editorial_marker_desk import active
    omitted = {"invalid_items", "observation_refs"}
    if active(view):
        omitted.update({"relation_events", "observations_sha256", "outline_sha256", "sha256"})
    result = {k:deepcopy(v) for k,v in view.items() if k not in omitted}
    if active(view):
        grouped = {}
        for row in result["marker_dispositions"]:
            body = {k:v for k,v in row.items() if k != "marker_ref"}
            grouped.setdefault(digest(body), dict(body, marker_refs=[]))["marker_refs"].append(row["marker_ref"])
        result["marker_dispositions"] = list(grouped.values())
    if view.get("local_profile"):
        from .editorial_local_leads import request_catalog
        result = request_catalog(result)
        if lead_windows is not None:


            dependencies = {ref for h in view["hypotheses"] for ref in h.get("editorial_lead_refs", [])}
            result["local_leads"] = [lead for lead in result["local_leads"]
                if lead["lead_ref"] in dependencies or any(
                    a <= lead["time_range"]["end_sec"] and lead["time_range"]["start_sec"] <= b
                    for a,b in lead_windows)]
            visible = {lead["lead_ref"] for lead in result["local_leads"]}
            result["lead_dispositions"] = [dict(row,lead_refs=[ref for ref in row["lead_refs"] if ref in visible])
                for row in result["lead_dispositions"] if visible.intersection(row["lead_refs"])]
    return result
