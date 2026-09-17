import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CORE_PATH = Path('research/pullback_stage1_core.py')
OUT = Path('results_pullback_stage3')
OUT.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location('core', CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

FULL_START = pd.Timestamp('2023-09-16 00:00:00', tz='UTC')
FULL_END = pd.Timestamp('2026-09-16 00:00:00', tz='UTC')
BASE_FEE = 0.0005
BASE_SLIP = 0.0002

CANDIDATE = {
    'XRPUSDT': 0.15,
    'SOLUSDT': 0.20,
}

NEIGHBORHOODS = {
    'XRPUSDT': [0.10, 0.125, 0.15, 0.175, 0.20],
    'SOLUSDT': [0.15, 0.175, 0.20, 0.225, 0.25],
}

BLOCKS = [
    ('B1', '2023-09-16', '2024-03-16'),
    ('B2', '2024-03-16', '2024-09-16'),
    ('B3', '2024-09-16', '2025-03-16'),
    ('B4', '2025-03-16', '2025-09-16'),
    ('B5', '2025-09-16', '2026-03-16'),
    ('B6', '2026-03-16', '2026-09-16'),
]

HALVES = [
    ('H1', '2023-09-16', '2025-03-16'),
    ('H2', '2025-03-16', '2026-09-16'),
]

EXPECTED = {
    ('XRPUSDT', 'BASE'): dict(final_equity=4351.11886708665, cagr_pct=63.23997448468608, mdd_pct=31.326920843656502),
    ('XRPUSDT', 'CAND'): dict(final_equity=4620.381764378673, cagr_pct=66.5394669683104, mdd_pct=31.280890178489777),
    ('SOLUSDT', 'BASE'): dict(final_equity=10172.706264838069, cagr_pct=116.64483165393942, mdd_pct=33.74341798997983),
    ('SOLUSDT', 'CAND'): dict(final_equity=11166.14038909758, cagr_pct=123.47792407793818, mdd_pct=35.34897727484703),
}


def ts(s):
    return pd.Timestamp(s, tz='UTC')


def run_once(raw, symbol, pb, start=FULL_START, end=FULL_END, cost_factor=1.0):
    core.START = start
    core.END = end
    core.FEE = BASE_FEE * cost_factor
    core.SLIP = BASE_SLIP * cost_factor
    x = raw[symbol]
    x = x[x.index < end]
    variant = {'entry_pb': None, 'add_pb': pb}
    name = 'BASE' if pb is None else f'PB{pb:.3f}'
    summary, eq, trades = core.backtest(symbol, x, name, variant)
    return summary, eq, trades


def trade_dependence(symbol, label, trades):
    if trades is None or trades.empty:
        return {
            'symbol': symbol, 'strategy': label, 'closed_trades': 0,
            'net_closed_pnl': 0.0, 'top1_win': np.nan, 'top3_wins': np.nan,
            'top1_share_pct': np.nan, 'top3_share_pct': np.nan,
            'net_ex_top1': np.nan, 'net_ex_top3': np.nan,
        }
    pnl = trades.net_pnl.astype(float)
    wins = pnl[pnl > 0].sort_values(ascending=False)
    net = float(pnl.sum())
    top1 = float(wins.iloc[0]) if len(wins) else 0.0
    top3 = float(wins.head(3).sum()) if len(wins) else 0.0
    return {
        'symbol': symbol, 'strategy': label, 'closed_trades': int(len(trades)),
        'net_closed_pnl': net, 'top1_win': top1, 'top3_wins': top3,
        'top1_share_pct': 100 * top1 / net if net > 0 else np.nan,
        'top3_share_pct': 100 * top3 / net if net > 0 else np.nan,
        'net_ex_top1': net - top1, 'net_ex_top3': net - top3,
    }


def compare_rows(symbol, period, start, end, base_s, cand_s):
    return {
        'symbol': symbol,
        'period': period,
        'start': str(start),
        'end_exclusive': str(end),
        'base_final': base_s['final_equity'],
        'candidate_final': cand_s['final_equity'],
        'base_return_pct': base_s['return_pct'],
        'candidate_return_pct': cand_s['return_pct'],
        'delta_return_pp': cand_s['return_pct'] - base_s['return_pct'],
        'base_cagr_pct': base_s['cagr_pct'],
        'candidate_cagr_pct': cand_s['cagr_pct'],
        'delta_cagr_pp': cand_s['cagr_pct'] - base_s['cagr_pct'],
        'base_mdd_pct': base_s['mdd_pct'],
        'candidate_mdd_pct': cand_s['mdd_pct'],
        'delta_mdd_pp': cand_s['mdd_pct'] - base_s['mdd_pct'],
        'base_calmar': base_s['calmar'],
        'candidate_calmar': cand_s['calmar'],
        'delta_calmar': cand_s['calmar'] - base_s['calmar'],
        'base_pf': base_s['pf'],
        'candidate_pf': cand_s['pf'],
        'delta_pf': cand_s['pf'] - base_s['pf'],
        'base_trades': base_s['closed_trades'],
        'candidate_trades': cand_s['closed_trades'],
    }


def fmt_table(df, cols=None):
    if cols is not None:
        df = df[cols]
    return df.to_markdown(index=False, floatfmt='.3f')


def main():
    # Load exactly the same Binance 1H source and Stage-1/2 execution logic, but only XRP/SOL.
    core.SYMBOLS = ['XRPUSDT', 'SOLUSDT']
    core.START = FULL_START
    core.END = FULL_END
    core.FEE = BASE_FEE
    core.SLIP = BASE_SLIP
    raw = core.load_data()

    # 1) Full-period baseline and exact Stage-2 candidate reproduction.
    full_rows = []
    dependence = []
    full_cache = {}
    for symbol in CANDIDATE:
        for label, pb in [('BASE', None), ('CANDIDATE', CANDIDATE[symbol])]:
            s, eq, tr = run_once(raw, symbol, pb)
            row = dict(s)
            row['strategy'] = label
            full_rows.append(row)
            full_cache[(symbol, label)] = (s, eq, tr)
            dependence.append(trade_dependence(symbol, label, tr))

    full = pd.DataFrame(full_rows)
    full.to_csv(OUT / 'full_period_candidate_vs_base.csv', index=False)
    dep = pd.DataFrame(dependence)
    dep.to_csv(OUT / 'big_winner_dependence.csv', index=False)

    # Hard invariance checks against Stage 2 canonical outputs.
    checks = []
    for symbol in CANDIDATE:
        for label, key in [('BASE', 'BASE'), ('CANDIDATE', 'CAND')]:
            s = full_cache[(symbol, label)][0]
            exp = EXPECTED[(symbol, key)]
            for metric in ['final_equity', 'cagr_pct', 'mdd_pct']:
                diff = float(s[metric] - exp[metric])
                ok = abs(diff) < 1e-8
                checks.append({'symbol': symbol, 'strategy': label, 'metric': metric,
                               'actual': s[metric], 'expected': exp[metric], 'diff': diff, 'pass': ok})
                if not ok:
                    raise AssertionError(f'Invariance failed {symbol} {label} {metric}: {s[metric]} != {exp[metric]}')
    pd.DataFrame(checks).to_csv(OUT / 'stage2_invariance_checks.csv', index=False)

    # 2) Neighborhood / plateau test at baseline transaction costs.
    neighborhood_rows = []
    for symbol, vals in NEIGHBORHOODS.items():
        base_s, _, _ = run_once(raw, symbol, None)
        neighborhood_rows.append({
            'symbol': symbol, 'pb_n': 0.0, 'label': 'BASE',
            'final_equity': base_s['final_equity'], 'return_pct': base_s['return_pct'],
            'cagr_pct': base_s['cagr_pct'], 'mdd_pct': base_s['mdd_pct'],
            'calmar': base_s['calmar'], 'pf': base_s['pf'], 'closed_trades': base_s['closed_trades'],
            'delta_cagr_pp_vs_base': 0.0, 'delta_mdd_pp_vs_base': 0.0, 'delta_calmar_vs_base': 0.0,
        })
        for pb in vals:
            s, _, _ = run_once(raw, symbol, pb)
            neighborhood_rows.append({
                'symbol': symbol, 'pb_n': pb, 'label': 'CANDIDATE' if abs(pb-CANDIDATE[symbol]) < 1e-12 else 'NEIGHBOR',
                'final_equity': s['final_equity'], 'return_pct': s['return_pct'],
                'cagr_pct': s['cagr_pct'], 'mdd_pct': s['mdd_pct'],
                'calmar': s['calmar'], 'pf': s['pf'], 'closed_trades': s['closed_trades'],
                'delta_cagr_pp_vs_base': s['cagr_pct'] - base_s['cagr_pct'],
                'delta_mdd_pp_vs_base': s['mdd_pct'] - base_s['mdd_pct'],
                'delta_calmar_vs_base': s['calmar'] - base_s['calmar'],
            })
    neigh = pd.DataFrame(neighborhood_rows)
    neigh.to_csv(OUT / 'parameter_neighborhood.csv', index=False)

    # 3) Independent reset tests in six-month blocks and two 18-month halves.
    block_rows = []
    for symbol, pb in CANDIDATE.items():
        for name, a, b in BLOCKS:
            a, b = ts(a), ts(b)
            bs, _, _ = run_once(raw, symbol, None, a, b)
            cs, _, _ = run_once(raw, symbol, pb, a, b)
            block_rows.append(compare_rows(symbol, name, a, b, bs, cs))
    blocks = pd.DataFrame(block_rows)
    blocks.to_csv(OUT / 'six_month_reset_blocks.csv', index=False)

    half_rows = []
    for symbol, pb in CANDIDATE.items():
        for name, a, b in HALVES:
            a, b = ts(a), ts(b)
            bs, _, _ = run_once(raw, symbol, None, a, b)
            cs, _, _ = run_once(raw, symbol, pb, a, b)
            half_rows.append(compare_rows(symbol, name, a, b, bs, cs))
    halves = pd.DataFrame(half_rows)
    halves.to_csv(OUT / 'half_period_reset_tests.csv', index=False)

    # 4) Cost stress: both fee and slippage scaled together, same factor for BASE and candidate.
    stress_rows = []
    for symbol, pb in CANDIDATE.items():
        for factor in [1.0, 1.5, 2.0]:
            bs, _, _ = run_once(raw, symbol, None, cost_factor=factor)
            cs, _, _ = run_once(raw, symbol, pb, cost_factor=factor)
            r = compare_rows(symbol, f'COST_{factor:.1f}X', FULL_START, FULL_END, bs, cs)
            r['cost_factor'] = factor
            r['fee_per_side_pct'] = BASE_FEE * factor * 100
            r['slippage_per_fill_pct'] = BASE_SLIP * factor * 100
            stress_rows.append(r)
    stress = pd.DataFrame(stress_rows)
    stress.to_csv(OUT / 'cost_stress.csv', index=False)

    # 5) Compact robustness counts; descriptive only, not a scoring rule.
    robust_rows = []
    for symbol, pb in CANDIDATE.items():
        ng = neigh[(neigh.symbol == symbol) & (neigh.label != 'BASE')]
        bg = blocks[blocks.symbol == symbol]
        hg = halves[halves.symbol == symbol]
        sg = stress[stress.symbol == symbol]
        d0 = dep[(dep.symbol == symbol) & (dep.strategy == 'BASE')].iloc[0]
        d1 = dep[(dep.symbol == symbol) & (dep.strategy == 'CANDIDATE')].iloc[0]
        robust_rows.append({
            'symbol': symbol, 'candidate_pb_n': pb,
            'neighbor_points_tested': len(ng),
            'neighbors_cagr_above_base': int((ng.delta_cagr_pp_vs_base > 0).sum()),
            'neighbors_calmar_above_base': int((ng.delta_calmar_vs_base > 0).sum()),
            'six_month_blocks_candidate_return_higher': int((bg.delta_return_pp > 0).sum()),
            'six_month_blocks_tested': len(bg),
            'halves_candidate_return_higher': int((hg.delta_return_pp > 0).sum()),
            'halves_tested': len(hg),
            'cost_scenarios_candidate_cagr_higher': int((sg.delta_cagr_pp > 0).sum()),
            'cost_scenarios_tested': len(sg),
            'base_top3_profit_share_pct': d0.top3_share_pct,
            'candidate_top3_profit_share_pct': d1.top3_share_pct,
            'candidate_minus_base_top3_share_pp': d1.top3_share_pct - d0.top3_share_pct,
            'base_net_ex_top1': d0.net_ex_top1,
            'candidate_net_ex_top1': d1.net_ex_top1,
            'base_net_ex_top3': d0.net_ex_top3,
            'candidate_net_ex_top3': d1.net_ex_top3,
        })
    robust = pd.DataFrame(robust_rows)
    robust.to_csv(OUT / 'robustness_summary.csv', index=False)

    # 6) Human-readable report. No automatic 'winner' declaration.
    full_view = full[['symbol','strategy','add_pb_n','final_equity','cagr_pct','mdd_pct','calmar','pf','closed_trades']]
    nview = neigh[['symbol','pb_n','label','final_equity','cagr_pct','mdd_pct','calmar','pf','delta_cagr_pp_vs_base','delta_mdd_pp_vs_base','delta_calmar_vs_base']]
    bview = blocks[['symbol','period','base_return_pct','candidate_return_pct','delta_return_pp','base_mdd_pct','candidate_mdd_pct','delta_mdd_pp','base_trades','candidate_trades']]
    hview = halves[['symbol','period','base_return_pct','candidate_return_pct','delta_return_pp','base_mdd_pct','candidate_mdd_pct','delta_mdd_pp']]
    sview = stress[['symbol','cost_factor','base_cagr_pct','candidate_cagr_pct','delta_cagr_pp','base_mdd_pct','candidate_mdd_pct','delta_mdd_pp','base_pf','candidate_pf']]

    md = [
        '# Turtle Stage 3 — XRP/SOL 추가진입 눌림 강건성 검사', '',
        '- 하이브리드 첫 진입은 사용하지 않음. 첫 진입은 canonical baseline과 동일한 돌파 즉시 100% 1유닛.',
        '- 검증 대상: XRP add-pullback 0.15N, SOL add-pullback 0.20N.',
        '- 데이터/엔진: Stage 1/2와 동일 Binance USDⓈ-M 1H, 이전 완료 일봉 Donchian/N, 0.05% fee/side + 0.02% adverse fill slippage baseline.',
        '- 기간: 2023-09-16 00:00 UTC ~ 2026-09-16 00:00 UTC 미만.',
        '- 강건성 축: 주변 파라미터, 6개월 독립 리셋, 18개월 반기별 독립 리셋, 비용 1.0x/1.5x/2.0x, 대형 승리 거래 의존도.', '',
        '## Stage 2 재현 확인', '',
        'Stage 2의 BASE와 정확한 후보값을 1e-8 허용오차로 재현하도록 assert를 걸었으며 실패 시 workflow가 중단된다.', '',
        '## Full-period BASE vs candidate', '', fmt_table(full_view), '',
        '## Parameter neighborhood', '', fmt_table(nview), '',
        '## Six-month independent reset blocks', '', fmt_table(bview), '',
        '## Two 18-month independent reset halves', '', fmt_table(hview), '',
        '## Transaction-cost stress', '', fmt_table(sview), '',
        '## Big-winner dependence', '', fmt_table(dep), '',
        '## Robustness counts', '', fmt_table(robust), '',
        '## Interpretation note', '',
        '이 검사는 후보가 특정 한 값/한 구간/낮은 비용에만 의존하는지 확인하기 위한 검증이다. '
        '6개월 블록은 거래 수가 적을 수 있으므로 단일 블록 결과를 과도하게 해석하지 않는다. '
        'top-winner 제거 수치는 원래 경로에서 해당 거래 PnL을 산술적으로 제외한 민감도 지표이며, 제거 후 포지션 사이징을 재시뮬레이션한 결과는 아니다.'
    ]
    (OUT / 'STAGE3_ROBUSTNESS_REPORT_KR.md').write_text('\n'.join(md), encoding='utf-8')

    machine = {
        'candidate': CANDIDATE,
        'period': {'start': str(FULL_START), 'end_exclusive': str(FULL_END)},
        'robustness_summary': robust.to_dict(orient='records'),
    }
    (OUT / 'stage3_summary.json').write_text(json.dumps(machine, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    print('STAGE3_ROBUSTNESS', json.dumps(machine, ensure_ascii=False, default=str), flush=True)
    print('\nFULL\n', full_view.to_string(index=False), flush=True)
    print('\nNEIGHBORHOOD\n', nview.to_string(index=False), flush=True)
    print('\nBLOCKS\n', bview.to_string(index=False), flush=True)
    print('\nCOST\n', sview.to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
