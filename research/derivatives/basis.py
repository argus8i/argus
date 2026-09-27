"""
research/derivatives/basis.py
=============================
Futures fair value, residual basis, rolling z-scores, carry, rollover and NSE expiry dates.

    F_fair = (S - PV(dividends before expiry)) * exp(r T)

A negative raw basis is often just a dividend or funding effect, so signals should use the residual
F - F_fair, measured from synchronised mid-quotes on both legs. Annualised carry is refused for
DTE < 5 days because a one-tick basis change then becomes a huge annualised number.

NSE individual-stock futures and options expire on the last Tuesday of the month (previous trading
day if that is a holiday), per NSE's product page cited in Codex's 24-Sep review. Holidays are an input.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Iterable, List, Optional, Sequence, Set, Tuple


def fair_futures(spot: float, r: float, t_years: float, dividends: Iterable[Tuple[float, float]] = ()) -> float:
    if spot <= 0 or t_years < 0:
        raise ValueError("spot must be positive and time non-negative")
    pv = sum(amount * math.exp(-r * t) for t, amount in dividends if 0 <= t < t_years)
    if pv >= spot:
        raise ValueError("dividend PV cannot exceed spot")
    return (spot - pv) * math.exp(r * t_years)


def residual_basis_bps(futures_price: float, fair_value: float) -> float:
    if fair_value <= 0:
        raise ValueError("fair value must be positive")
    return 1e4 * (futures_price - fair_value) / fair_value


def rolling_zscore(values: Sequence[float], window: int = 20) -> List[Optional[float]]:
    """z_t against the PREVIOUS `window` observations (the current point is excluded). None until
    the window is full or when the window has no dispersion."""
    out: List[Optional[float]] = []
    for i, x in enumerate(values):
        if i < window:
            out.append(None)
            continue
        prior = [float(v) for v in values[i - window:i]]
        mean = sum(prior) / window
        var = sum((p - mean) ** 2 for p in prior) / (window - 1)
        out.append(None if var <= 0 else (float(x) - mean) / math.sqrt(var))
    return out


def annualized_carry(futures_price: float, spot: float, dte_days: int, min_dte: int = 5) -> Optional[float]:
    if spot <= 0 or dte_days < min_dte:
        return None
    return (futures_price / spot - 1.0) * 365.0 / dte_days


def rollover_ratio(oi_near: float, oi_next: float, oi_far: float) -> Optional[float]:
    total = oi_near + oi_next + oi_far
    if total <= 0 or min(oi_near, oi_next, oi_far) < 0:
        return None
    return (oi_next + oi_far) / total


def rollover_velocity(ratios: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Session-over-session change in the roll ratio (the ratio itself is a level, not a velocity)."""
    out: List[Optional[float]] = [None]
    for a, b in zip(ratios, ratios[1:]):
        out.append(None if a is None or b is None else b - a)
    return out


def nse_monthly_expiry(year: int, month: int, holidays: Set[date] = frozenset(), weekday: int = 1) -> date:
    """Last `weekday` (Tuesday = 1) of the month, rolled back to the previous trading day on holidays/weekends."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    while d in holidays or d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
