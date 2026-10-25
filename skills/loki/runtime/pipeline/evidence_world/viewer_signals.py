"""Source-bound viewer observations; never entrants, retention or causal claims."""

from bisect import bisect_left, bisect_right

from statistics import median

CONTEXT_MS = 120000

def _time(row):
    t=row['payload'].get('observed_at_ms')
    return t if type(t) is int and t >= 0 else None

def _count(row):
    n=row['payload'].get('viewer_count')
    return n if type(n) is int and n >= 0 else None

def viewer_items(records):
    observations=[r for r in records if r['native_locator']['table']=='metadata_observations']
    changes=[r for r in records if r['native_locator']['table']=='metadata_changes']
    located=sorted((r for r in observations if _time(r) is not None),key=lambda r:(_time(r),r['ordinal']))
    times=[_time(r) for r in located]
    gaps=[b-a for a,b in zip(times,times[1:]) if b>a]
    expected_gap=median(gaps) if gaps else None
    gap_limit=3*expected_gap if expected_gap is not None else None
    rates=sorted(abs((_count(b)-_count(a))/((tb-ta)/1000))
        for a,b,ta,tb in zip(located,located[1:],times,times[1:])
        if _count(a) is not None and _count(b) is not None and tb>ta and
           (gap_limit is None or tb-ta<=gap_limit))

    def window(start,end):
        samples=located[bisect_left(times,start):bisect_right(times,end)]
        valid=[r for r in samples if _count(r) is not None]
        ts=[_time(r) for r in samples]
        span=[ts[0],ts[-1]] if ts else None
        elapsed=(_time(valid[-1])-_time(valid[0]))/1000 if len(valid)>1 else 0
        return dict(requested_interval_utc_ms=[start,end],observed_interval_utc_ms=span,
            sample_count=len(samples),valid_sample_count=len(valid),null_or_invalid_count=len(samples)-len(valid),
            zero_count=sum(_count(r)==0 for r in valid),median=median(_count(r) for r in valid) if valid else None,
            slope_per_second=(_count(valid[-1])-_count(valid[0]))/elapsed if elapsed>0 else None,
            large_gap_count=sum(b-a>gap_limit for a,b in zip(ts,ts[1:])) if gap_limit is not None else None,
            duplicate_time_count=sum(a==b for a,b in zip(ts,ts[1:])),
            capture_boundary_truncated=bool(times and (start<times[0] or end>times[-1])),
            source_record_ids=[r['record_id'] for r in samples[:12]],
            source_record_ids_truncated=len(samples)>12)

    result=[]
    for a,b in zip(located,located[1:]):
        ta,tb,na,nb=_time(a),_time(b),_count(a),_count(b)
        elapsed=tb-ta
        delta=nb-na if na is not None and nb is not None else None
        rate=delta/(elapsed/1000) if delta is not None and elapsed>0 else None
        flags=[]
        if elapsed==0: flags.append('duplicate_observation_time')
        if gap_limit is not None and elapsed>gap_limit: flags.append('collection_gap_no_interpolation')
        if na is None or nb is None: flags.append('null_or_invalid_viewer_count')
        if na==0 or nb==0: flags.append('observed_zero_not_missing')
        if tb-times[0]<=CONTEXT_MS: flags.append('near_capture_start_not_verified_broadcast_start')
        if times[-1]-ta<=CONTEXT_MS: flags.append('near_capture_end_not_verified_broadcast_end')
        related=[r for r in changes if type(r['raw_time']['start']) is int and type(r['raw_time']['end']) is int
                 and r['raw_time']['start']<=tb and r['raw_time']['end']>=ta]
        before,after=window(ta-CONTEXT_MS,ta),window(tb,tb+CONTEXT_MS)
        med_delta=after['median']-before['median'] if before['median'] is not None and after['median'] is not None else None
        result.append(dict(kind='viewer_change',family='live_metadata',record_id=b['record_id'],
            source_record_ids=[a['record_id'],b['record_id']],interval_sec=None,
            interval_utc_ms=[ta,tb] if elapsed>0 else None,clock_domain='utc_epoch_ms',
            playable=False,uncertainties=['vod_clock_unverified'],
            raw=dict(previous_count=na,current_count=nb,elapsed_ms=elapsed,delta=delta,
                     change_per_second=rate,increase_observed=delta is not None and delta>0),
            baseline=dict(method='absolute adjacent change per elapsed second; nongap valid pairs',
                median_sample_gap_ms=expected_gap,gap_limit_ms=gap_limit,sample_count=len(rates),
                observed_interval_utc_ms=[times[0],times[-1]],
                median_absolute_change_per_second=median(rates) if rates else None),
            relative=dict(delta_ratio_of_previous=delta/na if delta is not None and na>0 else None,
                absolute_rate_percentile=bisect_right(rates,abs(rate))/len(rates) if rate is not None and rates and
                    'collection_gap_no_interpolation' not in flags else None),
            before=before,after=after,observed_after_minus_before_median=med_delta,
            metadata_change_refs=[r['record_id'] for r in related],
            endpoint_changed_fields=[k for k in ('title','category_type','category_code','game_value') if a['payload'].get(k)!=b['payload'].get(k)],
            quality_flags=flags,coverage_complete=False,
            interpretation='Observed viewer levels; no individual entrances, scene causality or individual retention inferred.'))

    for r in observations:
        if _time(r) is None:
            result.append(dict(kind='viewer_observation_unlocated',family='live_metadata',record_id=r['record_id'],
                source_record_ids=[r['record_id']],interval_sec=None,interval_utc_ms=None,clock_domain='utc_epoch_ms',
                playable=False,quality_flags=['missing_or_invalid_observation_time'],coverage_complete=False))
    return result
