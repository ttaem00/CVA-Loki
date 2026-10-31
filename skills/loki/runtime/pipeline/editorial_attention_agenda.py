"""Bounded investigation projection for existing T2--T5 owners, never a ranker."""

def receipt(stage, inputs, produced=(), consumed=(), rejected=(), deferred=(), *, fatal=None):
    inputs, consumed, rejected, deferred = map(set, (inputs, consumed, rejected, deferred))
    unknown = (consumed | rejected | deferred) - inputs
    lost = inputs - consumed - rejected - deferred
    first = fatal or ("unknown_input_ref" if unknown else
                     "active_inputs_lost_without_reason" if inputs and lost == inputs else None)
    return dict(stage=stage,input_refs=sorted(inputs),produced_refs=sorted(set(produced)),
        consumed_refs=sorted(consumed),rejected_refs=sorted(rejected),deferred_refs=sorted(deferred),
        lost_without_reason=sorted(lost),first_break=first,next_stage_allowed=not bool(first))

def require_next(row):
    if not row["next_stage_allowed"]:
        raise ValueError("attention_first_break:"+row["stage"]+":"+row["first_break"])

def validate_receipts(rows):
    """Validate the preserved production receipt, not subsequent manager edits."""
    stages = ['attention_generation','d1d2_observation','point_story_selection','span_composition','highlight_result']
    if not isinstance(rows,list) or len(rows)!=len(stages):
        raise ValueError('attention_receipt_stages')
    for stage,row in zip(stages,rows):
        if not isinstance(row,dict) or row.get('stage')!=stage:
            raise ValueError('attention_receipt_stages')
        for key in ('input_refs','produced_refs','consumed_refs','rejected_refs','deferred_refs','lost_without_reason'):
            values=row.get(key)
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):
                raise ValueError('attention_receipt_refs')
        checked=receipt(stage,row['input_refs'],row['produced_refs'],row['consumed_refs'],row['rejected_refs'],row['deferred_refs'],fatal=row.get('first_break'))
        if any(checked[k]!=row.get(k) for k in ('lost_without_reason','first_break','next_stage_allowed')):
            raise ValueError('attention_receipt_disposition_mismatch')
        require_next(checked)
    if set(rows[0]['produced_refs'])!=set(rows[1]['input_refs']) or set(rows[3]['produced_refs'])!=set(rows[4]['input_refs']):
        raise ValueError('attention_receipt_stage_link_mismatch')
