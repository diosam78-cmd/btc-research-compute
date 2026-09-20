"""Observation-only export of complete winning cycles; preserves original trading engine.
Data/variants must match previously validated 88-cycle four-coin loss research.
"""
from pathlib import Path
import pandas as pd
from research import four_coin_loss_cycles as prior

OUT=Path('results_four_coin_profit_excursions')
OUT.mkdir(exist_ok=True)
old = "                if closed['net_pnl']<0:\n                    cycle_trace.extend(active_trace)\n"
new = "                cycle_trace.extend(active_trace)\n"
assert prior.fn_src.count(old)==1, 'Expected original winning-trace exclusion not found'
src=prior.fn_src.replace(old,new)
ns=dict(vars(prior.eng))
exec(compile(src,'<original_engine_with_all_trade_trace>','exec'),ns)
observed=ns['backtest']

def main():
    raw=prior.eng.load_data()
    winners=[]
    for sym in prior.expected:
        print('EXPORTING',sym,flush=True)
        s, eq, trades, cycles, events, trace = observed(sym,raw[sym],'LIVE_V5211_PARAMETERS',prior.variants[sym])
        exp_final, exp_trades=prior.expected[sym]
        assert abs(s['final_equity']-exp_final)<0.01,(sym,'equity',s['final_equity'],exp_final)
        assert len(trades)==exp_trades and len(cycles)==exp_trades,(sym,'cycle count mismatch')
        w=cycles[cycles.outcome=='WIN']
        lt=trace[trace.cycle_id.isin(w.cycle_id)]
        assert lt.cycle_id.nunique()==len(w),(sym,'missing winning path')
        for c in w.itertuples():
            t=lt[lt.cycle_id==c.cycle_id]
            assert len(t)>0 and t.iloc[-1].stage=='PRE_EXIT_MARK',(sym,c.cycle_id,'missing exit')
            assert abs(t.wallet_return_pct.max()-c.peak_pct)<1e-7,(sym,c.cycle_id,'peak mismatch')
            assert abs(t.wallet_return_pct.min()-c.trough_pct)<1e-7,(sym,c.cycle_id,'trough mismatch')
        winners.append(lt)
        print('VALIDATED',sym,'wins',len(w),'winning_hourly_rows',len(lt),flush=True)
    win_path=pd.concat(winners,ignore_index=True)
    win_path.to_csv(OUT/'winning_cycles_hourly_path.csv',index=False)
    print('COMPLETE',len(win_path),'winning_cycles',win_path[['symbol','cycle_id']].drop_duplicates().shape[0],flush=True)
if __name__=='__main__':
    main()
