"""Optional PageIndex hierarchy with revision-bound original-source addresses.

Headings are caller-authored interpretations. Tree construction consumes no
models and never marks raw text as read, claims coverage or creates cut clocks.
"""

from __future__ import annotations

from copy import deepcopy

import json

from pathlib import Path

from ._pageindex.markdown import (build_tree_from_nodes, extract_node_text_content,
                                 extract_nodes_from_markdown)

from .contracts import EvidenceError, canonical_json, digest, file_hash

from .index import atomic_json, build_lock

PROFILE = "pageindex_heading_map_v1"

UPSTREAM = "6d23caf416858f2ca136840305d1f479a86f6ef7"

def attach_search_nodes(service, result, *, max_bytes=12000):
    """Exact membership only: titles never gate the independent source search."""
    hierarchy = getattr(service, 'hierarchy', None)
    if hierarchy is None:
        return result
    projection = dict(hierarchy_projection_hash=hierarchy['projection_hash'])
    if len(canonical_json(dict(result,**projection)).encode('utf-8')) > max_bytes:
        return result
    result.update(projection)
    for hit in [*result['hits'],*result.get('unlocated_hits',[])]:
        records = {m['record_id'] for m in hit['evidence_members']}
        nodes = [n for n in hierarchy['nodes'] if records.intersection(n['record_ids'])]
        linked=[]
        for n in nodes[:3]:
            matched = sorted(records.intersection(n['record_ids']))
            node = dict(node_id=n['node_id'],title=n['title'],parents=n['parents'],
            matched_record_ids=matched[:8],entry_refs_omitted=len(matched)>8,
            projection_hash=hierarchy['projection_hash'],derived=True,coverage_complete=False,
            next_action=dict(action='read_evidence',args=dict(record_ids=matched[:8],
                families=[hit['family']],context_cues=0,context_order='source_order')))
            trial=dict(hit,hierarchy_nodes=[*linked,node],hierarchy_nodes_omitted=True,
                hierarchy_membership_scope='explicit_entry_refs_only_not_chapter_coverage')
            original=dict(hit);hit.update(trial)
            if len(canonical_json(result).encode('utf-8')) > max_bytes:
                hit.clear();hit.update(original)
                break
            linked.append(node)
        if linked:
            hit['hierarchy_nodes_omitted'] = len(linked)<len(nodes)
    return result

def prepare_hierarchy(service, config):
    if (not isinstance(config, dict) or set(config) != {"profile", "world_revision", "sections"}
            or config["profile"] != PROFILE or config["world_revision"] != service.world_revision):
        raise EvidenceError("hierarchy_world_or_profile_mismatch")
    sections = config["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 1000:
        raise EvidenceError("hierarchy_sections_required")
    lines, addresses, previous = [], [], 0
    with service.open() as db:
        for section in sections:
            if not isinstance(section, dict) or set(section) != {"title", "level", "record_ids"}:
                raise EvidenceError("hierarchy_section_invalid")
            title, level, refs = section["title"], section["level"], section["record_ids"]
            if (not isinstance(title, str) or not title.strip() or len(title) > 300 or
                    any(c in title for c in "\r\n") or type(level) is not int or
                    not 1 <= level <= min(6, previous + 1) or not isinstance(refs, list) or
                    not 1 <= len(refs) <= 128 or any(not isinstance(r, str) for r in refs) or
                    len(set(refs)) != len(refs)):
                raise EvidenceError("hierarchy_section_invalid")
            families = set()
            for record_id in refs:
                row = db.execute("SELECT family FROM records WHERE record_id=?", (record_id,)).fetchone()
                if not row:
                    raise EvidenceError("hierarchy_record_wrong_world")
                families.add(row[0])
            lines.extend(["#" * level + " " + title.strip(), "", "Original addresses: " + ",".join(refs), ""])
            addresses.append(dict(record_ids=list(refs), families=sorted(families)))
            previous = level
    markdown = "\n".join(lines)
    extracted, source_lines = extract_nodes_from_markdown(markdown)
    tree = build_tree_from_nodes(extract_node_text_content(extracted, source_lines))
    flat = []

    def visit(nodes, parents):
        for node in nodes:
            flat.append(dict(node_id=node["node_id"], title=node["title"], parents=list(parents)))
            visit(node["nodes"], [*parents, node["node_id"]])

    visit(tree, [])
    if len(flat) != len(addresses):
        raise EvidenceError("hierarchy_projection_ambiguous")
    generator = dict(upstream_revision=UPSTREAM, adapter_sha256=file_hash(__file__),
                     subset_sha256=file_hash(Path(__file__).parent / "_pageindex" / "markdown.py"))
    projection_hash = digest([markdown, generator, service.world_revision, PROFILE])
    nodes = [{**node, **address, "derived": True, "raw_read": False}
             for node, address in zip(flat, addresses)]
    result = dict(schema_version="ew.pageindex-map.v1", profile=PROFILE, upstream_revision=UPSTREAM,
                  source_id=service.source_id, world_revision=service.world_revision,
                  projection_hash=projection_hash, nodes=nodes, provider_calls=0,
                  generator_identity=generator, meaning_complete=False, coverage_complete=False)
    root = service.root / "pageindex" / projection_hash
    with build_lock(root):
        manifest = root / "manifest.json"
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8")) != result:
                raise EvidenceError("hierarchy_projection_changed")
        else:
            atomic_json(manifest, result)
    return result

def browse_hierarchy(service, *, question_id, cursor=0, limit=20, max_bytes=12000, families=None):
    hierarchy = getattr(service, "hierarchy", None)
    if hierarchy is None:
        raise EvidenceError("hierarchy_not_configured")
    if (not isinstance(question_id, str) or not question_id or type(limit) is not int or
            not 1 <= limit <= 20 or type(max_bytes) is not int or not 2000 <= max_bytes <= 128000):
        raise EvidenceError("invalid_hierarchy_browse")
    selected = service._families(families or list(service.policy["allowed"]))
    nodes = [n for n in hierarchy["nodes"] if set(n["families"]) <= set(selected)]
    binding = [service.world_revision, hierarchy["projection_hash"], question_id, selected, limit, max_bytes]
    tokens = {digest([binding, i]): i for i in range(1, len(nodes))}
    offset = 0 if type(cursor) is int and cursor == 0 else tokens.get(cursor)
    if offset is None:
        raise EvidenceError("hierarchy_cursor_mismatch")
    result = dict(schema_version="ew.pageindex-browse.v1", source_id=service.source_id,
        world_revision=service.world_revision, projection_hash=hierarchy["projection_hash"],
        nodes=[], next_cursor=None, provider_calls=0, derived=True,
        raw_read=False, meaning_complete=False, coverage_complete=False)
    index = offset
    while index < len(nodes) and len(result["nodes"]) < limit:
        node = deepcopy(nodes[index])
        node["next_action"] = dict(action="read_evidence", args=dict(record_ids=node["record_ids"],
            families=node["families"], context_cues=0, context_order="source_order"))
        trial = {**result, "nodes": [*result["nodes"], node],
                 "next_cursor": digest([binding, index + 1]) if index + 1 < len(nodes) else None}
        if len(canonical_json(trial).encode("utf-8")) > max_bytes:
            if not result["nodes"]:
                raise EvidenceError("hierarchy_page_bytes_too_small")
            break
        result = trial
        index += 1
    result["next_cursor"] = digest([binding, index]) if index < len(nodes) else None
    return result
