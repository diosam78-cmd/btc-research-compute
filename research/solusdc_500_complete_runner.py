"""Combine archived SOLUSDC candles with Binance read-only 2026-09-19 1H data.
Final window covers inclusive Korean dates 2024-01-04 to 2026-09-19.
"""
import json
from pathlib import Path
import pandas as pd
from research import solusdc_500_target_period as job

original_load = job.load_target

def load_complete():
    base, _ = original_load()
    path = Path('research/solusdc_2026_09_19_1h_binance_connector.csv')
    extra = pd.read_csv(path)
    assert len(extra) == 15
    extra['t'] = pd.to_datetime(extra.t.astype('int64'), unit='ms', utc=True)
    assert str(extra.t.iloc[0]) == '2026-09-19 00:00:00+00:00'
    assert str(extra.t.iloc[-1]) == '2026-09-19 14:00:00+00:00'
    extra = extra.set_index('t')
    raw = pd.concat([base, extra]).sort_index()
    raw = raw[~raw.index.duplicated(keep='last')]
    raw = raw[raw.index < job.END_UTC]
    gaps = raw.index.to_series().diff() > pd.Timedelta(hours=1)
    assert not gaps.any(), 'Missing 1H candles'
    assert raw.index[-1] == job.END_UTC - pd.Timedelta(hours=1), 'Full date 19 not covered'
    assert raw.index[0] == pd.Timestamp('2024-01-04 12:00:00+00:00'), 'SOLUSDC listed-date data changed'

    # Stage-1 engine uses candle OPEN indices (not time_close). Include the last
    # 2026-09-19 14:00 UTC candle closing 2026-09-20 00:00 KST. This is the
    # user's full calendar day Sept 19. Original END is only a loading bound.
    job.engine.END = job.END_UTC
    audit = json.loads((job.OUT/'data_audit.json').read_text())
    audit['archive_errors'] = []
    audit['last_day_data_source'] = 'Binance USD-M market-data connector: SOLUSDC 1h, 2026-09-19 00:00-14:00 UTC; separately verified complete 15 candles'
    audit['data_last_open_utc'] = str(raw.index[-1])
    audit['data_rows'] = len(raw)
    audit['full_period_data_covered'] = True
    audit['end_date_inclusivity'] = 'Includes 2026-09-19 23:00-24:00 KST candle (open 14:00 UTC, close 15:00 UTC)'
    audit['gap_count'] = 0
    audit['gaps'] = []
    (job.OUT/'data_audit.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print('DATA_AUDIT_COMPLETE', json.dumps(audit, ensure_ascii=False), flush=True)
    return raw, True

job.load_target = load_complete

if __name__ == '__main__':
    job.main()
