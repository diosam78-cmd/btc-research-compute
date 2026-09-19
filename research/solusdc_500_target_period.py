"""Exact-window research: SOLUSDC perpetual, 500 USDC, 2024-01-04 through 2026-09-19 KST.
Reuses audited Stage-1 Turtle engine for both BASE and add pullback=0.20N.
This is the independent synthetic wallet, not TradingView strategy tester or actual Binance P&L.
"""
import collections
import concurrent.futures
import json
import time
from pathlib import Path

import pandas as pd
import requests

from research import pullback_stage1 as engine

SYMBOL = 'SOLUSDC'
# User specified inclusive Korean calendar dates, no time on start date:
START_KST = pd.Timestamp('2024-01-04 00:00:00', tz='Asia/Seoul')
END_KST_EXCLUSIVE = pd.Timestamp('2026-09-20 00:00:00', tz='Asia/Seoul')
START_UTC = START_KST.tz_convert('UTC')
END_UTC = END_KST_EXCLUSIVE.tz_convert('UTC')
OUT = Path('results_solusdc_500_target_period')
OUT.mkdir(exist_ok=True)

engine.SYMBOLS = [SYMBOL]
engine.START_CASH = 500.0
engine.START = START_UTC
# Stage1's bar index is candle OPEN, Pine uses time_close < end; exclude
# candle OPEN 14:00 UTC as its CLOSE equals excluded boundary 15:00 UTC.
engine.END = END_UTC - pd.Timedelta(hours=1)


def get_rest_last_day(day):
    """Fallback when Binance Vision current daily archives are not published yet."""
    begin = int(pd.Timestamp(day, tz='UTC').timestamp() * 1000)
    end = min(begin + 86400_000, int(END_UTC.timestamp() * 1000))
    errs = []
    for host in ['https://fapi.binance.com', 'https://fapi1.binance.com', 'https://fapi2.binance.com']:
        try:
            r = requests.get(host + '/fapi/v1/klines', params={'symbol': SYMBOL, 'interval': '1h', 'startTime': begin, 'endTime': end - 1, 'limit': 1000}, timeout=20)
            r.raise_for_status()
            arr = r.json()
            if isinstance(arr, list) and arr:
                df = pd.DataFrame(arr)
                df = df.iloc[:, :5]
                df.columns = ['t', 'o', 'h', 'l', 'c']
                df['t'] = pd.to_datetime(df.t.astype('int64'), unit='ms', utc=True)
                for k in ['o','h','l','c']:
                    df[k] = pd.to_numeric(df[k])
                return df.set_index('t'), ''
            errs.append(host + ': empty result')
        except Exception as ex:
            errs.append(host + ': ' + str(ex)[:150])
    return None, '; '.join(errs)


def load_target():
    tasks = []
    for date in pd.date_range('2024-01-01', '2026-08-01', freq='MS'):
        tasks.append((SYMBOL, 'monthly', date.strftime('%Y-%m')))
    for day in pd.date_range('2026-09-01','2026-09-19', freq='D'):
        tasks.append((SYMBOL, 'daily', day.strftime('%Y-%m-%d')))
    pieces = []
    problems = []
    not_listed = []
    print('Archive requests:', len(tasks), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as ex:
        for task, frame, status in ex.map(engine.fetch_archive, tasks):
            if frame is not None:
                pieces.append(frame)
            elif status == 'NOT_LISTED':
                not_listed.append(task)
            else:
                problems.append({'archive': task[2], 'error': status})
    for task in not_listed:
        if task[1] == 'daily' and task[2] >= '2026-09-17':
            frame, err = get_rest_last_day(task[2])
            if frame is not None:
                pieces.append(frame)
                print('REST fallback succeeded:', task[2], len(frame), flush=True)
            else:
                problems.append({'archive': task[2], 'error': 'archive not listed and REST fallback unavailable: ' + err})
        else:
            problems.append({'archive': task[2], 'error': 'archive NOT_LISTED'})
    if not pieces:
        raise RuntimeError('No SOLUSDC data available')
    df = pd.concat(pieces).sort_index()
    df = df[~df.index.duplicated(keep='last')]
    df = df[df.index < END_UTC]
    diffs = df.index.to_series().diff()
    gaps = df.index[diffs > pd.Timedelta(hours=1)].strftime('%Y-%m-%dT%H:%M:%SZ').tolist()
    last_required_open = END_UTC - pd.Timedelta(hours=2)
    # Note Pine candle close must be strictly BEFORE END_UTC; last eligible
    # OPEN = END_UTC-2h.
    complete = len(df) > 0 and df.index.max() >= last_required_open and not gaps
    info = {
        'symbol': SYMBOL, 'requested_start_kst': str(START_KST),
        'requested_end_exclusive_kst': str(END_KST_EXCLUSIVE),
        'requested_start_utc': str(START_UTC), 'requested_end_exclusive_utc': str(END_UTC),
        'data_first_open_utc': str(df.index.min()),
        'data_last_open_utc': str(df.index.max()),
        'last_required_open_utc': str(last_required_open),
        'data_rows': len(df), 'gap_count': len(gaps), 'gaps': gaps[:30],
        'archive_errors': problems, 'full_period_data_covered': complete,
        'fee_per_fill_pct': engine.FEE*100, 'slippage_per_fill_pct': engine.SLIP*100,
        'initial_cash': engine.START_CASH, 'pyramiding_add_pullback_n': .2,
        'model_notes': '1H confirmed close; no funding or liquidation; simulated orders only.'
    }
    (OUT/'data_audit.json').write_text(json.dumps(info, indent=2, ensure_ascii=False))
    print('DATA_AUDIT', json.dumps(info, ensure_ascii=False), flush=True)
    if not complete:
        print('DATA INCOMPLETE: computing only covered period, do NOT cite as full target period', flush=True)
        engine.END = min(engine.END, df.index.max() + pd.Timedelta(hours=1))
    return df, complete


def main():
    raw, complete = load_target()
    rows = []
    for variant, pb in [('BASE_INSTANT_ADD', None), ('SOL_A20_FULL_PULLBACK', 0.2)]:
        summary, eq, trades = engine.backtest(SYMBOL, raw, variant, {'entry_pb':None, 'add_pb':pb})
        summary['full_period_data_covered'] = complete
        summary['first_equity_open_utc'] = str(eq.index.min())
        summary['last_equity_open_utc'] = str(eq.index.max())
        rows.append(summary)
        eq.to_csv(OUT/(variant+'_hourly_equity.csv'), header=True)
        trades.to_csv(OUT/(variant+'_completed_trades.csv'), index=False)
        month = eq.groupby(eq.index.tz_convert('Asia/Seoul').to_period('M')).last()
        monthly = month.to_frame('end_equity')
        monthly['start_equity'] = monthly.end_equity.shift(1).fillna(engine.START_CASH)
        monthly['return_pct'] = 100 * (monthly.end_equity / monthly.start_equity - 1)
        monthly.to_csv(OUT/(variant+'_monthly_KST.csv'))
        print('RESULT', json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    pd.DataFrame(rows).to_csv(OUT/'comparison.csv', index=False)
    (OUT/'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    print('FINISHED; complete_requested_data=', complete, flush=True)

if __name__ == '__main__':
    main()
