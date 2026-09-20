"""Instrument the already-used Turtle research engine; no changes to its trading logic.
USDT 2023-09-16 UTC inclusive to 2026-09-16 UTC exclusive, each wallet 1000.
All intratrade excursions are CONFIRMED 1-hour CLOSE mark-to-market, not 1h wicks.
"""
import inspect
import json
import textwrap
from pathlib import Path
import numpy as np
import pandas as pd
from research import pullback_stage1 as eng

OUT=Path('results_four_coin_loss_cycles'); OUT.mkdir(exist_ok=True)
fn_src=textwrap.dedent(inspect.getsource(eng.backtest))
anchor1='        px = float(bar.c)\n        event = False\n'
replacement1=('        px = float(bar.c)\n'
              '        pre_side, pre_units, pre_qty, pre_cash, pre_avg = side, units, qty, cash, avg\n'
              '        event = False\n')
anchor2='    trades = []\n    equity_rows = []\n'
replacement2=('    trades = []\n    equity_rows = []\n'
              '    cycle_rows, cycle_events, cycle_trace = [], [], []\n'
              '    active_cycle = None\n    active_trace = []\n')
anchor3='        equity_rows.append((t, eq))\n'
track='''        # OBSERVATION ONLY: executed AFTER the original engine has decided all orders.
        if pre_side == 0 and side != 0:
            active_cycle = dict(symbol=symbol, cycle_id=len(cycle_rows)+1,
                entry_time=str(t), side=side, starting_cash=pre_cash,
                first_fill=avg, first_qty=qty, frozen_N=entry_n,
                adds=0, peak_pct=-float('inf'), trough_pct=float('inf'),
                peak_time='', trough_time='', peak_price_pct=-float('inf'),
                trough_price_pct=float('inf'), first_positive_time='',
                entry_units=units, entry_cash_after=cash)
            active_trace=[]
            cycle_events.append(dict(symbol=symbol,cycle_id=active_cycle['cycle_id'],
                time=str(t),event='ENTRY', units=units,qty=qty,fill_price=avg,
                wallet_return_pct=100*(eq-pre_cash)/pre_cash, stop=stop))
        if active_cycle is not None:
            c=active_cycle
            if pre_side != 0 and side != 0 and units>pre_units:
                c['adds']+=units-pre_units
                cycle_events.append(dict(symbol=symbol,cycle_id=c['cycle_id'],
                    time=str(t),event='ADD',units=units,qty=qty,fill_price=last_fill,
                    wallet_return_pct=100*(eq-c['starting_cash'])/c['starting_cash'],stop=stop))
            is_exit=pre_side!=0 and side==0
            if is_exit:
                # Mark open position AT the exit trigger close, BEFORE adverse exit
                # slip/fee. This is the final pre-liquidation open-position observation.
                open_equity=pre_cash+pre_side*pre_qty*(px-pre_avg)
                mark_pct=100*(open_equity-c['starting_cash'])/c['starting_cash']
            else:
                mark_pct=100*(eq-c['starting_cash'])/c['starting_cash']
            price_pct=100*c['side']*(px/c['first_fill']-1)
            if mark_pct>c['peak_pct']:
                c['peak_pct']=mark_pct; c['peak_time']=str(t)
            if mark_pct<c['trough_pct']:
                c['trough_pct']=mark_pct; c['trough_time']=str(t)
            c['peak_price_pct']=max(c['peak_price_pct'],price_pct)
            c['trough_price_pct']=min(c['trough_price_pct'],price_pct)
            if mark_pct>0 and not c['first_positive_time']:
                c['first_positive_time']=str(t)
            active_trace.append(dict(symbol=symbol,cycle_id=c['cycle_id'],time=str(t),
                stage='PRE_EXIT_MARK' if is_exit else 'OPEN_MARK',
                close_price=px,units=pre_units if is_exit else units,
                qty=pre_qty if is_exit else qty,wallet_return_pct=mark_pct,
                first_fill_price_return_pct=price_pct))
            if is_exit:
                closed=trades[-1]
                final_pct=100*closed['net_pnl']/c['starting_cash']
                assert abs(cash-c['starting_cash']-closed['net_pnl'])<1e-6,(symbol,t,cash,c['starting_cash'],closed)
                assert c['adds']==closed['units']-1,(symbol,t,c['adds'],closed['units'])
                c.update(exit_time=str(t),duration_hours=(t-pd.Timestamp(c['entry_time'])).total_seconds()/3600,
                    outcome='LOSS' if closed['net_pnl']<0 else 'WIN' if closed['net_pnl']>0 else 'FLAT',
                    exit_reason=closed['reason'],net_pnl=closed['net_pnl'],
                    final_return_pct=final_pct,pre_exit_mark_pct=mark_pct,
                    peak_to_exit_pp=c['peak_pct']-final_pct,
                    ever_positive=c['peak_pct']>0,add_count=c['adds'],exit_units=closed['units'],
                    exit_qty=closed['qty'],avg_entry=closed['avg_entry'],exit_fill=closed['exit_price'],
                    peak_hour_from_entry=(pd.Timestamp(c['peak_time'])-pd.Timestamp(c['entry_time'])).total_seconds()/3600,
                    trough_hour_from_entry=(pd.Timestamp(c['trough_time'])-pd.Timestamp(c['entry_time'])).total_seconds()/3600,
                    hours_peak_to_exit=(t-pd.Timestamp(c['peak_time'])).total_seconds()/3600)
                cycle_rows.append(c)
                cycle_events.append(dict(symbol=symbol,cycle_id=c['cycle_id'],time=str(t),
                    event='EXIT_'+closed['reason'],units=0,qty=0.0,
                    fill_price=closed['exit_price'],wallet_return_pct=final_pct,stop=np.nan))
                if closed['net_pnl']<0:
                    cycle_trace.extend(active_trace)
                active_trace=[]; active_cycle=None
'''
for anchor in (anchor1,anchor2,anchor3):
    assert fn_src.count(anchor)==1, ('instrumentation anchor ambiguous',repr(anchor[:60]),fn_src.count(anchor))
fn_src=fn_src.replace(anchor1,replacement1).replace(anchor2,replacement2).replace(anchor3,anchor3+track)
ret='    return summary, frame.equity.rename(symbol), pd.DataFrame(trades)'
assert fn_src.count(ret)==1, ('return anchor',fn_src.count(ret))
fn_src=fn_src.replace(ret,'''    return (summary,frame.equity.rename(symbol),pd.DataFrame(trades),
            pd.DataFrame(cycle_rows),pd.DataFrame(cycle_events),pd.DataFrame(cycle_trace))''')
ns=dict(vars(eng)); exec(compile(fn_src,'<instrumented_original_research_engine>','exec'),ns)
observed=ns['backtest']

expected={'BTCUSDT':(6122.754677747175,26),
          'ETHUSDT':(4597.402574464877,31),
          'XRPUSDT':(4620.381764378673,19),
          'SOLUSDT':(11166.14038909758,12)}
variants={'BTCUSDT':dict(entry_pb=None,add_pb=None),
          'ETHUSDT':dict(entry_pb=None,add_pb=None),
          'XRPUSDT':dict(entry_pb=None,add_pb=.15),
          'SOLUSDT':dict(entry_pb=None,add_pb=.20)}

def main():
    raw=eng.load_data()
    all_cycles=[]; all_events=[]; all_trace=[]; summaries=[]
    for symbol in expected:
        print('ANALYZING',symbol,flush=True)
        s,eq,trades,cycles,events,trace=observed(symbol,raw[symbol],'LIVE_V5211_PARAMETERS',variants[symbol])
        exp_final,exp_trades=expected[symbol]
        assert abs(s['final_equity']-exp_final)<0.01,(symbol,'equity mismatch',s['final_equity'],exp_final)
        assert len(trades)==exp_trades,(symbol,'trade mismatch',len(trades),exp_trades)
        assert len(cycles)==len(trades),(symbol,'incomplete reconstructed cycles')
        if len(trace):
            assert (trace.cycle_id.isin(cycles.loc[cycles.outcome=='LOSS','cycle_id'])).all()
        summaries.append(s);all_cycles.append(cycles);all_events.append(events);all_trace.append(trace)
        print('VALIDATED',symbol,'final',round(s['final_equity'],4),'cycles',len(cycles),
            'losing',sum(cycles.outcome=='LOSS'),flush=True)
    cycles=pd.concat(all_cycles,ignore_index=True)
    events=pd.concat(all_events,ignore_index=True)
    traces=pd.concat(all_trace,ignore_index=True)
    losses=cycles[cycles.outcome=='LOSS'].copy()
    losses['recovered_profit_before_loss']=losses.peak_pct>0
    losses['loss_after_adds']=losses.add_count>0
    stats=[]
    for symbol,g in cycles.groupby('symbol',sort=False):
        lost=g[g.outcome=='LOSS']; won=g[g.outcome=='WIN']
        stats.append(dict(symbol=symbol,all_cycles=len(g),loss_cycles=len(lost),win_cycles=len(won),
          stop_loss_cycles=int(sum((lost.exit_reason=='STOP'))),channel_loss_cycles=int(sum((lost.exit_reason=='CHANNEL'))),
          loss_no_add=int(sum(lost.add_count==0)),loss_with_add=int(sum(lost.add_count>0)),
          loss_add_mean=float(lost.add_count.mean()),loss_add_median=float(lost.add_count.median()),
          loss_add_max=int(lost.add_count.max()),loss_peak_mean=float(lost.peak_pct.mean()),
          loss_peak_median=float(lost.peak_pct.median()),loss_trough_mean=float(lost.trough_pct.mean()),
          loss_trough_median=float(lost.trough_pct.median()),loss_final_mean=float(lost.final_return_pct.mean()),
          loss_final_median=float(lost.final_return_pct.median()),
          loss_peak_to_exit_mean=float(lost.peak_to_exit_pp.mean()),
          loss_ever_positive=int(sum(lost.peak_pct>0)),loss_ever_over_1pct=int(sum(lost.peak_pct>=1)),
          loss_ever_over_3pct=int(sum(lost.peak_pct>=3)),
          loss_duration_median_h=float(lost.duration_hours.median()),
          loss_net_pnl_sum=float(lost.net_pnl.sum()),win_net_pnl_sum=float(won.net_pnl.sum()),
          winner_add_mean=float(won.add_count.mean()),winner_add_median=float(won.add_count.median())))
    pd.DataFrame(summaries).to_csv(OUT/'reproduction_summary.csv',index=False)
    cycles.to_csv(OUT/'all_closed_cycles.csv',index=False)
    losses.to_csv(OUT/'loss_cycles_detailed.csv',index=False)
    events.to_csv(OUT/'all_cycle_events.csv',index=False)
    traces.to_csv(OUT/'loss_cycles_hourly_path.csv',index=False)
    pd.DataFrame(stats).to_csv(OUT/'four_coin_loss_summary.csv',index=False)
    meta=dict(source='Binance Vision USD-M 1h OHLC archives; original research engine instrumented after decisions',
              time='CSV times are 1h bar OPEN UTC; trading signals on the same bar CLOSE UTC+1h',
              start=str(eng.START),end_exclusive=str(eng.END),
              tested_symbols=list(expected),cash=1000,fee_per_fill=eng.FEE,slippage_per_fill=eng.SLIP,
              excursion='confirmed 1h close, wallet-equity return since cycle entry, includes entry/add fees; exit uses pre-fill mark then separately final net realization',
              stop='STOP and CHANNEL are distinguished, both may generate losses',
              no_funding_liquidation_intrabar_exchange_fills=True,
              validated_against_stage5_expected_final=True)
    (OUT/'methodology.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print('SUMMARIES',pd.DataFrame(stats).to_json(orient='records'),flush=True)
    print('COMPLETE ALL',len(cycles),'LOSSES',len(losses),'PATH ROWS',len(traces),flush=True)
if __name__=='__main__':main()
