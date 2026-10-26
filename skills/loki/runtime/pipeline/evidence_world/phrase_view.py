"""Bounded source phrases with native character spans and lexical contrast.

These are search hints, not inferred salient events, topics or editorial value.
Exact repeats affect occurrence counts, not independent votes. Statistical
windows are navigation scaffolding, never scene boundaries or cluster quotas.
"""

from collections import defaultdict

import json

import math

from pathlib import Path

import re

from .contracts import EvidenceError, canonical_json, digest, file_hash

from .index import atomic_json, build_lock, gram_tokens, normalize

from .records import assert_asset_fresh, utc_ranges, select_utc_records

PROFILE = 'source_surface_phrases_v1'

TOKEN = re.compile(r'[^\W_]+', re.UNICODE)

REACTION_ONLY = re.compile(r'[ㅋㅎㅠㅜ]+')

EMOTES_ONLY = re.compile(r'(?:\s*\{:[^{}]+:\}\s*)+')

WINDOW_MS = 120000

def _fresh(service):
    for asset in service.assets.values():
        assert_asset_fresh(asset)

def _phrases(text):
    if not isinstance(text,str) or EMOTES_ONLY.fullmatch(text):
        return
    words=list(TOKEN.finditer(text))
    for i in range(len(words)):
        for size in (1,2,3):
            group=words[i:i+size]
            if len(group)!=size or any(REACTION_ONLY.fullmatch(m.group()) for m in group):
                continue
            if any(not text[a.end():b.start()].isspace() for a,b in zip(group,group[1:])):
                continue
            start,end=group[0].start(),group[-1].end()
            surface=text[start:end]
            if len(surface)<2 or not any(c.isalpha() for c in surface):
                continue
            yield (' '.join(m.group().casefold() for m in group),start,end)

def _window(record):
    if record['time_index']:
        return ('vod_relative_ms',record['family'],record['time_index']['start_us']//(WINDOW_MS*1000))
    time=record.get('raw_time') or {}
    if time.get('unit')=='utc_epoch_ms' and type(time.get('start')) is int and time['start']>=0:
        return ('utc_epoch_ms',record['family'],time['start']//WINDOW_MS)
    return ('source_order',record['family'],record['asset_id'],record['ordinal']//12)

def _scope(service, families, interval_sec, interval_utc_ms, record_ids):
    if sum(x is not None for x in (interval_sec,interval_utc_ms,record_ids)) != 1:
        raise EvidenceError('phrase_scope_required')
    with service.open() as db:
        if record_ids is not None:
            if not isinstance(record_ids,list) or not 1<=len(record_ids)<=32 or any(not isinstance(r,str) for r in record_ids):
                raise EvidenceError('invalid_phrase_records')
            records=[]
            for rid in dict.fromkeys(record_ids):
                row=db.execute('SELECT payload_json FROM records WHERE record_id=?',(rid,)).fetchone()
                if row is None: raise EvidenceError('unknown_or_wrong_source_record')
                record=json.loads(row[0])
                if record['family'] not in families: raise EvidenceError('record_family_mismatch')
                records.append(record)
            return records
        if interval_utc_ms is not None:
            utc_ranges(service,[interval_utc_ms])
            if interval_utc_ms[1]-interval_utc_ms[0]>WINDOW_MS:
                raise EvidenceError('phrase_scope_too_large')
            return select_utc_records(db,families,[interval_utc_ms])
        if service.duration is None: raise EvidenceError('source_clock_unverified')
        if (not isinstance(interval_sec,list) or len(interval_sec)!=2 or
            any(type(t) not in (int,float) or not math.isfinite(t) for t in interval_sec) or
            not 0<=interval_sec[0]<interval_sec[1]<=service.duration or interval_sec[1]-interval_sec[0]>120):
            raise EvidenceError('invalid_phrase_interval')
        records=[]
        for family in families:
            records.extend(json.loads(row[0]) for row in db.execute(
                "SELECT payload_json FROM records WHERE family=? AND "
                "((time_kind='instant' AND start_us>=? AND start_us<?) OR "
                "(time_kind='interval' AND start_us<? AND end_us>?)) ORDER BY ordinal",
                (family,int(interval_sec[0]*1e6),int(interval_sec[1]*1e6),int(interval_sec[1]*1e6),int(interval_sec[0]*1e6))))
        return records

def _background_scope(service, identity, family, wanted):
    """Reuse small revision-bound window statistics, never a second n-gram index."""
    key={k:identity[k] for k in ('profile','world_revision','source_id','producer_sha256')}
    key['family']=family
    root=service.run_root/'phrase-background'/digest(key)
    path=root/'catalog.json'
    def load():
        try:
            saved=json.loads(path.read_text(encoding='utf8'))
            if saved['identity']!=key or saved['sha256']!=digest(saved['stats']):
                raise ValueError
            return saved['stats']
        except (OSError,ValueError,KeyError,TypeError):
            raise EvidenceError('phrase_background_changed') from None
    if path.is_file():
        return load(),None
    with build_lock(root):
        if path.is_file():
            return load(),None
        windows=set();unlocated=0;exceptions=defaultdict(list);background=defaultdict(set)
        with service.open() as db:
            for row in db.execute('SELECT payload_json FROM records WHERE family=? ORDER BY ordinal',(family,)):
                record=json.loads(row[0]);window=_window(record)
                windows.add(window)
                unlocated+=window[0]=='source_order'
                text=record['raw_text']
                if isinstance(text,str):
                    raw_words=TOKEN.findall(text.casefold())


                    indexed_words=set(TOKEN.findall(normalize(text)))
                    for word in set(raw_words):
                        if normalize(word) not in indexed_words:
                            exceptions[word].append(record['record_id'])
                    policy=service.manifest['identity']['representation']
                    if len(text)>policy['max_chars'] and re.search(r'\s{'+str(max(1,policy['fragment_overlap']-3))+r',}',text):
                        for word in set(raw_words):exceptions[word].append(record['record_id'])


                for term,_,_ in _phrases(text):
                    if term in wanted:background[term].add(window)
        stats=dict(background_windows=len(windows),unlocated_records=unlocated,
                   index_exception_words=dict(exceptions))
        _fresh(service)
        atomic_json(path,dict(identity=key,stats=stats,sha256=digest(stats)))
        return stats,background

def _anchor(term, policy):




    if policy['fragment_overlap']<max(policy['grams']):
        return None
    for word in sorted(term.split(),key=len,reverse=True):
        tokens=gram_tokens(word,policy)
        if tokens:
            return tokens[-1]
    tokens=gram_tokens(term,policy)
    return tokens[-1] if tokens else None

def _background(service, identity, occurrences):
    background=defaultdict(set);stats={}
    policy=service.manifest['identity']['representation']
    with service.open() as db:
        for family in identity['families']:
            wanted={term for f,term in occurrences if f==family}
            if not wanted:
                continue
            stats[family],cold=_background_scope(service,identity,family,wanted)
            if cold is not None:
                for term,windows in cold.items():background[(family,term)]=windows
                continue
            anchors={_anchor(term,policy) for term in wanted}
            if None in anchors:

                rows=db.execute('SELECT payload_json FROM records WHERE family=? ORDER BY ordinal',(family,))
                candidates=(json.loads(row[0]) for row in rows)
            else:
                exceptions=stats[family]['index_exception_words']
                ids={rid for term in wanted for word in term.split() for rid in exceptions.get(word,())}
                anchors=sorted(anchors)
                for start in range(0,len(anchors),64):
                    match=' OR '.join('"'+token+'"' for token in anchors[start:start+64])
                    for row in db.execute('SELECT u.members_json FROM gram_fts JOIN units u '
                            'ON u.unit_pk=gram_fts.rowid WHERE gram_fts MATCH ? AND u.family=?',(match,family)):
                        ids.update(m['record_id'] for m in json.loads(row[0]))
                def originals():
                    ordered=sorted(ids)
                    for start in range(0,len(ordered),500):
                        batch=ordered[start:start+500]
                        for row in db.execute('SELECT payload_json FROM records WHERE family=? AND record_id IN ('+
                                ','.join('?' for _ in batch)+')',(family,*batch)):
                            yield json.loads(row[0])
                candidates=originals()
            for record in candidates:
                window=_window(record)
                for term,_,_ in _phrases(record['raw_text']):
                    if term in wanted:
                        background[(family,term)].add(window)
    return background,stats

def _catalog(service, identity, selected):
    root=service.run_root/'phrase-views'/digest(identity)
    path=root/'catalog.json'
    if path.is_file():
        try:
            saved=json.loads(path.read_text(encoding='utf8'))
            if saved['sha256']!=digest(saved['catalog']) or saved['identity']!=identity:
                raise ValueError
        except (OSError,ValueError,KeyError,TypeError):
            raise EvidenceError('phrase_catalog_changed') from None
        return root,saved['catalog']
    occurrences=defaultdict(list)
    local_texts=defaultdict(set)
    local_counts=defaultdict(set)
    for record in selected:
        text=record['raw_text']
        if not isinstance(text,str): continue
        family=record['family']; text_hash=digest(text)
        local_texts[family].add(text_hash)
        for term,start,end in _phrases(text):
            key=(family,term)
            local_counts[key].add(text_hash)
            occurrences[key].append(dict(record_id=record['record_id'],raw_char_start=start,raw_char_end=end,
                surface=text[start:end],native_locator=record['native_locator'],raw_time=record['raw_time'],
                time_index=record['time_index'],clock_status=record['clock_status']))
    background=defaultdict(set);stats={}
    if occurrences:
        background,stats=_background(service,identity,occurrences)
    terms=[]
    for (family,term),origins in occurrences.items():
        df=len(background[(family,term)]); n=stats[family]['background_windows']
        local_fraction=len(local_counts[(family,term)])/max(1,len(local_texts[family]))
        specificity=local_fraction*math.log((n+1)/(df+1))
        first=origins[0]
        terms.append(dict(term_id=digest([identity,family,term]),family=family,
            surface=first['surface'][:240],surface_truncated=len(first['surface'])>240,
            original_occurrences=len(origins),distinct_local_original_texts=len(local_counts[(family,term)]),
            lexical_contrast=dict(local_text_fraction=local_fraction,background_windows=n,
                windows_with_expression=df,window_specificity=specificity,
                method='unique local original-text fraction times smoothed inverse window frequency',
                window_ms=WINDOW_MS,source_order_window_records=12,unlocated_records=stats[family]['unlocated_records']),
            original_refs=[{k:v for k,v in o.items() if k!='surface'} for o in origins[:6]],
            original_refs_truncated=len(origins)>6,
            next_action=dict(action='read_evidence',args=dict(record_ids=list(dict.fromkeys(o['record_id'] for o in origins[:6])),
                families=[family],context_cues=0)),
            search_other_occurrences_action=dict(action='search_evidence',args=dict(query=dict(text=first['surface'][:240],
                mode='exact',families=[family],include_unlocated=True))),
            event_relation='UNVERIFIED; repeated expression does not establish same event, speaker or callback'))
    terms.sort(key=lambda t:(-t['lexical_contrast']['window_specificity'],-len(t['surface']),t['term_id']))
    catalog=dict(terms=terms,selected_original_records=len(selected),
        selected_nontext_records=sum(not isinstance(r['raw_text'],str) for r in selected),
        original_read=False,meaning_complete=False,coverage_complete=False,
        background_record_scope='all policy-selected STT/chat originals; time windows or explicit source-order scaffolding')
    _fresh(service)
    atomic_json(path,dict(identity=identity,catalog=catalog,sha256=digest(catalog)))
    return root,catalog

def browse_phrases(service, *, question_id, families=None, interval_sec=None, interval_utc_ms=None,
                   record_ids=None,cursor=0,limit=8,max_bytes=12000):
    if type(limit) is not int or not 1<=limit<=20 or type(max_bytes) is not int or not 3000<=max_bytes<=32000:
        raise EvidenceError('invalid_phrase_page_budget')
    families=service._families(families or list(service.policy['required']))
    if not set(families)<={'stt','chat'}: raise EvidenceError('phrase_family_unsupported')
    _fresh(service)
    selected=_scope(service,families,interval_sec,interval_utc_ms,record_ids)
    identity=dict(profile=PROFILE,world_revision=service.world_revision,source_id=service.source_id,
        families=families,interval_sec=interval_sec,interval_utc_ms=interval_utc_ms,record_ids=record_ids,
        producer_sha256=file_hash(Path(__file__)))
    root,catalog=_catalog(service,identity,selected)
    page_identity=dict(identity,question_id=question_id,limit=limit,max_bytes=max_bytes,catalog_sha256=digest(catalog))
    offset=0
    if cursor:
        if not isinstance(cursor,str) or not re.fullmatch(r'[0-9a-f]{64}',cursor): raise EvidenceError('invalid_phrase_cursor')
        try: saved=json.loads((root/'cursors'/(cursor+'.json')).read_text(encoding='utf8'))
        except (OSError,ValueError): raise EvidenceError('unknown_phrase_cursor') from None
        if digest(saved)!=cursor or saved['identity']!=page_identity: raise EvidenceError('phrase_cursor_changed')
        offset=saved['offset']
    page=dict(schema_version='ew.phrase_page.v1',profile=PROFILE,source_id=service.source_id,
        world_revision=service.world_revision,question_id=question_id,interval_sec=interval_sec,interval_utc_ms=interval_utc_ms,
        record_ids=record_ids,selected_original_records=catalog['selected_original_records'],total_terms=len(catalog['terms']),
        terms=[],next_cursor=None,original_read=False,meaning_complete=False,coverage_complete=False,
        policy='Surface expressions with original character spans; lexical specificity is not salience, meaning or editing value. '
               'Exact repeats are occurrences, not independent votes. Same expression may refer to different events. '
               'Topics/events/callback relations require the same writer to read originals; quiet originals and independent search remain available.')
    next_offset=offset
    def response(terms, position):
        result=dict(page,terms=terms)
        if position<len(catalog['terms']):
            token=dict(identity=page_identity,offset=position)
            result['next_cursor']=digest(token)
            result['next_action']=dict(action='browse_evidence',args=dict(view='phrases',families=families,
                **({'record_ids':record_ids} if record_ids is not None else {'interval_utc_ms':interval_utc_ms} if interval_utc_ms is not None else {'interval_sec':interval_sec}),
                cursor=result['next_cursor'],limit=limit,max_bytes=max_bytes))
        result['status']='partial_page' if result['next_cursor'] else 'complete_phrases' if catalog['terms'] else 'empty'
        return result
    for term in catalog['terms'][offset:offset+limit]:
        candidate=response(page['terms']+[term],next_offset+1)
        if len(canonical_json(candidate).encode())>max_bytes: break
        page['terms'].append(term);next_offset+=1
    page=response(page['terms'],next_offset)
    if next_offset<len(catalog['terms']):
        if next_offset==offset: raise EvidenceError('phrase_page_too_small')
        token=dict(identity=page_identity,offset=next_offset);page['next_cursor']=digest(token)
        atomic_json(root/'cursors'/(page['next_cursor']+'.json'),token)
    if len(canonical_json(page).encode())>max_bytes: raise EvidenceError('phrase_page_too_small')
    _fresh(service)
    atomic_json(root/'pages'/(digest(page)+'.json'),page)
    service.state.event('phrase_page_returned',question_id=question_id,payload=dict(page_sha256=digest(page),original_read=False))
    return page
