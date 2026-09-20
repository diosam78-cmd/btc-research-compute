"""Independent four-coin Turtle V5.2.11 parameter study, NOT for live trading.
Compare current-equity-to-theoretical-next-stop risk budgets of 4/6/8 percent.
Research uses existing engine with precisely three ADD sizing assignments instrumented.
All stops are checked on 1h CLOSE, so budgets are projections, not hard risk guarantees.
"""
import inspect, json, textwrap, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import pullback_stage1 as eng

OUT=Path('results_add_risk_budget'); OUT.mkdir(exist_ok=True)
src=textwrap.dedent(inspect.getsource(eng.backtest))
anchor='    for bar in x.itertuples():\n'
assert src.count(anchor)==1
helper='''    # Diagnostic state is isolated to a single backtest invocation.
    risk_audit=[]
    cycle_outcomes=[]
    cycle_start_cash=None

    def cap_add(requested, fill_px, eq_now, admission_remaining):
        limit=variant.get('risk_cap_pct')
        if limit is None:
            return requested
        # Preserve original admission semantics: rejected full units stay rejected.
        if not valid_qty(requested, fill_px, p) or requested*fill_px > admission_remaining or eq_now<=0:
            return requested
        new_stop=fill_px-side*p['stop']*entry_n
        exit_px=fill_price(new_stop,-side)
        def projection(addq):
            fee=addq*fill_px*FEE
            totalq=qty+addq
            newavg=(avg*qty+fill_px*addq)/totalq
            stop_equity=cash-fee+side*totalq*(exit_px-newavg)-totalq*exit_px*FEE
            return 100.0*(eq_now-stop_equity)/eq_now
        full_risk=projection(requested)
        if full_risk <= limit+1e-10:
            return requested
        if variant['risk_mode']=='skip':
            approved=0.0
        else:
            # Projection is increasing with add quantity at this fixed new stop.
            # Search original quantity increments; never round upward or bypass
            # the original 3x admission gate and 50 USDT minimum notional.
            hi=int(math.floor(requested/p['step']+1e-9))
            lo=0
            while lo<hi:
                mid=(lo+hi+1)//2
                if projection(mid*p['step']) <= limit+1e-10:
                    lo=mid
                else:
                    hi=mid-1
            approved=lo*p['step']
            if not valid_qty(approved,fill_px,p):
                approved=0.0
        risk_audit.append(dict(symbol=symbol, variant=variant_name, bar_open_utc=str(t),
            pre_units=units, side=side, wallet_equity=eq_now, original_qty=requested,
            selected_qty=approved, projected_full_risk_pct=full_risk,
            projected_selected_risk_pct=projection(approved) if approved>0 else np.nan,
            proposed_stop=new_stop, decision='SKIP' if approved==0 else 'REDUCE'))
        return approved

'''
src=src.replace(anchor,helper+anchor)
anchor='        px = float(bar.c)\n        event = False\n'
assert src.count(anchor)==1
src=src.replace(anchor,'        px = float(bar.c)\n        pre_side, pre_cash = side, cash\n        event = False\n')
inst='                    addq = unit\n'
assert src.count(inst)==1,src.count(inst)
src=src.replace(inst,'                    addq = cap_add(unit, fp, eq, remaining)\n')
pb='                                fp = fill_price(px, side); addq = unit\n'
assert src.count(pb)==2,src.count(pb)
src=src.replace(pb,'                                fp = fill_price(px, side); addq = cap_add(unit, fp, eq, remaining)\n')
anchor='        equity_rows.append((t, eq))\n'
assert src.count(anchor)==1
src=src.replace(anchor,anchor+'''        if pre_side==0 and side!=0:
            cycle_start_cash=pre_cash
        if pre_side!=0 and side==0:
            trade=trades[-1]
            assert cycle_start_cash is not None
            cycle_outcomes.append(dict(symbol=symbol, variant=variant_name,
                entry_time=trade['entry_time'],exit_time=trade['exit_time'],
                exit_reason=trade['reason'],units=trade['units'],net_pnl=trade['net_pnl'],
                start_cash=cycle_start_cash,
                cycle_return_pct=100.0*trade['net_pnl']/cycle_start_cash))
            cycle_start_cash=None
''')
ret='    return summary, frame.equity.rename(symbol), pd.DataFrame(trades)'
assert src.count(ret)==1
src=src.replace(ret,'''    return (summary,frame.equity.rename(symbol),pd.DataFrame(trades),
            pd.DataFrame(risk_audit),pd.DataFrame(cycle_outcomes))''')
ns=dict(vars(eng))
exec(compile(src,'<unchanged_engine_instrumented_add_sizing>','exec'),ns)
research_engine=ns['backtest']
EXPECTED={'BTCUSDT':(6122.754677747175,26,9),
          'ETHUSDT':(4597.402574464877,31,12),
          'XRPUSDT':(4620.381764378673,19,7),
          'SOLUSDT':(11166.14038909758,12,7)}
MODES={'BTCUSDT':None,'ETHUSDT':None,'XRPUSDT':.15,'SOLUSDT':.20}
VARIANTS={'BASE':(None,'resize'), 'R4':(4.,'resize'),'R6':(6.,'resize'),
          'R8':(8.,'resize'),'S4':(4.,'skip'),'S6':(6.,'skip'),'S8':(8.,'skip')}

def metrics(eq,initial):
    eq=eq.dropna(); peak=eq.cummax(); dd=100*(peak-eq)/peak
    yr=(eq.index[-1]-eq.index[0]).total_seconds()/31557600
    fin=float(eq.iloc[-1]); mdd=float(dd.max())
    return dict(initial=initial,final=fin,return_pct=100*(fin/initial-1),
        cagr_pct=100*((fin/initial)**(1/yr)-1) if fin>0 else np.nan,
        mdd_pct=mdd,calmar=100*((fin/initial)**(1/yr)-1)/mdd if fin>0 and mdd>0 else np.nan,
        mdd_open_utc=str(dd.idxmax()))

def main():
    raw=eng.load_data()
    sums=[]; aggregate=[]; cycle_all=[]; audit_all=[]; equity_all={}; splits=[]
    for variant_name,(cap,mode) in VARIANTS.items():
        paths=[]
        for symbol,(expected,ntrades,nwins) in EXPECTED.items():
            variant=dict(entry_pb=None,add_pb=MODES[symbol],risk_cap_pct=cap,risk_mode=mode)
            s,eq,tr,ra,cy=research_engine(symbol,raw[symbol],variant_name,variant)
            if variant_name=='BASE':
                assert abs(s['final_equity']-expected)<.01,(symbol,s['final_equity'],expected)
                assert s['closed_trades']==ntrades and s['wins']==nwins,(symbol,s)
            assert len(cy)==len(tr),(symbol,variant_name,'cycle mismatch')
            if len(ra):
                assert (ra.projected_full_risk_pct>cap-1e-7).all()
                assert ((ra.selected_qty==0)|(ra.projected_selected_risk_pct<=cap+1e-7)).all()
                assert (ra.selected_qty<ra.original_qty).all()
            paths.append(eq)
            equity_all[(variant_name,symbol)]=eq
            cycle_all.append(cy)
            if len(ra):audit_all.append(ra)
            s.update(risk_cap_pct=cap,risk_mode=mode,risk_interventions=len(ra),
                risk_skips=int(sum(ra.selected_qty==0)) if len(ra) else 0,
                risk_resizes=int(sum(ra.selected_qty>0)) if len(ra) else 0,
                worst_cycle_return_pct=float(cy.cycle_return_pct.min()) if len(cy) else np.nan,
                worst_loss_usdt=float(cy.net_pnl.min()) if len(cy) else np.nan,
                total_loss_usdt=float(cy.loc[cy.net_pnl<0,'net_pnl'].sum()),
                win_pnl_sum=float(cy.loc[cy.net_pnl>0,'net_pnl'].sum()))
            sums.append(s)
            print('COIN',variant_name,symbol,round(s['final_equity'],2),
                  'MDD',round(s['mdd_pct'],3),'risk',len(ra),'skip',s['risk_skips'],flush=True)
        aggregate_equity=pd.concat(paths,axis=1).dropna().sum(axis=1)
        if variant_name=='BASE':
            assert abs(float(aggregate_equity.iloc[-1])-26506.679405688305)<.02
        aggregate_equity.rename(variant_name).to_csv(OUT/f'equity_{variant_name}.csv')
        aggregate.append(dict(variant=variant_name,**metrics(aggregate_equity,4000)))
        for start,end,label in [
            ('2023-09-16','2025-03-16','EARLY'),('2025-03-16','2026-09-16','LATE')]:
            seg=aggregate_equity.loc[(aggregate_equity.index>=pd.Timestamp(start,tz='UTC')) &
                                     (aggregate_equity.index<pd.Timestamp(end,tz='UTC'))]
            prev=aggregate_equity.loc[aggregate_equity.index<seg.index[0]].iloc[-1] if seg.index[0]>aggregate_equity.index[0] else 4000.
            splits.append(dict(variant=variant_name,period=label,**metrics(seg,prev)))
    c=pd.concat(cycle_all,ignore_index=True)
    s=pd.DataFrame(sums)
    a=pd.DataFrame(aggregate)
    base=c[c.variant=='BASE']
    big=base[base.net_pnl>0].sort_values('net_pnl',ascending=False).groupby('symbol').head(3).copy()
    retained=[]
    for variant in VARIANTS:
        if variant=='BASE': continue
        v=c[c.variant==variant]
        for symbol in EXPECTED:
            old=big[big.symbol==symbol]
            now=v[v.symbol==symbol]
            m=old.merge(now,on=['symbol','entry_time','exit_time'],how='left',suffixes=('_baseline','_candidate'))
            retained.append(dict(variant=variant,symbol=symbol,baseline_top3_pnl=float(old.net_pnl.sum()),
                matching_cycles=int(m.net_pnl_candidate.notna().sum()),
                matched_top3_pnl=float(m.net_pnl_candidate.fillna(0).sum()),
                unmatched_baseline_pnl=float(m.loc[m.net_pnl_candidate.isna(),'net_pnl_baseline'].sum())))
    baseline=a[a.variant=='BASE'].iloc[0]
    a['delta_final_usdt']=a.final-baseline.final
    a['delta_mdd_pp']=a.mdd_pct-baseline.mdd_pct
    a['delta_cagr_pp']=a.cagr_pct-baseline.cagr_pct
    s['baseline_final']=s.symbol.map(s[s.variant=='BASE'].set_index('symbol').final_equity)
    s['delta_final_usdt']=s.final_equity-s.baseline_final
    a.to_csv(OUT/'aggregate_comparison.csv',index=False)
    s.to_csv(OUT/'per_coin_comparison.csv',index=False)
    c.to_csv(OUT/'cycle_comparison.csv',index=False)
    if audit_all:pd.concat(audit_all,ignore_index=True).to_csv(OUT/'risk_interventions.csv',index=False)
    pd.DataFrame(splits).to_csv(OUT/'period_split.csv',index=False)
    pd.DataFrame(retained).to_csv(OUT/'top3_winner_preservation.csv',index=False)
    (OUT/'methodology.json').write_text(json.dumps(dict(
        live_code_unchanged='V5.2.11 unchanged; isolated GitHub research branch only',
        source='Binance USD-M OHLC 1h via existing engine',period='2023-09-16 UTC to 2026-09-16 UTC exclusive',
        symbols=list(EXPECTED),initial_each=1000,fee_per_fill=eng.FEE,slippage_per_fill=eng.SLIP,
        model='A risk budget is pct of CURRENT pre-add marked-to-market equity lost if ALL shares exit at updated STOP_N from the proposed add fill, including the add fee, exit fee and assumed adverse slip.',
        original_entry_exit_indicators_add_arm_and_add_trigger='unchanged',
        original_admission='3x gross gate, first requires original full unit admissible',
        resized_add='integer step search; 0 means no fill; original unit size remains the reference for later adds',
        execution='1h confirmed close; stop fill projection optimistic in gaps, not guaranteed risk limit',
        no_funding_intrabar_liquidation_partial_fills=True,
        baseline_reproduced=True,parameter_selection='prespecified 4 6 8 pct, resize/skip; in-sample research, not OOS'),indent=2))
    print('AGGREGATE',a.to_json(orient='records'),flush=True)
    print('DONE',len(s),'scenarios; baseline checked',flush=True)
if __name__=='__main__':main()
