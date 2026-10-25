"""Copy one admitted cache for a different OS; preserve its private origin bytes.

Consumer paths are POSIX metadata, never interpreted as Windows host paths.
No encoders, downloads, author ledgers, model weights or SDK files are written.
"""

from __future__ import annotations

from copy import deepcopy

import re

from pathlib import PurePosixPath, PureWindowsPath

from . import editorial_multimodal_navigation as mm

from . import editorial_multimodal_search as search

from .evidence_world.contracts import digest, file_hash, native_path

SCHEMA = 'editorial.multimodal-search-transport.v1'

def _posix(path):
    if not isinstance(path, str) or '\\' in path:
        raise ValueError('search_transport_consumer_path')
    value = PurePosixPath(path)
    if not value.is_absolute() or '..' in value.parts:
        raise ValueError('search_transport_consumer_path')
    return value

def _retained_path(path):
    """The same-OS owner may retain a Windows origin; no relative/UNC locators."""
    if not isinstance(path, str):
        raise ValueError('search_transport_consumer_path')
    if re.match(r'^\\\\\?\\[A-Za-z]:[\\/]', path):
        path = path[4:]
    if re.match(r'^[A-Za-z]:[\\/]', path):
        value = PureWindowsPath(path)
        if not value.is_absolute() or '..' in value.parts:
            raise ValueError('search_transport_consumer_path')
        return value
    return _posix(path)

def _read(ref, resolver):
    local = dict(ref, path=str(resolver(ref['path'])))
    return mm._bound_json(local, max_bytes=64_000_000)[0]

def _project(origin, mapping, runtime):
    """Closed field whitelist; all original occurrences and vector refs stay exact."""
    spec, material, result = (deepcopy(origin[k]) for k in ('spec', 'input', 'result'))
    binding = deepcopy(origin['request']['binding'])
    locator = spec['source']['asset']['source_locator']
    for key in ('path', 'meta_path'):
        if key in locator:
            locator[key] = mapping[locator[key]]
    source = spec['source']
    source['sha256'] = digest({k:v for k,v in source.items() if k != 'sha256'})
    binding['source_revision'] = source['sha256']
    binding['media']['path'] = mapping[binding['media']['path']]
    binding['runtime'] = deepcopy(runtime)
    spec['media_ref']['path'] = mapping[spec['media_ref']['path']]
    spec['output_root'] = mapping[spec['output_root']]
    spec['model_ref'] = deepcopy(runtime['model_ref'])
    spec['sdk_root'] = runtime['sdk_root']
    material['contract']['source_revision'] = source['sha256']
    if 'path' in material['contract']['media']:
        path = material['contract']['media']['path']
        material['contract']['media']['path'] = mapping[path]
    result['attempt']['source_input_sha256'] = digest(material)
    for stage in result['stages']:
        stage['path'] = mapping[stage['path']]
    return dict(spec=spec, input=material, result=result, binding=binding)

def _validate_projection(request, resolver, *, verify_copies=False):
    marker = request['origin_provenance']
    if marker.get('schema') != SCHEMA or not mm._sha(marker.get('origin_digest')):
        raise ValueError('search_transport_provenance')
    path = _retained_path if marker.get('kind') == 'retention' else _posix
    target = path(marker['archive_ref']['path']).parent.parent
    if (target.name != marker['origin_digest']
            or marker['archive_ref']['path'] != str(target/'origin'/'origin.json')
            or request['spec_ref']['path'] != str(target/'spec.json')
            or any(request['cache_ref'][name]['path'] != str(target/'cache'/(name+'.json'))
                for name in ('input', 'result'))):
        raise ValueError('search_transport_projection_changed')
    archived = _read(marker['archive_ref'], resolver)
    original = archived['request']
    if digest(original) != marker['origin_digest']:
        raise ValueError('search_transport_origin_changed')
    refs = dict(spec=original['spec_ref'], **original['cache_ref'])
    origin = dict(request=original)
    for name in ('spec', 'input', 'result', 'model'):
        ref = archived['archives'][name]
        if ref['sha256'] != refs[name]['sha256'] or ref['path'] != str(target/'origin'/(name+'.json')):
            raise ValueError('search_transport_origin_changed')
        origin[name] = _read(ref, resolver)
    consumer_model = _read(request['cache_ref']['model'], resolver)
    mm._validate_local_model_manifest(origin['model'])
    mm._validate_local_model_manifest(consumer_model)
    if search._basis(origin['model']) != search._basis(consumer_model):
        raise ValueError('search_transport_model_basis')
    expected = _project(origin, archived['mapping'], archived['runtime_policy'])
    if (request['binding'] != expected['binding']
            or request['cache_ref']['model'] != archived['runtime_policy']['model_ref']):
        raise ValueError('search_transport_projection_changed')
    for name, ref in [('spec', request['spec_ref']), ('input', request['cache_ref']['input']),
            ('result', request['cache_ref']['result'])]:
        if _read(ref, resolver) != expected[name]:
            raise ValueError('search_transport_projection_changed')
    for copied in archived['copies']:
        relative = path(copied['path']).relative_to(target)
        if not relative.parts or relative.parts[0] not in {'source', 'cache'}:
            raise ValueError('search_transport_projection_changed')
        if archived['mapping'].get(copied['origin']) != copied['path']:
            raise ValueError('search_transport_projection_changed')
        if verify_copies and file_hash(resolver(copied['path'])) != copied['sha256']:
            raise ValueError('search_transport_copy_changed')
    if 'job_tie' in archived:
        tie = archived['job_tie']
        if (not isinstance(tie, dict) or set(tie) != {'original_root', 'cold_root', 'prepared_ref', 'request_relative'}
                or tie['request_relative'] != _retained_path(original['spec_ref']['path']).relative_to(_retained_path(tie['original_root'])).as_posix()
                or not re.fullmatch(r'multimodal/request-[0-9a-f]{64}/spec.json', tie['request_relative'])
                or tie['prepared_ref']['path'] != str(path(tie['cold_root'])/'prepared.json')):
            raise ValueError('search_transport_job_changed')
        prepared = _read(tie['prepared_ref'], resolver)
        identity = prepared.get('identity') or {}
        if (prepared.get('schema_version') != 'chzz.current_conversation_prepared.v1'
                or digest({k:v for k,v in prepared.items() if k != 'prepared_revision'}) != prepared.get('prepared_revision')
                or identity.get('run_root') != tie['original_root']
                or identity.get('video_no') != origin['spec']['source']['asset']['video_no']
                or prepared.get('source', {}).get('asset', {}).get('bytes_sha256') != original['binding']['stt_sha256']
                or path(tie['cold_root']).parts[-3:-1] != (identity['video_no'], 'current-conversation')
                or path(tie['cold_root']).name != identity.get('job_id')):
            raise ValueError('search_transport_job_changed')
    return True

def validate_projection(request):
    """Consumer gate: prove only admitted path/receipt fields changed in transport."""
    return _validate_projection(request, native_path)

def projected_job_root(request):
    """Return an original-job tie only after the complete transport proof passes."""
    validate_projection(request)
    archived = _read(request['origin_provenance']['archive_ref'], native_path)
    return (native_path(archived['job_tie']['cold_root'])
            if 'job_tie' in archived else None)

def _project_query(material, result, model, consumer_model, vector_path):
    mm._validate_local_model_manifest(model)
    mm._validate_local_model_manifest(consumer_model)
    basis = search._basis(model)
    legacy = dict(basis, files_sha256=digest(model['files']))
    if (basis != search._basis(consumer_model) or material.get('basis') not in (basis, legacy)
            or result.get('basis') != material.get('basis') or result.get('status') != 'settled'
            or result.get('input_sha256') != digest(material)
            or not isinstance(material.get('query'), str) or not material['query'].strip()
            or len(material['query'].encode('utf-8')) > 2000):
        raise ValueError('search_transport_query_basis')
    projected_material, projected_result = deepcopy(material), deepcopy(result)
    projected_material['basis'] = basis
    projected_result['basis'] = basis
    projected_result['input_sha256'] = digest(projected_material)
    projected_result['vector']['path'] = str(vector_path)
    return projected_material, projected_result

def _validate_query_projection(ref, resolver):
    marker = ref['origin_provenance']
    if marker.get('schema') != SCHEMA or marker.get('kind') != 'query':
        raise ValueError('search_transport_provenance')
    target = _posix(marker['archive_ref']['path']).parent.parent
    if (marker['archive_ref']['path'] != str(target/'origin'/'origin.json')
            or any(ref[name]['path'] != str(target/'cache'/(name+'.json')) for name in ('input', 'result'))):
        raise ValueError('search_transport_query_changed')
    archive = _read(marker['archive_ref'], resolver)
    origin = {}
    for name in ('input', 'result', 'model'):
        saved = archive['archives'][name]
        if (saved['sha256'] != archive['original_ref'][name]['sha256']
                or saved['path'] != str(target/'origin'/(name+'.json'))):
            raise ValueError('search_transport_origin_changed')
        origin[name] = _read(saved, resolver)
    model = _read(ref['model'], resolver)
    vector_path = _posix(marker['archive_ref']['path']).parent.parent/'cache'/'query.npy'
    material, result = _project_query(origin['input'], origin['result'], origin['model'], model, vector_path)
    if (target.name != 'query-'+digest(dict(query=material['query'], basis=material['basis']))
            or ref['model'] != archive['runtime_policy']['model_ref']
            or _read(ref['input'], resolver) != material or _read(ref['result'], resolver) != result
            or file_hash(resolver(str(vector_path))) != result['vector']['sha256']):
        raise ValueError('search_transport_query_changed')
    return True

def validate_query_projection(ref):
    """Prove a query rebase/canonical basis normalization against its exact origin."""
    return _validate_query_projection(ref, native_path)
