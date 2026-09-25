"""
research/decision/estimator.py
==============================
Per-strategy estimate and per-signal expected net R (plan P6.2, A.11).

StrategyEstimate from ledger rows of one strategy (the caller chooses which evidence classes to pass;
promotion uses E2/E3 only):
    mean_net_r, se_cluster (CR1 by session), lb95 = mean - 1.645 se
    shrunk_gross_r = n / (n + n0) * mean_gross_r,  n0 = 85
        ASSUMPTION: a prior worth 85 trades centred on zero gross edge.
expected_net_r(signal) = shrunk_gross_r - fee_r(signal) - slip_r(signal), with the costs computed from the
signal's OWN stop distance and quantity (a tight stop makes the same rupee fee a larger fraction of R).
Every R here states its basis (plan rule 1.2.1): the estimate carries r_basis, and a signal is costed on the
same basis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.backtest.bars import tick_size
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.decision.stats import clustered_se, lb95

N0_SHRINK = 85            # ASSUMPTION (plan P6.2)


@dataclass(frozen=True)
class StrategyEstimate:
    strategy_id: str
    r_basis: str
    as_of: Optional[datetime]
    n: int
    n_sessions: int
    mean_net_r: float
    se_cluster: float
    lb95: float
    mean_gross_r: float
    shrunk_gross_r: float

    @classmethod
    def empty(cls, strategy_id: str, r_basis: str, as_of: Optional[datetime] = None) -> "StrategyEstimate":
        """No evidence: every estimate starts at zero edge (SHADOW ranking uses priorities, not this)."""
        return cls(strategy_id, r_basis, as_of, 0, 0, math.nan, math.nan, math.nan, math.nan, 0.0)


def estimate(strategy_id: str, rows: Sequence[Mapping[str, Any]], r_basis: str,
             as_of: Optional[datetime] = None, n0: float = N0_SHRINK) -> StrategyEstimate:
    """rows: dicts with net_r, gross_r, session and r_basis. Rows whose r_basis differs are refused, because
    mixing bases changes the unit of R."""
    if any(str(r.get("r_basis")) != r_basis for r in rows):
        raise ValueError(f"{strategy_id}: rows mix R bases; expected only {r_basis!r}")
    usable = [r for r in rows if r.get("net_r") is not None and math.isfinite(float(r["net_r"]))]
    if not usable:
        return StrategyEstimate.empty(strategy_id, r_basis, as_of)
    net = np.array([float(r["net_r"]) for r in usable])
    gross = np.array([float(r["gross_r"]) for r in usable if r.get("gross_r") is not None])
    sessions = [r["session"] for r in usable]
    n = len(net)
    se = clustered_se(net, sessions)
    mean = float(net.mean())
    mg = float(gross.mean()) if len(gross) else math.nan
    shrunk = n / (n + n0) * mg if math.isfinite(mg) else 0.0
    return StrategyEstimate(strategy_id, r_basis, as_of, n, len(set(sessions)), mean, se, lb95(mean, se), mg, shrunk)


@dataclass(frozen=True)
class SignalCost:
    fee_r: float
    slip_r: float
    fees_rs: float
    slip_rs: float
    risk_rs: float


def signal_cost(entry_ref: float, risk_px: float, side: str, qty: int, slippage_ticks: int = 1,
                product: ProductType = ProductType.MIS) -> SignalCost:
    """Round-trip cost of one planned signal in R. risk_px is the stop trigger (trigger basis) or the
    SL-limit price (stop_limit basis). Fees: DhanFeeEngine entry and exit legs at entry_ref (the exit price
    is unknown in advance; fees are almost flat in price inside the slot). Slippage: k ticks per side."""
    if qty <= 0 or not (entry_ref > 0) or abs(entry_ref - risk_px) <= 0:
        return SignalCost(math.nan, math.nan, math.nan, math.nan, math.nan)
    ent = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
    ext = OrderSide.SELL if ent == OrderSide.BUY else OrderSide.BUY
    fees = (DhanFeeEngine.calculate_leg(ent, entry_ref, qty, product).total_charges
            + DhanFeeEngine.calculate_leg(ext, entry_ref, qty, product).total_charges)
    slip = 2 * slippage_ticks * tick_size(entry_ref) * qty
    risk = abs(entry_ref - risk_px) * qty
    return SignalCost(fees / risk, slip / risk, fees, slip, risk)


def expected_net_r(est: StrategyEstimate, cost: SignalCost) -> float:
    """A.11: shrunk_gross_r - fee_r - slip_r. NaN when the signal's cost is undefined (never a pass)."""
    if not (math.isfinite(cost.fee_r) and math.isfinite(cost.slip_r)):
        return math.nan
    return est.shrunk_gross_r - cost.fee_r - cost.slip_r
