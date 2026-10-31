"""VOD-owned LanceDB vectors joined to the existing Evidence World SQL units.

No server, model download, author decision or retention owner. All paths in a
saved index are relative to its VOD family, so the existing 30-day relocation
can copy the database together with its originals and SQL snapshot.
"""

from __future__ import annotations

import json

import time

from contextlib import nullcontext

from functools import lru_cache

from .evidence_world.contracts import EvidenceError, digest, native_path

from .evidence_world.index import atomic_json, build_lock

SCHEMA = 'ew.gemma2_lance.v1'

FAMILIES = ('stt', 'chat')

def fuse_score_frontier(lexical, dense_scores, native_order):
    """Opt-in DBSF on the measured 20+20 frontier, retaining the full tail.

    Each branch uses mean +/- three sample standard deviations, as documented
    by Qdrant. Scores are not clipped; constant/singleton branches contribute
    0.5. Ties keep first appearance, including the lexical-first tie contract.
    """
    import numpy as np
    dense_ids = sorted(dense_scores, key=lambda uid: (-dense_scores[uid], uid))[:20]
    branches = ([ (hit['unit_id'], hit['retrieval_rank_score']) for hit in lexical[:20] ],
                [ (uid, dense_scores[uid]) for uid in dense_ids ])
    total = {}
    for branch in branches:
        if not branch:
            continue
        values = np.asarray([score for _, score in branch], dtype=np.float64)
        if not np.isfinite(values).all():
            raise EvidenceError('fusion_requires_finite_scores')
        std = float(values.std(ddof=1)) if len(values) > 1 else 0.
        normalized = ((values - (values.mean() - 3 * std)) / (6 * std)
                      if std else np.full(len(values), .5))
        for (uid, _), score in zip(branch, normalized):
            total[uid] = total.get(uid, 0.) + float(score)
    front = sorted(total, key=lambda uid: -total[uid])
    return front + [uid for uid in native_order if uid not in total]

def query_cache_root():
    from .config import runtime_data_root
    return native_path(runtime_data_root())/'work'/'_embedding-queries'

@lru_cache(maxsize=1)
def _encoder(model_json,sdk,threads,device):
    from .editorial_multimodal_navigation import _load_local_model
    return _load_local_model(json.loads(model_json),sdk,threads,device=device)

def units(db):
    return [dict(r) for r in db.execute("SELECT unit_id,family,start_us,end_us,norm_text,members_json "
        "FROM units WHERE family IN ('stt','chat') ORDER BY unit_id") if r['norm_text'].strip()]

def model_basis(runtime):
    from .editorial_multimodal_navigation import _bound_json, _validate_local_model_manifest
    model, _ = _bound_json(runtime['model_ref'])
    _validate_local_model_manifest(model)
    return digest(dict(model=model['model'], revision=model['revision'],
        files=sorted(model['files'], key=lambda r:r['file'])))

def _connect(root):
    import lancedb
    return lancedb.connect(str(root))

def _table(db, name):
    import pyarrow as pa
    schema = pa.schema([pa.field('key',pa.string()), pa.field('role',pa.string()),
        pa.field('vector',pa.list_(pa.float32(),768))])
    return db.create_table(name, schema=schema, exist_ok=True)

def _where_keys(keys):

    from .editorial_multimodal_navigation import _sha
    if not keys or any(not _sha(k) for k in keys):
        raise EvidenceError('gemma_vector_key')
    return 'key IN (' + ','.join("'"+k+"'" for k in keys) + ')'

def encode_cached(texts, *, role, root, runtime, model=None):
    """Content cache in the vector DB. A different question never re-encodes documents."""
    import numpy as np
    if role not in {'document','query'} or not texts:
        raise EvidenceError('gemma_vector_role')
    root = native_path(root)
    root.mkdir(parents=True,exist_ok=True)
    basis = model_basis(runtime)
    keys = [digest([basis,role,t]) for t in texts]
    unique = dict(zip(keys,texts))
    table = _table(_connect(root/'lance'), 'vectors')
    found = {}
    for offset in range(0,len(keys),256):
        batch = list(dict.fromkeys(keys[offset:offset+256]))
        found.update({r['key']:r['vector'] for r in table.search().where(_where_keys(batch))
            .select(['key','vector']).limit(len(batch)).to_list()})
    missing = [k for k in unique if k not in found]
    started = time.monotonic()
    if missing:
        if model is None:
            from .editorial_multimodal_navigation import _bound_json
            manifest,_ = _bound_json(runtime['model_ref'])
            model = _encoder(json.dumps(manifest,sort_keys=True),runtime['sdk_root'],
                runtime['limits']['cpu_threads'],runtime.get('device','auto'))
        tokenizer = model[0].processor.tokenizer
        encode = model.encode_document if role=='document' else model.encode_query
        for offset in range(0,len(missing),512):
            batch = missing[offset:offset+512]
            content = [unique[k] for k in batch]
            if any(len(tokenizer(t)['input_ids'])>8192 for t in content):
                raise EvidenceError('gemma_document_context')
            vector = np.asarray(encode(content,normalize_embeddings=True,batch_size=16,
                show_progress_bar=False,convert_to_numpy=True),dtype=np.float32)
            if vector.shape != (len(batch),768) or not np.isfinite(vector).all():
                raise EvidenceError('gemma_vector_shape')
            norms = np.linalg.norm(vector,axis=1,keepdims=True)
            if (norms==0).any():
                raise EvidenceError('gemma_vector_zero')
            vector = vector/norms
            rows = [dict(key=k,role=role,vector=v.tolist()) for k,v in zip(batch,vector)]
            table.merge_insert('key').when_not_matched_insert_all().execute(rows)
            found.update({r['key']:r['vector'] for r in rows})
    return np.asarray([found[k] for k in keys],dtype=np.float32), dict(
        input_rows=len(keys),unique_rows=len(unique),encoded_rows=len(missing),
        reused_rows=len(keys)-len(missing),elapsed_sec=time.monotonic()-started,
        device=str(getattr(model,'device','not_loaded_cache_hit')))

def encode_media_cached(content,*,modality,root,runtime,model,media_sha256,window):
    import numpy as np
    if modality not in {'image','audio','video'}:
        raise EvidenceError('gemma_media_modality')
    key=digest([model_basis(runtime),modality,media_sha256,window['start'],window['duration'],'fps1-256x144-pcm16k-v1'])
    table=_table(_connect(native_path(root)/'lance'),'vectors')
    rows=table.search().where(_where_keys([key])).select(['vector']).limit(1).to_list()
    if rows:return np.asarray(rows[0]['vector'],dtype=np.float32).reshape(1,768)
    vector=np.asarray(model.encode({modality:content},normalize_embeddings=True,batch_size=1,
        show_progress_bar=False,convert_to_numpy=True),dtype=np.float32).reshape(1,768)
    if not np.isfinite(vector).all() or not np.allclose(np.linalg.norm(vector,axis=1),1,atol=.005):
        raise EvidenceError('gemma_media_vector')
    table.merge_insert('key').when_not_matched_insert_all().execute([
        dict(key=key,role=modality,vector=vector[0].tolist())])
    return vector

def prepare(service, *, source_dir, runtime, cached_only=False):
    """Build or reuse source-bound vectors; optional admission uses cached_only."""
    import pyarrow as pa
    from .evidence_world.records import assert_asset_fresh
    for asset in service.assets.values():assert_asset_fresh(asset)
    root = native_path(source_dir)/'embedding-index'
    with service.open() as db:
        rows = units(db)
    if not rows:
        return None
    basis = model_basis(runtime)
    identity = digest([basis,rows])
    name = 'units_'+identity
    cfg = dict(schema_version=SCHEMA,table=name,units_sha256=digest(rows),model_basis=basis,
        relative_root='embedding-index')
    root.mkdir(parents=True,exist_ok=True)
    receipt = root/'manifests'/(identity+'.json')
    with (nullcontext() if cached_only else build_lock(root)):
        if receipt.is_file():
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if saved['config']!=cfg:
                raise EvidenceError('gemma_index_identity_changed')
            table=_connect(root/'lance').open_table(name)
            if table.count_rows()!=len(rows) or table.version!=saved['table_version']:
                raise EvidenceError('gemma_index_changed')
            return cfg
        if cached_only:
            return None
        vectors, stats = encode_cached([r['norm_text'] for r in rows],role='document',root=root,runtime=runtime)
        schema=pa.schema([pa.field('unit_id',pa.string()),pa.field('family',pa.string()),
            pa.field('start_us',pa.int64()),pa.field('end_us',pa.int64()),
            pa.field('members_json',pa.string()),pa.field('vector',pa.list_(pa.float32(),768))])
        data=[dict(unit_id=r['unit_id'],family=r['family'],start_us=r['start_us'],end_us=r['end_us'],
            members_json=r['members_json'],vector=v.tolist()) for r,v in zip(rows,vectors)]
        table=_connect(root/'lance').create_table(name,data=data,schema=schema,mode='overwrite')

        partitions=max(1,int(len(rows)**.5))
        if len(rows)>=256:
            table.create_index(metric='cosine',index_type='IVF_FLAT',num_partitions=partitions)
        atomic_json(receipt,dict(schema_version=SCHEMA,config=cfg,rows=len(rows),
            table_version=table.version,
            partitions=partitions if len(rows)>=256 else 0,stats=stats,
            join='Evidence World units.unit_id -> members_json -> records.record_id',
            coverage_families=list(FAMILIES),original_read=False))
        for asset in service.assets.values():assert_asset_fresh(asset)
    return cfg

def prepare_optional(service,*,source_dir,runtime,cached_only=True):
    """New authoring reuses settled vectors; lexical originals need no encoder."""
    if runtime is None:return None
    root=native_path(source_dir)/'embedding-index'
    try:
        result=prepare(service,source_dir=source_dir,runtime=runtime,cached_only=cached_only)
    except (ImportError,OSError,ValueError,RuntimeError) as exc:
        atomic_json(root/'preparation-status.json',dict(status='unavailable',
            reason=type(exc).__name__,original_search_available=True))
        return None
    if result is None:
        atomic_json(root/'preparation-status.json',dict(status='unavailable',
            reason='index_not_prepared',original_search_available=True))
    else:
        atomic_json(root/'preparation-status.json',dict(status='ready',config=result))
    return result

class SavedGemmaSearch:
    """Native vector search; SQL remains the authority for returned original IDs."""
    families=list(FAMILIES)
    strategy='local_query_only'
    prepares_query_outside_ranking_budget=True

    def __init__(self,config,*,source_dir):
        if (set(config)!={'schema_version','table','units_sha256','model_basis','relative_root'}
                or config['schema_version']!=SCHEMA or config['relative_root']!='embedding-index'):
            raise EvidenceError('gemma_index_config')
        self.config=config
        self.root=native_path(source_dir)/'embedding-index'
        self.identity=digest(config)
        self._query={}
        self._rows=None
        from .editorial_multimodal_navigation import resolve_local_runtime
        self.runtime=resolve_local_runtime()
        if self.runtime is None or model_basis(self.runtime)!=config['model_basis']:
            raise EvidenceError('gemma_index_model_unavailable')

    def validate(self,db,*,world_revision,deadline):
        from .editorial_multimodal_navigation import _sha
        cfg=self.config
        if (not _sha(cfg.get('units_sha256')) or not _sha(cfg.get('model_basis'))
                or not cfg.get('table','').startswith('units_') or not _sha(cfg['table'][6:])):
            raise EvidenceError('gemma_index_config')
        rows=units(db)
        if digest(rows)!=cfg['units_sha256'] or model_basis(self.runtime)!=cfg['model_basis']:
            raise EvidenceError('gemma_index_source_changed')
        path=self.root/'manifests'/(cfg['table'][6:]+'.json')
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved['config']!=cfg or saved['rows']!=len(rows):
            raise EvidenceError('gemma_index_changed')
        table=_connect(self.root/'lance').open_table(cfg['table'])
        if table.count_rows()!=len(rows) or table.version!=saved['table_version']:
            raise EvidenceError('gemma_index_changed')
        self._rows={r['unit_id']:r for r in rows}
        self._table=table
        self._partitions=saved['partitions']

    def query_vector(self,text,*,deadline):
        if text not in self._query:


            root=query_cache_root()
            with build_lock(root):
                vectors,_=encode_cached([text],role='query',root=root,runtime=self.runtime,
                    model=None)
            self._query[text]=vectors[0].tolist()
        return self._query[text]

    def calculate(self,db,query,lexical,*,deadline):
        from .evidence_world.query import _scoped_text
        ranked={}
        for text in [query['text'],*query.get('variants',[])]:
            search=self._table.search(self.query_vector(text,deadline=deadline)).distance_type('cosine')
            search=search.where('family IN ('+','.join("'"+f+"'" for f in query['families'])+')')


            if self._partitions:
                search=search.nprobes(self._partitions)
            for hit in search.select(['unit_id','_distance']).limit(len(self._rows)).to_list():
                uid=hit['unit_id']
                ranked[uid]=max(ranked.get(uid,-2.),1.-hit['_distance'])
        scoped={}
        for uid in ranked:
            _,members=_scoped_text(db,self._rows[uid],query)
            if members:scoped[uid]=members
        ids=sorted(scoped,key=lambda k:(-ranked[k],k))
        lexical_ids=[h['unit_id'] for h in lexical]
        for uid in lexical_ids:
            if uid not in ids:
                raise EvidenceError('gemma_index_missing_unit')


        ranks={"lexical":{uid:n for n,uid in enumerate(lexical_ids,1)},
               "dense":{uid:n for n,uid in enumerate(ids,1)}}
        order=[];seen=set()
        for n in range(max(len(lexical_ids),len(ids))):
            for branch,values in (("lexical",lexical_ids),("dense",ids)):
                if n>=len(values) or values[n] in seen:continue
                uid=values[n];seen.add(uid)
                order.append(dict(unit_id=uid,exposed_by=branch,
                    branch_ranks={name:rank[uid] for name,rank in ranks.items() if uid in rank}))
        by_id={h['unit_id']:h for h in lexical}
        mode = query.get('mode', 'hybrid_branch_v1')
        if mode == 'hybrid_dbsf_v1':
            native = {row['unit_id']: row for row in order}
            fused = fuse_score_frontier(lexical, {uid:ranked[uid] for uid in ids}, list(native))
            order = [native[uid] for uid in fused]
        return [dict(unit_id=r['unit_id'],family=self._rows[r['unit_id']]['family'],
            evidence_members=scoped[r['unit_id']],methods=[*by_id.get(r['unit_id'],{}).get('methods',[]),mode],
            retrieval_rank_score=1./(i+1),dense_cosine=ranked[r['unit_id']],branch_ranks=r['branch_ranks'],
            exposed_by=r['exposed_by'],representation_scope='source_bound_gemma2_lancedb_units')
            for i,r in enumerate(order)]
