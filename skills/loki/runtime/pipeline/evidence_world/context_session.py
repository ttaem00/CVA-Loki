"""Current Codex navigation in Loki's existing source flow, without model dispatch.

Open an immutable world, dispatch the same source tools, retain hypotheses, and
recall exact reads. This is context exploration, never generation authority or
an editable report. No native Codex history or producer database is inspected.
"""

from __future__ import annotations

import json

from .contracts import EvidenceError, digest

from .index import atomic_json, build_lock

ACTIONS = {"browse_evidence", "browse_chat", "search_evidence", "read_evidence"}

def _projection_contract(envelope):
    """Derived replay also binds its producer; original read memory stays valid."""
    args=envelope.get('args',{})
    if not isinstance(args,dict): return None
    action,view=envelope.get('action'),args.get('view')
    if action=='search_evidence' and isinstance(args.get('query'),dict) and 'record_ids' in args['query']:
        from . import query
        from .contracts import file_hash
        return dict(profile=query.RECORD_SCOPE_REVISION,query_revision=query.QUERY_REVISION,
                    producer_sha256=file_hash(query.__file__))
    if action=='browse_evidence' and view=='phrases':
        from . import phrase_view
        from .contracts import file_hash
        return dict(profile=phrase_view.PROFILE,producer_sha256=file_hash(phrase_view.__file__))
    if action=='browse_evidence' and view=='signals':
        from . import signal_view
        from .contracts import file_hash
        return dict(profile=signal_view.PROFILE,revision=signal_view.CATALOG_REVISION,
                    producer_sha256=file_hash(signal_view.__file__))
    return None

def act(service, envelope):
    if not isinstance(envelope, dict) or envelope.get("action") not in ACTIONS:
        raise EvidenceError("context_action_not_navigation")
    with service.open():
        pass
    if (envelope['action']=='browse_evidence' and isinstance(envelope.get('args'),dict)
            and envelope['args'].get('view') in {'hints', 'question'}):



        result = service.dispatch(envelope)
        atomic_json(service.run_root/'context-actions'/(digest([envelope,result])+'.json'),
            dict(action=envelope,result=result,result_sha256=digest(result)))
        return result
    hierarchy = (getattr(service, "hierarchy", None) or {}).get("projection_hash")
    projection=_projection_contract(envelope)
    identity=[envelope,hierarchy,*([projection] if projection else [])]
    path = service.run_root / "context-actions" / (digest(identity) + ".json")
    with build_lock(path.parent):
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["action"] != envelope or saved["result_sha256"] != digest(saved["result"]):
                raise EvidenceError("context_action_changed")
            return saved["result"]
        result = service.dispatch(envelope)
        atomic_json(path, dict(action=envelope, result=result, result_sha256=digest(result)))
        return result

def remember(service, *, question_id, text, read_ids):
    if (not isinstance(text, str) or not text.strip() or len(text) > 4000
            or not isinstance(read_ids, list) or not read_ids or len(set(read_ids)) != len(read_ids)):
        raise EvidenceError("context_note_requires_original_reads")
    with service.open():
        pass
    for read_id in read_ids:
        service.load_read(read_id)
    return service.state.event("context_working_note", question_id=question_id,
        payload=dict(text=text, read_ids=read_ids, derived=True,
                     original_evidence=False, meaning_complete=False))

def memory(service, *, query=None):
    if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 500):
        raise EvidenceError('invalid_memory_query')
    with service.open():
        pass
    notes = [e for e in service.state.load()["events"] if e["kind"] == "context_working_note"]
    for note in notes:
        event = {key:note[key] for key in ("kind", "question_id", "payload")}
        if note["event_id"] != digest([service.state.identity, event]):
            raise EvidenceError("context_note_changed")
        for read_id in note["payload"]["read_ids"]:
            service.load_read(read_id)
    if query is not None:
        terms = list(dict.fromkeys(query.casefold().split()))
        notes = [note for note in notes if any(term in note['payload']['text'].casefold() for term in terms)]
    return dict(source_id=service.source_id, world_revision=service.world_revision,
        owner=service.state.identity['owner'], notes=notes,
        retrieval_contract=service.retrieval_contract(), provider_calls=0,
        generation_authority=False, meaning_complete=False)
