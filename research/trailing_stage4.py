"""Research only: insert trailing partial exits into audited Stage1/2 engine."""
import inspect,json
from pathlib import Path
import numpy as np
import pandas as pd
import pullback_stage1_core as core
OUT=Path('results_turtle_stage4_trailing');OUT.mkdir(exist_ok=True)
COINS=['BTCUSDT','ETHUSDT','XRPUSDT','SOLUSDT']
PB={'BTCUSDT':None,'ETHUSDT':None,'XRPUSDT':0.15,'SOLUSDT':0.20}
START=pd.Timestamp('2023-09-16',tz='UTC'); END=pd.Timestamp('2026-09-16',tz='UTC')
ORIG={'BTCUSDT':6122.754677747175,'ETHUSDT':4597.402574464877,'XRPUSDT':4620.381764378673,'SOLUSDT':11166.14038909758}
VARS={'MIXED_NO_TRAIL':(0.,2.,1.5),'P25_T10':(.25,2.,1.),'P25_T15':(.25,2.,1.5),'P25_T20':(.25,2.,2.),'P50_T10':(.5,2.,1.),'P50_T15':(.5,2.,1.5),'P50_T20':(.5,2.,2.),'P25_A3_T15':(.25,3.,1.5)}

def build():
 s=inspect.getsource(core.backtest)
 def change(a,b,n=1):
  nonlocal s
  assert s.count(a)==n,(s.count(a),n,a[:90])
  s=s.replace(a,b)
 change('def backtest(','def backtest_trail(')
 change('    entry_time = None\n\n    # Pullback state.', '''    entry_time = None
    peak_close = np.nan
    trail_armed = False
    partial_done = False
    partial_pnl = 0.0
    partial_exit_events = 0
    partial_qty_sum = 0.0
    partial_rejected = 0
    entered_qty = 0.0

    # Pullback state.''')
 change('                net = realized - exit_fee - fees_in_pos\n','                net = partial_pnl + realized - exit_fee - fees_in_pos\n')
 change("                    'reason': 'STOP' if stop_hit else 'CHANNEL', 'net_pnl': net,\n", "                    'reason': 'STOP' if stop_hit else 'CHANNEL', 'net_pnl': net,\n                    'original_entry_qty': entered_qty, 'partial_done': partial_done,\n")
 change('                entry_arm = None; add_arm = None\n                event = True\n\n        # 2) ADD', '''                entry_arm = None; add_arm = None
                peak_close = np.nan; trail_armed = False; partial_done = False
                partial_pnl = 0.0; entered_qty = 0.0
                event = True

            # Original stop/channel take priority; only confirmed 1h CLOSE events.
            if not event and variant['trail_fraction'] > 0 and not partial_done:
                assert np.isfinite(peak_close),'Missing peak_close for open position'
                peak_close = max(peak_close, px) if side > 0 else min(peak_close, px)
                if side * (peak_close - avg) >= variant['trail_activation_n'] * entry_n:
                    trail_armed = True
                retracement = side * (peak_close - px)
                if trail_armed and retracement >= variant['trail_distance_n'] * entry_n:
                    fp = fill_price(px, -side)
                    sellq = floor_step(qty * variant['trail_fraction'], p['step'])
                    residual = qty - sellq
                    if valid_qty(sellq, fp, p) and valid_qty(residual, fp, p):
                        fraction = sellq / qty
                        fee_part = fees_in_pos * fraction
                        fees_in_pos -= fee_part
                        fee_exit = sellq * fp * FEE
                        pnl_part = side * sellq * (fp - avg)
                        cash += pnl_part - fee_exit
                        partial_pnl += pnl_part - fee_exit - fee_part
                        qty = residual
                        partial_done = True
                        partial_exit_events += 1
                        partial_qty_sum += sellq
                        if add_arm is not None:
                            add_arm = None
                            add_cancel += 1
                        event = True
                    else:
                        partial_rejected += 1

        # 2) ADD''')
 change("        if side and not event and units < p['maxu']:\n","        if side and not event and not partial_done and units < p['maxu']:\n")
 change('qty += addq; last_fill = fp; units += 1','qty += addq; entered_qty += addq; last_fill = fp; units += 1',3)
 change("                            stop = fp - d * p['stop'] * bar.n; units = 1; entry_time = t\n",'''                            stop = fp - d * p['stop'] * bar.n; units = 1; entry_time = t
                            peak_close = px; trail_armed = False; partial_done = False
                            partial_pnl = 0.0; entered_qty = q
''')
 change("        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,\n", "        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,\n        'partial_exit_events': partial_exit_events, 'partial_qty_sum': partial_qty_sum,\n        'partial_rejected': partial_rejected, 'trail_fraction': variant['trail_fraction'],\n        'trail_activation_n': variant['trail_activation_n'],\n        'trail_distance_n': variant['trail_distance_n'],\n")
 ns=core.__dict__.copy();exec(compile(s,'stage4_modified_core','exec'),ns)
 return ns['backtest_trail']

backtest_trail=build()
def run(data,sym,name,cfg,start=START,end=END,cost=1.):
 fraction,act,dist=cfg
 core.START,core.END=start,end
 core.FEE,core.SLIP=.0005*cost,.0002*cost
 backtest_trail.__globals__.update(START=start,END=end,FEE=core.FEE,SLIP=core.SLIP)
 v={'entry_pb':None,'add_pb':PB[sym],'trail_fraction':fraction,'trail_activation_n':act,'trail_distance_n':dist}
 return backtest_trail(sym,data[sym],name,v)

def main():
 data=core.load_data();check=[]
 for sym in COINS:
  core.START,core.END=START,END;core.FEE,core.SLIP=.0005,.0002
  a,ae,at=core.backtest(sym,data[sym],'REF',{'entry_pb':None,'add_pb':PB[sym]})
  b,be,bt=run(data,sym,'PATCH_DISABLED',VARS['MIXED_NO_TRAIL'])
  diff=float(np.max(np.abs(ae.to_numpy()-be.to_numpy())))
  td=float(np.max(np.abs(at.net_pnl.to_numpy()-bt.net_pnl.to_numpy()))) if len(at)==len(bt) and len(at) else (0. if len(at)==len(bt) else float('inf'))
  assert abs(a['final_equity']-ORIG[sym])<1e-7,(sym,a['final_equity'])
  assert diff<1e-7 and td<1e-7 and b['partial_exit_events']==0,(sym,diff,td)
  check.append({'symbol':sym,'expected':ORIG[sym],'actual':a['final_equity'],'hourly_max_error':diff,'trade_max_error':td})
 pd.DataFrame(check).to_csv(OUT/'baseline_invariance.csv',index=False)
 print('INVARIANCE_OK',json.dumps(check),flush=True)
 sums=[];paths={};trades=[]
 for name,cfg in VARS.items():
  paths[name]={}
  for sym in COINS:
   sm,eq,tr=run(data,sym,name,cfg);sums.append(sm);paths[name][sym]=eq
   if len(tr):trades.append(tr)
   print('SCREEN',name,sym,round(sm['final_equity'],4),round(sm['mdd_pct'],3),sm['partial_exit_events'],flush=True)
 summary=pd.DataFrame(sums);summary.to_csv(OUT/'summary.csv',index=False)
 trade_df=pd.concat(trades,ignore_index=True);trade_df.to_csv(OUT/'trades.csv',index=False)
 base=summary[summary.variant=='MIXED_NO_TRAIL'].set_index('symbol')
 comp=summary.copy()
 for c in ['final_equity','cagr_pct','mdd_pct','calmar','pf','closed_trades']:
  comp['delta_'+c]=comp[c]-comp.symbol.map(base[c])
 comp.to_csv(OUT/'per_symbol_screen.csv',index=False)
 aggregate=[]
 for name in VARS:
  eq=pd.concat(paths[name].values(),axis=1).dropna().sum(axis=1)
  final=float(eq.iloc[-1]);mdd=float((100*(eq.cummax()-eq)/eq.cummax()).max())
  years=(eq.index[-1]-eq.index[0]).total_seconds()/31557600
  cagr=100*((final/4000.)**(1/years)-1)
  aggregate.append({'variant':name,'final_equity_sum':final,'cagr_pct':cagr,'mdd_pct':mdd,'calmar':cagr/mdd,'partial_exits':int(summary[summary.variant==name].partial_exit_events.sum())})
  pd.DataFrame({'time':eq.index,'equity':eq.values}).to_csv(OUT/f'aggregate_{name}.csv',index=False)
 agg=pd.DataFrame(aggregate)
 for c in ['final_equity_sum','cagr_pct','mdd_pct','calmar']:
  agg['delta_'+c]=agg[c]-agg.loc[agg.variant=='MIXED_NO_TRAIL',c].iloc[0]
 agg.to_csv(OUT/'aggregate_screen.csv',index=False)
 keep=['MIXED_NO_TRAIL','P25_T15','P50_T15']
 windows=[]
 for start_s in ['2024-03-16','2024-09-16','2025-03-16']:
  start=pd.Timestamp(start_s,tz='UTC')
  for name in keep:
   for sym in COINS:
    sm,_,_=run(data,sym,name,VARS[name],start=start)
    windows.append({'window_start':start_s,**sm})
 pd.DataFrame(windows).to_csv(OUT/'shifted_starts.csv',index=False)
 costs=[]
 for factor in [1.5,2.0]:
  for name in keep:
   for sym in COINS:
    sm,_,_=run(data,sym,name,VARS[name],cost=factor)
    costs.append({'cost_factor':factor,**sm})
 pd.DataFrame(costs).to_csv(OUT/'cost_stress.csv',index=False)
 report=['# Turtle Stage 4: confirmed-close trailing partial-exit initial screen','',
 '- RESEARCH ONLY. 100% breakout entry, never hybrid. BTC/ETH original adds, XRP 0.15N, SOL 0.20N pullback adds.',
 '- Stage3 candidates were selected using the same 2023-2026 history. This is IS sensitivity, NOT pristine OOS.',
 '- Original full stop/Donchian exit has priority. After +2N unrealized favorable CLOSE excursion vs current VWAP, track best CLOSE.',
 '- At -1.0/-1.5/-2.0N from best close, close one 25% or 50% fraction MARKET at confirmed hour close, rounded to provisional qtyStep.',
 '- One partial maximum per campaign; freeze any further adds after partial; remaining position follows original Turtle full exits.',
 '- One order event per hour; min notional and min quantity enforced on both partial and remainder. Pro-rata entry fees allocated.',
 '- Fee 0.05% side, slippage 0.02% adverse fill. Funding, intrabar fills, liquidation and exchange reconciliation absent.',
 '- Original V3.1 live Pine not modified; no candidate is automatically approved for deployment.','',
 '## Baseline invariance','',pd.read_csv(OUT/'baseline_invariance.csv').to_markdown(index=False),'',
 '## Aggregate','',agg.to_markdown(index=False),'',
 '## Per coin','',comp[['symbol','variant','final_equity','cagr_pct','mdd_pct','calmar','pf','closed_trades','partial_exit_events','delta_cagr_pct','delta_mdd_pct','delta_calmar']].to_markdown(index=False),'',
 '## Shifted start sensitivity (NOT OOS)','',pd.read_csv(OUT/'shifted_starts.csv')[['window_start','symbol','variant','return_pct','mdd_pct','closed_trades','partial_exit_events']].to_markdown(index=False),'',
 '## Fee/slippage stress','',pd.read_csv(OUT/'cost_stress.csv')[['cost_factor','symbol','variant','return_pct','mdd_pct','closed_trades','partial_exit_events']].to_markdown(index=False)]
 (OUT/'STAGE4_REPORT.md').write_text('\n'.join(report),encoding='utf-8')
 print('AGGREGATE_JSON',agg.to_json(orient='records'),flush=True)
 print('PER_SYMBOL_JSON',comp[['symbol','variant','final_equity','cagr_pct','mdd_pct','calmar','pf','closed_trades','partial_exit_events','delta_cagr_pct','delta_mdd_pct']].to_json(orient='records'),flush=True)
 print('STAGE4_FINISHED',flush=True)
if __name__=='__main__':main()
