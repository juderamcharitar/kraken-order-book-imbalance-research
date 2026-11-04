#!/usr/bin/env python3
"""Kraken BTC/USD L2 Phase 3: incremental predictive-value diagnostics.

Reads Phase 1 CSV only. Does not change it or raw recordings.
Reconstructs exact-calendar-second labels strictly within original segments,
excludes intervals around receive-time warnings, and measures:
  * chronological 70/30 train/test OLS comparisons against momentum/microprice;
  * whether imbalance adds predictive value over these simpler alternatives;
  * 60-second-lagged imbalance as a temporal negative control;
  * association with returns starting one second AFTER the feature snapshot.

All results are exploratory and come from ONE pre-examined recording, NOT an
independent day-level validation, statistical significance test, causal effect,
fillable market-impact estimate, or profitable strategy assessment.

Python 3.11+, standard library only.
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HORIZONS=(1,5,10,30,60)
FEATURES=('imbalance10','momentum1s','microprice_bps')
MODELS={
    'imbalance_only':('imbalance10',),
    'momentum_only':('momentum1s',),
    'microprice_only':('microprice_bps',),
    'momentum_microprice':('momentum1s','microprice_bps'),
    'imbalance_momentum':('imbalance10','momentum1s'),
    'all_three':('imbalance10','momentum1s','microprice_bps'),
}


def utc_epoch(text):
    dt=datetime.fromisoformat(text.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Timestamp without UTC offset')
    return int(dt.timestamp())


def read_features(path):
    groups=defaultdict(list)
    required={'segment','second_utc','mid','imbalance10','microprice_minus_mid_bps','timestamp_warning_nearby'}
    n=0
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        missing=required-set(reader.fieldnames or [])
        if missing: raise ValueError('Missing columns: '+str(sorted(missing)))
        for lineno,r in enumerate(reader,2):
            n+=1
            try:
                seg=int(r['segment']); t=utc_epoch(r['second_utc'])
                mid=float(r['mid']); imb=float(r['imbalance10'])
                mic=float(r['microprice_minus_mid_bps'])
                warn=int(r['timestamp_warning_nearby'])
            except (ValueError,TypeError) as e:
                raise ValueError(f'Bad CSV line {lineno}: {e}') from e
            if not (math.isfinite(mid) and mid>0 and math.isfinite(imb)
                    and -1.000001<=imb<=1.000001 and math.isfinite(mic)
                    and warn in (0,1)):
                raise ValueError(f'Invalid row on CSV line {lineno}')
            groups[seg].append({'seg':seg,'t':t,'mid':mid,'imbalance10':imb,
                                'microprice_bps':mic,'warn':warn})
    for seg, rows in groups.items():
        rows.sort(key=lambda r:r['t'])
        if any(rows[i]['t']>=rows[i+1]['t'] for i in range(len(rows)-1)):
            raise ValueError(f'Segment {seg} contains repeated second_utc')
    if not groups: raise ValueError('No feature rows')
    return groups,n


def correlation(xy):
    if len(xy)<3:return None
    mx=math.fsum(x for x,y in xy)/len(xy)
    my=math.fsum(y for x,y in xy)/len(xy)
    xx=math.fsum((x-mx)**2 for x,y in xy)
    yy=math.fsum((y-my)**2 for x,y in xy)
    if xx<=0 or yy<=0:return None
    return math.fsum((x-mx)*(y-my) for x,y in xy)/math.sqrt(xx*yy)


def make_samples(groups, h):
    observations=[]; lagged=[]; lagged_same=[]; delayed=[]
    for seg,rows in groups.items():
        at={r['t']:i for i,r in enumerate(rows)}
        warnprefix=[0]
        for r in rows:warnprefix.append(warnprefix[-1]+r['warn'])
        def no_warning(a,b):
            if a>b:raise ValueError('time interval inverted')
            return warnprefix[b+1]-warnprefix[a]==0
        for i,r in enumerate(rows):
            t=r['t']
            jp=at.get(t-1);jf=at.get(t+h)
            if jf is None or jp is None or not no_warning(jp,jf): continue
            m_prev=rows[jp]['mid']; m_now=r['mid'];m_future=rows[jf]['mid']
            momentum=10000*(m_now/m_prev-1)
            y=10000*(m_future/m_now-1)
            record={'seg':seg,'t':t,'y':y,'imbalance10':r['imbalance10'],
                    'momentum1s':momentum,'microprice_bps':r['microprice_bps']}
            observations.append(record)
            jl=at.get(t-60)
            if jl is not None and no_warning(jl,jf):
                lagged.append((rows[jl]['imbalance10'],y))
                lagged_same.append((r['imbalance10'],y))
            jstart=at.get(t+1);jend=at.get(t+h+1)
            if jstart is not None and jend is not None and no_warning(jp,jend):
                late_y=10000*(rows[jend]['mid']/rows[jstart]['mid']-1)
                delayed.append((r['imbalance10'],late_y))
    return observations,lagged,lagged_same,delayed


def solve_linear(A,b):
    # Small Gaussian elimination with partial pivoting; ridge regularization
    # is added to predictor diagonal by the caller.
    n=len(b)
    for j in range(n):
        pivot=max(range(j,n),key=lambda r:abs(A[r][j]))
        if abs(A[pivot][j])<1e-12:return None
        if pivot!=j: A[j],A[pivot]=A[pivot],A[j];b[j],b[pivot]=b[pivot],b[j]
        denom=A[j][j]
        for k in range(j,n):A[j][k]/=denom
        b[j]/=denom
        for i in range(j+1,n):
            factor=A[i][j]
            for k in range(j,n):A[i][k]-=factor*A[j][k]
            b[i]-=factor*b[j]
    out=[0.0]*n
    for i in range(n-1,-1,-1):
        out[i]=b[i]-math.fsum(A[i][j]*out[j] for j in range(i+1,n))
    return out


def fit_ols(rows,keys):
    n=len(rows)
    ym=math.fsum(r['y'] for r in rows)/n
    xm={k:math.fsum(r[k] for r in rows)/n for k in keys}
    scale={k:math.sqrt(math.fsum((r[k]-xm[k])**2 for r in rows)/n) for k in keys}
    if any(v<1e-12 for v in scale.values()):return None
    Z=[[ (r[k]-xm[k])/scale[k] for k in keys ] for r in rows]
    centered_y=[r['y']-ym for r in rows]
    d=len(keys)
    A=[[math.fsum(z[i]*z[j] for z in Z) for j in range(d)] for i in range(d)]
    b=[math.fsum(z[i]*v for z,v in zip(Z,centered_y)) for i in range(d)]
    for i in range(d):A[i][i]+=1e-9*n
    beta=solve_linear(A,b)
    if beta is None:return None
    return (ym,xm,scale,beta,keys)


def predict(fit,row):
    ym,xm,scale,beta,keys=fit
    return ym+math.fsum(coef*(row[k]-xm[k])/scale[k] for coef,k in zip(beta,keys))


def score_chronological(samples,start,end,h):
    boundary=start+int((end-start)*.7)
    train=[r for r in samples if r['t']+h<=boundary]
    test=[r for r in samples if r['t']>=boundary+60]
    result={'train_n':len(train),'test_n':len(test),
            'boundary_utc':datetime.fromtimestamp(boundary,timezone.utc).isoformat(),
            'train_mean_future_return_bps':None,'test_mean_future_return_bps':None,
            'test_baseline_mse_bps2':None}
    if len(train)<100 or len(test)<100:return result
    ym=math.fsum(r['y'] for r in train)/len(train)
    base_mse=math.fsum((r['y']-ym)**2 for r in test)/len(test)
    result.update({'train_mean_future_return_bps':ym,
                   'test_mean_future_return_bps':math.fsum(r['y'] for r in test)/len(test),
                   'test_baseline_mse_bps2':base_mse})
    for name,keys in MODELS.items():
        fit=fit_ols(train,keys)
        if fit is None or base_mse<=0:
            result[f'{name}_test_r2']=None
            result[f'{name}_test_mse_bps2']=None
            continue
        mse=math.fsum((r['y']-predict(fit,r))**2 for r in test)/len(test)
        result[f'{name}_test_r2']=1-mse/base_mse
        result[f'{name}_test_mse_bps2']=mse
    r2all=result.get('all_three_test_r2')
    r2others=result.get('momentum_microprice_test_r2')
    result['incremental_r2_points_vs_momentum_microprice']=(r2all-r2others) if r2all is not None and r2others is not None else None
    return result


def fmt(v):return 'NA' if v is None else f'{v:+.4f}'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,required=True)
    p.add_argument('--outdir',type=Path,default=Path('kraken_research_phase3'))
    args=p.parse_args()
    if not args.features.is_file():p.error('Missing features file: '+str(args.features))
    groups,n=read_features(args.features)
    tmin=min(r['t'] for rows in groups.values() for r in rows)
    tmax=max(r['t'] for rows in groups.values() for r in rows)
    results=[]
    for h in HORIZONS:
        samples,lagged,lagged_same,delayed=make_samples(groups,h)
        result={'horizon_s':h,'n':len(samples),
                'imbalance_corr_with_future_mid_return':correlation([(r['imbalance10'],r['y']) for r in samples]),
                'lagged_60s_imbalance_corr':correlation(lagged),
                'current_imbalance_corr_on_lagged_eligible':correlation(lagged_same),
                'lagged_60s_n':len(lagged),
                'one_second_delayed_future_return_corr':correlation(delayed),
                'one_second_delayed_n':len(delayed),
                'scope':'One retrospective 24h observation period; exploratory'}
        result.update(score_chronological(samples,tmin,tmax,h))
        results.append(result)
        print(f'{h:>2}s n={len(samples):,} '
              f'corr_now={fmt(result["imbalance_corr_with_future_mid_return"])} '
              f'corr_x_60s_ago={fmt(result["lagged_60s_imbalance_corr"])} '
              f'corr_1s_delayed={fmt(result["one_second_delayed_future_return_corr"])} '
              f'test_R2_imb={fmt(result.get("imbalance_only_test_r2"))} '
              f'test_R2_mom={fmt(result.get("momentum_only_test_r2"))} '
              f'test_R2_all={fmt(result.get("all_three_test_r2"))} '
              f'increment_R2pts={fmt(result.get("incremental_r2_points_vs_momentum_microprice"))}',flush=True)
    args.outdir.mkdir(parents=True,exist_ok=True)
    columns=list(dict.fromkeys(k for row in results for k in row.keys()))
    with (args.outdir/'incremental_validation_summary.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns)
        w.writeheader();w.writerows(results)
    report={
        'research_stage':'Phase 3: temporal negative controls and incremental forecasting baselines',
        'input':str(args.features),'source_rows':n,'segments':len(groups),
        'data_period_start_utc':datetime.fromtimestamp(tmin,timezone.utc).isoformat(),
        'data_period_end_utc':datetime.fromtimestamp(tmax,timezone.utc).isoformat(),
        'methodology':[
            'Book features are observed at the end of a receipt-time second; response is the difference between its midpoint and the midpoint exactly h calendar seconds later, within the same segment.',
            'All baseline-model comparisons use the same samples with an observed prior-second midpoint, the same 70/30 time split and a 60-second test embargo.',
            'Features: imbalance10; one-second previous midpoint return; top-of-book microprice minus midpoint in basis points.',
            'Test R2 measures improvement against a constant equal to the TRAINING-set mean, not against a rich market benchmark.',
            'The 60-second-old imbalance is a temporal negative-control diagnostic. Compare it to current_imbalance_corr_on_lagged_eligible on the IDENTICAL eligible time rows.',
            'One-second-delayed return starts at t+1 and ends at t+h+1, requires exact observed seconds within the same segment; it is NOT a trade execution backtest.',
            'No information from a future book update is used to fit models; retrospective checking is based on Phase 1 CSV semantics.',
        ],
        'limitations':[
            'The day and the feature were already examined; a 70/30 chronological split is NOT a truly independent/preregistered external test.',
            'The per-second data are autocorrelated; large sample size is not equivalent to independent observations.',
            'Controls are narrow. A rich market-impact or order-flow benchmark was not evaluated.',
            'No actual trades, market-order aggression, execution price, transaction fees, latency model, or fillability is validated.',
            'Local receive-time seconds may lag exchange event timestamps; the known 1.64s receive clock reversal is excluded by warning guards.',
            'No day-level replication, robust uncertainty interval, or causal interpretation.',
        ],'results':results}
    (args.outdir/'incremental_validation_summary.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(f'SUMMARY: horizons={len(HORIZONS)} segments={len(groups)} feature_rows={n:,}')
    print('CSV:',(args.outdir/'incremental_validation_summary.csv').resolve())
    print('JSON:',(args.outdir/'incremental_validation_summary.json').resolve())
    print('WARNING: single-day exploratory analysis, not causal impact or trading alpha.')

if __name__=='__main__':main()
