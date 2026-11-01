"""Local projections for the existing T2 owner; no new semantic owner or ranker."""

from copy import deepcopy

from .evidence_world.contracts import digest

PROFILE = "chunk_local_v1"

def request_catalog(catalog):
    result=deepcopy(catalog)
    if result.get("local_profile")!=PROFILE:
        return result
    result.pop("local_lead_issues",None)
    result.pop("local_relation_issues",None)
    result.pop("disposition_issues",None)
    for key in ('attention_receipts','attention_issues','attention_selection_issues'):
        result.pop(key,None)
    if "lead_dispositions" in result:
        grouped={}
        for row in result["lead_dispositions"]:
            body={k:v for k,v in row.items() if k!="lead_ref"}
            grouped.setdefault(digest(body),dict(body,lead_refs=[]))["lead_refs"].append(row["lead_ref"])
        result["lead_dispositions"]=list(grouped.values())
    for lead in result["local_leads"]:
        lead.pop("source_occurrences",None)
        lead.pop("source_revision",None)


    result["markers"]=[{k:v for k,v in r.items() if k in {"marker_ref","family","time_hint","routing_status"}
        or k=="hint" and r["routing_status"]!="delivered"} for r in result["markers"]]
    return result

def check_lead_refs(refs, view, label):
    from .editorial_working_view import check_refs
    check_refs(refs,{r["lead_ref"] for r in view.get("local_leads",[])},label)
