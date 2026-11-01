"""A bounded question/anchor projection over existing raw reads and views.

No new corpus, semantic ranking, provider, or memory owner. Only the original
reader materializes evidence; notes/signals/hints remain fallible navigation.
"""

import math

from .contracts import EvidenceError, canonical_json, digest

PROFILE = 'source_question_context_v1'

def _spans(records, utc=False):
    if utc:
        return [[int(r['raw_time']['start']), int(r['raw_time']['end'])]
                for r in records if (r.get('raw_time') or {}).get('unit') == 'utc_epoch_ms']
    return [[r['time_index']['start_us']/1e6,r['time_index']['end_us']/1e6]
            for r in records if r.get('time_index')]

def _overlap(span, spans):
    return bool(span and all(type(t) in (int,float) for t in span)
                and any(span[0] < b and span[1] >= a for a,b in spans))

def browse_question(service, *, question_id, families, query, interval_sec=None,
                    interval_utc_ms=None, record_ids=None, cursor=0, max_bytes=16000):
    if (not isinstance(query,str) or not query.strip() or len(query)>500
            or type(max_bytes) is not int or not 4000 <= max_bytes <= 128000
            or sum(x is not None for x in (interval_sec,interval_utc_ms,record_ids)) != 1):
        raise EvidenceError('invalid_question_anchor')
    families=service._families(families or [f for f in ('stt','chat')
        if f in service.policy['allowed'] and service.family_status.get(f) in {'present','partial'}])
    if not set(families) <= {'stt','chat'}:
        raise EvidenceError('question_requires_text_families')
    args=dict(families=families,context_cues=0,max_bytes=max(2000,max_bytes-6000))
    if record_ids is not None:
        if not isinstance(record_ids,list) or not 1 <= len(record_ids) <= 8:
            raise EvidenceError('invalid_question_anchor')
        args.update(record_ids=record_ids,context_order='source_order')
    else:
        span=interval_sec if interval_sec is not None else interval_utc_ms
        scale=1 if interval_sec is not None else 1000
        if (not isinstance(span,list) or len(span)!=2 or
                any(type(t) not in (int,float) or not math.isfinite(t) for t in span)
                or span[0]<0 or not 0 < span[1]-span[0] <= 120*scale):
            raise EvidenceError('invalid_question_anchor')
        args['intervals_sec' if interval_sec is not None else 'intervals_utc_ms']=[span]
    if cursor != 0:
        args['cursor']=cursor
    raw=service.read(question_id=question_id,**args)
    from .signal_view import _catalog
    _,catalog=_catalog(service)
    seconds,utc=_spans(raw['records']),_spans(raw['records'],True)
    observations=[item for item in catalog['items'] if _overlap(
        item.get('interval_utc_ms') if item.get('clock_domain')=='utc_epoch_ms' else item.get('interval_sec'),
        utc if item.get('clock_domain')=='utc_epoch_ms' else seconds)]
    from .context_session import memory
    notes=memory(service,query=query)['notes']


    terms=query.casefold().split()
    owner_notes=[dict(kind='author_working_note',payload=dict(text=text,
        original_evidence=False,meaning_complete=False),relation_state='hypothesis')
        for text in getattr(service,'question_notes',[]) if any(t in text.casefold() for t in terms)]
    notes=[*notes,*owner_notes]
    hints=dict(status='not_configured',entries=[],original_read=False,coverage_complete=False)
    if getattr(service,'summary_hints_config',None):
        from .summary_hints import browse_hints
        try:
            page=browse_hints(service,question_id=question_id,families=families,limit=10,max_bytes=12000)

            hints=dict(status='current_index',entries=[row for row in page.get('ready_index', page['index'])
                if row['status']=='ready' and _overlap(
                    [row['window'].get('startMs'),row['window'].get('endMs')],utc)] if utc else [],
                next_cursor=page['next_cursor'],original_read=False,coverage_complete=False,
                source_projection_hash=page['source_projection_hash'],gaps=page['gaps'])
        except EvidenceError as exc:
            if str(exc) in {'source_revision_changed','world_revision_mismatch','wrong_source'}:
                raise
            hints.update(status='unavailable',reason=str(exc))
    next_args={k:v for k,v in args.items() if k!='cursor'}
    if raw['next_cursor']:
        next_args['cursor']=raw['next_cursor']
    nav=dict(profile=PROFILE,query=query,source_id=service.source_id,
        world_revision=service.world_revision,owner=service.state.identity['owner'],
        observations=observations[:4],signal_catalog_revision=digest(catalog),
        notes=notes[-3:],summary_hints=hints,
        materialized_read_ids=list(dict.fromkeys(e['payload']['read_id'] for e in service.state.load()['events']
            if e['kind']=='read_materialized'))[-16:],
        next_original_action=dict(action='read_evidence',args=next_args),
        independent_search_action=dict(action='search_evidence',args=dict(query=dict(text=query,mode='lexical',families=families),page=dict(limit=8))),
        omitted=dict(observations=max(0,len(observations)-4),notes=max(0,len(notes)-3),hints=0),
        meaning_complete=False,absence_claim=False,
        policy='Original page is materialized, not understood; notes/observations/hints are distinct and fallible. Materialized reads do not prove delivery or coverage.')
    stt_ids=[r['record_id'] for r in raw['records'] if r['family']=='stt']
    if stt_ids:
        nav['source_order_context_action']=dict(action='read_evidence',args=dict(
            record_ids=list(dict.fromkeys(stt_ids))[:8],families=['stt'],context_cues=2,
            context_order='source_order',max_bytes=16000))

    nav['notes']=[{**note,'payload':{**note['payload'],'text':note['payload']['text'][:512]},
                  'text_truncated':len(note['payload']['text'])>512,
                  'selection_reason':{'literal_note_terms':[t for t in terms if t in note['payload']['text'].casefold()]},
                  'relation_state':'hypothesis_requires_original_recheck'} for note in notes[-3:]]
    result={**raw,'question_navigation':nav}
    while len(canonical_json(result).encode('utf-8'))>max_bytes:
        target=next((key for key in ('hints','observations','notes')
            if (hints['entries'] if key=='hints' else nav[key])),None)
        if target is None:
            raise EvidenceError('question_page_bytes_too_small')
        (hints['entries'] if target=='hints' else nav[target]).pop()
        nav['omitted'][target]+=1
    return result
