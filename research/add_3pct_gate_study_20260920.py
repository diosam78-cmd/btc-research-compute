"""Research-only: postpone an otherwise valid ADD until pre-fill cycle ROI >= 3%.
Original live Pine, webhooks, baseline orders and exit rules are untouched.
Time is Binance 1h OPEN UTC, execution occurs at close of the labelled bar.
"""
import inspect
import json
import math
import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
from research import pullback_stage1 as eng

OUT = Path('results_add_3pct_gate')
OUT.mkdir(exist_ok=True)
THRESHOLD_PCT = 3.0
EXPECTED = {'BTCUSDT': (6122.754677747175, 26), 'ETHUSDT': (4597.402574464877, 31),
            'XRPUSDT': (4620.381764378673, 19), 'SOLUSDT': (11166.14038909758, 12)}
VARIANTS = {'BTCUSDT': dict(entry_pb=None, add_pb=None),
            'ETHUSDT': dict(entry_pb=None, add_pb=None),
            'XRPUSDT': dict(entry_pb=None, add_pb=.15),
            'SOLUSDT': dict(entry_pb=None, add_pb=.20)}

src = textwrap.dedent(inspect.getsource(eng.backtest))
def once(a, b):
    global src
    assert src.count(a) == 1, ('anchor mismatch', repr(a[:100]), src.count(a))
    src = src.replace(a, b)

once('def backtest(symbol, x, variant_name, variant):',
     'def backtest(symbol, x, variant_name, variant, gate_pct=None):')
once('    trades = []\n    equity_rows = []\n', '''    trades = []
    equity_rows = []
    cycle_start_cash = None
    pending_add = False
    deferred_count = pending_filled = pending_canceled = 0
    deferred_events, add_events, cycle_events = [], [], []
''')
once('        px = float(bar.c)\n        event = False\n', '''        px = float(bar.c)
        pre_side, pre_units, pre_qty, pre_avg, pre_cash = side, units, qty, avg, cash
        pre_cycle_start, pre_pending = cycle_start_cash, pending_add
        pending_filled_this_bar = False
        event = False
''')

# Only replace the ADD section. The ENTRY section and baseline exit logic remain untouched.
a = src.index('        # 2) ADD or ADD-ARM / pullback execution.')
b = src.index('        # 3) ENTRY or ENTRY-ARM / pullback execution.')
add_section = src[a:b]
assert add_section.count('if side and not event and units < p[\'maxu\']:') == 1
add_section = add_section.replace("if side and not event and units < p['maxu']:",
                                  "if side and not event and units < p['maxu'] and not pending_add:")
valid = 'if valid_qty(addq, fp, p) and addq * fp <= remaining and eq > 0:'
assert add_section.count(valid) == 3, add_section.count(valid)
add_section = add_section.replace(valid,
    'if valid_qty(addq, fp, p) and addq * fp <= remaining and eq > 0 and '
    '(gate_pct is None or 100.0 * (eq / cycle_start_cash - 1.0) >= gate_pct):')
pat = re.compile(r'(?m)^([ ]*)else:\n\1    rejected \+= 1$')
def defer_else(m):
    ind = m.group(1)
    return (f'{ind}else:\n'
            f'{ind}    if (gate_pct is not None and valid_qty(addq, fp, p) '
            f'and addq * fp <= remaining and eq > 0 '
            f'and 100.0 * (eq / cycle_start_cash - 1.0) < gate_pct):\n'
            f'{ind}        pending_add = True\n'
            f'{ind}        deferred_count += 1\n'
            f'{ind}        deferred_events.append(dict(symbol=symbol, time=str(t), '
            f'units_before=units, pre_roi_pct=100.0*(eq/cycle_start_cash-1.0), '
            f'price=px, add_style="PULLBACK" if pb is not None else "INSTANT"))\n'
            f'{ind}    else:\n'
            f'{ind}        rejected += 1')
add_section, n = pat.subn(defer_else, add_section)
assert n == 3, ('failed deferral anchors', n)

# A queued order takes priority over any FRESH ADD setup, but EXIT always wins.
# Recalculate equity and 3x cap at the current confirmed close. A queued order
# is NOT executed in advance, at the old trigger price, or after an exit.
pending_logic = '''        # 2a) Previously valid ADD postponed solely by insufficient pre-fill ROI.
        if side and not event and pending_add and gate_pct is not None and units < p['maxu']:
            eq_before_pending = cash + side * qty * (px - avg)
            roi_before_pending = 100.0 * (eq_before_pending / cycle_start_cash - 1.0)
            if roi_before_pending >= gate_pct:
                remaining = max(0.0, (eq_before_pending * CAP - qty * px) / (1.0 + CAP * FEE))
                fp = fill_price(px, side)
                addq = unit
                if valid_qty(addq, fp, p) and addq * fp <= remaining and eq_before_pending > 0:
                    fee = addq * fp * FEE
                    cash -= fee; fees_in_pos += fee
                    avg = (avg * qty + fp * addq) / (qty + addq)
                    qty += addq; last_fill = fp; units += 1
                    stop = last_fill - side * p['stop'] * entry_n
                    add_exec += 1
                    pending_filled += 1
                    pending_filled_this_bar = True
                    pending_add = False
                    add_arm = None
                    event = True
                else:
                    rejected += 1

'''
src = src[:a] + pending_logic + add_section + src[b:]

# Observe original strategy orders AFTER decisions; do not change account state.
once('        eq = cash + (side * qty * (px - avg) if side else 0.0)\n', '''        if pre_side == 0 and side != 0:
            assert units == 1 and cycle_start_cash is None
            cycle_start_cash = pre_cash
        if pre_side != 0 and side != 0 and units > pre_units:
            assert units == pre_units + 1
            pre_eq = pre_cash + pre_side * pre_qty * (px - pre_avg)
            roi = 100.0 * (pre_eq / pre_cycle_start - 1.0)
            if gate_pct is not None:
                assert roi + 1e-10 >= gate_pct, (symbol, t, roi)
            add_events.append(dict(symbol=symbol, variant=variant_name, time=str(t),
                 entry_time=str(entry_time), units_before=pre_units, units_after=units,
                 pre_roi_pct=roi, pre_equity=pre_eq, first_wallet=pre_cycle_start,
                 delayed=pending_filled_this_bar, price=px, fill=last_fill, add_qty=unit))
        if pre_side != 0 and side == 0:
            closed = trades[-1]
            assert pre_cycle_start is not None
            assert abs(cash - pre_cycle_start - closed['net_pnl']) < 1e-5
            cycle_events.append(dict(**closed, starting_cash=pre_cycle_start,
                  final_return_pct=100.0 * closed['net_pnl'] / pre_cycle_start,
                  exit_units=pre_units, had_pending_at_exit=bool(pre_pending)))
            if pending_add:
                pending_canceled += 1
            pending_add = False
            cycle_start_cash = None
        eq = cash + (side * qty * (px - avg) if side else 0.0)
''')
once('    return summary, frame.equity.rename(symbol), pd.DataFrame(trades)', '''    summary['gate_pct'] = gate_pct
    summary['deferred_orders'] = deferred_count
    summary['deferred_filled'] = pending_filled
    summary['deferred_canceled_by_exit'] = pending_canceled
    summary['pending_at_period_end'] = int(pending_add)
    assert deferred_count == pending_filled + pending_canceled + int(pending_add), (
        symbol, deferred_count, pending_filled, pending_canceled, pending_add)
    return (summary, frame.equity.rename(symbol), pd.DataFrame(trades),
            pd.DataFrame(cycle_events), pd.DataFrame(add_events), pd.DataFrame(deferred_events))''')

# Original function namespace retains exact source indicator, data, fee, order-size functions.
ns = dict(vars(eng))
exec(compile(src, '<original_engine_plus_isolated_3pct_add_gate>', 'exec'), ns)
replay = ns['backtest']


def run():
    data = eng.load_data()
    summaries, cycles, adds, deferred = [], [], [], []
    curves = {}
    for name, gate in [('BASE', None), ('WAIT_3PCT', THRESHOLD_PCT)]:
        curves[name] = {}
        for symbol in EXPECTED:
            s, equity, trade, cyc, add, wait = replay(symbol, data[symbol], name, VARIANTS[symbol], gate)
            if name == 'BASE':
                expected_final, expected_n = EXPECTED[symbol]
                assert abs(s['final_equity'] - expected_final) < .01, (symbol, s['final_equity'], expected_final)
                assert len(trade) == expected_n, (symbol, len(trade), expected_n)
                assert s['deferred_orders'] == 0 and s['deferred_filled'] == 0
            assert len(cyc) == len(trade)
            assert all(cyc['exit_units'] <= eng.PARAMS[symbol[:-4]]['maxu'])
            print('CHECK', name, symbol, 'final', round(s['final_equity'], 4),
                  'trade', len(trade), 'wins', s['wins'], 'mdd', round(s['mdd_pct'], 3),
                  'deferred', s['deferred_orders'], 'filled', s['deferred_filled'],
                  'canceled', s['deferred_canceled_by_exit'], flush=True)
            summaries.append(s)
            curves[name][symbol] = equity
            cycles.append(cyc)
            adds.append(add)
            deferred.append(wait)
    s = pd.DataFrame(summaries)
    s.to_csv(OUT / 'per_coin_comparison.csv', index=False)
    pd.concat(cycles, ignore_index=True).to_csv(OUT / 'all_closed_cycles.csv', index=False)
    pd.concat(adds, ignore_index=True).to_csv(OUT / 'executed_add_orders.csv', index=False)
    pd.concat([x for x in deferred if not x.empty], ignore_index=True).to_csv(OUT / 'deferred_add_orders.csv', index=False)
    aggregate = []
    for name in curves:
        equity = pd.concat(curves[name].values(), axis=1)
        assert len(equity) == len(next(iter(curves[name].values()))) and not equity.isna().any().any()
        eq = equity.sum(axis=1)
        eq.rename('four_wallet_total').to_csv(OUT / f'equity_{name}.csv')
        peak = eq.cummax()
        dd = ((peak - eq) / peak * 100)
        yrs = (eq.index[-1] - eq.index[0]).total_seconds() / 31557600
        sub = s[s.variant == name]
        complete = pd.concat([x for x in cycles if not x.empty and x.variant.iloc[0] == name])
        aggregate.append(dict(variant=name, initial=4000.0, final=float(eq.iloc[-1]),
                 profit=float(eq.iloc[-1]-4000), cagr_pct=100*((float(eq.iloc[-1])/4000)**(1/yrs)-1),
                 mdd_pct=float(dd.max()), mdd_time=str(dd.idxmax()),
                 closed_trades=int(sub.closed_trades.sum()), wins=int(sub.wins.sum()),
                 losses=int(sub.losses.sum()), total_add_exec=int(sub.add_exec.sum()),
                 deferred_orders=int(sub.deferred_orders.sum()),
                 deferred_filled=int(sub.deferred_filled.sum()),
                 deferred_canceled=int(sub.deferred_canceled_by_exit.sum()),
                 pending_at_end=int(sub.pending_at_period_end.sum()),
                 winner_gross=float(complete.loc[complete.net_pnl>0, 'net_pnl'].sum()),
                 loser_gross=float(complete.loc[complete.net_pnl<0, 'net_pnl'].sum()),
                 worst_cycle_return_pct=float(complete.final_return_pct.min())))
    a = pd.DataFrame(aggregate)
    a.to_csv(OUT / 'aggregate_comparison.csv', index=False)
    with open(OUT / 'methodology.json', 'w') as f:
        json.dump({'study': '3pct pre-trade cycle-wallet ROI gate on executed ADD only',
                   'decision': 'EXITS > queued ADD > original ADD/ARM > ENTRY; max 1 order per confirmed hour',
                   'start_utc_inclusive': str(eng.START), 'end_utc_exclusive': str(eng.END),
                   'bar_timestamps': 'hour OPEN UTC; order at hour CLOSE',
                   'baseline_per_coin_end_equity_and_trades_reproduced': True,
                   'start_wallet_each': 1000, 'gate_roi_pct': THRESHOLD_PCT,
                   'pre_add_roi': '100*(cash+side*held_qty*(current_hour_close-avg_entry))/cycle_entry_start_cash-100',
                   'pending': 'one originally admissible ADD deferred solely by ROI below 3, full original unit size, next hour close with ROI >=3, recomputed 3x cap; canceled on exit',
                   'fee_per_fill': eng.FEE, 'adverse_slippage_per_fill': eng.SLIP,
                   'no_funding_liquidation_intrabar_real_fills': True,
                   'live_script_and_webhooks_untouched': True}, f, indent=2)
    print('AGGREGATE', a.to_json(orient='records'), flush=True)
    print('DONE', OUT, flush=True)

if __name__ == '__main__':
    run()
