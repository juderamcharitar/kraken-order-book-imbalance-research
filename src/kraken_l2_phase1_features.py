#!/usr/bin/env python3
"""Kraken BTC/USD L2 Phase 1: per-second order book metrics and exploratory response.

Designed for the 2026-11-03 to 2026-11-04 24-hour recording with 33 segments.

PREREQUISITE: validate all 33 source segments using validate_kraken_l2_checksums_v2.py
and review all failures/warnings. This script DOES NOT independently re-check CRCs;
it preserves original recorded update order and reconstructs book state afresh from
one snapshot per segment.

Read-only toward the original recordings; outputs only into --outdir.
Supports seven separately recovered .recovered.jsonl.gz segments.

Features use LAST OBSERVED book in each UTC receipt second (not event-time
interpolation). Missing seconds are not filled. Future-return comparisons use
EXACT calendar-second offsets within the SAME segment and do not cross gaps.
One known 1.64-second receive-time regression is clamped to preserve message
ordering; any second touched by the regression is excluded from forward-label
research. Received timestamps reflect local clock, NOT exchange clock.

This is displayed liquidity / hypothetical instantaneous book walking only;
there is NO proof of causal market impact, trade executions, or profitability.
"""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import statistics

ORDER_SIZES=(0.01, 0.1, 1.0)
HORIZONS=(1, 5, 10, 30, 60)
HEADER=[
    'segment','second_utc','update_messages','last_receive_utc',
    'best_bid','best_ask','mid','spread_usd','spread_bps',
    'bid1_btc','ask1_btc','bid10_btc','ask10_btc','bid100_btc','ask100_btc',
    'imbalance1','imbalance10','imbalance100','microprice','microprice_minus_mid_bps',
]
for _side in ('buy','sell'):
    for _q in ORDER_SIZES:
        _k=str(_q)
        HEADER.extend((f'{_side}_{_k}btc_fill_fraction', f'{_side}_{_k}btc_cost_vs_mid_bps',
                       f'{_side}_{_k}btc_walk_vs_best_bps'))
HEADER += ['timestamp_warning_nearby']


def book_walk(prices, book, qty_btc, mid, best, side):
    left=qty_btc
    total=0.0
    for price in prices:
        take=min(left, book[price])
        total+=price*take
        left-=take
        if left<=1e-10:
            break
    filled=max(0.0,qty_btc-left)
    fraction=min(1.0,filled/qty_btc)
    if left>1e-10 or filled<=0:
        return fraction, '', ''
    vwap=total/filled
    if side=='buy':
        return fraction, (vwap-mid)/mid*10000, (vwap-best)/mid*10000
    return fraction, (mid-vwap)/mid*10000, (best-vwap)/mid*10000


def get_features(bids, asks, seg, sec, recv, count):
    bid_prices=sorted(bids, reverse=True)
    ask_prices=sorted(asks)
    if not bid_prices or not ask_prices:
        raise ValueError(f'Segment {seg}: empty book side')
    bp=bid_prices[0];ap=ask_prices[0]
    if bp>=ap:
        raise ValueError(f'Segment {seg}: crossed/locked book')
    mid=(bp+ap)/2
    b1=bids[bp];a1=asks[ap]
    b10=sum(bids[p] for p in bid_prices[:10]);a10=sum(asks[p] for p in ask_prices[:10])
    b100=sum(bids.values());a100=sum(asks.values())
    def imbalance(b,a):
        return (b-a)/(b+a) if b+a else ''
    micro=(ap*b1+bp*a1)/(b1+a1) if b1+a1 else mid
    d={'segment':seg,'second_utc':datetime.fromtimestamp(sec,timezone.utc).isoformat(),
       'update_messages':count,'last_receive_utc':recv,
       'best_bid':bp,'best_ask':ap,'mid':mid,'spread_usd':ap-bp,
       'spread_bps':(ap-bp)/mid*10000,
       'bid1_btc':b1,'ask1_btc':a1,'bid10_btc':b10,'ask10_btc':a10,
       'bid100_btc':b100,'ask100_btc':a100,
       'imbalance1':imbalance(b1,a1),'imbalance10':imbalance(b10,a10),
       'imbalance100':imbalance(b100,a100),'microprice':micro,
       'microprice_minus_mid_bps':(micro-mid)/mid*10000,
       'timestamp_warning_nearby':0}
    for side, prices, book, best in (
        ('buy',ask_prices,asks,ap),('sell',bid_prices,bids,bp)):
        for size in ORDER_SIZES:
            k=f'{side}_{size}btc'
            fill,cost,walk=book_walk(prices,book,size,mid,best,side)
            d[f'{k}_fill_fraction']=fill
            d[f'{k}_cost_vs_mid_bps']=cost
            d[f'{k}_walk_vs_best_bps']=walk
    return d


def apply(book, entries):
    for x in entries:
        p=float(x['price']);q=float(x['qty'])
        if not math.isfinite(p) or not math.isfinite(q) or p<=0 or q<0:
            raise ValueError('Nonfinite/nonpositive book price or negative quantity')
        if q==0:book.pop(p,None)
        else:book[p]=q


def trim(book, descending, depth):
    if len(book)>depth:
        for p in sorted(book,reverse=descending)[depth:]:
            del book[p]


def dt_seconds(text):
    return int(datetime.fromisoformat(text.replace('Z','+00:00')).timestamp())


def input_path(folder,entry):
    base=entry['file']
    if entry['state']=='complete':
        return folder/base
    if entry['state']=='partial':
        if not base.endswith('.jsonl.gz'):
            raise ValueError(f'Unexpected partial filename: {base}')
        return folder/(base[:-len('.jsonl.gz')]+'.recovered.jsonl.gz')
    raise ValueError(f'Unsupported manifest state: {entry["state"]}')


def analyze_segment(folder,entry,depth,csv_writer):
    seg=entry['index']; path=input_path(folder,entry)
    if not path.is_file():
        raise FileNotFoundError(f'{path} not found; recover interrupted segments first')
    bids={};asks={};n_snapshot=0;n_ack=0;n_meta=0;n_updates=0;n_completed=0
    prev_receive=None; prev_raw_receive=None; clock_regressions=[]; seconds=[]
    current_sec=None;current_recv=None;updates_this_second=0
    first_recv=None;last_recv=None
    def flush():
        if current_sec is None:return
        features=get_features(bids,asks,seg,current_sec,current_recv,updates_this_second)
        seconds.append(features)
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for lineno,line in enumerate(stream,1):
            obj=json.loads(line)
            kind=obj.get('record_type')
            if kind=='meta':
                n_meta+=1
                if obj['payload'].get('symbol')!='BTC/USD' or obj['payload'].get('depth')!=depth:
                    raise ValueError(f'{seg}: wrong symbol/depth in metadata')
                continue
            if kind=='subscription_ack':
                n_ack+=1
                if obj['payload'].get('success') is not True:
                    raise ValueError(f'{seg}: subscription failed')
                continue
            if kind=='capture_complete':
                n_completed+=1
                if obj['payload'].get('updates')!=n_updates:
                    raise ValueError(f'{seg}: complete marker updates mismatch')
                continue
            if kind not in ('snapshot','update'):
                raise ValueError(f'{seg}: unknown record type {kind} line {lineno}')
            if n_meta!=1 or n_ack!=1:
                raise ValueError(f'{seg}: book data before meta/ACK')
            m=obj['payload']
            if m.get('channel')!='book' or m.get('type')!=kind or len(m.get('data',[]))!=1:
                raise ValueError(f'{seg}: invalid book envelope at line {lineno}')
            data=m['data'][0]
            if data.get('symbol')!='BTC/USD':
                raise ValueError(f'{seg}: symbol mismatch')
            recv=obj.get('received_utc')
            if not isinstance(recv,str) or not recv:
                raise ValueError(f'{seg}: no receive time at line {lineno}')
            if prev_raw_receive is not None and recv<prev_raw_receive:
                clock_regressions.append({'line':lineno,'previous':prev_raw_receive,'current':recv})
            prev_raw_receive=recv
            # Keep one count per actual raw clock reversal, not every message
            # that temporarily remains behind the prior wall-clock high water.
            if prev_receive is not None and recv<prev_receive:
                recv=prev_receive
            prev_receive=recv
            sec=dt_seconds(recv)
            if first_recv is None:first_recv=recv
            last_recv=recv
            if current_sec is not None and sec!=current_sec:
                if sec<current_sec:
                    raise ValueError('Effective receive second regressed unexpectedly')
                flush()  # latest book state from previous second; no future update applied yet
                current_sec=None
            if kind=='snapshot':
                n_snapshot+=1
                if n_snapshot!=1 or n_updates:
                    raise ValueError(f'{seg}: duplicate/out-of-order snapshot')
                bids={float(x['price']):float(x['qty']) for x in data['bids'] if float(x['qty'])>0}
                asks={float(x['price']):float(x['qty']) for x in data['asks'] if float(x['qty'])>0}
            else:
                if n_snapshot!=1:raise ValueError(f'{seg}: update before snapshot')
                n_updates+=1
                apply(bids,data.get('bids',[]))
                apply(asks,data.get('asks',[]))
            trim(bids,True,depth)
            trim(asks,False,depth)
            if current_sec is None:
                current_sec=sec;updates_this_second=0
            if kind=='update':updates_this_second+=1
            current_recv=recv
    flush()
    if n_meta!=1 or n_ack!=1 or n_snapshot!=1 or n_updates!=entry['updates']:
        raise ValueError(f'{seg}: framing/count mismatch meta={n_meta} ack={n_ack} '
                         f'snapshot={n_snapshot} updates={n_updates} expected={entry["updates"]}')
    if n_completed!=(1 if entry['state']=='complete' else 0):
        raise ValueError(f'{seg}: unexpected number of end markers')
    # Mark +/- 10 seconds around any local clock reversal as unsuitable for
    # short-horizon response calculation (even though book state is valid).
    warned_seconds=set()
    for r in clock_regressions:
        t=dt_seconds(r['previous'])
        warned_seconds.update(range(t-10,t+11))
        t2=dt_seconds(r['current'])
        warned_seconds.update(range(t2-10,t2+11))
    for r in seconds:
        s=dt_seconds(r['second_utc'])
        if s in warned_seconds:r['timestamp_warning_nearby']=1
    # Rows are written only after warning flags are set, so exported data agree.
    for r in seconds:
        csv_writer.writerow(r)
    summary={'segment':seg,'state':entry['state'],'source_file':path.name,
             'messages':n_updates,'observed_seconds':len(seconds),
             'first_receive_utc':first_recv,'last_receive_utc':last_recv,
             'receive_timestamp_regressions':len(clock_regressions),
             'warnings':clock_regressions,
             'observed_seconds_adjacent_to_clock_warning':sum(dt_seconds(r['second_utc']) in warned_seconds for r in seconds)}
    return summary,seconds,warned_seconds


def summarize(values):
    vals=sorted(v for v in values if isinstance(v,(int,float)) and math.isfinite(v))
    if not vals:return {'n':0}
    n=len(vals)
    return {'n':n,'mean':statistics.fmean(vals),'median':statistics.median(vals),
            'p05':vals[int(.05*(n-1))],'p95':vals[int(.95*(n-1))],
            'min':vals[0],'max':vals[-1]}


def sample_correlation(pairs):
    n=len(pairs)
    if n<3:return None
    sx=sum(x for x,y in pairs);sy=sum(y for x,y in pairs)
    mx=sx/n;my=sy/n
    xx=sum((x-mx)**2 for x,y in pairs)
    yy=sum((y-my)**2 for x,y in pairs)
    xy=sum((x-mx)*(y-my) for x,y in pairs)
    return xy/math.sqrt(xx*yy) if xx>0 and yy>0 else None


def exploratory_response(grouped,anomaly_seconds):
    """Descriptive in-sample correlation only. No inference, prediction or causality."""
    output={}
    for horizon in HORIZONS:
        pairs={'imbalance1':[],'imbalance10':[],'imbalance100':[],
               'microprice_minus_mid_bps':[]}
        for seg,rows in grouped.items():
            lookup={dt_seconds(r['second_utc']):r for r in rows}
            bad=anomaly_seconds[seg]
            for t,r in lookup.items():
                later=lookup.get(t+horizon)
                if later is None:continue
                if any(t<=bt<=t+horizon for bt in bad):continue
                if t+horizon-t>0 and r['mid']>0 and later['mid']>0:
                    future=(later['mid']/r['mid']-1)*10000
                    for key in pairs:
                        x=r[key]
                        if isinstance(x,(float,int)) and math.isfinite(x):
                            pairs[key].append((x,future))
        output[f'{horizon}s']={key:{'n':len(v),'corr_with_future_mid_return':sample_correlation(v),
                                     'mean_future_mid_return_bps':statistics.fmean(y for _,y in v) if v else None}
                                for key,v in pairs.items()}
    return output


def run(args):
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    if manifest.get('symbol')!='BTC/USD' or manifest.get('depth')!=100:
        raise ValueError('This stage expects BTC/USD and depth 100')
    segment_entries=manifest['segments']
    if args.segment is not None:
        segment_entries=[e for e in segment_entries if e['index']==args.segment]
        if not segment_entries:raise ValueError(f'Segment {args.segment} not found')
    args.outdir.mkdir(parents=True,exist_ok=True)
    features_path=args.outdir/'one_second_l2_features.csv'
    result_path=args.outdir/'segment_audit.csv'
    all_rows=[];segment_summaries=[];grouped={};anomaly_seconds={}
    with features_path.open('w',newline='',encoding='utf-8') as fout:
        writer=csv.DictWriter(fout,fieldnames=HEADER,extrasaction='ignore')
        writer.writeheader()
        for e in segment_entries:
            report,rows,bad=analyze_segment(args.manifest.parent,e,manifest['depth'],writer)
            segment_summaries.append(report)
            grouped[e['index']]=rows
            anomaly_seconds[e['index']]=bad
            all_rows.extend(rows)
            print(f"{e['index']:02} {e['state']:<8} PASS "
                  f"updates={report['messages']:,} observed_seconds={len(rows):,} "
                  f"clock_regressions={report['receive_timestamp_regressions']}",flush=True)
    with result_path.open('w',newline='',encoding='utf-8') as fout:
        writer=csv.DictWriter(fout,fieldnames=[
            'segment','state','source_file','messages','observed_seconds',
            'first_receive_utc','last_receive_utc','receive_timestamp_regressions',
            'observed_seconds_adjacent_to_clock_warning'],extrasaction='ignore')
        writer.writeheader();writer.writerows(segment_summaries)
    stats_keys=('spread_usd','spread_bps','mid','bid1_btc','ask1_btc','bid10_btc',
                'ask10_btc','bid100_btc','ask100_btc','imbalance1','imbalance10','imbalance100',
                'microprice_minus_mid_bps',
                'buy_0.1btc_cost_vs_mid_bps','sell_0.1btc_cost_vs_mid_bps',
                'buy_1.0btc_cost_vs_mid_bps','sell_1.0btc_cost_vs_mid_bps')
    report={'project':'BTC/USD Kraken L2 descriptive research — Phase 1',
            'source_manifest':str(args.manifest),'segments_processed':len(segment_entries),
            'update_messages_processed':sum(x['messages'] for x in segment_summaries),
            'one_second_observations':len(all_rows),
            'receive_timestamp_regressions':sum(x['receive_timestamp_regressions'] for x in segment_summaries),
            'per_segment':segment_summaries,
            'statistics':{key:summarize(r[key] for r in all_rows) for key in stats_keys},
            'exploratory_same_segment_forward_midprice_correlations':exploratory_response(grouped,anomaly_seconds),
            'definitions':{
                'sampling':'Last observed book message within a UTC receipt second; unobserved seconds omitted',
                'spread_bps':'(best ask - best bid)/midprice * 10000',
                'imbalance':'(displayed bid BTC depth - displayed ask BTC depth)/(sum of those depths)',
                'microprice':'(best ask*bid1 size + best bid*ask1 size)/(bid1 size+ask1 size)',
                'cost_vs_mid_bps':'Hypothetical immediate book walk, relative to midpoint; full-fill only',
                'response':'Future midprice at exact calendar-second offset, same segment only; no gap crossing',
                'timestamps':'Local receive UTC, may differ from exchange time; known backward jump clamped',
            },
            'limits':['No independent CRC32 re-check in this script; relies on prior 33-segment full checksum validation',
                      'No actual executions, market-order aggressor flags, order IDs, or verified causal market impact',
                      'Available depth only; hypothetically walking the book omits fees, latency, cancellations and subsequent reaction',
                      'Clock anomalies near affected intervals excluded from descriptive response summaries',
                      'Correlations are retrospective within the same sample and are NOT out-of-sample predictive validation',
                      'One 24-hour session is not enough for robust generalization over market regimes']}
    (args.outdir/'phase1_summary.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(f"SUMMARY: segments={len(segment_entries)} updates={report['update_messages_processed']:,} "
          f"observed_seconds={len(all_rows):,} timestamp_regressions={report['receive_timestamp_regressions']}")
    for k in ('spread_bps','imbalance10','bid10_btc','ask10_btc','buy_0.1btc_cost_vs_mid_bps'):
        st=report['statistics'][k]
        print(f"  {k}: mean={st.get('mean')} median={st.get('median')} n={st['n']}")
    for h in ('1s','5s','30s','60s'):
        d=report['exploratory_same_segment_forward_midprice_correlations'][h]['imbalance10']
        print(f"  descriptive corr(imbalance10, future mid return {h}): {d['corr_with_future_mid_return']} (n={d['n']})")
    print(f'CSV: {features_path.resolve()}')
    print(f'SUMMARY_JSON: {(args.outdir/"phase1_summary.json").resolve()}')
    print(f'SEGMENT_AUDIT: {result_path.resolve()}')
    print('No causal market-impact or trading-profitability claim is established.')


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--outdir',type=Path,default=Path('kraken_research_phase1'))
    p.add_argument('--segment',type=int,default=None,help='First run --segment 1 to smoke-test the file format')
    a=p.parse_args()
    run(a)

if __name__=='__main__':
    main()
