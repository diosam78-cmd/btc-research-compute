from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AddDecision:
    allowed: bool
    current_leverage: float
    projected_leverage: float
    reason: str


def assess_portfolio_add(
    *,
    equity: float,
    current_gross_notional: float,
    proposed_add_notional: float,
    leverage_cap: float,
) -> AddDecision:
    """Assess a full additional order against a shared-wallet gross leverage cap.

    The function is deliberately all-or-nothing: it never shrinks a proposed
    add to fit under the cap. That makes execution deterministic and lets the
    caller decide whether a later signal can try again.
    """
    values = {
        "equity": equity,
        "current_gross_notional": current_gross_notional,
        "proposed_add_notional": proposed_add_notional,
        "leverage_cap": leverage_cap,
    }
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    if equity <= 0:
        raise ValueError("equity must be > 0")
    if current_gross_notional < 0 or proposed_add_notional < 0:
        raise ValueError("notionals must be >= 0")
    if leverage_cap <= 0:
        raise ValueError("leverage_cap must be > 0")

    current = current_gross_notional / equity
    projected = (current_gross_notional + proposed_add_notional) / equity
    allowed = projected <= leverage_cap + 1e-12
    reason = "within_cap" if allowed else "full_add_would_exceed_cap"
    return AddDecision(allowed, current, projected, reason)
