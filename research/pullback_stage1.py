import io
import json
import math
import time
import zipfile
import collections
import concurrent.futures
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Canonical 4-coin independent-wallet baseline as of 2026-09-17.
PARAMS = {
    'BTC': dict(entry=40, exit=7, nlen=14, nmax=4.0, stop=1.5, add=0.5, maxu=6, risk=0.01, step=0.001, minq=0.001),
    'ETH': dict(entry=20, exit=10, nlen=10, nmax=9.0, stop=2.25, add=0.25, maxu=5, risk=0.01, step=0.001, minq=0.001),
    'XRP': dict(entry=30, exit=10, nlen=14, nmax=5.0, stop=2.25, add=0.5, maxu=3, risk=0.01, step=1.0, minq=1.0),
    'SOL': dict(entry=90, exit=7, nlen=20, nmax=6.0, stop=2.5, add=0.5, maxu=5, risk=0.015, step=0.01, minq=0.01),
}

# Stage 1 intentionally changes only pullback timing. No partial entries yet.
# entry_pb/add_pb = retracement from favorable post-trigger CLOSE extreme, measured in frozen N.
VARIANTS = {
    'BASE':      dict(entry_pb=None, add_pb=None),
    'E25':       dict(entry_pb=0.25, add_pb=None),
    'E50':       dict(entry_pb=0.50, add_pb=None),
    'A25':       dict(entry_pb=None, add_pb=0.25),
    'A50':       dict(entry_pb=None, add_pb=0.50),
    'EA25':      dict(entry_pb=0.25, add_pb=0.25),
    'EA50':      dict(entry_pb=0.50, add_pb=0.50),
    'E25_A50':   dict(entry_pb=0.25, add_pb=0.50),
    'E50_A25':   dict(entry_pb=0.50, add_pb=0.25),
}

START = pd.Timestamp('2023-09-16 00:00:00', tz='UTC')
END = pd.Timestamp('2026-09-16 00:00:00', tz='UTC')
FEE = 0.0005
SLIP = 0.0002
CAP = 3.0
LEVERAGE = 4.0
MIN_NOTIONAL = 50.0
START_CASH = 1000.0
SYMBOLS = [c + 'USDT' for c in PARAMS]
OUT = Path('results_pullback_stage1')
OUT.mkdir(exist_ok=True)


def fetch_archive(task):
    symbol, typ, day = task
    url = f'https://data.binance.vision/data/futures/um/{typ}/klines/{symbol}/1h/{symbol}-1h-{day}.zip'
    err = ''
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                return task, None, 'NOT_LISTED'
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            fn = next(x for x in z.namelist() if x.endswith('.csv'))
            df = pd.read_csv(z.open(fn), header=None, usecols=[0, 1, 2, 3, 4], dtype=str)
            df.columns = ['t', 'o', 'h', 'l', 'c']
            df = df[pd.to_numeric(df.t, errors='coerce').notna()].copy()
            for col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            # Binance Vision may use microseconds in newer archives; normalize.
            df.t = df.t.apply(lambda x: x / 1000 if x > 1e15 else x)
            df.t = pd.to_datetime(df.t, unit='ms', utc=True)
            return task, df.dropna().set_index('t'), ''
        except Exception as exc:
            err = str(exc)
            time.sleep(1 + attempt)
    return task, None, err


def load_data():
    tasks = []
    for symbol in SYMBOLS:
        y, m = 2023, 1
        while (y, m) <= (2026, 8):
            tasks.append((symbol, 'monthly', f'{y:04d}-{m:02d}'))
            m += 1
            if m == 13:
                y, m = y + 1, 1
        for d in range(1, 17):
            tasks.append((symbol, 'daily', f'2026-09-{d:02d}'))

    pieces = collections.defaultdict(list)
    errors = collections.defaultdict(list)
    print(f'Downloading {len(tasks)} Binance archives', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for task, df, status in ex.map(fetch_archive, tasks):
            sym = task[0]
            if df is not None:
                pieces[sym].append(df)
            elif status != 'NOT_LISTED':
                errors[sym].append(task[2] + ':' + status)

    out = {}
    for symbol in SYMBOLS:
        if errors[symbol]:
            raise RuntimeError(f'{symbol} archive errors: {errors[symbol][:5]}')
        if not pieces[symbol]:
            raise RuntimeError(f'{symbol}: no data')
        x = pd.concat(pieces[symbol]).sort_index()
        x = x[~x.index.duplicated(keep='last')]
        # Warmup from Jan-2023, actual test begins START.
        x = x[(x.index >= pd.Timestamp('2023-01-01', tz='UTC')) & (x.index < END)]
        gaps = int((x.index.to_series().diff() > pd.Timedelta(hours=1)).sum())
        if gaps:
            raise RuntimeError(f'{symbol}: internal missing-hour gaps={gaps}')
        out[symbol] = x
    return out


def wilder_rma(s, n):
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if len(s) < n:
        return out
    val = float(s.iloc[:n].mean())
    out.iloc[n - 1] = val
    for i in range(n, len(s)):
        val = (val * (n - 1) + float(s.iloc[i])) / n
        out.iloc[i] = val
    return out


def add_indicators(x, p):
    d = x.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'})
    counts = x.c.resample('1D').count()
    d = d[counts == 24].copy()
    prev = d.c.shift(1)
    tr = pd.concat([d.h - d.l, (d.h - prev).abs(), (d.l - prev).abs()], axis=1).max(axis=1)
    n = wilder_rma(tr, p['nlen'])
    ema = d.c.ewm(span=50, adjust=False).mean()
    ind = pd.DataFrame({
        'eh': d.h.rolling(p['entry']).max().shift(1),
        'el': d.l.rolling(p['entry']).min().shift(1),
        'xh': d.h.rolling(p['exit']).max().shift(1),
        'xl': d.l.rolling(p['exit']).min().shift(1),
        'n': n.shift(1),
        'prev': prev,
        'ema': ema.shift(1),
        'old': ema.shift(11),
    })
    ind['npct'] = 100 * ind.n / ind.prev
    ind['shortOK'] = (ind.prev < ind.ema) & (ind.ema < ind.old)
    return x.join(ind, on=x.index.floor('D'))


def floor_step(q, step):
    return math.floor(q / step + 1e-10) * step


def fill_price(px, order_side):
    return px * (1.0 + order_side * SLIP)


def valid_qty(q, px, p):
    return q >= p['minq'] and q * px >= MIN_NOTIONAL


def backtest(symbol, x, variant_name, variant):
    coin = symbol[:-4]
    p = PARAMS[coin]
    x = add_indicators(x, p)

    cash = START_CASH
    peak = START_CASH
    side = 0
    qty = 0.0
    avg = np.nan
    unit = 0.0
    entry_n = np.nan
    last_fill = np.nan
    stop = np.nan
    units = 0
    fees_in_pos = 0.0
    entry_time = None

    # Pullback state.
    entry_arm = None  # dict(dir,n,level,extreme,time)
    add_arm = None    # dict(extreme,ref_fill,time)

    trades = []
    equity_rows = []
    entry_arms = entry_exec = entry_cancel = 0
    add_arms = add_exec = add_cancel = 0
    entry_pb_realized = []
    add_pb_realized = []
    rejected = 0
    max_gross = max_gross_lev = max_margin_pct = 0.0
    max_dd = 0.0
    max_dd_time = None

    for bar in x.itertuples():
        t = bar.Index
        if t < START:
            continue
        px = float(bar.c)
        event = False

        # 1) EXIT always has highest priority.
        if side:
            stop_hit = px <= stop if side > 0 else px >= stop
            channel_hit = px <= bar.xl if side > 0 else px >= bar.xh
            if stop_hit or channel_hit:
                fp = fill_price(px, -side)
                exit_fee = qty * fp * FEE
                realized = side * qty * (fp - avg)
                net = realized - exit_fee - fees_in_pos
                cash += realized - exit_fee
                trades.append({
                    'symbol': symbol, 'variant': variant_name, 'entry_time': str(entry_time), 'exit_time': str(t),
                    'side': side, 'units': units, 'qty': qty, 'avg_entry': avg, 'exit_price': fp,
                    'reason': 'STOP' if stop_hit else 'CHANNEL', 'net_pnl': net,
                })
                side = 0; qty = 0.0; avg = np.nan; unit = 0.0; entry_n = np.nan
                last_fill = np.nan; stop = np.nan; units = 0; fees_in_pos = 0.0; entry_time = None
                entry_arm = None; add_arm = None
                event = True

        # 2) ADD or ADD-ARM / pullback execution.
        if side and not event and units < p['maxu']:
            pb = variant['add_pb']
            if pb is None:
                trigger = last_fill + side * p['add'] * entry_n
                hit = px >= trigger if side > 0 else px <= trigger
                if hit:
                    eq = cash + side * qty * (px - avg)
                    remaining = max(0.0, (eq * CAP - qty * px) / (1.0 + CAP * FEE))
                    fp = fill_price(px, side)
                    addq = unit
                    if valid_qty(addq, fp, p) and addq * fp <= remaining and eq > 0:
                        fee = addq * fp * FEE
                        cash -= fee; fees_in_pos += fee
                        avg = (avg * qty + fp * addq) / (qty + addq)
                        qty += addq; last_fill = fp; units += 1
                        stop = last_fill - side * p['stop'] * entry_n
                        add_exec += 1; event = True
                    else:
                        rejected += 1
            else:
                # Manage existing add arm first.
                if add_arm is not None:
                    if side > 0:
                        if px <= add_arm['ref_fill']:
                            add_arm = None; add_cancel += 1
                        else:
                            add_arm['extreme'] = max(add_arm['extreme'], px)
                            if px <= add_arm['extreme'] - pb * entry_n:
                                eq = cash + side * qty * (px - avg)
                                remaining = max(0.0, (eq * CAP - qty * px) / (1.0 + CAP * FEE))
                                fp = fill_price(px, side); addq = unit
                                if valid_qty(addq, fp, p) and addq * fp <= remaining and eq > 0:
                                    add_pb_realized.append((add_arm['extreme'] - px) / entry_n)
                                    fee = addq * fp * FEE; cash -= fee; fees_in_pos += fee
                                    avg = (avg * qty + fp * addq) / (qty + addq)
                                    qty += addq; last_fill = fp; units += 1
                                    stop = last_fill - side * p['stop'] * entry_n
                                    add_exec += 1; add_arm = None; event = True
                                else:
                                    rejected += 1
                    else:
                        if px >= add_arm['ref_fill']:
                            add_arm = None; add_cancel += 1
                        else:
                            add_arm['extreme'] = min(add_arm['extreme'], px)
                            if px >= add_arm['extreme'] + pb * entry_n:
                                eq = cash + side * qty * (px - avg)
                                remaining = max(0.0, (eq * CAP - qty * px) / (1.0 + CAP * FEE))
                                fp = fill_price(px, side); addq = unit
                                if valid_qty(addq, fp, p) and addq * fp <= remaining and eq > 0:
                                    add_pb_realized.append((px - add_arm['extreme']) / entry_n)
                                    fee = addq * fp * FEE; cash -= fee; fees_in_pos += fee
                                    avg = (avg * qty + fp * addq) / (qty + addq)
                                    qty += addq; last_fill = fp; units += 1
                                    stop = last_fill - side * p['stop'] * entry_n
                                    add_exec += 1; add_arm = None; event = True
                                else:
                                    rejected += 1
                # If not armed and no order this bar, arm when normal add trigger is crossed.
                if not event and add_arm is None:
                    trigger = last_fill + side * p['add'] * entry_n
                    hit = px >= trigger if side > 0 else px <= trigger
                    if hit:
                        add_arm = {'extreme': px, 'ref_fill': last_fill, 'time': t}
                        add_arms += 1

        # 3) ENTRY or ENTRY-ARM / pullback execution.
        if side == 0 and not event:
            pb = variant['entry_pb']
            # Manage previously armed setup.
            if pb is not None and entry_arm is not None:
                d = entry_arm['dir']; armn = entry_arm['n']; level = entry_arm['level']
                if d > 0:
                    if px <= level:
                        entry_arm = None; entry_cancel += 1
                    else:
                        entry_arm['extreme'] = max(entry_arm['extreme'], px)
                        if px <= entry_arm['extreme'] - pb * armn:
                            fp = fill_price(px, d)
                            riskq = cash * p['risk'] / armn
                            maxq = max(0.0, cash * CAP / (fp * (1.0 + CAP * FEE)))
                            q = floor_step(min(riskq, maxq), p['step'])
                            if valid_qty(q, fp, p) and cash > 0:
                                entry_pb_realized.append((entry_arm['extreme'] - px) / armn)
                                fee = q * fp * FEE; cash -= fee; fees_in_pos = fee
                                side = d; qty = q; avg = fp; unit = q; entry_n = armn; last_fill = fp
                                stop = fp - d * p['stop'] * armn; units = 1; entry_time = t
                                entry_exec += 1; entry_arm = None; event = True
                            else:
                                rejected += 1
                else:
                    if px >= level:
                        entry_arm = None; entry_cancel += 1
                    else:
                        entry_arm['extreme'] = min(entry_arm['extreme'], px)
                        if px >= entry_arm['extreme'] + pb * armn:
                            fp = fill_price(px, d)
                            riskq = cash * p['risk'] / armn
                            maxq = max(0.0, cash * CAP / (fp * (1.0 + CAP * FEE)))
                            q = floor_step(min(riskq, maxq), p['step'])
                            if valid_qty(q, fp, p) and cash > 0:
                                entry_pb_realized.append((px - entry_arm['extreme']) / armn)
                                fee = q * fp * FEE; cash -= fee; fees_in_pos = fee
                                side = d; qty = q; avg = fp; unit = q; entry_n = armn; last_fill = fp
                                stop = fp - d * p['stop'] * armn; units = 1; entry_time = t
                                entry_exec += 1; entry_arm = None; event = True
                            else:
                                rejected += 1

            # If still flat, detect a fresh breakout signal.
            if side == 0 and not event and entry_arm is None and pd.notna(bar.n) and bar.n > 0 and pd.notna(bar.eh) and pd.notna(bar.el) and pd.notna(bar.npct) and bar.npct <= p['nmax']:
                long_hit = px > bar.eh
                short_hit = px < bar.el and bool(bar.shortOK)
                if long_hit != short_hit:
                    d = 1 if long_hit else -1
                    if pb is None:
                        fp = fill_price(px, d)
                        riskq = cash * p['risk'] / bar.n
                        maxq = max(0.0, cash * CAP / (fp * (1.0 + CAP * FEE)))
                        q = floor_step(min(riskq, maxq), p['step'])
                        if valid_qty(q, fp, p) and cash > 0:
                            fee = q * fp * FEE; cash -= fee; fees_in_pos = fee
                            side = d; qty = q; avg = fp; unit = q; entry_n = bar.n; last_fill = fp
                            stop = fp - d * p['stop'] * bar.n; units = 1; entry_time = t
                            entry_exec += 1; event = True
                        else:
                            rejected += 1
                    else:
                        entry_arm = {
                            'dir': d, 'n': float(bar.n), 'level': float(bar.eh if d > 0 else bar.el),
                            'extreme': px, 'time': t,
                        }
                        entry_arms += 1

        eq = cash + (side * qty * (px - avg) if side else 0.0)
        gross = qty * px
        grosslev = gross / eq if eq > 0 else np.inf
        marginpct = 100 * (gross / LEVERAGE) / eq if eq > 0 else np.inf
        peak = max(peak, eq)
        dd = 100 * (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd; max_dd_time = t
        max_gross = max(max_gross, gross)
        max_gross_lev = max(max_gross_lev, grosslev)
        max_margin_pct = max(max_margin_pct, marginpct)
        equity_rows.append((t, eq))

    frame = pd.DataFrame(equity_rows, columns=['time', 'equity']).set_index('time')
    final = float(frame.equity.iloc[-1])
    years = (frame.index[-1] - frame.index[0]).total_seconds() / 31557600.0
    pnl = [r['net_pnl'] for r in trades]
    wins = [v for v in pnl if v > 0]; losses = [v for v in pnl if v < 0]
    pf = sum(wins) / -sum(losses) if losses else np.nan
    cagr = 100 * ((final / START_CASH) ** (1 / years) - 1) if final > 0 else np.nan
    calmar = cagr / max_dd if max_dd > 0 else np.nan

    years_ret = {}
    for year, grp in frame.groupby(frame.index.year):
        first = START_CASH if year == frame.index[0].year else float(frame.loc[frame.index < grp.index[0], 'equity'].iloc[-1])
        last = float(grp.equity.iloc[-1])
        years_ret[str(year)] = 100 * (last / first - 1)

    summary = {
        'symbol': symbol, 'variant': variant_name, 'entry_pb_n': variant['entry_pb'], 'add_pb_n': variant['add_pb'],
        'start_equity': START_CASH, 'final_equity': final, 'return_pct': 100 * (final / START_CASH - 1),
        'cagr_pct': cagr, 'mdd_pct': max_dd, 'calmar': calmar, 'closed_trades': len(trades),
        'wins': len(wins), 'losses': len(losses), 'win_pct': 100 * len(wins) / len(trades) if trades else 0.0,
        'pf': pf, 'max_gross': max_gross, 'max_gross_leverage': max_gross_lev,
        'max_initial_margin_pct': max_margin_pct, 'rejected_orders': rejected,
        'entry_arms': entry_arms, 'entry_exec': entry_exec, 'entry_cancel': entry_cancel,
        'add_arms': add_arms, 'add_exec': add_exec, 'add_cancel': add_cancel,
        'avg_entry_pullback_n': float(np.mean(entry_pb_realized)) if entry_pb_realized else np.nan,
        'avg_add_pullback_n': float(np.mean(add_pb_realized)) if add_pb_realized else np.nan,
        'year_returns_json': json.dumps(years_ret, sort_keys=True),
    }
    return summary, frame.equity.rename(symbol), pd.DataFrame(trades)


def main():
    raw = load_data()
    summaries = []
    paths = collections.defaultdict(dict)
    all_trades = []

    for variant_name, variant in VARIANTS.items():
        print(f'=== VARIANT {variant_name}: {variant} ===', flush=True)
        for symbol in SYMBOLS:
            summary, eq, trades = backtest(symbol, raw[symbol], variant_name, variant)
            summaries.append(summary)
            paths[variant_name][symbol] = eq
            if not trades.empty:
                all_trades.append(trades)
            print('RESULT', json.dumps(summary, default=str), flush=True)

    s = pd.DataFrame(summaries)
    base = s[s.variant == 'BASE'].set_index('symbol')
    comp = s.copy()
    comp['baseline_final'] = comp.symbol.map(base.final_equity)
    comp['baseline_cagr'] = comp.symbol.map(base.cagr_pct)
    comp['baseline_mdd'] = comp.symbol.map(base.mdd_pct)
    comp['baseline_calmar'] = comp.symbol.map(base.calmar)
    comp['delta_final_pct_vs_base'] = 100 * (comp.final_equity / comp.baseline_final - 1)
    comp['delta_cagr_pp'] = comp.cagr_pct - comp.baseline_cagr
    comp['delta_mdd_pp'] = comp.mdd_pct - comp.baseline_mdd
    comp['delta_calmar'] = comp.calmar - comp.baseline_calmar
    comp.to_csv(OUT / 'per_symbol_comparison.csv', index=False)
    s.to_csv(OUT / 'summary.csv', index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(OUT / 'trades.csv', index=False)

    agg_rows = []
    for variant_name in VARIANTS:
        eq = pd.concat(paths[variant_name].values(), axis=1).dropna().sum(axis=1)
        peak = eq.cummax(); dd = 100 * (peak - eq) / peak
        final = float(eq.iloc[-1]); mdd = float(dd.max())
        years = (eq.index[-1] - eq.index[0]).total_seconds() / 31557600.0
        cagr = 100 * ((final / 4000.0) ** (1 / years) - 1)
        calmar = cagr / mdd if mdd > 0 else np.nan
        rows = comp[comp.variant == variant_name]
        agg_rows.append({
            'variant': variant_name, 'final_equity_sum': final, 'return_pct': 100 * (final / 4000 - 1),
            'cagr_pct': cagr, 'aggregate_mdd_pct': mdd, 'calmar': calmar,
            'coins_cagr_better_than_base': int((rows.delta_cagr_pp > 0).sum()),
            'coins_mdd_lower_than_base': int((rows.delta_mdd_pp < 0).sum()),
            'coins_calmar_better_than_base': int((rows.delta_calmar > 0).sum()),
            'median_delta_cagr_pp': float(rows.delta_cagr_pp.median()),
            'median_delta_mdd_pp': float(rows.delta_mdd_pp.median()),
            'median_delta_calmar': float(rows.delta_calmar.median()),
            'entry_arms': int(rows.entry_arms.sum()), 'entry_cancel': int(rows.entry_cancel.sum()),
            'add_arms': int(rows.add_arms.sum()), 'add_cancel': int(rows.add_cancel.sum()),
        })
        pd.DataFrame({'time': eq.index, 'equity': eq.values}).to_csv(OUT / f'aggregate_{variant_name}.csv', index=False)

    agg = pd.DataFrame(agg_rows)
    baseagg = agg[agg.variant == 'BASE'].iloc[0]
    agg['delta_final_pct_vs_base'] = 100 * (agg.final_equity_sum / baseagg.final_equity_sum - 1)
    agg['delta_cagr_pp'] = agg.cagr_pct - baseagg.cagr_pct
    agg['delta_mdd_pp'] = agg.aggregate_mdd_pct - baseagg.aggregate_mdd_pct
    agg['delta_calmar'] = agg.calmar - baseagg.calmar
    agg.to_csv(OUT / 'aggregate_comparison.csv', index=False)

    # Compact machine-readable report in logs.
    report = {
        'period': {'start': str(START), 'end_exclusive': str(END)},
        'method': '1H close-only, previous completed daily Donchian/N, market shadow fills with 0.02% adverse slippage and 0.05%/side fee; funding/liquidation/intrabar excluded.',
        'pullback_rule': 'Arm on normal breakout/add trigger. Track favorable CLOSE extreme. Execute full unit only after specified N retracement. Entry setup cancels if close returns through frozen breakout level. Add setup cancels if close returns through prior fill. No timeout. No partial fills in stage 1.',
        'aggregate': agg.to_dict(orient='records'),
    }
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print('AGGREGATE_REPORT', json.dumps(report, default=str), flush=True)


if __name__ == '__main__':
    main()
