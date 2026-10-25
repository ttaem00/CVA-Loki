"""Explicit context contracts for the standalone v2 handoff; no product imports."""

SEMANTIC = {'spoken_text','audience_text','media_observation'}

def validate_context(result,manifest,base,api,*,verify_native=True):
    require=api['require']; unique=api['unique_index']; contains=api['locator_contains']; select=api['select_native']
    sources={s['id']:s for s in manifest['sources']}; coverage={s['source_id']:s for s in result['coverage']}
    evidence={e['id']:e for e in result['evidence_ledger']}; stories={s['story_ref']:s for s in result['stories']}
    points={p['id']:p for p in result['broadcast_map']['points']}; highlights={h['highlight_id']:h for h in result['editorial_highlights']}
    reads=unique(result['context_reads'],'id','Context reads'); relations=unique(result['context_relations'],'id','Context relations')
    spans=unique([s for h in highlights.values() for s in h['source_spans']],'span_id','Selected spans')
    previous_ids=set(evidence)|set(stories)|set(points)|set(highlights)
    previous_ids |= {r['id'] for r in result['broadcast_map']['d1']+result['broadcast_map']['d2']+result['pending_highlights']+result['signal_decisions']+result['unresolved']}
    added=list(reads)+list(relations)+list(spans)
    require(len(set(added))==len(added) and not (set(added)&previous_ids),'Context IDs must be globally unique')
    cache={}
    def mapped_semantic(eid):
        return evidence[eid]['support_type'] in SEMANTIC and evidence[eid]['clock_status']=='mapped'
    def overlaps(eid,span):
        e=evidence[eid]; a,b=e['canonical_start_sec'],e['canonical_end_sec']
        return a is not None and b is not None and a<span['end_sec'] and (b>span['start_sec'] or a==b==span['start_sec'])
    def read_covers(read,eid):
        e=evidence[eid]
        return any(x['source_id']==e['source_id'] and contains(x['locator'],e['locator']) for x in read['source_reads'])
    def named_reads(ids,purpose=None,resolved=False):
        require(set(ids)<=set(reads),'Unknown context read')
        selected=[reads[i] for i in ids]
        if purpose: require(any(purpose in r['purposes'] for r in selected),'Required context read purpose absent: '+purpose)
        if resolved: require(all(r['status']=='resolved' for r in selected),'Selected context depends on unresolved read')
        return selected
    def read_supports(ids,refs):
        require(all(any(e in reads[r]['evidence_refs'] and read_covers(reads[r],e) for r in ids) for e in refs),
                'Context evidence not supported by its cited native reads')
    for rd in reads.values():
        require(set(rd['evidence_refs'])<=set(evidence),'Context read cites unknown evidence')
        require(set(rd['span_refs'])<=set(spans),'Context read cites unknown span')
        if rd['status']=='resolved': require(bool(rd['source_reads']) and bool(rd['evidence_refs']),'Resolved read requires actual sources and evidence')
        if 'composition' not in rd['purposes']: require(not rd['span_refs'],'Only composition reads declare edit order')
        for src in rd['source_reads']:
            require(src['source_id'] in sources,'Context read source absent')
            s=sources[src['source_id']]
            require(s['availability'] in ('present','partial'),'Context read uses unavailable source')
            if verify_native: select(s,src['locator'],base,manifest,cache)
            require(any(contains(x,src['locator']) for x in coverage[s['id']]['inspected']),
                    'Context read exceeds declared inspected coverage')
        require(all(read_covers(rd,e) for e in rd['evidence_refs']),'Context read evidence outside its own native locators')
    for story in stories.values():
        refs=set(story['evidence_refs']); roles=story['roles']
        require(len(roles['setup'])<=1,'Story setup stores at most one minimum-sufficient start')
        all_roles=set().union(*(set(v) for v in roles.values()))
        require(bool(all_roles) and all_roles<=refs,'Story roles require owned evidence')
        require(all(mapped_semantic(e) for e in all_roles),'Story roles need timed content evidence, not navigation signals')
        named_reads(story['context_read_refs'],'story_context')
        read_supports(story['context_read_refs'],refs)
    for point in points.values():
        story=stories[point['story_ref']]; anchor=point['start_anchor']; eid=anchor['evidence_ref']
        require(eid in point['evidence_refs'] and eid in story['roles'][anchor['role']],'Point anchor must be owned by its Story role and Point')
        require(mapped_semantic(eid),'Point anchor must be timed content')
        e=evidence[eid]; t=point['timestamp']; a,b=e['canonical_start_sec'],e['canonical_end_sec']
        require((a<=t<b) or a==b==t,'Point timestamp is outside its start anchor')
        candidates=[rid for rid in story['context_read_refs'] if 'point_start' in reads[rid]['purposes'] and reads[rid]['status']=='resolved']
        require(any(eid in reads[rid]['evidence_refs'] for rid in candidates),'Point start lacks resolved native context read')
    for relation in relations.values():
        a,b=relation['from_story_ref'],relation['to_story_ref']
        require(a in stories and b in stories,'Relation Story absent')
        fr=set(relation['from_evidence_refs']); to=set(relation['to_evidence_refs']); links=set(relation['link_evidence_refs'])
        require(fr<=set(stories[a]['evidence_refs']) and to<=set(stories[b]['evidence_refs']),'Relation endpoints borrow another Story evidence')
        require(links<=set(evidence),'Relation has unknown link evidence')
        require(fr!=to or a!=b,'Self relation needs distinct source occurrences')
        named_reads(relation['read_refs'],'relation',relation['status']=='confirmed')
        read_supports(relation['read_refs'],fr|to|links)
        if relation['status']=='confirmed':
            require(bool(links) and all(mapped_semantic(e) for e in fr|to|links),'Confirmed relation requires content at both ends and connecting evidence')
            require(relation['uncertainty'] is None,'Confirmed relation cannot retain unresolved relation uncertainty')
        if relation['status']=='uncertain': require(bool(relation['uncertainty']),'Uncertain relation must state what is missing')
    for h in highlights.values():
        hstories=set(h['story_refs']); hspans={s['span_id']:s for s in h['source_spans']}
        hrefs=set(h['evidence_refs'])
        require(set(h['context_relation_refs'])<=set(relations),'Highlight relation absent')
        graph={s:set() for s in hstories}
        required_refs=set().union(*(set(stories[s]['evidence_refs']) for s in hstories))
        for rid in h['context_relation_refs']:
            rel=relations[rid]; a,b=rel['from_story_ref'],rel['to_story_ref']
            require(rel['status']=='confirmed','Highlight cannot depend on uncertain/rejected relation')
            require({a,b}<=hstories,'Highlight relation escapes its Story set')
            graph[a].add(b); graph[b].add(a)
            required_refs.update(rel['from_evidence_refs']+rel['to_evidence_refs']+rel['link_evidence_refs'])
        visited=set(); queue=[next(iter(hstories))]
        while queue:
            current=queue.pop()
            if current in visited: continue
            visited.add(current); queue.extend(graph[current]-visited)
        require(visited==hstories,'Multiple Stories require an evidenced connected context, not a bag of refs')
        previous=None
        for span in h['source_spans']:
            bound_refs=set(); seen_bindings=set()
            for binding in span['story_bindings']:
                sid,role=binding['story_ref'],binding['role']; refs=set(binding['evidence_refs'])
                require(sid in hstories,'Span binding Story outside Highlight')
                require((sid,role) not in seen_bindings,'Duplicate Story/role binding; combine its evidence refs')
                seen_bindings.add((sid,role))
                require(refs<=set(stories[sid]['roles'][role]),'Span binding does not match canonical Story role')
                require(refs<=set(span['evidence_refs']),'Span binding evidence missing from span')
                require(all(overlaps(e,span) for e in refs),'Span role evidence is outside actual footage')
                bound_refs.update(refs)
            require(set(span['evidence_refs'])==bound_refs,'Every selected span evidence needs explicit Story/role binding')
            exclusion=span['exclusion_before']
            if previous is None:
                require(exclusion is None,'First span has no intervening gap')
            else:
                require(previous['end_sec']<span['start_sec'],'Keep source chronological; continuous/touching spans must merge')
                require(exclusion is not None,'A positive gap requires evidence-backed exclusion')
                require(set(exclusion['evidence_refs'])<=hrefs,'Gap evidence missing from Highlight record')
                require(all(mapped_semantic(e) and previous['end_sec']<evidence[e]['canonical_start_sec']<span['start_sec']
                            for e in exclusion['evidence_refs']),'Gap exclusion needs semantic evidence strictly inside discarded footage')
                rd=named_reads([exclusion['read_ref']],'gap',True)[0]
                read_supports([rd['id']],exclusion['evidence_refs'])


                gap_sources={evidence[e]['source_id'] for e in hrefs if mapped_semantic(e)
                             and sources[evidence[e]['source_id']]['kind'] in ('speech_srt','display_srt')}
                speech_sources={sid for sid in gap_sources if sources[sid]['kind']=='speech_srt'}
                if speech_sources: gap_sources=speech_sources
                for sid in gap_sources if verify_native else ():
                    src=sources[sid]
                    if src['format']!='srt': continue
                    require(src['timebase']['unit'] in ('vod_seconds','clip_seconds'),'SRT clock must use native seconds')
                    cues=api['srt_cues'](cache[sid])
                    for ordinal,(a,b,_) in enumerate(cues,1):
                        a=api['native_seconds'](a,src,manifest); b=api['native_seconds'](b,src,manifest)
                        if a<span['start_sec'] and b>previous['end_sec']:
                            loc={'type':'srt_cue','first':ordinal,'last':ordinal,'pointer':None,'media_start_sec':None,'media_end_sec':None}
                            require(any(x['source_id']==sid and contains(x['locator'],loc) for x in rd['source_reads']),
                                    'Gap read omits available SRT cues inside discarded footage')
                required_refs.update(exclusion['evidence_refs'])
            previous=span
        require(required_refs<=hrefs,'Highlight dropped canonical Story/relation/gap context')
        dispositions=unique(h['evidence_dispositions'],'evidence_ref','Evidence dispositions')
        require(set(dispositions)==hrefs,'Every supplied Highlight context ref needs exactly one disposition')
        for eid,d in dispositions.items():
            actual={s['span_id'] for s in h['source_spans'] if eid in s['evidence_refs']}
            require(set(d['span_refs'])==actual,'Evidence disposition disagrees with actual span use')
            require((d['use']=='span_support')==bool(actual),'Context-only evidence cannot masquerade as selected footage')
        roles_seen=set(); covered_spans=set()
        for beat in h['story_beats']:
            require(beat['role'] not in roles_seen,'One beat per role; aggregate span refs')
            roles_seen.add(beat['role'])
            require(set(beat['span_refs'])<=set(hspans),'Story beat refers to foreign span')
            allowed=set().union(*(set(hspans[s]['evidence_refs']) for s in beat['span_refs']))
            require(set(beat['evidence_refs'])<=allowed,'Story beat uses context that is not in its selected spans')
            require(all(set(hspans[s]['evidence_refs'])&set(beat['evidence_refs']) for s in beat['span_refs']),
                    'Story beat span has no corresponding beat evidence')
            covered_spans.update(beat['span_refs'])
        require(covered_spans==set(hspans),'Every span needs an explained editorial beat')
        review=named_reads([h['composition_review']['read_ref']],'composition',True)[0]
        require(review['span_refs']==[s['span_id'] for s in h['source_spans']],'Composition reread must preserve exact final span order')
        selected_refs=set().union(*(set(s['evidence_refs']) for s in h['source_spans']))
        read_supports([review['id']],selected_refs)
        previous_read_index=-1
        for src_read in review['source_reads'] if verify_native else ():
            _,a,b,_=select(sources[src_read['source_id']],src_read['locator'],base,manifest,cache)
            require(a is not None and b is not None,'Composition native read must have a verified canonical clock')
            matched=[i for i,span in enumerate(h['source_spans'])
                     if a<span['end_sec'] and (b>span['start_sec'] or a==b==span['start_sec'])]
            require(len(matched)==1,'Composition native read must address one selected span, not a whole envelope')
            require(matched[0]>=previous_read_index,'Native composition reads are not in the declared edit order')
            previous_read_index=matched[0]
    outcomes=unique(result['story_outcomes'],'story_ref','Story outcomes')
    require(set(outcomes)==set(stories),'Every Story requires a selection outcome, even for zero Highlights')
    for sid,outcome in outcomes.items():
        require(set(outcome['evidence_refs'])<=set(stories[sid]['evidence_refs']),'Story outcome uses foreign evidence')
        actual={h['highlight_id'] for h in highlights.values() if sid in h['story_refs']}
        require(set(outcome['highlight_refs'])==actual,'Story outcome disagrees with Highlight lineage')
        require((outcome['decision']=='selected')==bool(actual),'Selected Story outcome needs an actual Highlight')
    return []
