"""Run-local evidence events. A read is not automatically a resolved question."""

from __future__ import annotations

import json

from .contracts import EvidenceError, digest, native_path

from .index import atomic_json, build_lock

class EvidenceState:
    def __init__(self, run_root, *, source_id, world_revision, owner):
        self.root = native_path(run_root) / "state"
        self.identity = dict(source_id=source_id, world_revision=world_revision, owner=owner)
        self.path = self.root / "state.json"

    def load(self):
        if not self.path.is_file():
            return dict(schema_version="ew.state.v1", identity=self.identity, events=[], questions={})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        if saved["identity"] != self.identity:
            raise EvidenceError("state_revision_mismatch")
        return saved

    def event(self, kind, *, question_id, payload):
        if not isinstance(question_id, str) or not question_id.strip():
            raise EvidenceError("question_id_required")
        event = dict(kind=kind, question_id=question_id, payload=payload)
        event_id = digest([self.identity, event])
        with build_lock(self.root):
            saved = self.load()
            if not any(e["event_id"] == event_id for e in saved["events"]):
                saved["events"].append(dict(event, event_id=event_id, sequence=len(saved["events"])))
                question = saved["questions"].setdefault(question_id, {"status": "unresolved", "pending": {}})
                if kind == "read_materialized":
                    read_query = payload["read_query_id"]
                    if payload.get("next_cursor"):
                        question["pending"][read_query] = payload["next_cursor"]
                    else:
                        question["pending"].pop(read_query, None)
                if kind == "question_resolved":
                    question["status"] = payload["status"]
                    question["reason"] = payload["reason"]
                    question["read_ids"] = payload["read_ids"]
                atomic_json(self.path, saved)
        return event_id

    def delivered(self, *, request_sha256, read_ids, question_id, consumer_owner=None):
        known = {e["payload"]["read_id"] for e in self.load()["events"] if e["kind"] == "read_materialized"}
        if not set(read_ids) <= known:
            raise EvidenceError("undelivered_read_reference")
        payload = dict(request_sha256=request_sha256, read_ids=read_ids)
        if consumer_owner is not None:
            if not isinstance(consumer_owner, str) or not consumer_owner.strip():
                raise EvidenceError("consumer_owner_required")
            payload["consumer_owner"] = consumer_owner
        return self.event("raw_delivered", question_id=question_id, payload=payload)

    def resolve(self, *, question_id, status, reason, read_ids):
        saved = self.load()
        known = {rid for e in saved["events"] if e["kind"] == "raw_delivered"
                 for rid in e["payload"]["read_ids"]}
        deliveries = [e for e in saved["events"] if e["kind"] == "raw_delivered"]
        current = set(deliveries[-1]["payload"]["read_ids"]) if deliveries else set()
        if status not in {"resolved", "unresolved", "budget_exhausted"} or not isinstance(reason, str) or not reason:
            raise EvidenceError("invalid_question_resolution")
        if not set(read_ids) <= known:
            raise EvidenceError("question_cites_unread_evidence")
        if not set(read_ids) <= current:
            raise EvidenceError("question_requires_current_request_raw")
        if status == "resolved" and (not read_ids or saved["questions"].get(question_id, {}).get("pending")):
            raise EvidenceError("question_has_incomplete_read")
        self.event("question_resolved", question_id=question_id,
                   payload=dict(status=status, reason=reason, read_ids=read_ids))
        return self.load()["questions"][question_id]
