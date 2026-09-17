"""Stage 5 research ONLY: divide each scheduled PYRAMID add, NOT first entry.
Uses the pinned Stage1 hourly-close shadow engine; never changes the live V3.1 bot.
"""
import inspect
import json
import textwrap
from pathlib import Path
import numpy as np
import pandas as pd
import pullback_stage1_core as core

OUT = Path('results_split_add_stage5'); OUT.mkdir(exist_ok=True)
COINS = ['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT']
MIX_PB = {'BTCUSDT':None,'ETHUSDT':None,'XRPUSDT':.15,'SOLUSDT':.20}
CAND_PB = {'BTCUSDT':.15,'ETHUSDT':.15,'XRPUSDT':.15,'SOLUSDT':.20}
START = pd.Timestamp('2023-09-16',tz='UTC')
END = pd.Timestamp('2026-09-16',tz='UTC')
EXPECTED_MIX = {'BTCUSDT':6122.754677747175,'ETHUSDT':4597.402574464877,
                'XRPUSDT':4620.381764378673,'SOLUSDT':11166.14038909758}
CONFIGS = {
    'ORIGINAL_ALL_INSTANT': (None, None),
    'MIXED_RESEARCH': ('mixed',None),
    'FULL_PULLBACK_ALL': (0.0,'candidate'),
    'SPLIT50_COIN': (.50,'candidate'),
    'SPLIT25_COIN': (.25,'candidate'),
    'SPLIT75_COIN': (.75,'candidate'),
    'SPLIT50_PB10': (.50,.10),
    'SPLIT50_PB15': (.50,.15),
    'SPLIT50_PB20': (.50,.20),
    'SPLIT50_PB25': (.50,.25),
}


def patched_backtest():
    source=inspect.getsource(core.backtest)
    a=source.index('        # 2) ADD or ADD-ARM / pullback execution.')
    b=source.index('        # 3) ENTRY or ENTRY-ARM / pullback execution.')
    original=source[a:b]
    # Every split signal plans ONE original unit only; no later extra quantity.
    split_code='''
if side and not event and (units < p['maxu'] - 1e-9 or add_arm is not None):
    if add_arm is not None:
        # A pending remainder is the only pyramid order allowed until filled/cancelled.
        ref = add_arm['ref_fill']
        if side * (px-ref) <= 0:
            add_arm = None
            add_cancel += 1
            split_cancel += 1
        else:
            add_arm['extreme'] = max(add_arm['extreme'],px) if side > 0 else min(add_arm['extreme'],px)
            retreat = side * (add_arm['extreme']-px)
            if retreat >= variant['add_pb']*entry_n:
                second = add_arm['remaining']
                fp=fill_price(px,side)
                eq=cash+side*qty*(px-avg)
                capacity=max(0.0,(eq*CAP-qty*px)/(1.0+CAP*FEE))
                if valid_qty(second,fp,p) and second*fp <= capacity+1e-9 and eq>0:
                    fee=second*fp*FEE
                    cash-=fee; fees_in_pos+=fee
                    avg=(avg*qty+fp*second)/(qty+second)
                    qty+=second; units+=second/unit
                    last_fill=fp
                    new_stop=fp-side*p['stop']*entry_n
                    stop=max(stop,new_stop) if side>0 else min(stop,new_stop)
                    split_second+=1; add_exec+=1
                    add_arm=None; event=True
                else:
                    # Order cannot execute at real-world min-size/notional/cap: cancel.
                    rejected+=1; split_remainder_reject+=1
                    add_arm=None; add_cancel+=1
    if not event and add_arm is None and units < p['maxu']-1e-9:
        threshold=last_fill+side*p['add']*entry_n
        hit=px>=threshold if side>0 else px<=threshold
        if hit:
            original_unit=min(unit,(p['maxu']-units)*unit)
            target=floor_step(original_unit,p['step'])
            first=floor_step(target*variant['split_first'],p['step'])
            second=round(target-first,10)
            fp=fill_price(px,side)
            eq=cash+side*qty*(px-avg)
            capacity=max(0.0,(eq*CAP-qty*px)/(1.0+CAP*FEE))
            if (target<=unit+1e-9 and first>0 and second>0
                and valid_qty(first,fp,p) and valid_qty(second,fp,p)
                and first*fp<=capacity+1e-9 and eq>0):
                prev_fill=last_fill
                fee=first*fp*FEE
                cash-=fee; fees_in_pos+=fee
                avg=(avg*qty+fp*first)/(qty+first)
                qty+=first; last_fill=fp; units+=first/unit
                new_stop=fp-side*p['stop']*entry_n
                stop=max(stop,new_stop) if side>0 else min(stop,new_stop)
                add_arm=dict(ref_fill=prev_fill,extreme=px,remaining=second,
                             planned_total=target,first=first,armed_at=str(t))
                add_arms+=1; add_exec+=1; split_first+=1
                assert add_arm['first']+add_arm['remaining']<=unit+1e-9
                event=True
            else:
                rejected+=1; split_first_reject+=1
'''
    split=textwrap.indent(textwrap.dedent(split_code).lstrip('\n'),'            ')
    repl=('        if variant.get("split_first") is None:\n'+textwrap.indent(original,'    ')
          +'        else:\n'+split+'\n')
    source=source[:a]+repl+source[b:]
    source=source.replace('def backtest(', 'def backtest_split(', 1)
    anchor='    rejected = 0\n'
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+'    split_first=split_second=split_cancel=split_first_reject=split_remainder_reject=0\n',1)
    anchor="        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,\n"
    assert source.count(anchor)==1
    source=source.replace(anchor,anchor+"        'split_first_exec':split_first, 'split_second_exec':split_second,\n        'split_cancel':split_cancel,'split_first_reject':split_first_reject,\n        'split_remainder_reject':split_remainder_reject,\n")
    namespace=core.__dict__.copy()
    exec(compile(source,'stage5_split_core','exec'),namespace)
    return namespace['backtest_split']

RUN=patched_backtest()

def variant_for(sym,name):
    fraction,pb=CONFIGS[name]
    if fraction=='mixed': return dict(entry_pb=None,add_pb=MIX_PB[sym],split_first=None)
    if fraction is None: return dict(entry_pb=None,add_pb=None,split_first=None)
    if fraction==0.0: return dict(entry_pb=None,add_pb=CAND_PB[sym],split_first=None)
    if pb=='candidate': pb=CAND_PB[sym]
    return dict(entry_pb=None,add_pb=pb,split_first=fraction)

def execute(raw,sym,name,start=START,cost=1.0):
    core.START=start;core.END=END;core.FEE=.0005*cost;core.SLIP=.0002*cost
    RUN.__globals__.update(START=start,END=END,FEE=core.FEE,SLIP=core.SLIP)
    return RUN(sym,raw[sym],name,variant_for(sym,name))

def main():
    raw=core.load_data()
    # NO-SPLIT patch must exactly reproduce BOTH original and selected Stage4 mixed baseline.
    invariance=[]
    for sym in COINS:
        for name in ('ORIGINAL_ALL_INSTANT','MIXED_RESEARCH','FULL_PULLBACK_ALL'):
            v=variant_for(sym,name)
            core.START=START;core.END=END;core.FEE=.0005;core.SLIP=.0002
            a,ae,at=core.backtest(sym,raw[sym],'REFERENCE',v)
            b,be,bt=execute(raw,sym,name)
            curve=float(np.max(np.abs(ae.to_numpy()-be.to_numpy())))
            pnl=(float(np.max(np.abs(at.net_pnl.to_numpy()-bt.net_pnl.to_numpy())))
                 if len(at)==len(bt) and len(at) else (0 if len(at)==len(bt) else float('inf')))
            assert curve<1e-7 and pnl<1e-7,(sym,name,curve,pnl)
            if name=='MIXED_RESEARCH':assert abs(a['final_equity']-EXPECTED_MIX[sym])<1e-7
            invariance.append(dict(symbol=sym,variant=name,curve_difference=curve,trade_pnl_difference=pnl,
                                   final_equity=b['final_equity']))
    pd.DataFrame(invariance).to_csv(OUT/'baseline_invariance.csv',index=False)
    print('BASELINE_INVARIANCE_PASS',len(invariance),flush=True)
    rows=[]; trades=[];paths={}
    for name in CONFIGS:
        paths[name]={}
        for sym in COINS:
            row,eq,ledger=execute(raw,sym,name)
            rows.append(row);paths[name][sym]=eq
            if len(ledger): trades.append(ledger)
            assert row['split_second_exec']<=row['split_first_exec']
            print('RESULT',sym,name,round(row['final_equity'],4),round(row['mdd_pct'],3),
                  row['split_first_exec'],row['split_second_exec'],row['split_cancel'],flush=True)
    s=pd.DataFrame(rows);s.to_csv(OUT/'summary.csv',index=False)
    pd.concat(trades,ignore_index=True).to_csv(OUT/'trades.csv',index=False)
    mixed=s[s.variant=='MIXED_RESEARCH'].set_index('symbol')
    original=s[s.variant=='ORIGINAL_ALL_INSTANT'].set_index('symbol')
    comp=s.copy()
    for metric in ('final_equity','cagr_pct','mdd_pct','calmar'):
        comp['mixed_'+metric]=comp.symbol.map(mixed[metric]);comp['delta_mixed_'+metric]=comp[metric]-comp['mixed_'+metric]
        comp['original_'+metric]=comp.symbol.map(original[metric]);comp['delta_original_'+metric]=comp[metric]-comp['original_'+metric]
    comp.to_csv(OUT/'per_coin_comparison.csv',index=False)
    aggregate=[]
    for name in CONFIGS:
        eq=pd.concat([paths[name][sym] for sym in COINS],axis=1).dropna().sum(axis=1)
        pk=eq.cummax();mdd=float((100*(pk-eq)/pk).max())
        yrs=(eq.index[-1]-eq.index[0]).total_seconds()/31557600
        cagr=100*((eq.iloc[-1]/4000)**(1/yrs)-1)
        sub=s[s.variant==name]
        aggregate.append(dict(variant=name,final_equity_sum=float(eq.iloc[-1]),return_pct=100*(eq.iloc[-1]/4000-1),
                              cagr_pct=cagr,mdd_pct=mdd,calmar=cagr/mdd if mdd else np.nan,
                              first_orders=int(sub.split_first_exec.sum()),second_orders=int(sub.split_second_exec.sum()),
                              canceled=int(sub.split_cancel.sum()),rejects=int(sub.split_first_reject.sum()+sub.split_remainder_reject.sum())))
        pd.DataFrame({'time':eq.index,'equity':eq.values}).to_csv(OUT/f'aggregate_{name}.csv',index=False)
    agg=pd.DataFrame(aggregate);agg.to_csv(OUT/'aggregate_comparison.csv',index=False)
    # Parameter-neighborhood results above are exploratory, not untouched OOS.
    # Cost stress and shifted starts only for two prespecified 50:50 candidates.
    checks=[]
    for cost in (1.5,2.0):
        for name in ('MIXED_RESEARCH','SPLIT50_COIN','SPLIT50_PB20'):
            for sym in COINS:
                row,_,_=execute(raw,sym,name,cost=cost)
                checks.append(dict(cost_factor=cost,**row))
    pd.DataFrame(checks).to_csv(OUT/'cost_stress.csv',index=False)
    shifts=[]
    for start in ('2024-03-16','2024-09-16','2025-03-16'):
        for name in ('MIXED_RESEARCH','SPLIT50_COIN','SPLIT50_PB20'):
            for sym in COINS:
                row,_,_=execute(raw,sym,name,start=pd.Timestamp(start,tz='UTC'))
                shifts.append(dict(start_date=start,**row))
    pd.DataFrame(shifts).to_csv(OUT/'shifted_start.csv',index=False)
    report=['# Stage 5: split pyramid additions (research only)','',
            '2023-09-16 UTC inclusive to 2026-09-16 UTC exclusive; four separate $1000 USDT wallets.',
            'Initial entry NEVER split, 100% original breakout. No partial/trailing exits.',
            'Original unit is a HARD quantity budget for each add trigger; first+second <= one original unit.',
            'First MARKET add at +coin-specific original add interval; remainder MARKET at next CONFIRMED hourly CLOSE retreat from favorable extreme.',
            'Cancel pending remainder when close returns through the previously filled add reference or original STOP/Donchian exits.',
            'Do not arm another add while remainder outstanding. First and second buys count fractional units; max units bounded.',
            'Stop cannot loosen on either split fill; risk cap/min size/min notional independently checked on BOTH orders.',
            'No more than one fill per hourly bar. Shorts are symmetrically implemented.',
            'Fee 0.05% each fill and adverse slippage 0.02% per fill; funding/liquidation/intrabar paths/exchange rejection omitted.',
            'Earlier Stage2 XRP/SOL values were selected from the same 2023-2026 sample. This test is IS, NOT independent OOS.',
            '', '## Invariance','',pd.DataFrame(invariance).to_markdown(index=False),'',
            '## Four-wallet sum','',agg.to_markdown(index=False,floatfmt='.4f'),'',
            '## Per coin','',comp[['symbol','variant','final_equity','cagr_pct','mdd_pct','calmar',
                'split_first_exec','split_second_exec','split_cancel','delta_mixed_final_equity',
                'delta_original_final_equity']].to_markdown(index=False,floatfmt='.4f'),'',
            '## 1.5x/2x costs','',pd.DataFrame(checks)[['cost_factor','symbol','variant','final_equity','cagr_pct','mdd_pct']].to_markdown(index=False),'',
            '## Shifted start (sensitivity only)','',pd.DataFrame(shifts)[['start_date','symbol','variant','return_pct','mdd_pct','split_first_exec','split_second_exec']].to_markdown(index=False)]
    (OUT/'STAGE5_REPORT.md').write_text('\n'.join(report),encoding='utf-8')
    print('AGG_JSON',agg.to_json(orient='records'),flush=True)
    print('FINISHED_STAGE5',flush=True)
if __name__=='__main__':main()
