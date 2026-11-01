"""Read-only validation for this kit. Python 3.10+, standard library only.

The schema checker implements the keywords used in the two bundled schemas;
it is NOT a general JSON Schema implementation. Unknown keywords fail closed.
Checks structure, relationships, native locators, and literal excerpts, not
editorial quality, speaker identity, or whether a model understood the file.
"""

import copy

import csv

import hashlib

import io

import json

import math

import re

from datetime import datetime

from pathlib import Path

if __package__:
    from .context_contract import validate_context
    from .collaboration_periods import recover_collaboration_periods
else:
    from context_contract import validate_context
    from collaboration_periods import recover_collaboration_periods

ROOT = Path(__file__).resolve().parent

class Invalid(ValueError): pass

def require(ok, message):
    if not ok: raise Invalid(message)

def read_json(path):
    def bad(value): raise Invalid('Non-JSON numeric constant: '+value)
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'Duplicate JSON key: '+key)
            result[key]=value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=bad, object_pairs_hook=pairs)

def schema_check(value, spec, root=None, path='$'):
    root = spec if root is None else root
    allowed = {'$schema','$id','$defs','$ref','title','description','type','required','properties','additionalProperties',
               'items','minItems','maxItems','uniqueItems','enum','const','minimum','exclusiveMinimum','minLength','maxLength','pattern'}
    require(not (set(spec)-allowed), f'{path}: unsupported schema keyword {set(spec)-allowed}')
    if '$ref' in spec:
        target = root
        require(spec['$ref'].startswith('#/'), 'Only local schema references supported')
        for part in spec['$ref'][2:].split('/'): target=target[part.replace('~1','/').replace('~0','~')]
        schema_check(value,target,root,path)
        return
    types = spec.get('type',[])
    if isinstance(types,str): types=[types]
    matches = {'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
               'null':value is None,'boolean':isinstance(value,bool),
               'number':isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value),
               'integer':isinstance(value,int) and not isinstance(value,bool)}
    if types: require(any(matches[t] for t in types),f'{path}: expected {types}')
    if 'enum' in spec: require(value in spec['enum'],f'{path}: invalid enum {value!r}')
    if 'const' in spec: require(value == spec['const'],f'{path}: wrong constant')
    if isinstance(value,dict):
        require(set(spec.get('required',[])) <= set(value),f'{path}: required field missing')
        props=spec.get('properties',{})
        if spec.get('additionalProperties') is False:
            require(set(value) <= set(props),f'{path}: forbidden field {set(value)-set(props)}')
        for k,v in value.items():
            if k in props: schema_check(v,props[k],root,path+'.'+k)
    elif isinstance(value,list):
        require(len(value)>=spec.get('minItems',0),f'{path}: too few items')
        require(len(value)<=spec.get('maxItems',float('inf')),f'{path}: too many items')
        if spec.get('uniqueItems'):
            encoded=[json.dumps(x,sort_keys=True,ensure_ascii=False) for x in value]
            require(len(encoded)==len(set(encoded)),f'{path}: duplicate values')
        for i,v in enumerate(value): schema_check(v,spec.get('items',{}),root,f'{path}[{i}]')
    elif isinstance(value,str):
        require(len(value)>=spec.get('minLength',0),f'{path}: string too short')
        require(len(value)<=spec.get('maxLength',float('inf')),f'{path}: string too long')
        if 'pattern' in spec: require(re.search(spec['pattern'],value) is not None,f'{path}: pattern mismatch')
    elif isinstance(value,(int,float)) and not isinstance(value,bool):
        if 'minimum' in spec: require(value>=spec['minimum'],f'{path}: below minimum')
        if 'exclusiveMinimum' in spec: require(value>spec['exclusiveMinimum'],f'{path}: below exclusive minimum')

def unique_index(rows, field, label):
    result={r[field]:r for r in rows}
    require(len(result)==len(rows),label+': duplicate IDs')
    return result

def safe_source(base, filename):
    require(':' not in filename and not filename.startswith(('/','\\')), 'Source filename must be relative')
    p=(base/filename).resolve()
    require(p.is_relative_to(base.resolve()),'Source path leaves input directory')
    return p

def clock(text):
    h,m,s = text.replace(',','.').split(':')
    require(0<=int(m)<60 and 0<=float(s)<60 and int(h)>=0,'Invalid SRT clock')
    return int(h)*3600+int(m)*60+float(s)

def srt_cues(text):
    cues=[]
    for block in re.split(r'\n\s*\n',text.replace('\r\n','\n').strip()):
        lines=block.splitlines()
        if not lines: continue
        offset=1 if len(lines)>1 and '-->' in lines[1] else 0
        require('-->' in lines[offset],'Unparseable SRT block')
        a,b=lines[offset].split('-->',1)
        a,b=clock(a.strip()),clock(b.strip().split()[0])
        require(a<b,'Invalid SRT cue range')
        cues.append((a,b,'\n'.join(lines[offset+1:])))
    return cues

def pointer_get(data,pointer):
    require(pointer=='' or pointer.startswith('/'),'Invalid JSON Pointer')
    if pointer=='': return data
    for token in pointer[1:].split('/'):
        require(not re.search(r'~(?![01])',token),'Invalid JSON Pointer escape')
        key=token.replace('~1','/').replace('~0','~')
        data=data[int(key)] if isinstance(data,list) else data[key]
    return data

def lookup(data,field):
    if field is None: return None
    if field.startswith('/'): return pointer_get(data,field)
    for part in field.split('.'):
        if not isinstance(data,dict) or part not in data: return None
        data=data[part]
    return data

def locator_contains(outer, inner):
    if outer['type']!=inner['type']: return False
    if outer['type'] in ('line','srt_cue'):
        return outer['first']<=inner['first']<=inner['last']<=outer['last']
    if outer['type']=='json_pointer':
        return outer['pointer']=='' or outer['pointer']==inner['pointer'] or inner['pointer'].startswith(outer['pointer']+'/')
    return outer['media_start_sec']<=inner['media_start_sec']<inner['media_end_sec']<=outer['media_end_sec']

def native_seconds(raw, source, manifest):
    if raw is None: return None
    tb=source['timebase']; unit=tb['unit']
    if tb['mapping_status']!='known': return None
    require(tb['offset_sec'] is not None,'Known clock requires offset_sec')
    if unit=='absolute_iso8601':
        started=manifest['vod']['started_at']
        require(started is not None,'Absolute clock requires VOD started_at')
        a=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        b=datetime.fromisoformat(started.replace('Z','+00:00'))
        require(a.tzinfo is not None and b.tzinfo is not None,'Absolute clocks require timezone')
        value=(a-b).total_seconds()
    elif unit in ('vod_seconds','clip_seconds'): value=float(raw)
    elif unit=='vod_milliseconds': value=float(raw)/1000
    else: return None
    return value+tb['offset_sec']

def select_native(source, locator, base, manifest, cache):
    path=safe_source(base,source['file'])
    require(path.is_file(),f'Missing source file: {source["file"]}')
    if source['id'] not in cache:
        raw=path.read_bytes()
        if source['sha256']: require(hashlib.sha256(raw).hexdigest()==source['sha256'],'Source hash mismatch: '+source['id'])
        cache[source['id']]=raw.decode('utf-8-sig') if source['format']!='media' else None
    text=cache[source['id']]
    kind=locator['type']; first,last=locator['first'],locator['last']
    a,b=None,None
    if kind in ('srt_cue','line'):
        require(first is not None and last is not None and first<=last,'Invalid ordinal range')
        require(locator['pointer'] is None and locator['media_start_sec'] is None and locator['media_end_sec'] is None,'Mixed locator authorities')
        require(text is not None,'Cannot read media as text')
        if kind=='srt_cue':
            require(source['format']=='srt','srt_cue locator requires SRT')
            units=srt_cues(text)
            require(last<=len(units),'SRT ordinal outside file')
            chosen=units[first-1:last]
            content='\n'.join(c[2] for c in chosen)
            a=native_seconds(min(c[0] for c in chosen),source,manifest)
            b=native_seconds(max(c[1] for c in chosen),source,manifest)
        else:
            require(source['format'] in ('jsonl','csv','text','markdown'),'line locator requires line-oriented input')
            units=text.splitlines()
            require(last<=len(units),'Line ordinal outside file')
            content='\n'.join(units[first-1:last])
            times=[]
            if source['format']=='jsonl':
                for line in units[first-1:last]:
                    if line.strip(): times.append(native_seconds(lookup(json.loads(line),source['field_map']['timestamp']),source,manifest))
            elif source['format'] in ('text','markdown') and source['kind']=='notes':
                for line in units[first-1:last]:
                    match=re.search(r'(?<!\d)(?:(\d{2,}):)?([0-5]\d):([0-5]\d)(?!\d)',line)
                    if match:
                        raw=int(match[1] or 0)*3600+int(match[2])*60+int(match[3])
                        times.append(native_seconds(raw,source,manifest))
            elif source['format']=='csv' and source['field_map']['timestamp']:

                rows=list(csv.DictReader(io.StringIO(text)))
                require(len(units)==len(rows)+1,'Multiline CSV requires a dedicated native reader')
                for ordinal in range(max(2,first),last+1):
                    times.append(native_seconds(lookup(rows[ordinal-2],source['field_map']['timestamp']),source,manifest))
            times=[t for t in times if t is not None]
            if times: a,b=min(times),max(times)
        total=len(units)
    elif kind=='json_pointer':
        require(first is None and last is None and locator['media_start_sec'] is None and locator['media_end_sec'] is None,'Mixed locator authorities')
        require(source['format']=='json' and isinstance(locator['pointer'],str),'JSON Pointer requires JSON input')
        selected=pointer_get(json.loads(text),locator['pointer'])
        content=json.dumps(selected,ensure_ascii=False)
        a=native_seconds(lookup(selected,source['field_map']['timestamp']),source,manifest); b=a
        total=None
    else:
        require(kind=='media_range' and source['format']=='media','media_range requires media asset')
        require(first is None and last is None and locator['pointer'] is None,'Mixed locator authorities')
        require(locator['media_start_sec'] is not None and locator['media_end_sec'] is not None,'Media range required')
        require(0<=locator['media_start_sec']<locator['media_end_sec'],'Invalid media range')
        content=None; total=None
        a=native_seconds(locator['media_start_sec'],source,manifest); b=native_seconds(locator['media_end_sec'],source,manifest)
    return content,a,b,total

def validate(result, manifest, base, *, verify_native=True):
    schema_check(manifest,read_json(ROOT/'input-manifest.schema.json'))


    projected = copy.deepcopy(result)
    collaboration = recover_collaboration_periods(result.get('collaboration_periods', []), result.get('duration_sec'))
    if 'collaboration_periods' in projected:
        projected['collaboration_periods'] = collaboration['periods']
    schema_check(projected,read_json(ROOT/'editorial-result.schema.json'))
    require(result['input_id']==manifest['input_id'],'Input ID mismatch')
    require(result['source_vod_id']==manifest['vod']['id'],'VOD ID mismatch')
    require(result['duration_sec']==manifest['vod']['duration_sec'],'Duration mismatch')
    require(result['example_kind']==manifest['example_kind'],'Synthetic/real mismatch')
    sources=unique_index(manifest['sources'],'id','sources')
    coverage=unique_index(result['coverage'],'source_id','coverage')
    require(set(coverage)==set(sources),'Coverage must enumerate every source')
    ev=unique_index(result['evidence_ledger'],'id','evidence')
    for period in result.get('collaboration_periods', []) if isinstance(result.get('collaboration_periods', []), list) else []:
        if not isinstance(period, dict):
            continue

        if not isinstance(period.get('evidence_refs', []), list):
            continue
        require({ref for ref in period.get('evidence_refs', []) if isinstance(ref, str)} <= set(ev), 'Unknown collaborator evidence reference')
    d1=unique_index(result['broadcast_map']['d1'],'id','D1')
    d2=unique_index(result['broadcast_map']['d2'],'id','D2')
    points=unique_index(result['broadcast_map']['points'],'id','Points')
    stories=unique_index(result['stories'],'story_ref','Stories')
    highlights=unique_index(result['editorial_highlights'],'highlight_id','Highlights')
    pending=unique_index(result['pending_highlights'],'id','Pending')
    decisions=unique_index(result['signal_decisions'],'id','Decisions')
    unresolved=unique_index(result['unresolved'],'id','Unresolved')
    groups=[set(x) for x in (ev,d1,d2,points,stories,highlights,pending,decisions,unresolved)]
    require(sum(map(len,groups))==len(set().union(*groups)),'IDs must be globally unique')
    item_ids=set().union(*[set(x) for x in (d1,d2,points,stories,highlights,pending)])
    duration=result['duration_sec']; warnings=[item['code'] for item in collaboration['diagnostics']]; cache={}
    def bounded(a,b,allow_instant=False):
        require(a>=0 and (a<=b if allow_instant else a<b),'Invalid time range')
        if duration is not None: require(b<=duration,'Time outside VOD duration')
    for s in sources.values():
        for rg in s['coverage_ranges']: bounded(rg['start_sec'],rg['end_sec'])
    def refs(row):
        require(set(row['evidence_refs'])<=set(ev),'Unknown evidence reference')
    semantic={'spoken_text','audience_text','media_observation'}
    def has_content(row, timed=False):
        refs(row)
        require(any(ev[e]['support_type'] in semantic and (not timed or ev[e]['clock_status']=='mapped') for e in row['evidence_refs']),
                'Selected item lacks content evidence; signals/notes are insufficient')
    kind_allowed={'speech_srt':{'spoken_text'},'display_srt':{'spoken_text','display_marker'},'chat':{'audience_text'},
                  'peaks':{'navigation_signal'},'notes':{'author_note'},'viewer_clips':{'clip_metadata'},'media':{'media_observation'}}
    locator_seen=set()
    for e in ev.values():
        require(e['source_id'] in sources,'Unknown evidence source')
        s=sources[e['source_id']]
        require(s['availability'] in ('present','partial'),'Evidence refers to unavailable/empty source')
        require(e['support_type'] in kind_allowed[s['kind']],'Evidence type does not match source kind')
        marker=(e['source_id'],json.dumps(e['locator'],sort_keys=True))
        require(marker not in locator_seen,'Duplicate native locator under multiple evidence IDs')
        locator_seen.add(marker)
        content,a,b,_=select_native(s,e['locator'],base,manifest,cache) if verify_native else (None,None,None,None)
        if e['support_type']=='media_observation':
            require(e['excerpt'] is None,'Media observations cannot invent a transcript quote')
            warnings.append('Media content and actual viewing/listening require human verification: '+e['id'])
        elif verify_native:
            require(bool(e['excerpt']) and e['excerpt'] in content,'Evidence excerpt is not present at native locator')
        if e['clock_status']=='mapped':
            require(s['timebase']['mapping_status']=='known','Mapped evidence with unknown source clock')
            ea,eb=e['canonical_start_sec'],e['canonical_end_sec']
            require(ea is not None and eb is not None,'Mapped evidence requires both timestamps')
            bounded(ea,eb,True)
            if a is not None:
                require(abs(ea-a)<0.002 and abs(eb-b)<0.002,'Evidence timestamps disagree with native locator')
            elif verify_native:
                warnings.append('Native timestamp could not be automatically cross-checked: '+e['id'])
        else: require(e['canonical_start_sec'] is None and e['canonical_end_sec'] is None,'Unknown/untimed evidence cannot claim precise canonical time')
    def check_structure(rows):
        previous=-1
        for r in rows:
            bounded(r['start_sec'],r['end_sec']); refs(r)
            require(r['start_sec']>=previous,'Sibling structure must be sorted and non-overlapping')
            previous=r['end_sec']
            if r['support_status']=='supported': has_content(r)
    check_structure(list(d1.values()))
    for parent in d1:
        check_structure([d for d in d2.values() if d['d1_ref']==parent])
    for d in d2.values():
        require(d['d1_ref'] in d1,'D2 parent missing')
        parent=d1[d['d1_ref']]
        require(parent['start_sec']<=d['start_sec']<d['end_sec']<=parent['end_sec'],'D2 outside D1')
    story_refs=[p['story_ref'] for p in points.values()]
    require(len(story_refs)==len(set(story_refs)) and set(story_refs)==set(stories),'Point/Story must be a bijection')
    times=[p['timestamp'] for p in points.values()]
    require(times==sorted(times),'Points must be sorted by timestamp')
    for p in points.values():
        has_content(p,True)
        require(p['d1_ref'] in d1,'Point D1 missing')
        parent=d1[p['d1_ref']]
        require(parent['start_sec']<=p['timestamp']<parent['end_sec'],'Point outside D1')
        if p['d2_ref'] is not None:
            require(p['d2_ref'] in d2,'Point D2 missing')
            child=d2[p['d2_ref']]
            require(child['d1_ref']==p['d1_ref'],'Point D1/D2 disagree')
            require(child['start_sec']<=p['timestamp']<child['end_sec'],'Point outside D2')
    def check_claims(row):
        for c in row['claims']:
            refs(c)
            require(set(c['evidence_refs'])<=set(row['evidence_refs']),'Claim evidence must also be in object evidence_refs')
    for s in stories.values(): has_content(s); check_claims(s)
    for h in highlights.values():
        has_content(h,True); check_claims(h)
        require(set(h['point_refs'])<=set(points),'Unknown Highlight Point')
        require(set(h['story_refs'])=={points[p]['story_ref'] for p in h['point_refs']},'Highlight Story/Point lineage mismatch')
        seen=[]
        for span in h['source_spans']:
            bounded(span['start_sec'],span['end_sec']); has_content(span,True)
            require(set(span['evidence_refs'])<=set(h['evidence_refs']),'Span evidence must also be in Highlight evidence_refs')
            require(any(ev[e]['support_type'] in semantic and ev[e]['clock_status']=='mapped'
                        and ev[e]['canonical_start_sec']<span['end_sec'] and ev[e]['canonical_end_sec']>=span['start_sec']
                        for e in span['evidence_refs']), 'Span has no content evidence at its own time')
            require(all(span['end_sec']<=a or span['start_sec']>=b for a,b in seen),'Overlapping duplicate spans within Highlight')
            seen.append((span['start_sec'],span['end_sec']))
        if h['verification']!='text_only':
            require(any(ev[e]['support_type']=='media_observation' for e in h['evidence_refs']),'Media verification claim lacks media evidence')
    for p in pending.values():
        refs(p)
        for span in p['source_spans']:
            bounded(span['start_sec'],span['end_sec']); refs(span)
            require(set(span['evidence_refs'])<=set(p['evidence_refs']),'Pending span evidence not in candidate')
    for d in decisions.values():
        refs(d); require(set(d['result_refs'])<=item_ids,'Decision refers to absent result')
        if d['decision'] in ('selected','structure_only'): require(bool(d['result_refs']),'Selected decision must reference result')
    for u in unresolved.values():
        require(set(u['source_ids'])<=set(sources),'Unknown unresolved source')
        require(set(u['affected_ids'])<=item_ids,'Unknown unresolved result')
    for rg in result['timeline_gaps']: bounded(rg['start_sec'],rg['end_sec'])
    for sid,c in coverage.items():
        s=sources[sid]
        if s['availability']=='not_attached':
            require(c['parse_status'] in ('not_read','inaccessible') and c['semantic_read_status'] in ('not_read','inaccessible') and not c['inspected'],
                    'Absent source cannot be marked read')
        if c['semantic_read_status']=='complete':
            require(c['parse_status']=='complete','Complete reading requires complete parsing')
            require(c['inspected'] or s['availability']=='empty','Complete reading requires native coverage')
        inspected=[]; total=None
        for loc in c['inspected']:
            require(s['availability'] in ('present','partial'),'Cannot inspect unavailable source')
            if verify_native: _,_,_,total=select_native(s,loc,base,manifest,cache)
            if loc['type'] in ('srt_cue','line'): inspected.extend(range(loc['first'],loc['last']+1))
        if c['semantic_read_status']=='complete' and total is not None:
            require(set(inspected)==set(range(1,total+1)),'Complete reading leaves uninspected native units')
        if c['semantic_read_status']=='complete' and s['format']=='json' and s['availability']!='empty':
            require(any(x['type']=='json_pointer' and x['pointer']=='' for x in c['inspected']),
                    'Complete JSON reading must explicitly include the root pointer')
    for e in ev.values():
        c=coverage[e['source_id']]
        require(c['semantic_read_status'] in ('complete','partial'),'Evidence from source declared unread')
        require(any(locator_contains(x,e['locator']) for x in c['inspected']),'Evidence locator lies outside declared inspected range')
    if result['analysis_status']=='complete':
        require(duration is not None and duration>0,'Complete analysis requires known positive duration')
        require(not result['timeline_gaps'],'Complete analysis cannot contain known timeline gaps')
        for sid,s in sources.items():
            if s['availability'] in ('present','partial','empty'):
                require(s['availability']!='partial' and coverage[sid]['semantic_read_status']=='complete','Complete analysis with partially read input')
        ranges=[]
        for s in sources.values():
            if s['kind'] in ('speech_srt','display_srt','media') and s['availability']=='present':
                require(s['timebase']['mapping_status']=='known','Complete content source needs known clock')
                ranges.extend((x['start_sec'],x['end_sec']) for x in s['coverage_ranges'])
        reached=0
        for a,b in sorted(ranges):
            require(a<=reached,'Complete content coverage has gaps')
            reached=max(reached,b)
        require(reached>=duration,'Complete analysis lacks declared full content coverage')
    if result['analysis_status']=='needs_input':
        require(not points and not stories and not highlights,'needs_input cannot contain confirmed content results')
    warnings.extend(validate_context(result,manifest,base,{'locator_contains':locator_contains,'native_seconds':native_seconds,'require':require,'select_native':select_native,'srt_cues':srt_cues,'unique_index':unique_index},verify_native=verify_native))
    if not verify_native:
        warnings.append('Native source hashes, excerpts, clocks and full read coverage were not verified.')
    return sorted(set(warnings))
