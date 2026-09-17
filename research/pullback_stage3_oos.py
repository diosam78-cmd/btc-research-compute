import importlib.util
import collections
import concurrent.futures
import json
from pathlib import Path

import numpy as np
import pandas as pd

CORE_PATH = Path('research/pullback_stage1_core.py')
OUT = Path('results_pullback_stage3_oos')
OUT.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location('core', CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

WARMUP_START = pd.Timestamp('2020-01-01 00:00:00', tz='UTC')
OOS_START = pd.Timestamp('2021-01-01 00:00:00', tz='UTC')
OOS_END = pd.Timestamp('2023-09-16 00:00:00', tz='UTC')
BASE_FEE = 0.0005
BASE_SLIP = 0.0002
SYMBOLS = ['XRPUSDT', 'SOLUSDT']
CANDIDATE = {'XRPUSDT': 0.15, 'SOLUSDT': 0.20}
NEIGHBORHOODS = {
    'XRPUSDT': [0.10, 0.125, 0.15, 0.175, 0.20],
    'SOLUSDT': [0.15, 0.175, 0.20, 0.225, 0.25],
}
YEAR_WINDOWS = [
    ('2021', '2021-01-01', '2022-01-01'),
    ('2022', '2022-01-01', '2023-01-01'),
    ('2023_PART', '2023-01-01', '2023-09-16'),
]


def month_iter(y1, m1, y2, m2):
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        yield y, m
        m += 1
        if m == 13:
            y += 1
            m = 1


def load_oos_data():
    tasks = []
    for symbol in SYMBOLS:
        for y, m in month_iter(2020, 1, 2023, 8):
            tasks.append((symbol, 'monthly', f'{y:04d}-{m:02d}'))
        for d in range(1, 17):
            tasks.append((symbol, 'daily', f'2023-09-{d:02d}'))

    pieces = collections.defaultdict(list)
    errors = collections.defaultdict(list)
    print(f'Downloading {len(tasks)} OOS Binance archives', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for task, df, status in ex.map(core.fetch_archive, tasks):
            sym = task[0]
            if df is not None:
                pieces[sym].append(df)
            elif status != 'NOT_LISTED':
                errors[sym].append(task[2] + ':' + status)

    out = {}
    availability = []
    for symbol in SYMBOLS:
        if errors[symbol]:
            raise RuntimeError(f'{symbol} archive errors: {errors[symbol][:5]}')
        if not pieces[symbol]:
            raise RuntimeError(f'{symbol}: no OOS data')
        x = pd.concat(pieces[symbol]).sort_index()
        x = x[~x.index.duplicated(keep='last')]
        x = x[(x.index >= WARMUP_START) & (x.index < OOS_END)]
        if x.empty:
            raise RuntimeError(f'{symbol}: empty after OOS date filter')
        gaps = int((x.index.to_series().diff() > pd.Timedelta(hours=1)).sum())
        if gaps:
            # Missing time before listing is naturally absent; only count internal gaps after first row.
            diffs = x.index.to_series().diff()
            bad = diffs[diffs > pd.Timedelta(hours=1)]
            raise RuntimeError(f'{symbol}: internal missing-hour gaps={gaps}, first={bad.head(3).to_dict()}')
        availability.append({'symbol': symbol, 'first_hour': str(x.index[0]), 'last_hour': str(x.index[-1]), 'rows': len(x)})
        out[symbol] = x
    pd.DataFrame(availability).to_csv(OUT / 'data_availability.csv', index=False)
    return out


def run_once(raw, symbol, pb, start=OOS_START, end=OOS_END, cost_factor=1.0):
    core.START = start
    core.END = end
    core.FEE = BASE_FEE * cost_factor
    core.SLIP = BASE_SLIP * cost_factor
    x = raw[symbol]
    x = x[x.index < end]
    variant = {'entry_pb': None, 'add_pb': pb}
    name = 'BASE' if pb is None else f'PB{pb:.3f}'
    return core.backtest(symbol, x, name, variant)


def dep(symbol, strategy, trades):
    if trades is None or trades.empty:
        return {'symbol': symbol, 'strategy': strategy, 'closed_trades': 0}
    pnl = trades.net_pnl.astype(float)
    wins = pnl[pnl > 0].sort_values(ascending=False)
    net = float(pnl.sum())
    top1 = float(wins.head(1).sum())
    top3 = float(wins.head(3).sum())
    return {
        'symbol': symbol, 'strategy': strategy, 'closed_trades': len(trades),
        'net_closed_pnl': net, 'top1_win': top1, 'top3_wins': top3,
        'top1_share_pct': 100*top1/net if net > 0 else np.nan,
        'top3_share_pct': 100*top3/net if net > 0 else np.nan,
        'net_ex_top1': net-top1, 'net_ex_top3': net-top3,
    }


def compare(symbol, period, bs, cs):
    return {
        'symbol': symbol, 'period': period,
        'base_return_pct': bs['return_pct'], 'candidate_return_pct': cs['return_pct'],
        'delta_return_pp': cs['return_pct']-bs['return_pct'],
        'base_cagr_pct': bs['cagr_pct'], 'candidate_cagr_pct': cs['cagr_pct'],
        'delta_cagr_pp': cs['cagr_pct']-bs['cagr_pct'],
        'base_mdd_pct': bs['mdd_pct'], 'candidate_mdd_pct': cs['mdd_pct'],
        'delta_mdd_pp': cs['mdd_pct']-bs['mdd_pct'],
        'base_calmar': bs['calmar'], 'candidate_calmar': cs['calmar'],
        'delta_calmar': cs['calmar']-bs['calmar'],
        'base_pf': bs['pf'], 'candidate_pf': cs['pf'],
        'delta_pf': cs['pf']-bs['pf'],
        'base_trades': bs['closed_trades'], 'candidate_trades': cs['closed_trades'],
    }


def main():
    raw = load_oos_data()

    # Full pre-selection OOS comparison.
    full_rows = []
    dep_rows = []
    for symbol, pb in CANDIDATE.items():
        for strategy, v in [('BASE', None), ('CANDIDATE', pb)]:
            s, eq, tr = run_once(raw, symbol, v)
            r = dict(s); r['strategy'] = strategy
            full_rows.append(r)
            dep_rows.append(dep(symbol, strategy, tr))
    full = pd.DataFrame(full_rows)
    full.to_csv(OUT / 'oos_full_period.csv', index=False)
    dependence = pd.DataFrame(dep_rows)
    dependence.to_csv(OUT / 'oos_big_winner_dependence.csv', index=False)

    # Neighborhood stability in the earlier market regime.
    nr = []
    for symbol, vals in NEIGHBORHOODS.items():
        bs, _, _ = run_once(raw, symbol, None)
        nr.append({'symbol': symbol, 'pb_n': 0.0, 'label': 'BASE',
                   'return_pct': bs['return_pct'], 'cagr_pct': bs['cagr_pct'], 'mdd_pct': bs['mdd_pct'],
                   'calmar': bs['calmar'], 'pf': bs['pf'], 'closed_trades': bs['closed_trades'],
                   'delta_cagr_pp_vs_base': 0.0, 'delta_mdd_pp_vs_base': 0.0, 'delta_calmar_vs_base': 0.0})
        for pb in vals:
            s, _, _ = run_once(raw, symbol, pb)
            nr.append({'symbol': symbol, 'pb_n': pb,
                       'label': 'CANDIDATE' if abs(pb-CANDIDATE[symbol]) < 1e-12 else 'NEIGHBOR',
                       'return_pct': s['return_pct'], 'cagr_pct': s['cagr_pct'], 'mdd_pct': s['mdd_pct'],
                       'calmar': s['calmar'], 'pf': s['pf'], 'closed_trades': s['closed_trades'],
                       'delta_cagr_pp_vs_base': s['cagr_pct']-bs['cagr_pct'],
                       'delta_mdd_pp_vs_base': s['mdd_pct']-bs['mdd_pct'],
                       'delta_calmar_vs_base': s['calmar']-bs['calmar']})
    neigh = pd.DataFrame(nr)
    neigh.to_csv(OUT / 'oos_parameter_neighborhood.csv', index=False)

    # Calendar reset windows: 2021, 2022, 2023 partial.
    wr = []
    for symbol, pb in CANDIDATE.items():
        for period, a, b in YEAR_WINDOWS:
            a = pd.Timestamp(a, tz='UTC'); b = pd.Timestamp(b, tz='UTC')
            bs, _, _ = run_once(raw, symbol, None, a, b)
            cs, _, _ = run_once(raw, symbol, pb, a, b)
            wr.append(compare(symbol, period, bs, cs))
    windows = pd.DataFrame(wr)
    windows.to_csv(OUT / 'oos_calendar_reset_windows.csv', index=False)

    # OOS cost stress.
    sr = []
    for symbol, pb in CANDIDATE.items():
        for factor in [1.0, 1.5, 2.0]:
            bs, _, _ = run_once(raw, symbol, None, cost_factor=factor)
            cs, _, _ = run_once(raw, symbol, pb, cost_factor=factor)
            r = compare(symbol, f'COST_{factor:.1f}X', bs, cs)
            r['cost_factor'] = factor
            sr.append(r)
    stress = pd.DataFrame(sr)
    stress.to_csv(OUT / 'oos_cost_stress.csv', index=False)

    # Compact summary.
    summary = []
    for symbol in SYMBOLS:
        b = full[(full.symbol==symbol)&(full.strategy=='BASE')].iloc[0]
        c = full[(full.symbol==symbol)&(full.strategy=='CANDIDATE')].iloc[0]
        ng = neigh[(neigh.symbol==symbol)&(neigh.label!='BASE')]
        wg = windows[windows.symbol==symbol]
        sg = stress[stress.symbol==symbol]
        db = dependence[(dependence.symbol==symbol)&(dependence.strategy=='BASE')].iloc[0]
        dc = dependence[(dependence.symbol==symbol)&(dependence.strategy=='CANDIDATE')].iloc[0]
        summary.append({
            'symbol': symbol, 'candidate_pb_n': CANDIDATE[symbol],
            'oos_base_cagr_pct': b.cagr_pct, 'oos_candidate_cagr_pct': c.cagr_pct,
            'oos_delta_cagr_pp': c.cagr_pct-b.cagr_pct,
            'oos_base_mdd_pct': b.mdd_pct, 'oos_candidate_mdd_pct': c.mdd_pct,
            'oos_delta_mdd_pp': c.mdd_pct-b.mdd_pct,
            'oos_base_calmar': b.calmar, 'oos_candidate_calmar': c.calmar,
            'oos_delta_calmar': c.calmar-b.calmar,
            'neighbors_cagr_above_base': int((ng.delta_cagr_pp_vs_base>0).sum()),
            'neighbors_tested': len(ng),
            'calendar_windows_candidate_return_higher': int((wg.delta_return_pp>0).sum()),
            'calendar_windows_tested': len(wg),
            'cost_scenarios_candidate_cagr_higher': int((sg.delta_cagr_pp>0).sum()),
            'cost_scenarios_tested': len(sg),
            'base_top3_share_pct': db.top3_share_pct,
            'candidate_top3_share_pct': dc.top3_share_pct,
        })
    summary = pd.DataFrame(summary)
    summary.to_csv(OUT / 'oos_summary.csv', index=False)

    md = [
        '# Turtle Stage 3B — 이전 기간 OOS 강건성 검사', '',
        '- 후보 선택에 사용한 2023-09-16 이후 구간을 제외하고, 더 이전 구간에서 고정된 XRP 0.15N / SOL 0.20N을 재검증.',
        '- 하이브리드 진입 없음. 첫 진입은 기존 돌파 즉시 1유닛.',
        f'- OOS period: {OOS_START} ~ {OOS_END} (exclusive).',
        '- Baseline cost: fee 0.05%/side, adverse slippage 0.02%/fill.', '',
        '## Data availability', '', pd.read_csv(OUT/'data_availability.csv').to_markdown(index=False), '',
        '## OOS full-period', '', full[['symbol','strategy','add_pb_n','final_equity','return_pct','cagr_pct','mdd_pct','calmar','pf','closed_trades']].to_markdown(index=False, floatfmt='.3f'), '',
        '## OOS parameter neighborhood', '', neigh.to_markdown(index=False, floatfmt='.3f'), '',
        '## OOS calendar reset windows', '', windows.to_markdown(index=False, floatfmt='.3f'), '',
        '## OOS cost stress', '', stress.to_markdown(index=False, floatfmt='.3f'), '',
        '## OOS big-winner dependence', '', dependence.to_markdown(index=False, floatfmt='.3f'), '',
        '## OOS summary', '', summary.to_markdown(index=False, floatfmt='.3f'), '',
        '해석 시 각 기간의 거래 수가 적다는 점과, 이 OOS 구간 역시 현재 전략의 다른 파라미터 설계 과정에서 간접적으로 참조되었을 가능성을 배제할 수 없다는 점을 고려한다.'
    ]
    (OUT/'STAGE3B_OOS_REPORT_KR.md').write_text('\n'.join(md), encoding='utf-8')
    (OUT/'oos_summary.json').write_text(json.dumps(summary.to_dict(orient='records'), ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    print('OOS_SUMMARY', json.dumps(summary.to_dict(orient='records'), default=str), flush=True)
    print('\nFULL OOS\n', full[['symbol','strategy','final_equity','cagr_pct','mdd_pct','calmar','pf','closed_trades']].to_string(index=False), flush=True)
    print('\nWINDOWS\n', windows.to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
