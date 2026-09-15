import math

import pandas as pd
import pytest

from btc_compute import (
    TurtleConfig,
    assess_portfolio_add,
    floor_to_step,
    original_wilder_n,
    shifted_donchian,
)


def test_config_requires_explicit_valid_parameters():
    cfg = TurtleConfig(
        entry_window=7,
        exit_window=3,
        n_window=4,
        risk_fraction=0.01,
        stop_n=1.5,
        add_every_n=0.4,
        max_units=3,
        leverage_cap=2.0,
        fee_rate=0.0004,
        slippage_rate=0.0001,
        qty_step=0.01,
        min_qty=0.01,
        min_notional=5.0,
    )
    cfg.validate()


def test_original_wilder_n_recursive_formula():
    tr = pd.Series([1.0, 2.0, 3.0, 7.0, 5.0])
    out = original_wilder_n(tr, 3)
    assert math.isnan(out.iloc[0])
    assert math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx((2 * 2.0 + 7.0) / 3.0)
    assert out.iloc[4] == pytest.approx((2 * out.iloc[3] + 5.0) / 3.0)


def test_donchian_channel_is_shifted_one_bar():
    high = pd.Series([1, 2, 3, 100, 4], dtype=float)
    low = pd.Series([0, 1, 2, 3, -10], dtype=float)
    upper, lower = shifted_donchian(high, low, 3)
    assert math.isnan(upper.iloc[2])
    assert upper.iloc[3] == pytest.approx(3.0)
    assert lower.iloc[3] == pytest.approx(0.0)
    assert upper.iloc[4] == pytest.approx(100.0)
    assert lower.iloc[4] == pytest.approx(1.0)


def test_floor_to_step_never_rounds_up():
    assert floor_to_step(1.239, 0.01) == pytest.approx(1.23)
    assert floor_to_step(1.20, 0.01) == pytest.approx(1.20)


def test_portfolio_add_is_all_or_nothing():
    accepted = assess_portfolio_add(
        equity=100.0,
        current_gross_notional=120.0,
        proposed_add_notional=30.0,
        leverage_cap=1.6,
    )
    assert accepted.allowed is True
    assert accepted.projected_leverage == pytest.approx(1.5)

    rejected = assess_portfolio_add(
        equity=100.0,
        current_gross_notional=150.0,
        proposed_add_notional=20.0,
        leverage_cap=1.6,
    )
    assert rejected.allowed is False
    assert rejected.reason == "full_add_would_exceed_cap"
    assert rejected.projected_leverage == pytest.approx(1.7)
