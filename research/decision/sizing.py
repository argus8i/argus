"""
research/decision/sizing.py
===========================
Position sizing and the volatility-targeting multiplier (plan P6.3, A.10), with Yashu's Adjusted A1
parameters (25 Sep 2026), which replace the plan's Rs 58,333.33 slot cap:

    MAX_SLOTS = 3, SLOT_CAP_RS = 38,000.00, AGGREGATE_EXPOSURE_CAP_RS = 1,14,000.00 (absolute notional of
    active positions plus pending entry reservations; long and short are added, never netted),
    RISK_BUDGET_RS = 1,500 (maximum planned risk per trade, unchanged).

This module is the single source of these constants for research/decision (allocator.py and stress.py import
them). Consumers outside research/decision still carry the old Rs 58,333.33 (see research/notes/p6_report.md).

    qty = floor( min( 1500 / |entry_ref - stop_limit|, 38000 / entry_ref, free_cash / entry_ref ) )
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

RISK_BUDGET_RS = 1500.0                  # maximum planned risk per trade (Adjusted A1 keeps it)
MAX_SLOTS = 3                            # Adjusted A1
SLOT_CAP_RS = 38000.00                   # Adjusted A1 (was 58,333.33)
AGGREGATE_EXPOSURE_CAP_RS = 114000.00    # Adjusted A1: active + pending, absolute notional
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
    """A.10 before the multiplier. 0 (no trade) for any invalid input: non-numeric, bool, NaN/inf,
    non-positive price or caps, zero stop distance, or no cash."""
    if not all(_num(v) for v in (entry_ref, stop_limit, free_cash, risk_budget_rs, slot_cap_rs)):
        return 0
    risk = abs(entry_ref - stop_limit)
    if not (entry_ref > 0 and risk > 0) or free_cash <= 0 or risk_budget_rs <= 0 or slot_cap_rs <= 0:
        return 0
    return max(0, int(math.floor(min(risk_budget_rs / risk, slot_cap_rs / entry_ref, free_cash / entry_ref) + 1e-9)))


def final_qty(qty: int, mult: Multiplier) -> int:
    m = min(1.0, max(0.0, mult.m))
    return int(math.floor(m * qty + 1e-9))
