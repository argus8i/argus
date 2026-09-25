"""
research/decision/sizing.py
===========================
Position sizing and the volatility-targeting multiplier (plan P6.3, A.10).

    qty = floor( min( 1500 / |entry_ref - stop_limit|, 58333.33 / entry_ref, free_cash / entry_ref ) )
    m   = clip( VIX_ref / VIX_t, 0.5, 1.0 )          VIX_ref = median of the prior 250 daily closes (>= 120)
    m   = 0.5 if VIX rose >= 15% versus the previous daily close (overnight jump)
    m   = 0   if VIX_t is missing, invalid, or older than 5 minutes; or VIX_ref cannot be formed  -> no entries
    qty_final = floor(m * qty);  m is never above 1.

ASSUMPTIONS (to be registered with the first study that uses them): the +15% jump threshold, the 5-minute
staleness limit and the 0.5 floor. The vix_regime.py multipliers are NOT used (plan P6.3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

import numpy as np

RISK_BUDGET_RS = 1500.0
SLOT_CAP_RS = 58333.33
VIX_LOOKBACK, VIX_MIN = 250, 120
JUMP = 0.15                              # ASSUMPTION
STALE = timedelta(minutes=5)             # ASSUMPTION
FLOOR = 0.5                              # ASSUMPTION


@dataclass(frozen=True)
class Multiplier:
    m: float
    reason: str
    vix_ref: Optional[float] = None


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def vol_target_multiplier(vix_t: Optional[float], vix_ts: Optional[datetime],
                          vix_daily_history: Sequence[float], now: datetime) -> Multiplier:
    """vix_daily_history: daily closes strictly before today, oldest first (the caller guarantees it)."""
    if not _num(vix_t) or vix_t <= 0:
        return Multiplier(0.0, "VIX_MISSING_OR_INVALID")
    if vix_ts is None or now - vix_ts > STALE or vix_ts > now:
        return Multiplier(0.0, "VIX_STALE")
    hist = [float(x) for x in list(vix_daily_history)[-VIX_LOOKBACK:] if _num(x) and x > 0]
    if len(hist) < VIX_MIN:
        return Multiplier(0.0, f"VIX_HISTORY_{len(hist)}_OF_{VIX_MIN}")
    ref = float(np.median(hist))
    if vix_t / hist[-1] - 1.0 >= JUMP:
        return Multiplier(FLOOR, "VIX_OVERNIGHT_JUMP", ref)
    return Multiplier(float(min(1.0, max(FLOOR, ref / vix_t))), "OK", ref)


def base_qty(entry_ref: float, stop_limit: float, free_cash: float,
             risk_budget_rs: float = RISK_BUDGET_RS, slot_cap_rs: float = SLOT_CAP_RS) -> int:
    """A.10 before the multiplier. 0 for invalid geometry or no cash."""
    risk = abs(entry_ref - stop_limit)
    if not (entry_ref > 0 and risk > 0 and math.isfinite(risk)) or free_cash <= 0:
        return 0
    return max(0, int(math.floor(min(risk_budget_rs / risk, slot_cap_rs / entry_ref, free_cash / entry_ref) + 1e-9)))


def final_qty(qty: int, mult: Multiplier) -> int:
    m = min(1.0, max(0.0, mult.m))
    return int(math.floor(m * qty + 1e-9))
