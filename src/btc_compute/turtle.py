from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TurtleConfig:
    """Parameter container for a generic Turtle-style research engine.

    No production defaults are provided on purpose. Callers must provide every
    research parameter explicitly so private selections do not leak into this
    public package.
    """

    entry_window: int
    exit_window: int
    n_window: int
    risk_fraction: float
    stop_n: float
    add_every_n: float
    max_units: int
    leverage_cap: float
    fee_rate: float
    slippage_rate: float
    qty_step: float
    min_qty: float
    min_notional: float

    def validate(self) -> None:
        ints = {
            "entry_window": self.entry_window,
            "exit_window": self.exit_window,
            "n_window": self.n_window,
            "max_units": self.max_units,
        }
        for name, value in ints.items():
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        positive = {
            "risk_fraction": self.risk_fraction,
            "stop_n": self.stop_n,
            "add_every_n": self.add_every_n,
            "leverage_cap": self.leverage_cap,
            "qty_step": self.qty_step,
            "min_qty": self.min_qty,
            "min_notional": self.min_notional,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")

        for name, value in {
            "fee_rate": self.fee_rate,
            "slippage_rate": self.slippage_rate,
        }.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0")


def floor_to_step(quantity: float, step: float) -> float:
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError("quantity must be finite and >= 0")
    if not math.isfinite(step) or step <= 0:
        raise ValueError("step must be finite and > 0")
    return math.floor(quantity / step + 1e-12) * step


def original_wilder_n(true_range: pd.Series, window: int) -> pd.Series:
    """Original Turtle/Wilder-style N smoothing.

    The first value is the arithmetic mean of the first ``window`` true-range
    observations. Subsequent values use the recursive formula
    ((window - 1) * previous + current) / window.
    """
    if window <= 0:
        raise ValueError("window must be > 0")

    values = pd.to_numeric(true_range, errors="coerce").to_numpy(dtype=float)
    out = np.full(values.shape, np.nan, dtype=float)
    if len(values) < window:
        return pd.Series(out, index=true_range.index, dtype=float)

    first = values[:window]
    if np.isnan(first).any():
        return pd.Series(out, index=true_range.index, dtype=float)

    out[window - 1] = float(first.mean())
    for i in range(window, len(values)):
        current = values[i]
        previous = out[i - 1]
        if np.isnan(current) or np.isnan(previous):
            out[i] = np.nan
        else:
            out[i] = ((window - 1) * previous + current) / window

    return pd.Series(out, index=true_range.index, dtype=float)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")
    close = pd.to_numeric(close, errors="coerce")
    prev_close = close.shift(1)
    parts = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return parts.max(axis=1)


def shifted_donchian(
    high: pd.Series,
    low: pd.Series,
    window: int,
) -> tuple[pd.Series, pd.Series]:
    """Return prior-bar Donchian high/low channels to avoid look-ahead bias."""
    if window <= 0:
        raise ValueError("window must be > 0")
    upper = pd.to_numeric(high, errors="coerce").rolling(window, min_periods=window).max().shift(1)
    lower = pd.to_numeric(low, errors="coerce").rolling(window, min_periods=window).min().shift(1)
    return upper, lower


def adverse_fill(raw_price: float, order_side: int, slippage_rate: float, cost_multiplier: float = 1.0) -> float:
    """Apply adverse slippage. ``order_side`` is +1 buy and -1 sell."""
    if order_side not in (-1, 1):
        raise ValueError("order_side must be -1 or +1")
    if raw_price <= 0 or slippage_rate < 0 or cost_multiplier < 0:
        raise ValueError("invalid price/cost input")
    return raw_price * (1.0 + order_side * slippage_rate * cost_multiplier)
