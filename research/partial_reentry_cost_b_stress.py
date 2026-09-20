"""Stress selected neighboring 40pct fractional variants under 1x/1.5x/2x costs.
This is in-sample sensitivity, not independent validation or live trading.
"""
from pathlib import Path
import json
import pandas as pd
from research import pullback_stage1 as eng
from research import partial_reentry_followup as f
OUT=Path('results_partial_reentry_cost_b');OUT.mkdir(exist_ok=True)

def main():
 raw=eng.load_data()
 lines=[];details=[]
 for mult in (1.,1.5,2.):
  for variant in ('BASE','XRP_SOL_A','XRP_SOL_B','XRP_A_SOL_C','XRP_B_SOL_D'):
   m,_,_,d,tr,_=f.run_one(raw,variant,f.CASES[variant],mult)
   lines.append(m)
   details.append(d.assign(variant=variant,cost_factor=mult))
   print('COST',mult,variant,'final',round(m['final'],3),'MDD',round(m['mdd_pct'],3),flush=True)
 a=pd.DataFrame(lines);a.to_csv(OUT/'cost_stress_comparison.csv',index=False)
 pd.concat(details,ignore_index=True).to_csv(OUT/'per_coin_cost_stress.csv',index=False)
 for mult,g in a.groupby('cost_factor'):
  base=g[g.variant=='BASE'].iloc[0]
  assert len(g)==5
  for row in g.itertuples():
   print('DELTA',mult,row.variant,round(row.final-base.final,3),round(row.mdd_pct-base.mdd_pct,3),flush=True)
 (OUT/'methodology.json').write_text(json.dumps(dict(
  source='same original four coin research simulation and Binance 1h archive',
  study='cost sensitivity, hindsight-selected coins and parameters; NOT out of sample',
  original_live_unchanged=True,baseline_verified=True,
  fee_side=[.0005,.00075,.001],slippage_side=[.0002,.0003,.0004]),indent=2))
if __name__=='__main__': main()
