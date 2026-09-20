"""Research-only: export the exact original Binance 1h OHLC used by V5.2.11 research.
No live Pine, webhook or trading strategy changes.
"""
import json
from pathlib import Path
from research import pullback_stage1 as eng

OUT = Path('results_ma_winner_prices_20260920')
OUT.mkdir(exist_ok=True)

def main():
    raw = eng.load_data()
    result = {}
    for symbol, bars in raw.items():
        assert {'o','h','l','c'}.issubset(bars.columns)
        x = bars[['o','h','l','c']].copy()
        assert x.index.is_unique and x.index.is_monotonic_increasing
        assert not x.isna().any().any()
        assert x.index.to_series().diff().dropna().eq(__import__('pandas').Timedelta(hours=1)).all(), symbol
        x.index.name='time'
        x.to_csv(OUT / f'{symbol}_1h_ohlc.csv.gz',compression='gzip')
        result[symbol] = dict(rows=len(x), first=str(x.index[0]), last=str(x.index[-1]))
        print('VERIFIED_OHLC',symbol,result[symbol],flush=True)
    (OUT/'data_manifest.json').write_text(json.dumps(dict(source='research.pullback_stage1.load_data / Binance Vision USD-M futures 1-hour original candles',prices=result,interval='1h',bar_times='OPEN UTC; close timestamps add one hour',research_only=True),indent=2))
if __name__=='__main__': main()
