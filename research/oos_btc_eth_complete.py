"""Compute previously missing BTC/ETH baseline results on Stage3B OOS window.
Uses pinned, unchanged Stage1 engine and identical cost/filters.
"""
import collections
import concurrent.futures
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pullback_stage1_core as core

START = pd.Timestamp('2021-01-01T00:00:00Z')
END = pd.Timestamp('2023-09-16T00:00:00Z')
WARMUP = pd.Timestamp('2020-01-01T00:00:00Z')
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT']
OUT = Path('results_oos_btc_eth'); OUT.mkdir(exist_ok=True)

def month_iter(y0, m0, y1, m1):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m == 13: y, m = y+1, 1

def fetch_all(tasks):
    pieces, errors = collections.defaultdict(list), collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for task, frame, status in ex.map(core.fetch_archive, tasks):
            symbol = task[0]
            if frame is not None:
                pieces[symbol].append(frame)
            elif status != 'NOT_LISTED':
                errors[symbol].append((task,status))
    return pieces, errors

def load():
    tasks = [(sym, 'monthly', f'{y:04d}-{m:02d}') for sym in SYMBOLS for y,m in month_iter(2020,1,2023,8)]
    tasks += [(sym,'daily',f'2023-09-{d:02d}') for sym in SYMBOLS for d in range(1,17)]
    print('Initial archive count:',len(tasks),flush=True)
    pieces, errors = fetch_all(tasks)
    repair=[]
    for sym in SYMBOLS:
        if errors[sym] or not pieces[sym]:
            raise RuntimeError(f'{sym} archive errors: {errors[sym][:3]} or empty')
        x = pd.concat(pieces[sym]).sort_index()
        x = x[~x.index.duplicated(keep='last')]
        x=x[(x.index>=WARMUP)&(x.index<END)]
        idx=x.index
        for i in range(1,len(idx)):
            if idx[i]-idx[i-1]>pd.Timedelta(hours=1):
                first=idx[i-1]+pd.Timedelta(hours=1)
                last=idx[i]-pd.Timedelta(hours=1)
                for day in pd.date_range(first.floor('D'),last.floor('D'),freq='D'):
                    repair.append((sym,'daily',day.strftime('%Y-%m-%d')))
    repair=list(dict.fromkeys(repair))
    print('Boundary gap repair archive count:',len(repair),flush=True)
    if repair:
        repaired, repair_errors=fetch_all(repair)
        for sym in SYMBOLS:
            pieces[sym].extend(repaired[sym]); errors[sym].extend(repair_errors[sym])
    out={}; availability=[]
    for sym in SYMBOLS:
        if errors[sym]: raise RuntimeError(f'{sym} errors: {errors[sym][:3]}')
        x=pd.concat(pieces[sym]).sort_index()
        x=x[~x.index.duplicated(keep='last')]
        x=x[(x.index>=WARMUP)&(x.index<END)]
        delta=x.index.to_series().diff()
        gaps=delta[delta>pd.Timedelta(hours=1)]
        if len(gaps): raise RuntimeError(f'{sym}: unresolved gaps {gaps.head().to_dict()}')
        if x.index[0] > START-pd.Timedelta(days=200): raise RuntimeError(f'{sym}: insufficient warmup')
        if x.index[-1] != END-pd.Timedelta(hours=1): raise RuntimeError(f'{sym}: wrong end {x.index[-1]}')
        out[sym]=x
        availability.append(dict(symbol=sym,first=str(x.index[0]),last=str(x.index[-1]),bars=len(x)))
    pd.DataFrame(availability).to_csv(OUT/'availability.csv',index=False)
    return out

def main():
    raw=load()
    core.START=START; core.END=END
    core.FEE=0.0005; core.SLIP=0.0002; core.CAP=3.0; core.LEVERAGE=4.0
    rows=[]
    for sym in SYMBOLS:
        variants={'BASE':dict(entry_pb=None,add_pb=None)}
        if sym=='XRPUSDT': variants['XRP_PB015']=dict(entry_pb=None,add_pb=0.15)
        if sym=='SOLUSDT': variants['SOL_PB020']=dict(entry_pb=None,add_pb=0.20)
        for label,variant in variants.items():
            row,eq,trades=core.backtest(sym,raw[sym],label,variant)
            rows.append(row)
            pd.DataFrame({'time':eq.index,'equity':eq.values}).to_csv(OUT/f'{sym}_{label}_equity.csv',index=False)
            print('OOS_RESULT',json.dumps({k:row[k] for k in ('symbol','variant','final_equity','return_pct','cagr_pct','mdd_pct','closed_trades')},default=str),flush=True)
    report=pd.DataFrame(rows)
    report.to_csv(OUT/'oos_all_four_returns.csv',index=False)
    ref=report[(report.symbol=='XRPUSDT')&(report.variant=='BASE')].iloc[0]
    assert abs(ref.final_equity-831.3313132564001)<1e-7, 'XRP previously reported OOS reproduction mismatch'
    ref2=report[(report.symbol=='SOLUSDT')&(report.variant=='BASE')].iloc[0]
    assert ref2.closed_trades==0 and ref2.final_equity==1000.0, 'SOL previously reported OOS reproduction mismatch'
    print('INVARIANCE_PASS: XRP and SOL historical OOS reproduced',flush=True)
    (OUT/'REPORT.md').write_text('# 2021–2023 four-coin OOS return comparison\n\nPeriod: 2021-01-01 UTC inclusive to 2023-09-16 UTC exclusive. Independent initial USDT 1000 per coin. Fee 0.05% each side, adverse slippage 0.02% each fill. Stage1 baseline engine unmodified; hybrid/partial trailing excluded. OOS BTC/ETH were not present in original Stage3B two-coin report.\n\n'+report[['symbol','variant','final_equity','return_pct','cagr_pct','mdd_pct','closed_trades']].to_markdown(index=False,floatfmt='.3f')+'\n\nXRP and SOL historical results reproduced. Backtest excludes funding fees, liquidation and intrabar execution.\n',encoding='utf-8')

if __name__=='__main__': main()
