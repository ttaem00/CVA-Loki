"""Derived chat orientation with exact occurrence groups and original rereads.

Sampler selections are hints only. Every distinct original wording survives
pagination, including minority replies, questions, commands and long suffixes.
"""

from collections import Counter

import json

import math

from .contracts import EvidenceError, digest, canonical_json

from .index import atomic_json

from .records import assert_asset_fresh, chat_analysis_rows, utc_ranges, select_utc_records

PROFILE = "exact_chat_groups_v2"

GROUP_COLUMNS = ["group_id", "excerpt", "excerpt_truncated", "occurrence_count", "first_sec", "last_sec",
                 "distribution_10s", "representative_sampler_hint", "reaction_hints", "original_record_id"]

def browse_chat(service, *, question_id, interval_sec=None, interval_utc_ms=None, limit=8, cursor=None, max_bytes=12000):
    service._families(["chat"])
    native = interval_utc_ms is not None
    if native:
        utc_ranges(service, [interval_utc_ms])
        if interval_sec is not None or interval_utc_ms[1]-interval_utc_ms[0] > 120000:
            raise EvidenceError("invalid_chat_view_query")
    elif service.duration is None:
        raise EvidenceError("source_clock_unverified")
    elif (not isinstance(interval_sec,list) or len(interval_sec)!=2 or
            any(type(x) not in (int,float) or not math.isfinite(x) for x in interval_sec)
            or not 0<=interval_sec[0]<interval_sec[1]<=service.duration
            or interval_sec[1]-interval_sec[0]>120):
        raise EvidenceError("invalid_chat_view_query")
    if (type(limit) is not int or not 1<=limit<=20 or
            type(max_bytes) is not int or not 3000<=max_bytes<=32000):
        raise EvidenceError("invalid_chat_view_query")
    identity=dict(profile=PROFILE,world_revision=service.world_revision,question_id=question_id,
                  interval_sec=interval_sec,limit=limit,max_bytes=max_bytes)
    if native:
        identity['interval_utc_ms'] = interval_utc_ms
    root=service.run_root/'chat-views'/digest(identity)
    offset=0
    if cursor is not None:
        if not isinstance(cursor,str) or len(cursor)!=64 or any(c not in '0123456789abcdef' for c in cursor):
            raise EvidenceError('invalid_chat_view_cursor')
        path=root/'cursors'/(cursor+'.json')
        if not path.is_file():raise EvidenceError('invalid_chat_view_cursor')
        saved=json.loads(path.read_text(encoding='utf-8'))
        if digest(saved)!=cursor or saved['identity']!=identity:raise EvidenceError('chat_view_cursor_changed')
        offset=saved['offset']
    with service.open() as db:
        records=select_utc_records(db, ['chat'], [interval_utc_ms]) if native else [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM records WHERE family='chat' AND start_us>=? AND start_us<? ORDER BY ordinal",
            (int(interval_sec[0]*1e6),int(interval_sec[1]*1e6)))]
    from ..chat_semantic_sampler import sample_semantic_chat_rows_with_stats
    from ..chat_analyzer import is_laughter_message, is_question_message, is_surprise_message
    assets=[a for a in service.assets.values() if a.family=='chat']
    analysis=list(chat_analysis_rows(service, records))
    ms_by_id={r['record_id']:r['ms'] for r in analysis}
    sampled=sample_semantic_chat_rows_with_stats(analysis,limit=12)
    hints={(r['ms'],r['msg']) for r in sampled.rows}
    groups={}
    for r in records:
        if not isinstance(r['raw_text'],str):
            continue

        groups.setdefault(r['raw_text'],[]).append(r)
    rows=[]
    for text, members in groups.items():
        representative=next((r for r in members if (ms_by_id[r['record_id']],text.strip()[:240]) in hints),members[0])
        times=[ms_by_id[r['record_id']] if native else ms_by_id[r['record_id']]/1000 for r in members]
        rows.append(dict(group_id=digest([service.world_revision,[r['record_id'] for r in members]]),
            excerpt=text[:240],excerpt_truncated=len(text)>240,occurrence_count=len(members),
            first_sec=min(times),last_sec=max(times),
            distribution_10s=[[bucket,count] for bucket,count in sorted(Counter(int(t//10)*10 for t in times).items())],
            representative_sampler_hint=(ms_by_id[representative['record_id']],text.strip()[:240]) in hints,
            reaction_hints=dict(laughter=is_laughter_message(text),question=is_question_message(text),
                                surprise=is_surprise_message(text)),
            next_action=dict(action='read_evidence',args=dict(record_ids=[representative['record_id']],
                families=['chat'],context_cues=0)),
            more_occurrences_action=dict(action='read_evidence',args=dict(
                **({'intervals_utc_ms':[interval_utc_ms]} if native else {'intervals_sec':[interval_sec]}),
                families=['chat'],context_cues=0))))
        if native:
            row=rows[-1]
            row['first_utc_ms']=row.pop('first_sec')
            row['last_utc_ms']=row.pop('last_sec')
            row.pop('distribution_10s')
            row['distribution_10s_utc_ms']=[[t,n] for t,n in sorted(Counter(int(t//10000)*10000 for t in times).items())]
    columns=[{'first_sec':'first_utc_ms','last_sec':'last_utc_ms','distribution_10s':'distribution_10s_utc_ms'}.get(k,k)
             for k in GROUP_COLUMNS] if native else GROUP_COLUMNS
    result=dict(schema_version='ew.chat_view.v1',profile=PROFILE,source_id=service.source_id,
        world_revision=service.world_revision,question_id=question_id,interval_sec=interval_sec,
        collection_status=service.family_status['chat'],total_occurrences=len(records),distinct_exact_texts=len(rows),
        non_text_occurrences=sum(not isinstance(r['raw_text'],str) for r in records),
        group_columns=columns,group_rows=[],next_cursor=None,derived=True,original_read=False,meaning_complete=False,
        sampler_hint_summary={k:sampled.stats[k] for k in ('selected_count','near_repeat_match_count','one_user_repeat_removed_count')},
        grouping='full original text equality; no near-text merging',
        read_template=dict(action='read_evidence',families=['chat'],context_cues=0,record_ids='use row.original_record_id'),
        all_occurrences_action=dict(action='read_evidence',args=dict(
            **({'intervals_utc_ms':[interval_utc_ms]} if native else {'intervals_sec':[interval_sec]}),families=['chat'],context_cues=0)))
    result['clock_domain']='utc_epoch_ms' if native else 'vod_relative_ms'
    if native:
        result['interval_utc_ms']=interval_utc_ms
    atomic_json(root/'stored-groups.json',dict(identity=identity,groups=rows,sampler_stats=sampled.stats))
    next_offset=offset
    for row in rows[offset:offset+limit]:
        projected=dict(row,original_record_id=row['next_action']['args']['record_ids'][0])
        values=[projected[k] for k in columns]
        candidate=dict(result,group_rows=result['group_rows']+[values])
        if len(canonical_json(candidate).encode('utf-8'))>max_bytes-250:break
        result['group_rows'].append(values);next_offset+=1
    if next_offset<len(rows):
        if next_offset==offset:raise EvidenceError('chat_view_page_too_small')
        token=dict(identity=identity,offset=next_offset);result['next_cursor']=digest(token)
        atomic_json(root/'cursors'/(result['next_cursor']+'.json'),token)
    result['status']='partial_page' if result['next_cursor'] else 'complete_groups' if rows else 'empty'
    for asset in assets:assert_asset_fresh(asset)
    service.state.event('chat_orientation_returned',question_id=question_id,
        payload=dict(group_ids=[r[0] for r in result['group_rows']],next_cursor=result['next_cursor'],original_read=False))
    atomic_json(root/'pages'/(digest([offset,result])+'.json'),result)
    return result
