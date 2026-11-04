#!/usr/bin/env python3
"""Kraken BTC/USD L2 Phase 4: time-block uncertainty and test-decile stress checks.

Read-only analysis of the Phase 1 one_second_l2_features.csv, Python standard library only.
Primary setting is the 5-second return (selected after earlier exploration).

Computes:
* within-five-minute correlation of 10-level book imbalance and h-second forward
  MIDPRICE returns, with a 5-minute time-block bootstrap sensitivity interval;
* a within-block circular-shift placebo diagnostic (not a definitive p-value);
* test-period deciles of imbalance, using thresholds determined entirely from
  the chronological 70% TRAINING period, with a block-bootstrap interval for
  the test top-minus-bottom return difference;
* correlation and decile statistics without crossing segment boundaries or
  passing through the documented receive-timestamp anomaly.

Interpretation limits: ONE pre-explored 24h observation, overlapping labels,
no external/day-level replication, no execution prices/fees/fills or causal
market-impact identification. Bootstrap intervals and placebo p-values are
within-day sensitivity checks, NOT generalizable significance claims.
"""
import argparse
import bisect
import csv
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def epoch(v):
    d=datetime.fromisoformat(v.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('Timestamp requires UTC offset')
    return int(d.timestamp())


def percentile(sorted_values, fraction):
    pos=(len(sorted_values)-1)*fraction
    lo=int(pos); hi=min(lo+1,len(sorted_values)-1)
    return sorted_values[lo]*(1-(pos-lo))+sorted_values[hi]*(pos-lo)


def corr_from_sums(n,sxx,syy,sxy):
    return sxy/math.sqrt(sxx*syy) if n>=3 and sxx>0 and syy>0 else None


def load_samples(path,h):
    required={'segment','second_utc','mid','imbalance10','timestamp_warning_nearby'}
    groups=defaultdict(list)
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        missing=required-set(reader.fieldnames or [])
        if missing: raise ValueError('Missing required columns: '+repr(sorted(missing)))
        for lineno,row in enumerate(reader,2):
            try:
                seg=int(row['segment']); t=epoch(row['second_utc'])
                mid=float(row['mid']); imbalance=float(row['imbalance10'])
                warning=int(row['timestamp_warning_nearby'])
            except (ValueError,TypeError) as e:
                raise ValueError(f'Line {lineno}: {e}') from e
            if not (math.isfinite(mid) and mid>0 and math.isfinite(imbalance)
                    and -1.000001<=imbalance<=1.000001 and warning in (0,1)):
                raise ValueError(f'Invalid mid, imbalance, or warning on line {lineno}')
            groups[seg].append((t,mid,imbalance,warning))
    if not groups: raise ValueError('No feature rows')
    samples=[]
    for seg, rows in groups.items():
        rows.sort()
        if any(rows[i][0]==rows[i+1][0] for i in range(len(rows)-1)):
            raise ValueError(f'Duplicate observed UTC second in segment {seg}')
        at={row[0]:i for i,row in enumerate(rows)}
        prefix=[0]
        for row in rows: prefix.append(prefix[-1]+row[3])
        for i,(t,mid,x,warning) in enumerate(rows):
            prev=at.get(t-1)
            j=at.get(t+h)
            # Match Phase 3's eligibility, including prior-second observation
            if prev is None or j is None or prefix[j+1]-prefix[prev]>0:
                continue
            y=10000*(rows[j][1]/mid-1)
            samples.append({'seg':seg,'t':t,'x':x,'y':y,'block':(seg,t//300)})
    samples.sort(key=lambda r:(r['t'],r['seg']))
    return samples, len(groups),sum(map(len,groups.values()))


def group_5min(rows,min_size=20):
    blocks=defaultdict(list)
    for row in rows: blocks[row['block']].append(row)
    return [block for block in blocks.values() if len(block)>=min_size]


def block_stats(block):
    n=len(block)
    mx=math.fsum(r['x'] for r in block)/n
    my=math.fsum(r['y'] for r in block)/n
    x=[r['x']-mx for r in block]
    y=[r['y']-my for r in block]
    return {'n':n,'sxx':math.fsum(v*v for v in x),
            'syy':math.fsum(v*v for v in y),
            'sxy':math.fsum(a*b for a,b in zip(x,y)),
            'xcenter':x,'ycenter':y}


def calc_within(stats):
    n=sum(st['n'] for st in stats)
    xx=sum(st['sxx'] for st in stats)
    yy=sum(st['syy'] for st in stats)
    xy=sum(st['sxy'] for st in stats)
    return corr_from_sums(n,xx,yy,xy)


def bootstrap_within(stats,rng,reps):
    v=[];k=len(stats)
    for _ in range(reps):
        chosen=[stats[rng.randrange(k)] for _ in range(k)]
        r=calc_within(chosen)
        if r is not None: v.append(r)
    v.sort()
    return [percentile(v,.025),percentile(v,.975)] if v else [None,None]


def shift_placebo(stats,rng,permutations,minimum_shift=60):
    # Rotation preserves serial structure of x and all within-block means.
    # Use only blocks long enough to rotate at least minimum_shift positions
    # away in either direction. Correlations on these SAME blocks are compared.
    usable=[st for st in stats if st['n']>=2*minimum_shift+1]
    if len(usable)<8: return {'blocks':len(usable),'n':sum(s['n'] for s in usable),
                              'observed_corr':None,'placebo_p_two_sided':None,'null_q95_abs':None}
    obs=calc_within(usable)
    denom=math.sqrt(sum(s['sxx'] for s in usable)*sum(s['syy'] for s in usable))
    null=[]
    for _ in range(permutations):
        cross=0.
        for st in usable:
            x=st['xcenter']; y=st['ycenter']; n=st['n']
            shift=rng.randint(minimum_shift,n-minimum_shift)
            cross+=sum(x[(i+shift)%n]*v for i,v in enumerate(y))
        null.append(cross/denom)
    p=(1+sum(abs(v)>=abs(obs) for v in null))/(permutations+1)
    ab=sorted(abs(v) for v in null)
    return {'blocks':len(usable),'n':sum(s['n'] for s in usable),
            'observed_corr':obs,'placebo_p_two_sided':p,
            'null_q95_abs':percentile(ab,.95),
            'warning':'Placebo p-value is only a shift-sensitivity diagnostic, not independent-data statistical significance.'}


def decile_results(train,test,rng,reps):
    sorted_x=sorted(r['x'] for r in train)
    if len(sorted_x)<100 or len(test)<100: raise ValueError('Insufficient train/test samples')
    cutoffs=[sorted_x[min(int(len(sorted_x)*j/10),len(sorted_x)-1)] for j in range(1,10)]
    counts=[0]*10; sums=[0.]*10; positive=[0]*10
    test_blocks=defaultdict(lambda:[[0,0.0] for _ in range(10)])
    for r in test:
        i=bisect.bisect_right(cutoffs,r['x'])
        counts[i]+=1;sums[i]+=r['y'];positive[i]+=(r['y']>0)
        test_blocks[r['block']][i][0]+=1
        test_blocks[r['block']][i][1]+=r['y']
    deciles=[{'decile':i+1,'test_n':counts[i],
              'mean_5s_future_mid_return_bps':sums[i]/counts[i] if counts[i] else None,
              'positive_return_fraction':positive[i]/counts[i] if counts[i] else None}
             for i in range(10)]
    if not counts[0] or not counts[9]:
        raise ValueError('Extreme test deciles empty; distribution shift or tied thresholds')
    difference=deciles[9]['mean_5s_future_mid_return_bps']-deciles[0]['mean_5s_future_mid_return_bps']
    blocks=list(test_blocks.values()); k=len(blocks)
    boot=[]
    for _ in range(reps):
        nb=nt=0; sb=st=0.
        for _ in range(k):
            block=blocks[rng.randrange(k)]
            nb+=block[0][0];sb+=block[0][1]
            nt+=block[9][0];st+=block[9][1]
        if nb and nt:boot.append(st/nt-sb/nb)
    boot.sort()
    ci=[percentile(boot,.025),percentile(boot,.975)] if boot else [None,None]
    return {'train_n':len(train),'test_n':len(test),
            'train_quantile_cutoffs':cutoffs,
            'deciles':deciles,'test_top_minus_bottom_bps':difference,
            'test_top_minus_bottom_block_bootstrap_95pct_interval_bps':ci,
            'test_5min_blocks':k}


def fmt(v):return 'NA' if v is None else f'{v:+.5f}'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--features',type=Path,required=True)
    ap.add_argument('--outdir',type=Path,default=Path('kraken_research_phase4'))
    ap.add_argument('--horizon',type=int,default=5,choices=(1,5,10,30,60))
    ap.add_argument('--bootstrap',type=int,default=2000)
    ap.add_argument('--permutations',type=int,default=300)
    ap.add_argument('--seed',type=int,default=20261104)
    args=ap.parse_args()
    if not args.features.is_file():ap.error('Features CSV not found: '+str(args.features))
    if args.bootstrap<100 or args.permutations<20:ap.error('Bootstrap >=100 and permutations >=20 required')
    samples,segs,rows=load_samples(args.features,args.horizon)
    if len(samples)<1000:ap.error('Not enough eligible forward return pairs')
    rng=random.Random(args.seed)
    blocks=group_5min(samples)
    stats=[block_stats(b) for b in blocks]
    r=calc_within(stats)
    ci=bootstrap_within(stats,rng,args.bootstrap)
    placebo=shift_placebo(stats,rng,args.permutations)
    tmin=min(r['t'] for r in samples);tmax=max(r['t'] for r in samples)
    bound=tmin+int((tmax-tmin)*.7)
    train=[r for r in samples if r['t']+args.horizon<=bound]
    test=[r for r in samples if r['t']>=bound+60]
    quantiles=decile_results(train,test,rng,args.bootstrap)
    result={'research_stage':'Phase 4: within-day 5-minute block stress tests',
            'source_features_csv':str(args.features), 'source_feature_rows':rows,
            'segments':segs,'horizon_seconds':args.horizon,
            'eligible_pairs':len(samples),'five_minute_blocks':len(stats),
            'within_block_corr':r,'within_block_block_bootstrap_95pct_interval':ci,
            'placebo':placebo,'chronological_boundary_utc':datetime.fromtimestamp(bound,timezone.utc).isoformat(),
            'decile_test':quantiles,'random_seed':args.seed,
            'cautions':[
                'All results use a single already-examined 24h recording, so neither bootstrap intervals nor placebo p-values demonstrate cross-day generalization.',
                'The chosen five-second horizon was selected after exploring the data. Other horizons are sensitivity checks, not preregistered tests.',
                'Correlations are of displayed order book and future MIDPRICE changes, not fillable or net-of-fee trade P&L.',
                'Block bootstrap assumes blocks approximate dependence units; 5-minute spacing is a sensitivity choice, not an independence guarantee.',
                'Circular shifts are within 5-minute blocks, using positions in observed second samples; this is a placebo, not a fully valid experiment.',
                'The decile train/test uses previous second and exact future second within one segment, excludes clock-warning intervals, and never crosses reconnect boundaries.',
                'Decile test means describe future returns given contemporaneous observed imbalance; no execution, latency or live-trading assumptions are established.'
            ]}
    args.outdir.mkdir(parents=True,exist_ok=True)
    (args.outdir/'phase4_statistical_stress.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    with (args.outdir/'test_imbalance_deciles.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(quantiles['deciles'][0].keys()))
        w.writeheader();w.writerows(quantiles['deciles'])
    print(f'SUMMARY: horizon={args.horizon}s eligible_pairs={len(samples):,} '
          f'segments={segs} five_minute_blocks={len(stats)} train_n={len(train):,} test_n={len(test):,}')
    print(f'WITHIN_5MIN_CORR: {fmt(r)} block_bootstrap_95pct=[{fmt(ci[0])}, {fmt(ci[1])}]')
    print('TIME_SHIFT_PLACEBO: observed_corr={} p_two_sided={} blocks={} null_95pct_abs={}'.format(
        fmt(placebo['observed_corr']),fmt(placebo['placebo_p_two_sided']),
        placebo['blocks'],fmt(placebo['null_q95_abs'])))
    print('TEST_DECILES_MEAN_BPS: '+' '.join(f"D{d['decile']}={fmt(d['mean_5s_future_mid_return_bps'])}" for d in quantiles['deciles']))
    spread=quantiles['test_top_minus_bottom_bps'];dci=quantiles['test_top_minus_bottom_block_bootstrap_95pct_interval_bps']
    print(f'TEST_D10_MINUS_D1_BPS: {fmt(spread)} block_bootstrap_95pct=[{fmt(dci[0])}, {fmt(dci[1])}]')
    print('JSON:',(args.outdir/'phase4_statistical_stress.json').resolve())
    print('CSV:',(args.outdir/'test_imbalance_deciles.csv').resolve())
    print('CAUTION: all tests are single-day exploratory diagnostics; not causal, independent, or investable.')

if __name__=='__main__':main()
