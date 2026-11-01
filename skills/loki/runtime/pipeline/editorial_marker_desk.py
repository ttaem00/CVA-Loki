"""Source-bound projections and composition checks for the existing editorial owners.

No semantic selection happens here. Missing owner decisions remain unreviewed.
"""

from copy import deepcopy

PROFILE = "editorial_desk_v2"

FORMS = {"short", "episode", "montage", "long"}

STATUSES = {"open", "changed", "resolved", "uncertain", "abandoned"}

def active(view):
    return bool(view and view.get("profile") == PROFILE)

def text_required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "_required")

def validate_story_updates(updates, view, allowed):
    from .editorial_working_view import check_refs
    if not isinstance(updates, list):
        raise ValueError("story_thread_updates_required")
    for row in updates:
        check_refs([row.get("thread_ref")], {t["thread_ref"] for t in view["threads"]}, "story_thread")
        if row.get("status") not in STATUSES:
            raise ValueError("story_thread_status_invalid")
        text_required(row.get("meaning"), "story_thread_meaning")
        check_refs(row.get("evidence_refs"), set(allowed), "story_thread_evidence")
        if not row["evidence_refs"]:
            raise ValueError("story_thread_evidence_required")
    return deepcopy(updates)

def composition_fields(proposal, normalized, contract, evidence):
    from .editorial_working_view import check_refs
    view, groups, portfolio = contract["view"], contract["groups"], contract["portfolio"]
    by_ref = {h["hypothesis_ref"]: h for h in view["hypotheses"]}
    group_refs = proposal.get("editorial_group_refs")
    check_refs(group_refs, {g["hypothesis_ref"] for g in groups}, "composition_group")
    if not group_refs:
        raise ValueError("composition_group_required")
    required = {ref for g in groups if g["hypothesis_ref"] in group_refs for ref in g.get("editorial_hypothesis_refs", [])}
    refs = proposal.get("editorial_hypothesis_refs")
    check_refs(refs, set(by_ref), "composition_hypothesis")
    if not required <= set(refs):
        raise ValueError("composition_dropped_hypothesis")
    thread_refs = proposal.get("thread_refs")
    check_refs(thread_refs, {t["thread_ref"] for t in view["threads"]}, "composition_thread")
    expected_threads = {t for ref in refs for t in by_ref[ref].get("thread_refs", [])}
    if not expected_threads <= set(thread_refs):
        raise ValueError("composition_dropped_thread")
    form = proposal.get("composition_form")
    if view.get("local_profile"):
        from .editorial_local_leads import check_lead_refs
        check_lead_refs(proposal.get("editorial_lead_refs"),view,"composition_lead")
        for span in proposal["source_spans"]:
            check_lead_refs(span.get("editorial_lead_refs"),view,"span_lead")
            if not set(span["editorial_lead_refs"]) <= set(proposal["editorial_lead_refs"]):
                raise ValueError("span_lead_outside_composition")
    if form not in FORMS:
        raise ValueError("composition_form_required")
    beats = {b["beat_ref"]: b for ref in refs for b in by_ref[ref]["ordered_beats"]}
    realized = set()
    realized_order = []
    for span in proposal["source_spans"]:
        check_refs(span.get("editorial_beat_refs"), set(beats), "composition_beat")
        for ref in span["editorial_beat_refs"]:
            a,b = beats[ref]["window_sec"]
            if not a < span["end_sec"] or not span["start_sec"] < b:
                raise ValueError(f"composition_beat_outside_inspection_window: beat={ref}; "
                    f"inspection=[{a},{b}]; span=[{span['start_sec']},{span['end_sec']}]")
        realized.update(span["editorial_beat_refs"])
        realized_order.extend(ref for ref in span["editorial_beat_refs"] if ref not in realized_order)
    decisions = {r["hypothesis_ref"]: r["decision"] for r in portfolio}
    changed = (set(beats) - realized or realized_order != [ref for ref in beats if ref in realized]
               or any(form not in by_ref[r].get("possible_forms", []) for r in refs)
               or len(normalized["source_spans"]) == 1 and
               (form == "montage" or any(decisions.get(r) == "combine" for r in refs)
                or any("callback" in b["role"].lower() for b in beats.values())))
    change = proposal.get("composition_change")
    if changed or change:
        if not isinstance(change, dict):
            raise ValueError("composition_change_requires_original_reason")
        text_required(change.get("reason"), "composition_change_reason")
        check_refs(change.get("evidence_refs"), set(evidence), "composition_change_original")
        if not change["evidence_refs"]:
            raise ValueError("composition_change_requires_original_read")


    for span in normalized["source_spans"]:
        if view.get("local_profile"):
            span["editorial_lead_refs"] = list(dict.fromkeys(ref for raw in proposal["source_spans"]
                if raw["start_sec"] < span["end_sec"] and span["start_sec"] < raw["end_sec"]
                for ref in raw["editorial_lead_refs"]))
        span["editorial_beat_refs"] = list(dict.fromkeys(b for raw in proposal["source_spans"]
            if raw["start_sec"] < span["end_sec"] and span["start_sec"] < raw["end_sec"]
            for b in raw["editorial_beat_refs"]))
    return dict(editorial_group_refs=group_refs, editorial_hypothesis_refs=refs, thread_refs=thread_refs,
        composition_form=form, **({"editorial_lead_refs":proposal["editorial_lead_refs"]} if view.get("local_profile") else {}),
        **({"composition_change": deepcopy(change)} if change else {}))
