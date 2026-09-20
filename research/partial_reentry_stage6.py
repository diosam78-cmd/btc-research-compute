"""Research-only: two-tier profit take / adverse reentry / breakout restore.
No live code or webhooks. Original decisions preserve priority and are checked for invariance.
All decisions use completed 1h CLOSE; price pullbacks measured vs partial exit bar CLOSE.
"""
import inspect, textwrap, json, math
from pathlib import Path
import numpy as np
import pandas as pd
from research import pullback_stage1 as eng

OUT=Path('results_partial_reentry'); OUT.mkdir(exist_ok=True)
EXPECTED={'BTCUSDT':(6122.754677747175,26,9),'ETHUSDT':(4597.402574464877,31,12),
          'XRPUSDT':(4620.381764378673,19,7),'SOLUSDT':(11166.14038909758,12,7)}
PB={'BTCUSDT':None,'ETHUSDT':None,'XRPUSDT':.15,'SOLUSDT':.20}
# Activation in percent of cycle-entry wallet; two distinct levels, not price or ROE.
# Pullback is adverse percent of partial-exit bar closing PRICE, not percentage points of PnL.
# A second test with activation at 10/20 and 15/30 is deliberately included to protect big winners.
CASES={
 'BASE':None,
 'USER_7_15_Q30_P5':(7,15,.30,5),
 'USER_7_15_Q30_P2':(7,15,.30,2),
 '7_15_Q20_P2':(7,15,.20,2),
 '7_15_Q20_P3':(7,15,.20,3),
 '7_15_Q30_P3':(7,15,.30,3),
 '7_15_Q40_P2':(7,15,.40,2),
 '10_20_Q20_P2':(10,20,.20,2),
 '10_20_Q20_P3':(10,20,.20,3),
 '10_20_Q30_P2':(10,20,.30,2),
 '10_20_Q30_P3':(10,20,.30,3),
 '10_20_Q40_P3':(10,20,.40,3),
 '15_30_Q20_P3':(15,30,.20,3),
 '15_30_Q30_P3':(15,30,.30,3),
 '15_30_Q30_P5':(15,30,.30,5),
 '15_30_Q40_P3':(15,30,.40,3),
 '20_40_Q20_P3':(20,40,.20,3),
 '20_40_Q30_P3':(20,40,.30,3),
}

source=textwrap.dedent(inspect.getsource(eng.backtest))
def patch(a,b,n=1):
 global source
 assert source.count(a)==n,('ambiguous patch',source.count(a),n,a[:110])
 source=source.replace(a,b)
patch('    for bar in x.itertuples():\n', '''    # Auxiliary state; does not alter Turtle triggers or the original order cap.
    cycle_cash0=None
    flags=[False,False]
    slots=[]
    sale_events=[]
    extra_fees=0.0
    extra_orders=0
    rejects=0
    skipped_min=0
    completed_roundtrips=0
    max_unrestored=0.0

    def acceptable(q,px):
        return valid_qty(q,px,p)

    for bar in x.itertuples():
''')
patch('        px = float(bar.c)\n        event = False\n', '''        px = float(bar.c)
        pre_side, pre_cash=side,cash
        event = False
''')
# The engine's full close has priority. No partial/reentry may override original STOP/CHANNEL.
patch('                entry_arm = None; add_arm = None\n                event = True\n\n        # 2) ADD', '''                entry_arm = None; add_arm = None
                event = True

        # Standalone partial-profit/re-entry overlay; at most one new overlay order
        # per confirmed bar. Any accepted overlay order defers Turtle ADD one bar.
        if side and not event and variant.get('stages') is not None:
            first,second,fraction,pullback=variant['stages']
            levels=(first,second)
            eq_before=cash+side*qty*(px-avg)
            wallet_pct=100.0*(eq_before-cycle_cash0)/cycle_cash0
            # Restore pre-existing slots first, in chronological order.
            for slot in slots:
                if event: break
                adverse=100.0*side*(slot['reference']-px)/slot['reference']
                if slot['state']=='WAIT_PULLBACK' and adverse>=pullback:
                    q=slot['half1']; fp=fill_price(px,side)
                    remaining=max(0.0,(eq_before*CAP-qty*px)/(1.0+CAP*FEE))
                    if acceptable(q,fp) and q*fp<=remaining+1e-9:
                        oldq=qty; cash-=q*fp*FEE;fees_in_pos+=q*fp*FEE
                        avg=(avg*oldq+fp*q)/(oldq+q);qty+=q
                        extra_fees+=q*fp*FEE;extra_orders+=1
                        slot['state']='WAIT_BREAKOUT';slot['half1_fill']=str(t)
                        sale_events.append(dict(symbol=symbol,variant=variant_name,time=str(t),
                            cycle_entry=str(entry_time),tier=slot['tier'],event='REBUY_PULLBACK_HALF',
                            qty=q,fill=fp,wallet_pct=wallet_pct,reference=slot['reference']))
                        event=True
                    else: rejects+=1
                elif slot['state']=='WAIT_BREAKOUT' and side*(px-slot['reference'])>0:
                    q=slot['half2'];fp=fill_price(px,side)
                    remaining=max(0.0,(eq_before*CAP-qty*px)/(1.0+CAP*FEE))
                    if acceptable(q,fp) and q*fp<=remaining+1e-9:
                        oldq=qty;cash-=q*fp*FEE;fees_in_pos+=q*fp*FEE
                        avg=(avg*oldq+fp*q)/(oldq+q);qty+=q
                        extra_fes=q*fp*FEE;extra_fees+=extra_fes;extra_orders+=1
                        slot['state']='COMPLETE';completed_roundtrips+=1
                        sale_events.append(dict(symbol=symbol,variant=variant_name,time=str(t),
                            cycle_entry=str(entry_time),tier=slot['tier'],event='REBUY_BREAKOUT_HALF',
                            qty=q,fill=fp,wallet_pct=wallet_pct,reference=slot['reference']))
                        event=True
                    else: rejects+=1
            if not event:
                for ix,level in enumerate(levels):
                    if flags[ix] or wallet_pct<level: continue
                    fp=fill_price(px,-side)
                    q=floor_step(qty*fraction,p['step'])
                    leftover=qty-q
                    half1=floor_step(q*.5,p['step']);half2=q-half1
                    # No sale unless BOTH future halves and the remaining position
                    # are independently orderable under original minQty/minNotional.
                    if q>0 and acceptable(q,fp) and acceptable(leftover,fp) and \\
                       acceptable(half1,px) and acceptable(half2,px):
                        oldq=qty;weight=q/oldq
                        allocated_fee=fees_in_pos*weight
                        fees_in_pos-=allocated_fee
                        realized=side*q*(fp-avg)
                        sale_fee=q*fp*FEE
                        cash+=realized-sale_fee
                        extra_fees+=sale_fee;extra_orders+=1
                        qty=leftover
                        flags[ix]=True
                        slots.append(dict(tier=ix+1,state='WAIT_PULLBACK',reference=px,
                            sold=q,half1=half1,half2=half2,sale_fill=fp,
                            sale_realized_net=realized-sale_fee-allocated_fee))
                        sale_events.append(dict(symbol=symbol,variant=variant_name,time=str(t),
                            cycle_entry=str(entry_time),tier=ix+1,event='PARTIAL_TAKE_PROFIT',
                            qty=q,fill=fp,wallet_pct=wallet_pct,reference=px))
                        event=True
                    else:
                        skipped_min+=1
                    break
            max_unrestored=max(max_unrestored,sum(s['sold'] if s['state']=='WAIT_PULLBACK' else
                s['half2'] if s['state']=='WAIT_BREAKOUT' else 0.0 for s in slots))

        # 2) ADD''')
# After the entry event, store wallet-at-entry and reset all auxiliary states.
patch('        equity_rows.append((t, eq))\n', '''        equity_rows.append((t, eq))
        if pre_side==0 and side!=0:
            cycle_cash0=pre_cash
            flags=[False,False];slots=[]
        elif pre_side!=0 and side==0:
            # Full close's original net_pnl excludes previous partial realizations.
            # Cash delta from BEFORE the initial entry contains ALL entry/add/rebuy
            # fees, partial proceeds, closing fee, and all trading PnL exactly once.
            tr=trades[-1]
            tr['net_pnl']=cash-cycle_cash0
            tr['partial_tiers']=sum(s['sold']>0 for s in slots)
            tr['restored_tiers']=sum(s['state']=='COMPLETE' for s in slots)
            tr['unrestored_qty']=sum(s['sold'] if s['state']=='WAIT_PULLBACK' else
                s['half2'] if s['state']=='WAIT_BREAKOUT' else 0.0 for s in slots)
            flags=[False,False];slots=[];cycle_cash0=None
''')
# Baseline still exact because the above net_pnl equals original gross-loss-fees.
patch("        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,\n", "        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,\n        'overlay_orders':extra_orders, 'overlay_fees':extra_fees, 'overlay_rejected':rejects,\n        'skipped_min_qty':skipped_min,'complete_roundtrips':completed_roundtrips,\n        'max_unrestored_qty':max_unrestored,\n")
patch('    return summary, frame.equity.rename(symbol), pd.DataFrame(trades)',
      '    return summary, frame.equity.rename(symbol), pd.DataFrame(trades), pd.DataFrame(sale_events)')
namespace=dict(vars(eng));exec(compile(source,'<research_only_partial_restore>','exec'),namespace)
run_sim=namespace['backtest']

def metrics(curve):
    eq=curve.dropna();peak=eq.cummax();dd=100*(peak-eq)/peak
    yrs=(eq.index[-1]-eq.index[0]).total_seconds()/31557600.
    final=float(eq.iloc[-1]);mdd=float(dd.max())
    return dict(final=final,mdd_pct=mdd,cagr_pct=100*((final/4000)**(1/yrs)-1),
                calmar=100*((final/4000)**(1/yrs)-1)/mdd if mdd>0 else np.nan)

def main():
    data=eng.load_data()
    stats=[]; curves={};trades_all=[];events_all=[]
    for name,conf in CASES.items():
        paths={}
        for symbol,(target,ntr,nwin) in EXPECTED.items():
            v=dict(entry_pb=None,add_pb=PB[symbol],stages=conf)
            sm,eq,tr,events=run_sim(symbol,data[symbol],name,v)
            if name=='BASE':
                original,orig_eq,orig_tr=eng.backtest(symbol,data[symbol],name,
                    dict(entry_pb=None,add_pb=PB[symbol]))
                assert abs(sm['final_equity']-target)<.001
                assert sm['closed_trades']==ntr and sm['wins']==nwin
                assert np.max(np.abs(eq.to_numpy()-orig_eq.to_numpy()))<1e-7
                assert np.max(np.abs(tr.net_pnl.to_numpy()-orig_tr.net_pnl.to_numpy()))<1e-7
            assert abs(float(tr.net_pnl.sum())+1000-sm['final_equity'])<.05,(name,symbol,'PnL ledger')
            if len(events):
                assert all(events.event.isin(['PARTIAL_TAKE_PROFIT','REBUY_PULLBACK_HALF','REBUY_BREAKOUT_HALF']))
            sm['worst_trade_usdt']=float(tr.net_pnl.min()) if len(tr) else np.nan
            sm['winning_pnl_total']=float(tr.loc[tr.net_pnl>0,'net_pnl'].sum())
            sm['losing_pnl_total']=float(tr.loc[tr.net_pnl<0,'net_pnl'].sum())
            stats.append(sm);paths[symbol]=eq
            trades_all.append(tr.assign(variant=name))
            if len(events):events_all.append(events)
            print('RESULT',name,symbol,'final',round(sm['final_equity'],2),
                  'mdd',round(sm['mdd_pct'],2),'extra',sm['overlay_orders'],flush=True)
        cur=pd.concat(paths.values(),axis=1).dropna().sum(axis=1)
        curves[name]=cur
        cur.rename('equity').to_csv(OUT/f'curve_{name}.csv')
    detail=pd.DataFrame(stats)
    agg=[]
    for name,conf in CASES.items():
        d=detail[detail.variant==name]
        agg.append(dict(variant=name,config=json.dumps(conf),**metrics(curves[name]),
            overlay_orders=int(d.overlay_orders.sum()),roundtrips=int(d.complete_roundtrips.sum()),
            min_rejections=int(d.skipped_min_qty.sum()),reentry_rejections=int(d.overlay_rejected.sum()),
            winning_pnl=float(d.winning_pnl_total.sum()),losing_pnl=float(d.losing_pnl_total.sum()),
            trades=int(d.closed_trades.sum()),wins=int(d.wins.sum()),worst_trade_usdt=float(d.worst_trade_usdt.min())))
    a=pd.DataFrame(agg);base=a.iloc[0]
    a['delta_final']=a.final-base.final;a['delta_mdd_pp']=a.mdd_pct-base.mdd_pct
    a['delta_winning_pnl']=a.winning_pnl-base.winning_pnl
    a['loss_saved']=a.losing_pnl-base.losing_pnl
    a.to_csv(OUT/'aggregate_comparison.csv',index=False)
    detail.to_csv(OUT/'per_coin_comparison.csv',index=False)
    pd.concat(trades_all,ignore_index=True).to_csv(OUT/'all_trades.csv',index=False)
    if events_all:pd.concat(events_all,ignore_index=True).to_csv(OUT/'all_overlay_orders.csv',index=False)
    # Same-history splits: diagnostic only, not pristine unseen out-of-sample.
    splits=[]
    for name,eq in curves.items():
        for a0,b0,label in [('2023-09-16','2025-03-16','EARLY'),
                           ('2025-03-16','2026-09-16','LATE')]:
            sub=eq[(eq.index>=pd.Timestamp(a0,tz='UTC'))&(eq.index<pd.Timestamp(b0,tz='UTC'))]
            initial=float(eq[eq.index<sub.index[0]].iloc[-1]) if sub.index[0]>eq.index[0] else 4000
            splits.append(dict(variant=name,period=label,initial=initial,final=float(sub.iloc[-1]),
                return_pct=100*(float(sub.iloc[-1])/initial-1)))
    pd.DataFrame(splits).to_csv(OUT/'period_split.csv',index=False)
    (OUT/'methodology.json').write_text(json.dumps(dict(
        baseline_verified=True,live_V5211_unchanged=True,symbols=list(EXPECTED),
        period='2023-09-16 UTC to 2026-09-16 UTC exclusive',per_wallet_start=1000,
        trigger='first touch of 2 wallet return thresholds vs PRE-ENTRY cash',
        pullback='adverse confirmed 1h close PRICE move percent from partial sale close',
        breakout='confirmed CLOSE strictly beyond the same partial sale CLOSE, after pullback-half buy',
        order_priority='original full EXIT, oldest reserved restoration, tier take profit, original turtle ADD, original entry',
        max_one_event_per_bar=True, fees=eng.FEE,slippage=eng.SLIP,
        one_partial_per_tier_per_cycle=True, original_turtle_add_enabled=True,
        no_lookahead=True,no_funding_liquidations_intrabar_or_partial_fills=True,
        selection='parameter screening on previously studied period; not independent OOS'),indent=2))
    print('AGGREGATE',a.to_json(orient='records'),flush=True)
    print('DONE',flush=True)
if __name__=='__main__':main()
