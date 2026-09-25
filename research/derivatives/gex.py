"""
research/derivatives/gex.py
===========================
Strike-by-strike gamma exposure, gamma flip and pin-risk diagnostics.

    GEX_1% = 0.01 * S^2 * sum_i sign_i * Gamma_i * OI_i * Lot_i      (Rs of delta change per 1% spot move)

The dealer sign is NOT observable per stock from public NSE data: strike OI has no side, and the
participant-wise OI file is, as far as this project has verified, aggregated across all stocks.
So the default is DealerSign.UNKNOWN, which reports only the sign-free absolute exposure and refuses
to compute a signed total or a gamma flip. Any sign convention is an explicit assumption by the caller.
Lot size is the NSE contract lot, not the US x100 multiplier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from research.derivatives.greeks import bs_greeks


class DealerSign(str, Enum):
    UNKNOWN = "UNKNOWN"
    DEALER_LONG_CALLS_SHORT_PUTS = "DEALER_LONG_CALLS_SHORT_PUTS"   # US index convention, untested for NSE stocks
    DEALER_SHORT_ALL = "DEALER_SHORT_ALL"                           # customers net long options
    EXPLICIT = "EXPLICIT"


@dataclass(frozen=True)
class OptionRow:
    strike: float
    kind: str               # "C" or "P"
    oi_contracts: int
    lot_size: int
    iv: float
    t_years: float

    def __post_init__(self) -> None:
        if self.kind not in ("C", "P"):
            raise ValueError("kind must be 'C' or 'P'")
        if self.strike <= 0 or self.oi_contracts < 0 or self.lot_size <= 0 or self.iv <= 0 or self.t_years <= 0:
            raise ValueError(f"invalid option row {self}")


@dataclass(frozen=True)
class GexResult:
    spot: float
    abs_gex_1pct: float
    signed_gex_1pct: Optional[float]
    sign_convention: DealerSign
    by_strike: Tuple[Tuple[float, str, float], ...]     # (strike, kind, unsigned GEX_1%)
    top2_strike_share: float


def _signs(rows: Sequence[OptionRow], sign: DealerSign, explicit: Optional[Sequence[int]]) -> Optional[np.ndarray]:
    if sign == DealerSign.UNKNOWN:
        return None
    if sign == DealerSign.DEALER_LONG_CALLS_SHORT_PUTS:
        return np.array([1.0 if r.kind == "C" else -1.0 for r in rows])
    if sign == DealerSign.DEALER_SHORT_ALL:
        return -np.ones(len(rows))
    if explicit is None or len(explicit) != len(rows) or any(s not in (-1, 1) for s in explicit):
        raise ValueError("EXPLICIT sign needs one +1/-1 per row")
    return np.asarray(explicit, dtype=float)


def _unsigned(spot: float, rows: Sequence[OptionRow], r: float, q: float) -> np.ndarray:
    out = np.empty(len(rows))
    for i, row in enumerate(rows):
        gamma = bs_greeks(spot, row.strike, row.t_years, r, row.iv, q, row.kind)["gamma"]
        out[i] = 0.01 * spot * spot * gamma * row.oi_contracts * row.lot_size
    return out


def gamma_exposure(spot: float, rows: Sequence[OptionRow], r: float, q: float = 0.0,
                   sign: DealerSign = DealerSign.UNKNOWN, explicit_signs: Optional[Sequence[int]] = None) -> GexResult:
    if spot <= 0 or not rows:
        raise ValueError("need a positive spot and at least one option row")
    g = _unsigned(spot, rows, r, q)
    signs = _signs(rows, sign, explicit_signs)
    per_strike: Dict[float, float] = {}
    for row, val in zip(rows, g):
        per_strike[row.strike] = per_strike.get(row.strike, 0.0) + val
    total = float(g.sum())
    top2 = sum(sorted(per_strike.values(), reverse=True)[:2]) / total if total > 0 else 0.0
    return GexResult(spot=spot, abs_gex_1pct=total,
                     signed_gex_1pct=None if signs is None else float(np.dot(signs, g)),
                     sign_convention=sign,
                     by_strike=tuple((row.strike, row.kind, float(v)) for row, v in zip(rows, g)),
                     top2_strike_share=float(top2))


def gamma_flip(rows: Sequence[OptionRow], r: float, lo: float, hi: float, q: float = 0.0,
               sign: DealerSign = DealerSign.UNKNOWN, explicit_signs: Optional[Sequence[int]] = None,
               n_grid: int = 301) -> Optional[float]:
    """Spot level where signed GEX crosses zero (IVs held fixed). None if the sign is unknown or no crossing."""
    signs = _signs(rows, sign, explicit_signs)
    if signs is None or not 0 < lo < hi:
        return None
    grid = np.linspace(lo, hi, n_grid)
    values = np.array([float(np.dot(signs, _unsigned(s, rows, r, q))) for s in grid])
    for i in range(len(grid) - 1):
        a, b = values[i], values[i + 1]
        if a == 0.0:
            return float(grid[i])
        if a * b < 0:
            return float(grid[i] + (grid[i + 1] - grid[i]) * a / (a - b))
    return None


def pin_risk(spot: float, rows: Sequence[OptionRow], sigma_annual: float) -> Dict[str, float]:
    """Heuristic: OI share of the nearest strike, discounted by its distance in expected-move units.
    Only meaningful near a stock's monthly expiry; not evidence of pinning by itself."""
    if spot <= 0 or sigma_annual <= 0 or not rows:
        raise ValueError("need positive spot, sigma and rows")
    oi_by_strike: Dict[float, int] = {}
    for row in rows:
        oi_by_strike[row.strike] = oi_by_strike.get(row.strike, 0) + row.oi_contracts * row.lot_size
    nearest = min(oi_by_strike, key=lambda k: (abs(k - spot), k))
    total = sum(oi_by_strike.values())
    share = oi_by_strike[nearest] / total if total else 0.0
    t = min(r.t_years for r in rows)
    distance = abs(spot - nearest) / (spot * sigma_annual * math.sqrt(t))
    return {"nearest_strike": nearest, "oi_share": share, "distance_sigma": distance,
            "score": share * math.exp(-0.5 * distance * distance)}


def hedge_flow_share(abs_gex_1pct: float, expected_move_pct: float, adv_value_rs: float) -> float:
    """Hedging flow implied by an expected move, as a fraction of average daily traded value.
    Below ~1% of ADV, gamma hedging cannot plausibly drive intraday price."""
    if adv_value_rs <= 0:
        raise ValueError("ADV must be positive")
    return abs(abs_gex_1pct) * abs(expected_move_pct) / adv_value_rs
