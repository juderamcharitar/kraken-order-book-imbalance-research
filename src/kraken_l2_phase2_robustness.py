#!/usr/bin/env python3
"""Kraken L2 Phase 2: robustness diagnostics for Phase 1 order-book imbalance.

Reads ONLY Phase 1 one_second_l2_features.csv. Does not modify source CSV.
Performs exact-second, within-segment forward-return matching; protects clock
warning intervals; reports pooled vs segment/time-demeaned and nonoverlapping
correlations, per-segment correlations, and a chronological 70/30 linear
predictor diagnostic. Not a preregistered test, profit simulation, or causal
market-impact experiment. Standard Python 3 only.
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, fmean

HORIZONS = (1,5,10,30,60)


def is_finite(value):
    return isinstance(value,(float,int)) and math.isfinite(value)


def corr(pairs):
    n=len(pairs)
    if n<3:
        return None
    mx=fmean(p[0] for p in pairs)
    my=fmean(p[1] for p in pairs)
    sxx=sum((p[0]-mx)**2 for p in pairs)
    syy=sum((p[1]-my)**2 for p in pairs)
    if sxx<=0 or syy<=0:
        return None
    return sum((p[0]-mx)*(p[1]-my) for p in pairs)/math.sqrt(sxx*syy)


def demeaned_corr(records, group_key):
    sums=defaultdict(lambda:[0.,0.,0])
    for rec in records:
        k=group_key(rec)
        acc=sums[k]
        acc[0]+=rec['x'];acc[1]+=rec['y'];acc[2]+=1
    means={k:(v[0]/v[2],v[1]/v[2]) for k,v in sums.items()}
    return corr([(r['x']-means[group_key(r)][0],r['y']-means[group_key(r)][1]) for r in records])


def parse_time(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:
        raise ValueError('UTC timezone offset missing in second_utc')
    return int(d.timestamp())


def read_data(path):
    groups=defaultdict(list)
    raw=0
    with path.open('r',encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        required={'segment','second_utc','mid','imbalance10','timestamp_warning_nearby'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'Expected columns missing: {required - set(reader.fieldnames or [])}')
        for line,row in enumerate(reader,2):
            raw+=1
            try:
                seg=int(row['segment'])
                t=parse_time(row['second_utc'])
                mid=float(row['mid'])
                x=float(row['imbalance10'])
                flag=int(row['timestamp_warning_nearby'])
            except (TypeError,ValueError) as err:
                raise ValueError(f'Line {line}: malformed feature data: {err}') from err
            if not (is_finite(mid) and mid>0 and is_finite(x) and -1.000001<=x<=1.000001):
                raise ValueError(f'Line {line}: invalid midpoint or imbalance')
            groups[seg].append({'segment':seg,'t':t,'mid':mid,'x':x,'warning':bool(flag)})
    for seg,rows in groups.items():
        rows.sort(key=lambda r:r['t'])
        if len({r['t'] for r in rows})!=len(rows):
            raise ValueError(f'Segment {seg}: duplicate second_utc detected')
    return groups,raw


def records_for_horizon(groups,h):
    samples=[]
    for seg,rows in groups.items():
        bytime={r['t']:i for i,r in enumerate(rows)}
        warn_prefix=[0]
        for r in rows:
            warn_prefix.append(warn_prefix[-1]+int(r['warning']))
        for i,r in enumerate(rows):
            j=bytime.get(r['t']+h)
            if j is None:
                continue
            # Do not use any interval crossing a recorded clock-anomaly warning.
            if warn_prefix[j+1]-warn_prefix[i]>0:
                continue
            nxt=rows[j]
            y=10000.*(nxt['mid']/r['mid']-1.)
            if not is_finite(y):
                continue
            samples.append({'segment':seg,'t':r['t'],'x':r['x'],'y':y})
    return samples


def split_diagnostic(records, start, end, h):
    # Fixed 70% chronological split, with a 60s embargo on test starts.
    boundary=start+int((end-start)*0.7)
    train=[r for r in records if r['t']+h<=boundary]
    test=[r for r in records if r['t']>=boundary+60]
    result={'split_epoch_utc':datetime.fromtimestamp(boundary, timezone.utc).isoformat(),
            'train_n':len(train),'test_n':len(test),'test_r2_vs_constant':None,
            'test_corr':None,'test_mse_bps2':None,'constant_mse_bps2':None,
            'slope_bps_per_imbalance_unit':None}
    if len(train)<10 or len(test)<10:return result
    mx=fmean(r['x'] for r in train)
    my=fmean(r['y'] for r in train)
    den=sum((r['x']-mx)**2 for r in train)
    if den<=0:return result
    slope=sum((r['x']-mx)*(r['y']-my) for r in train)/den
    intercept=my-slope*mx
    mse=fmean((r['y']-(intercept+slope*r['x']))**2 for r in test)
    mse_base=fmean((r['y']-my)**2 for r in test)
    result.update({'test_r2_vs_constant':(1-mse/mse_base) if mse_base>0 else None,
                   'test_corr':corr([(r['x'],r['y']) for r in test]),
                   'test_mse_bps2':mse,'constant_mse_bps2':mse_base,
                   'slope_bps_per_imbalance_unit':slope})
    return result


def show(x):
    return 'NA' if x is None else f'{x:+.4f}'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',required=True,type=Path,help='Phase 1 one_second_l2_features.csv')
    p.add_argument('--outdir',default=Path('kraken_research_phase2'),type=Path)
    args=p.parse_args()
    if not args.features.is_file():
        p.error(f'Features CSV not found: {args.features}')
    groups,n=read_data(args.features)
    start=min(r['t'] for rs in groups.values() for r in rs)
    end=max(r['t'] for rs in groups.values() for r in rs)
    args.outdir.mkdir(parents=True,exist_ok=True)
    summary=[];per_seg=[]
    for h in HORIZONS:
        samples=records_for_horizon(groups,h)
        # Pre-declared, UTC-aligned non-overlapping forward-return starts.
        nonoverlap=[r for r in samples if r['t']%h==0]
        seg_corrs=[]
        for seg in sorted(groups):
            sub=[r for r in samples if r['segment']==seg]
            c=corr([(r['x'],r['y']) for r in sub])
            per_seg.append({'horizon_s':h,'segment':seg,'n':len(sub),'correlation':c})
            if len(sub)>=100 and c is not None:
                seg_corrs.append(c)
        # Observational, time-demeaned diagnostic uses complete retrospective buckets,
        # not information accessible for real-time trading.
        s={'horizon_s':h,'n':len(samples),
           'pooled_corr':corr([(r['x'],r['y']) for r in samples]),
           'within_segment_corr':demeaned_corr(samples,lambda r:r['segment']),
           'within_segment_5min_bucket_corr':demeaned_corr(samples,lambda r:(r['segment'],r['t']//300)),
           'nonoverlap_n':len(nonoverlap),
           'nonoverlap_corr':corr([(r['x'],r['y']) for r in nonoverlap]),
           'eligible_segments_n_100':len(seg_corrs),
           'positive_segment_corrs_n_100':sum(c>0 for c in seg_corrs),
           'median_segment_corr_n_100':median(seg_corrs) if seg_corrs else None}
        s.update(split_diagnostic(samples,start,end,h))
        summary.append(s)
        print(f'{h:>2}s n={len(samples):,} pooled={show(s["pooled_corr"])} '
              f'within_5min={show(s["within_segment_5min_bucket_corr"])} '
              f'nonoverlap={show(s["nonoverlap_corr"])} '
              f'segments_positive={s["positive_segment_corrs_n_100"]}/{s["eligible_segments_n_100"]} '
              f'holdout_R2={show(s["test_r2_vs_constant"])}',flush=True)
    def save_csv(path,rows):
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
            writer.writeheader();writer.writerows(rows)
    save_csv(args.outdir/'robustness_summary.csv',summary)
    save_csv(args.outdir/'per_segment_correlations.csv',per_seg)
    report={'analysis':'Phase 2 exploratory robustness diagnostics',
            'input_features':str(args.features),'source_rows':n,
            'segments':len(groups), 'start_utc':datetime.fromtimestamp(start, timezone.utc).isoformat(),
            'end_utc':datetime.fromtimestamp(end, timezone.utc).isoformat(),
            'results':summary,
            'qualifications':[
                'All choices and comparisons are post hoc for this one 24-hour recording; there is no fully unseen/preregistered dataset.',
                'No independent market data source or price-feed validation.',
                'Five-minute demeaning is a retrospective robustness control and not usable in a live strategy.',
                'Chronological 70/30 diagnostic has a 60-second embargo and compares OLS to training-set mean, not to all reasonable baselines.',
                'No day-level uncertainty quantification; 86,000 seconds do not represent 86,000 independent observations.',
                'Midprice return does not account for spread, fees, impact, latency, fill probabilities or cancellations.',
                'Observations require exact UTC seconds within each original segment; unobserved seconds and clock warnings are excluded.',
                'Research project measures correlations and hypothetical liquidity; no causal market-impact identification.'
            ]}
    (args.outdir/'robustness_summary.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(f'SUMMARY: horizons={len(HORIZONS)}; segments={len(groups)}; feature_rows={n:,}')
    print(f'CSV: {(args.outdir/"robustness_summary.csv").resolve()}')
    print(f'PER_SEGMENT: {(args.outdir/"per_segment_correlations.csv").resolve()}')
    print(f'JSON: {(args.outdir/"robustness_summary.json").resolve()}')
    print('CAUTION: single-day exploratory diagnostics; not independent scientific replication or live trading profitability.')


if __name__=='__main__':
    main()
