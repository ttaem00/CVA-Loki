"""Question-anchored pages over an immutable, existing document group artifact.

This reader never encodes, clusters, ranks or interprets an event. Every member
keeps its occurrence clock and original address; outside originals stay separate.
"""

from copy import deepcopy

import json

from .contracts import EvidenceError, canonical_json, digest, file_hash, native_path

from .index import atomic_json, build_lock

from .vector_clusters import DOCUMENT_COSINE_NAVIGATION, _occurrence

PROFILE = 'question_document_groups_v1'

def descriptor(service, path):
    return dict(profile=PROFILE, artifact=dict(path=str(native_path(path).resolve()),sha256=file_hash(path)),
        policy_sha256=digest(DOCUMENT_COSINE_NAVIGATION),reader_sha256=file_hash(__file__),
        source_id=service.source_id,world_revision=service.world_revision,
        source_identity_sha256=digest(service.manifest['identity']))

def _assert_identity(service, selected):
    try:
        current=descriptor(service,selected['artifact']['path'])
    except (OSError,TypeError,ValueError) as exc:
        raise EvidenceError('cluster_reader_identity_mismatch') from exc
    if selected != current:
        raise EvidenceError('cluster_reader_identity_mismatch')

def configure(service, selected, *, defer_details=False):
    if (not isinstance(selected,dict) or set(selected)!={'profile','artifact','policy_sha256',
            'reader_sha256','source_id','world_revision','source_identity_sha256'}
            or not isinstance(selected.get('artifact'),dict)
            or set(selected['artifact'])!={'path','sha256'}):
        raise EvidenceError('cluster_reader_descriptor')
    _assert_identity(service,selected)
    if defer_details:


        service.cluster_reader=dict(descriptor=deepcopy(selected),by_unit=None)
        return
    try:
        artifact=json.loads(native_path(selected['artifact']['path']).read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise EvidenceError('cluster_artifact_invalid') from exc
    if (not isinstance(artifact,dict) or artifact.get('schema_version')!='ew.document-cosine-navigation.v1'
            or artifact.get('profile')!=DOCUMENT_COSINE_NAVIGATION['profile']
            or artifact.get('policy')!=DOCUMENT_COSINE_NAVIGATION
            or artifact.get('source_id')!=service.source_id
            or artifact.get('world_revision')!=service.world_revision
            or not isinstance(artifact.get('groups'),list) or not artifact['groups']):
        raise EvidenceError('cluster_artifact_identity_mismatch')
    by_unit={}
    with service.open() as db:
        for group in artifact['groups']:
            if (not isinstance(group,dict) or group.get('kind') not in {'cosine_group','singleton','uncompared'}
                    or not isinstance(group.get('occurrences'),list) or not group['occurrences']):
                raise EvidenceError('cluster_artifact_group')
            for occurrence in group['occurrences']:
                if not isinstance(occurrence,dict) or not isinstance(occurrence.get('unit_id'),str):
                    raise EvidenceError('cluster_artifact_unit')
                unit_id=occurrence['unit_id']
                row=db.execute('SELECT * FROM units WHERE unit_id=?',(unit_id,)).fetchone()
                if row is None or unit_id in by_unit or occurrence!=_occurrence(dict(row)):
                    raise EvidenceError('cluster_artifact_unit_member_clock_mismatch')
                by_unit[unit_id]=group
            ids=[o['unit_id'] for o in group['occurrences']]
            representative=db.execute('SELECT norm_text FROM units WHERE unit_id=?',(ids[0],)).fetchone()[0]
            if (group.get('group_id')!=digest([artifact['policy'],group['kind'],ids])
                    or group.get('representative_unit_id')!=ids[0]
                    or group.get('representative_text')!=representative
                    or group.get('same_event_inferred') is not False
                    or group.get('original_read') is not False
                    or (group['kind']=='cosine_group' and len(ids)<2)
                    or (group['kind']=='singleton' and len(ids)!=1)):
                raise EvidenceError('cluster_artifact_group_mismatch')
    if len(by_unit)!=artifact.get('input_units'):
        raise EvidenceError('cluster_artifact_unit_count_mismatch')
    _assert_identity(service,selected)

    service.cluster_reader=dict(descriptor=deepcopy(selected),by_unit=by_unit)

def _members(service):
    if service.cluster_reader['by_unit'] is None:


        configure(service,service.cluster_reader['descriptor'])
    return service.cluster_reader['by_unit']

def identity(service):
    reader=getattr(service,'cluster_reader',None)
    if reader is None:
        return None
    selected=reader['descriptor']
    _assert_identity(service,selected)
    return digest(selected)

def projection(service, envelope):
    if not isinstance(envelope,dict):
        return None
    args=envelope.get('args',{})
    if (isinstance(args,dict) and (envelope.get('action')=='search_evidence'
            or (envelope.get('action')=='browse_evidence' and args.get('view')=='related_group'))):
        return identity(service)
    return None

def attach(service, result, *, query, page, max_bytes):
    binding=identity(service)
    if binding is None:
        return
    hits=[*result['hits'],*result.get('unlocated_hits',[])]
    if not hits:
        return
    by_unit=_members(service)
    scope=dict(reader=binding,question_id=result['question_id'],query=query,page=page,
        query_id=result['query_id'],unit_ids=[h['unit_id'] for h in hits])
    search_id=digest(scope)
    linked=False
    for hit in hits:
        group=by_unit.get(hit['unit_id'])
        if group is None or group['kind']!='cosine_group':
            continue
        hit['related_group_action']=dict(action='browse_evidence',args=dict(view='related_group',
            search_id=search_id,anchor_unit_id=hit['unit_id']))
        if len(canonical_json(result).encode('utf-8'))>max_bytes:
            del hit['related_group_action']
        else:
            linked=True
    if linked:
        path=service.run_root/'related-group-searches'/(search_id+'.json')
        with build_lock(path.parent):
            if path.exists():
                if json.loads(path.read_text(encoding='utf-8'))!=scope:
                    raise EvidenceError('cluster_search_binding_changed')
            else:
                atomic_json(path,scope)

def browse(service, *, question_id, search_id, anchor_unit_id, families=None,
           cursor=0,limit=20,max_bytes=12000):
    binding=identity(service)
    if binding is None:
        raise EvidenceError('cluster_reader_not_configured')
    if (not isinstance(question_id,str) or not question_id.strip()
            or not isinstance(search_id,str) or len(search_id)!=64
            or any(c not in '0123456789abcdef' for c in search_id)
            or not isinstance(anchor_unit_id,str)
            or type(limit) is not int or not 1<=limit<=20
            or type(max_bytes) is not int or not 2000<=max_bytes<=128000):
        raise EvidenceError('cluster_browse_invalid')
    try:
        scope=json.loads((service.run_root/'related-group-searches'/(search_id+'.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise EvidenceError('cluster_search_binding_missing') from exc
    if (digest(scope)!=search_id or scope.get('reader')!=binding
            or scope.get('question_id')!=question_id or anchor_unit_id not in scope.get('unit_ids',[])):
        raise EvidenceError('cluster_search_anchor_mismatch')
    group=_members(service).get(anchor_unit_id)
    if group is None or group['kind']!='cosine_group':
        raise EvidenceError('cluster_anchor_not_grouped')
    selected=service._families(families or list(service.policy['allowed']))
    with service.open():
        pass
    members=[dict(unit_id=o['unit_id'],family=o['family'],start_us=o['start_us'],end_us=o['end_us'],
        member=deepcopy(m)) for o in group['occurrences'] if o['family'] in selected for m in o['members']]
    page_binding=[binding,question_id,search_id,anchor_unit_id,selected,limit,max_bytes]
    offsets={digest([page_binding,i]):i for i in range(1,len(members))}
    offset=0 if type(cursor) is int and cursor==0 else offsets.get(cursor) if isinstance(cursor,str) else None
    if offset is None:
        raise EvidenceError('cluster_cursor_mismatch')
    result=dict(schema_version='ew.related-group-page.v1',source_id=service.source_id,
        world_revision=service.world_revision,reader_revision=binding,group_id=group['group_id'],
        question_id=question_id,search_id=search_id,query_id=scope['query_id'],
        anchor_unit_id=anchor_unit_id,members=[],next_cursor=None,status='complete',
        provider_calls=0,derived=True,raw_read=False,meaning_complete=False,coverage_complete=False,
        same_event_inferred=False,outside_navigation='source_order')

    def continuation(value,index):
        token=digest([page_binding,index]) if index<len(members) else None
        value.update(next_cursor=token,status='partial_page' if token else 'complete')
        if token:
            value['next_action']=dict(action='browse_evidence',args=dict(view='related_group',
                search_id=search_id,anchor_unit_id=anchor_unit_id,families=selected,
                limit=limit,max_bytes=max_bytes,cursor=token))
        else:
            value.pop('next_action',None)
        return value

    index=offset
    while index<len(members) and len(result['members'])<limit:
        occurrence=members[index]
        occurrence['next_action']=dict(action='read_evidence',args=dict(
            record_ids=[occurrence['member']['record_id']],families=[occurrence['family']],
            context_cues=0,context_order='source_order'))
        trial=continuation(dict(result,members=[*result['members'],occurrence]),index+1)
        if len(canonical_json(trial).encode('utf-8'))>max_bytes:
            if not result['members']:
                raise EvidenceError('cluster_page_bytes_too_small')
            break
        result=trial
        index+=1

    if not result['next_cursor']:
        result.pop('next_action',None)
    return result
