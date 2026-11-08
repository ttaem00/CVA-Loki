"""Bounded navigation over existing document vectors, never editorial events.

The search document prefix is reused honestly: this is document-cosine
navigation, not a new encoder run with the model's clustering task prefix.
"""

from copy import deepcopy

import json

from .contracts import EvidenceError, digest

DOCUMENT_COSINE_NAVIGATION = {
    'profile': 'document_cosine_navigation_v1',
    'algorithm': 'greedy_cosine_star_v1',
    'vector_role': 'document',
    'dimension': 768,
    'threshold': .8,
    'min_group_size': 2,
    'max_units': 30000,
    'max_pair_comparisons': 16000000,
    'comparison_batch': 1024,
}

def _occurrence(row):
    if (not isinstance(row, dict)
            or not {'unit_id','family','start_us','end_us','members_json','norm_text'} <= set(row)
            or not isinstance(row['unit_id'],str) or not row['unit_id']
            or row['family'] not in {'stt','chat'} or not isinstance(row['norm_text'],str)):
        raise EvidenceError('vector_navigation_unit')
    start,end=row['start_us'],row['end_us']
    if not ((start is None and end is None) or
            (type(start) is int and type(end) is int and 0<=start<=end)):
        raise EvidenceError('vector_navigation_clock')
    try:
        members=json.loads(row['members_json'])
    except (TypeError,ValueError) as exc:
        raise EvidenceError('vector_navigation_members') from exc
    if (not isinstance(members,list) or not members or any(
            not isinstance(member,dict)
            or set(member)!={'record_id','raw_char_start','raw_char_end','role'}
            or not isinstance(member['record_id'],str) or not member['record_id']
            or type(member['raw_char_start']) is not int or type(member['raw_char_end']) is not int
            or not 0<=member['raw_char_start']<=member['raw_char_end']
            or not isinstance(member['role'],str) or not member['role']
            for member in members)):
        raise EvidenceError('vector_navigation_members')
    return dict(unit_id=row['unit_id'],family=row['family'],start_us=start,end_us=end,
                members=deepcopy(members))

def build_navigation(rows, vectors, *, source_id, world_revision):
    """Return exact occurrences plus a PageIndex projection, with bounded work.

    A seed-star group says only that members resemble its document vector.
    Transitivity, event identity, importance, evidence and cut boundaries are
    never inferred. Uncompared units remain explicit outside grouped navigation.
    """
    import numpy as np
    policy=deepcopy(DOCUMENT_COSINE_NAVIGATION)
    if (not isinstance(source_id,str) or not source_id or not isinstance(world_revision,str)
            or not world_revision or not isinstance(rows,list) or not 1<=len(rows)<=policy['max_units']):
        raise EvidenceError('vector_navigation_input')
    occurrences=[_occurrence(row) for row in rows]
    if len({row['unit_id'] for row in rows})!=len(rows):
        raise EvidenceError('vector_navigation_duplicate_unit')
    try:
        values=np.asarray(vectors,dtype=np.float32)
    except (TypeError,ValueError) as exc:
        raise EvidenceError('vector_navigation_vectors') from exc
    if (values.shape!=(len(rows),policy['dimension']) or not np.isfinite(values).all()
            or not np.allclose(np.linalg.norm(values,axis=1),1.,atol=.005)):
        raise EvidenceError('vector_navigation_vectors')

    values=values/np.linalg.norm(values,axis=1,keepdims=True)
    order=sorted(range(len(rows)),key=lambda i:(rows[i]['start_us'] is None,
        rows[i]['start_us'] or 0,rows[i]['family'],rows[i]['unit_id']))
    remaining=set(order)
    groups=[]
    comparisons=0

    def add(kind, indices):
        members=[occurrences[i] for i in indices]
        groups.append(dict(group_id=digest([policy,kind,[m['unit_id'] for m in members]]),
            kind=kind,occurrences=members,representative_unit_id=members[0]['unit_id'],
            representative_text=rows[indices[0]]['norm_text'],
            same_event_inferred=False,original_read=False))

    for seed in order:
        if seed not in remaining:
            continue
        candidates=[i for i in order if i in remaining and i!=seed]
        matched=[seed]
        complete=True
        for offset in range(0,len(candidates),policy['comparison_batch']):
            available=policy['max_pair_comparisons']-comparisons
            batch=candidates[offset:offset+min(policy['comparison_batch'],available)]
            if not batch:
                complete=False
                break
            scores=values[batch] @ values[seed]
            comparisons+=len(batch)
            matched.extend(i for i,score in zip(batch,scores) if score>=policy['threshold'])
            if len(batch)<min(policy['comparison_batch'],len(candidates)-offset):
                complete=False
                break
        if len(matched)>=policy['min_group_size']:
            add('cosine_group',matched)
            remaining.difference_update(matched)
        elif complete:
            add('singleton',[seed])
            remaining.remove(seed)
        if not complete:
            break
    if remaining:
        add('uncompared',[i for i in order if i in remaining])

    sections=[]
    outside=[]

    def pages(title, refs):
        unique=list(dict.fromkeys(refs))


        for offset in range(0,len(unique),64):
            sections.append(dict(title=f'{title} / {offset//64+1}',level=1,
                                 record_ids=unique[offset:offset+64]))

    for number,group in enumerate(groups,1):
        refs=[member['record_id'] for occurrence in group['occurrences'] for member in occurrence['members']]
        if group['kind']=='cosine_group':
            excerpt=' '.join(group['representative_text'].split())[:160]
            pages(f'Related document expressions {number}: {excerpt}',refs)
        else:
            outside.extend(refs)
    pages('Outside grouped navigation; source originals remain available',outside)
    hierarchy=(dict(profile='pageindex_heading_map_v1',world_revision=world_revision,sections=sections)
               if len(sections)<=1000 else None)
    return dict(schema_version='ew.document-cosine-navigation.v1',profile=policy['profile'],
        source_id=source_id,world_revision=world_revision,policy=policy,groups=groups,
        input_units=len(rows),pair_comparisons=comparisons,
        grouping_complete=not remaining,hierarchy=hierarchy,
        hierarchy_status='ready' if hierarchy is not None else 'section_limit',
        hierarchy_sections_required=len(sections),outside_navigation='source_order',
        unindexed_source_coverage='outside this projection; use source-order browse',
        derived=True,original_read=False,
        meaning_complete=False,coverage_complete=False,editorial_priority_generated=False)
