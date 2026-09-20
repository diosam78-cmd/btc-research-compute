"""Isolated follow-up after initial screening. Post-hoc coin selection is NOT independent OOS."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research import pullback_stage1 as eng
from research import partial_reentry_stage6 as study

OUT=Path('results_partial_reentry_followup');OUT.mkdir(exist_ok=True)
COINS=list(study.EXPECTED)
DEFAULT=None
A=(15,30,.30,3)
B=(15,30,.40,3)
C=(7,15,.30,3)
D=(7,15,.40,2)
CASES={
 'BASE':{},
 'ALL_A':dict.fromkeys(COINS,A),
 'XRP_SOL_A':{'XRPUSDT':A,'SOLUSDT':A},
 'XRP_SOL_B':{'XRPUSDT':B,'SOLUSDT':B},
 'XRP_A_SOL_C':{'XRPUSDT':A,'SOLUSDT':C},
 'XRP_B_SOL_D':{'XRPUSDT':B,'SOLUSDT':D},
 'XRP_ONLY_A':{'XRPUSDT':A},
 'SOL_ONLY_A':{'SOLUSDT':A},
}
COSTS=(1.,1.5,2.)

def run_one(data,name,map_cfg,cost=1.):
    # Keep all computations on the same actual price history. Both original
    # and patched simulators use the exact same scaled trading costs.
    fee=.0005*cost; slip=.0002*cost
    study.run_sim.__globals__.update(FEE=fee,SLIP=slip)
    eng.FEE=fee;eng.SLIP=slip
    paths={};records=[];cycles=[];events=[]
    for sym in COINS:
        cfg=map_cfg.get(sym,DEFAULT)
        s,eq,tr,ev=study.run_sim(sym,data[sym],name,
            {'entry_pb':None,'add_pb':study.PB[sym],'stages':cfg})
        if name=='BASE' and cost==1.:
            expected,ntr,nwin=study.EXPECTED[sym]
            assert abs(float(eq.iloc[-1])-expected)<.001
            assert len(tr)==ntr and s['wins']==nwin
        assert abs(float(tr.net_pnl.sum())+1000.-s['final_equity'])<.05
        paths[sym]=eq;records.append(s)
        cycles.append(tr.assign(variant=name,cost_factor=cost))
        if len(ev):events.append(ev.assign(cost_factor=cost))
    aggregate=pd.concat(paths.values(),axis=1).dropna().sum(axis=1)
    met=study.metrics(aggregate)
    r=pd.DataFrame(records)
    alltr=pd.concat(cycles,ignore_index=True)
    met.update(variant=name,cost_factor=cost,
       fees_assumption=fee,slippage_assumption=slip,
       trades=int(len(alltr)),wins=int(sum(alltr.net_pnl>0)),
       losing_pnl=float(alltr.loc[alltr.net_pnl<0,'net_pnl'].sum()),
       winning_pnl=float(alltr.loc[alltr.net_pnl>0,'net_pnl'].sum()),
       overlay_orders=int(r.overlay_orders.sum()),
       roundtrips=int(r.complete_roundtrips.sum()),
       min_rejections=int(r.skipped_min_qty.sum()),
       reinvestment_rejections=int(r.overlay_rejected.sum()),
       worst_trade_pct=float((100*alltr.net_pnl/alltr.groupby('symbol').net_pnl.transform('size')).min()) if False else np.nan)
    return met,paths,aggregate,r,alltr,events

def main():
    raw=eng.load_data()
    metrics=[];coin=[];trades=[];events=[];curves={}
    for name,cfg in CASES.items():
        m,p,eq,r,tr,e=run_one(raw,name,cfg)
        metrics.append(m);coin.append(r.assign(strategy=name,cost_factor=1.))
        trades.append(tr);events.extend(e);curves[name]=eq
        print('SELECTIVE',name,round(m['final'],2),round(m['mdd_pct'],2),m['overlay_orders'],flush=True)
        if name in ('BASE','XRP_SOL_A','XRP_A_SOL_C','XRP_SOL_B'):
            pd.concat(p,axis=1).rename_axis('bar_open_utc').to_csv(OUT/f'coin_equity_{name}.csv')
        eq.rename('equity').to_csv(OUT/f'aggregate_equity_{name}.csv')
    for factor in COSTS[1:]:
        for name in ('BASE','XRP_SOL_A','XRP_A_SOL_C'):
            m,_,_,r,tr,e=run_one(raw,name,CASES[name],factor)
            metrics.append(m);coin.append(r.assign(strategy=name,cost_factor=factor))
            trades.append(tr);events.extend(e)
            print('COST',factor,name,round(m['final'],2),round(m['mdd_pct'],2),flush=True)
    aggregate=pd.DataFrame(metrics)
    aggregate.to_csv(OUT/'aggregate_comparison.csv',index=False)
    coins=pd.concat(coin,ignore_index=True)
    coins.to_csv(OUT/'per_coin_comparison.csv',index=False)
    alltr=pd.concat(trades,ignore_index=True)
    alltr.to_csv(OUT/'trade_comparison.csv',index=False)
    if events:pd.concat(events,ignore_index=True).to_csv(OUT/'order_events.csv',index=False)
    split=[]
    for name,eq in curves.items():
        for start,end,label in [('2023-09-16','2025-03-16','EARLY'),
                                ('2025-03-16','2026-09-16','LATE')]:
            sub=eq[(eq.index>=pd.Timestamp(start,tz='UTC')) & (eq.index<pd.Timestamp(end,tz='UTC'))]
            beg=float(eq[eq.index<sub.index[0]].iloc[-1]) if sub.index[0]>eq.index[0] else 4000.
            split.append(dict(variant=name,period=label,start_equity=beg,end_equity=float(sub.iloc[-1]),
                return_pct=100*(float(sub.iloc[-1])/beg-1)))
    pd.DataFrame(split).to_csv(OUT/'split_existing_history.csv',index=False)
    base=alltr[(alltr.variant=='BASE')&(alltr.cost_factor==1.)]
    top=base[base.net_pnl>0].sort_values('net_pnl',ascending=False).groupby('symbol').head(3)
    retained=[]
    for name in CASES:
        current=alltr[(alltr.variant==name)&(alltr.cost_factor==1.)]
        for sym in COINS:
            old=top[top.symbol==sym]
            new=current[current.symbol==sym]
            j=old.merge(new,on=['symbol','entry_time','exit_time'],how='left',suffixes=('_old','_new'))
            retained.append(dict(variant=name,symbol=sym,
               top3_old_net=float(old.net_pnl.sum()),
               matched=int(j.net_pnl_new.notna().sum()),
               matched_new_net=float(j.net_pnl_new.fillna(0.).sum())))
    pd.DataFrame(retained).to_csv(OUT/'top3_profit_preservation.csv',index=False)
    (OUT/'methodology.json').write_text(json.dumps(dict(
       selection='XRP/SOL selective design chosen AFTER seeing the same 2023-26 screening history; in-sample only',
       baseline_matches=True,original_live_unchanged=True,
       costs_multipliers=list(COSTS),
       note='Full historical reruns; no post-hoc trade outcome filters, coin selection itself post-hoc',
       definitions='Wallet thresholds, adverse price pullback from take-profit close, half reentry on retrace, half after breakout close',
       long_short_symmetric=True,stops_priority=True,original_adds_not_disabled=True,
       funding_liquidation_intrabar_missing=True),indent=2))
    print('AGGREGATE',aggregate.to_json(orient='records'),flush=True)
    print('FINISHED',flush=True)
if __name__=='__main__':main()
