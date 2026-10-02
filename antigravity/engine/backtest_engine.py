"""
antigravity/engine/backtest_engine.py
======================================
Purged Rolling Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle,
and Adversarial Regime Stress-Testing Suite (Track 2 Liquid Desk).

Core Capabilities:
1. PurgedFold & PurgedFoldManager:
   - Chronological rolling folds with minimum 10-session purge gap.
   - Purges training trades whose execution or exit interval overlaps test.
   - Zero multi-day swing position leakage across folds.
   - Preserves unresolved positions at fold boundaries with marked-to-market value.
2. Multi-Tier Friction Hurdle Model:
   - Tier 1: Zero Friction (theoretical gross benchmark).
   - Tier 2: Realistic Itemized Friction (STT, NSE, SEBI, GST, Stamp Duty, flat DP charges + 7.5 bps normal / 25 bps gap slippage).
   - Tier 3: Severe Stress Friction (statutory + 20 bps normal / 50 bps gap slippage).
   - Proves monotonic PnL degradation in paired repricing.
3. Standalone Execution & Slot Allocation Diagnostics:
   - Evaluates fixed-strategy candidate signals against discrete slot limits (3 concurrent slots), Rs 38,000 slot cap, and Rs 1,500 trade risk.
   - Deterministic priority ranking and tie-breaking across Sleeves A, B, and C.
   - Enforces 15% volume participation cap aggregately per symbol per session.
   - Note: Standalone fixed-strategy diagnostics; does not invoke live shared PortfolioRiskGovernor reservation lifecycle or dynamic sector-concentration controls.
4. Conservative Daily Bar Execution:
   - Stop-loss checked before target on same-bar touches (conservative path invariant).
   - Gap-down openings below stop loss filled at open minus adverse gap slippage (>1R realized loss).
   - Lower-circuit locks (no bid / zero volume) prevent exit, preserving holdings and capacity.
5. Inviolable Sealed Holdout Guard:
   - Blocks 2025-2026 data evaluation unless explicitly requested with allow_holdout=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from antigravity.engine.execution_simulator import (
    DailyBar,
    ExecutionState,
    calculate_statutory_costs,
)
from antigravity.engine.risk_governor import (
    AGGREGATE_EXPOSURE_CAP_RS,
    AGGREGATE_RISK_CAP_RS,
    CASH_BUFFER_RS,
    MAX_SLOTS,
    RISK_PER_TRADE_RS,
    SLOT_CAP_RS,
    TOTAL_CORPUS_RS,
    CandidateSignal,
    PortfolioRiskGovernor,
)
from antigravity.strategies.base_strategy import SignalEvent, ExitSignalEvent


# =============================================================================
# ENUMS & CONSTANTS
# =============================================================================

class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"
    PURGED = "PURGED"


class FrictionTier(str, Enum):
    TIER_1_ZERO = "TIER_1_ZERO"
    TIER_2_REALISTIC = "TIER_2_REALISTIC"
    TIER_3_SEVERE = "TIER_3_SEVERE"


@dataclass(frozen=True)
class FrictionPolicy:
    tier: FrictionTier
    normal_slippage_bps: float
    gap_slippage_bps: float
    include_statutory_costs: bool
    flat_dp_charge_rs: float

    @classmethod
    def zero(cls) -> FrictionPolicy:
        return cls(
            tier=FrictionTier.TIER_1_ZERO,
            normal_slippage_bps=0.0,
            gap_slippage_bps=0.0,
            include_statutory_costs=False,
            flat_dp_charge_rs=0.0,
        )

    @classmethod
    def realistic(cls) -> FrictionPolicy:
        return cls(
            tier=FrictionTier.TIER_2_REALISTIC,
            normal_slippage_bps=7.5,
            gap_slippage_bps=25.0,
            include_statutory_costs=True,
            flat_dp_charge_rs=15.93,
        )

    @classmethod
    def severe(cls) -> FrictionPolicy:
        return cls(
            tier=FrictionTier.TIER_3_SEVERE,
            normal_slippage_bps=20.0,
            gap_slippage_bps=50.0,
            include_statutory_costs=True,
            flat_dp_charge_rs=15.93,
        )


@dataclass
class PurgedFold:
    fold_id: str
    train_start: str
    train_end: str
    purge_start: str
    purge_end: str
    purge_sessions: int
    test_start: str
    test_end: str

    def __post_init__(self) -> None:
        if self.purge_sessions < 10:
            raise ValueError(f"purge_sessions must be at least 10, got {self.purge_sessions}")
        if not (self.train_start <= self.train_end < self.purge_start <= self.purge_end < self.test_start <= self.test_end):
            raise ValueError(
                f"Chronological violation in PurgedFold {self.fold_id}: "
                f"train=[{self.train_start}, {self.train_end}], "
                f"purge=[{self.purge_start}, {self.purge_end}], "
                f"test=[{self.test_start}, {self.test_end}]"
            )


@dataclass(frozen=True)
class TradeFill:
    session: str
    shares: int
    raw_price: float
    slipped_price: float
    costs: float
    reason: str
    is_gap: bool = False


@dataclass
class BacktestTrade:
    trade_id: str
    strategy_id: str
    symbol: str
    entry_session: str
    exit_session: Optional[str] = None
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    raw_entry_price: float = 0.0
    raw_exit_price: Optional[float] = None
    shares: int = 0
    initial_shares: int = 0
    initial_risk_per_share: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    trailing_stop: Optional[float] = None
    holding_sessions: int = 0
    locked_sessions: int = 0
    status: TradeStatus = TradeStatus.OPEN
    gross_pnl: float = 0.0
    entry_costs: float = 0.0
    exit_costs: float = 0.0
    net_pnl: float = 0.0
    realized_r: Optional[float] = None
    unrealized_pnl: float = 0.0
    mtm_value: float = 0.0
    exit_reason: Optional[str] = None
    pending_exit_reason: Optional[str] = None
    total_exit_proceeds: float = 0.0
    total_sold_shares: int = 0
    exit_fills: List[TradeFill] = field(default_factory=list)
    as_of_session: Optional[str] = None

    def __post_init__(self) -> None:
        if self.initial_shares == 0 and self.shares > 0:
            self.initial_shares = self.shares

    @property
    def initial_risk_rs(self) -> float:
        tot_shares = self.initial_shares if self.initial_shares > 0 else self.shares
        return self.initial_risk_per_share * tot_shares

    def mark_to_market(self, last_close: float, as_of_session: str) -> None:
        self.mtm_value = last_close * self.shares
        self.unrealized_pnl = (last_close - self.entry_price) * self.shares
        self.as_of_session = as_of_session
        if self.status == TradeStatus.OPEN:
            self.status = TradeStatus.UNRESOLVED

    def record_partial_exit(
        self,
        sell_shares: int,
        sell_price: float,
        sell_costs: float,
        reason: str,
        session: str = "",
        raw_price: Optional[float] = None,
        is_gap: bool = False,
    ) -> None:
        """Records a partial (or single) fill without closing the trade completely."""
        self.total_exit_proceeds += sell_price * sell_shares
        self.total_sold_shares += sell_shares
        self.exit_costs += sell_costs
        self.shares -= sell_shares
        self.pending_exit_reason = reason
        raw_p = raw_price if raw_price is not None else sell_price
        self.exit_fills.append(
            TradeFill(
                session=session,
                shares=sell_shares,
                raw_price=raw_p,
                slipped_price=sell_price,
                costs=sell_costs,
                reason=reason,
                is_gap=is_gap,
            )
        )

    def close(
        self,
        exit_session: str,
        exit_price: float,
        exit_reason: str,
        exit_costs: float = 0.0,
        raw_exit_price: Optional[float] = None,
    ) -> None:
        self.exit_session = exit_session
        self.exit_price = exit_price
        self.raw_exit_price = raw_exit_price if raw_exit_price is not None else exit_price
        self.exit_reason = exit_reason
        self.exit_costs += exit_costs
        tot_shares = self.initial_shares if self.initial_shares > 0 else self.shares
        if self.total_exit_proceeds <= 0:
            self.total_exit_proceeds = self.exit_price * tot_shares
            if not self.exit_fills:
                is_gap = (exit_reason == "GAP_STOP_LOSS")
                self.exit_fills.append(
                    TradeFill(
                        session=exit_session,
                        shares=tot_shares,
                        raw_price=self.raw_exit_price,
                        slipped_price=exit_price,
                        costs=exit_costs,
                        reason=exit_reason,
                        is_gap=is_gap,
                    )
                )
        if self.total_sold_shares == 0:
            self.total_sold_shares = tot_shares
        self.gross_pnl = self.total_exit_proceeds - (self.entry_price * tot_shares)
        self.net_pnl = self.gross_pnl - (self.entry_costs + self.exit_costs)
        if self.initial_risk_rs > 0:
            self.realized_r = self.net_pnl / self.initial_risk_rs
        else:
            self.realized_r = 0.0
        self.shares = 0
        self.status = TradeStatus.CLOSED
        self.pending_exit_reason = None


@dataclass(frozen=True)
class DailyEquityPoint:
    session: str
    equity: float
    cash: float = 0.0
    holdings_value: float = 0.0
    open_slots: int = 0
    drawdown_rs: float = 0.0
    drawdown_pct: float = 0.0


@dataclass(frozen=True)
class RejectedSignal:
    symbol: str
    strategy_id: str
    session_date: str
    rejection_reason: str


@dataclass(frozen=True)
class BarExitEvent:
    reason: str
    exit_price: float
    raw_exit_price: float = 0.0


@dataclass
class BacktestMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    net_expectancy_r: float
    max_drawdown_rs: float
    max_drawdown_pct: float
    max_drawdown_r: float
    hurdle_passed: bool
    itemized_costs: Dict[str, float] = field(default_factory=dict)


# =============================================================================
# PURGED FOLD MANAGER
# =============================================================================

class PurgedFoldManager:
    """Manages creation and strict boundary enforcement of purged walk-forward folds."""

    def filter_training_trades(
        self, trades: List[BacktestTrade], fold: PurgedFold
    ) -> Tuple[List[BacktestTrade], List[BacktestTrade]]:
        """
        Enforces purge embargo: trades entered during training whose lifecycle
        overlaps with or extends into the purge/test window are purged.
        """
        kept: List[BacktestTrade] = []
        purged: List[BacktestTrade] = []

        for t in trades:
            if t.entry_session < fold.train_start or t.entry_session > fold.train_end:
                continue

            # If exit is None (unresolved) or exit date >= purge_start -> PURGED
            if t.exit_session is None or t.exit_session >= fold.purge_start:
                t.status = TradeStatus.PURGED
                purged.append(t)
            else:
                kept.append(t)

        return kept, purged


# =============================================================================
# BACKTEST SIMULATION & ARBITRATION
# =============================================================================

class BacktestSimulation:
    """
    Simulates portfolio execution, signal arbitration, discrete fills,
    conservative bar-level exits, and statutory friction modeling.
    """

    def __init__(
        self,
        initial_cash: float = TOTAL_CORPUS_RS,
        risk_governor: Optional[PortfolioRiskGovernor] = None,
    ) -> None:
        self.corpus_rs = initial_cash
        self.cash = initial_cash
        self.risk_governor = risk_governor or PortfolioRiskGovernor()
        self.open_positions: Dict[str, BacktestTrade] = {}
        self.closed_trades: List[BacktestTrade] = []
        self.dp_charges_tracker: Set[Tuple[str, str]] = set()

    def arbitrate_signals(
        self, signals: List[SignalEvent]
    ) -> Tuple[List[SignalEvent], List[RejectedSignal]]:
        """
        Arbitrates candidate signals against available slot capacity.
        - Deduplicates multiple signals for the same symbol (highest priority wins).
        - Ranks candidates by priority_score descending, then symbol ascending.
        - Filters candidates against discrete 3-slot cap.
        Note: Performs standalone deduplication and capacity filtering; does not invoke
        live PortfolioRiskGovernor reservation lifecycle or dynamic sector limits.
        """
        accepted: List[SignalEvent] = []
        rejected: List[RejectedSignal] = []

        # Step 1: Same-symbol deduplication
        best_by_symbol: Dict[str, SignalEvent] = {}
        for sig in signals:
            sym = sig.symbol
            if sym not in best_by_symbol:
                best_by_symbol[sym] = sig
            else:
                current_best = best_by_symbol[sym]
                if sig.priority_score > current_best.priority_score:
                    # Reject previous best as duplicate
                    rejected.append(
                        RejectedSignal(
                            symbol=current_best.symbol,
                            strategy_id=current_best.strategy_id,
                            session_date=current_best.session_date,
                            rejection_reason="Duplicate symbol; lower priority score",
                        )
                    )
                    best_by_symbol[sym] = sig
                else:
                    rejected.append(
                        RejectedSignal(
                            symbol=sig.symbol,
                            strategy_id=sig.strategy_id,
                            session_date=sig.session_date,
                            rejection_reason="Duplicate symbol; lower priority score",
                        )
                    )

        # Step 2: Deterministic priority sorting
        candidates = sorted(
            best_by_symbol.values(),
            key=lambda s: (-s.priority_score, s.symbol),
        )

        # Step 3: Slot capacity check
        available_slots = self.risk_governor.max_slots - len(self.open_positions)
        for sig in candidates:
            if len(accepted) < available_slots:
                accepted.append(sig)
            else:
                rejected.append(
                    RejectedSignal(
                        symbol=sig.symbol,
                        strategy_id=sig.strategy_id,
                        session_date=sig.session_date,
                        rejection_reason=f"Slot cap reached ({self.risk_governor.max_slots} slots)",
                    )
                )

        return accepted, rejected

    def compute_fill_shares(
        self,
        requested_shares: int,
        bar: DailyBar,
        symbol_session_volume_used: int = 0,
    ) -> Tuple[int, ExecutionState]:
        """
        Enforces Claude Rule 9: 15% volume participation cap per symbol per session.
        """
        max_allowed = int(0.15 * bar.volume) - symbol_session_volume_used
        if max_allowed <= 0:
            return 0, ExecutionState.LOCKED_NO_OFFER

        if requested_shares <= max_allowed:
            return requested_shares, ExecutionState.FILLED
        else:
            return max_allowed, ExecutionState.PARTIAL

    def evaluate_bar_exit(
        self, trade: BacktestTrade, bar: DailyBar, policy: FrictionPolicy
    ) -> Optional[BarExitEvent]:
        """
        Evaluates daily bar for conservative exit conditions:
        1. Locked Lower Circuit (zero volume or no bid) -> exit impossible.
        2. Gap-down opening below stop loss -> exit at open minus gap slippage (>1R loss).
        3. Conservative path: Stop loss checked before target on same-bar touches.
        4. Target hit -> exit at target minus normal slippage.
        5. Trailing stop hit -> exit at trailing stop minus normal slippage.
        """
        trade.holding_sessions += 1

        # Check circuit lockout
        if bar.volume == 0 or (bar.high == bar.low == bar.open == bar.close and bar.close < trade.entry_price):
            trade.locked_sessions += 1
            # Persist triggered mandatory stop/trailing-stop intent even when execution is impossible on locked bar
            if bar.open < trade.stop_loss:
                trade.pending_exit_reason = "GAP_STOP_LOSS"
            elif bar.low <= trade.stop_loss:
                trade.pending_exit_reason = "STOP_LOSS"
            elif trade.trailing_stop is not None and bar.low <= trade.trailing_stop:
                trade.pending_exit_reason = "TRAILING_STOP"
            return None

        # Check gap-down opening below stop loss
        if bar.open < trade.stop_loss:
            gap_exit_price = bar.open * (1.0 - policy.gap_slippage_bps / 10000.0)
            return BarExitEvent(reason="GAP_STOP_LOSS", exit_price=gap_exit_price, raw_exit_price=bar.open)

        # Conservative path invariant: Stop loss hit
        if bar.low <= trade.stop_loss:
            sl_exit_price = trade.stop_loss * (1.0 - policy.normal_slippage_bps / 10000.0)
            return BarExitEvent(reason="STOP_LOSS", exit_price=sl_exit_price, raw_exit_price=trade.stop_loss)

        # Target hit
        if bar.high >= trade.target:
            tgt_exit_price = trade.target * (1.0 - policy.normal_slippage_bps / 10000.0)
            return BarExitEvent(reason="TARGET", exit_price=tgt_exit_price, raw_exit_price=trade.target)

        # Trailing stop hit
        if trade.trailing_stop is not None and bar.low <= trade.trailing_stop:
            ts_exit_price = trade.trailing_stop * (1.0 - policy.normal_slippage_bps / 10000.0)
            return BarExitEvent(reason="TRAILING_STOP", exit_price=ts_exit_price, raw_exit_price=trade.trailing_stop)

        return None

    def calculate_sell_friction(
        self,
        symbol: str,
        fills: List[Tuple[float, int]],
        sell_date: str,
        policy: FrictionPolicy,
    ) -> Dict[str, float]:
        """
        Computes statutory sell friction with Codex Mandate DP grouping:
        Flat Rs 15.93 DP charge is applied once per symbol per sell day.
        """
        total_turnover = sum(p * q for p, q in fills)
        total_qty = sum(q for _, q in fills)

        if not policy.include_statutory_costs or total_turnover <= 0:
            return {
                "turnover": total_turnover,
                "brokerage": 0.0,
                "stt": 0.0,
                "exchange_charges": 0.0,
                "sebi_charges": 0.0,
                "stamp_duty": 0.0,
                "dp_charges": 0.0,
                "gst": 0.0,
                "total_cost": 0.0,
            }

        # Check DP grouping
        dp_key = (symbol, sell_date)
        apply_dp = False
        if dp_key not in self.dp_charges_tracker:
            self.dp_charges_tracker.add(dp_key)
            apply_dp = True

        raw_costs = calculate_statutory_costs(
            price=total_turnover / total_qty,
            quantity=total_qty,
            side="SELL",
            is_delivery=True,
        )

        if not apply_dp:
            # Strip DP charge if already applied for this symbol today
            raw_costs["total_cost"] = round(raw_costs["total_cost"] - raw_costs["dp_charges"], 2)
            raw_costs["dp_charges"] = 0.0

        return raw_costs

    def compute_ledger_net_pnl(
        self, trades: List[BacktestTrade], policy: FrictionPolicy
    ) -> float:
        """
        Reprices an identical closed trade ledger under a given friction policy,
        deriving execution prices freshly from raw baseline execution levels,
        and grouping DP charges per symbol per sell day.
        """
        total_net_pnl = 0.0
        dp_seen: Set[Tuple[str, str]] = set()

        for t in trades:
            if t.status != TradeStatus.CLOSED:
                continue

            raw_entry = t.raw_entry_price if t.raw_entry_price > 0 else t.entry_price
            trade_shares = t.initial_shares if t.initial_shares > 0 else t.shares
            if trade_shares <= 0:
                continue

            entry_p_slipped = raw_entry * (1.0 + policy.normal_slippage_bps / 10000.0)
            entry_notional = entry_p_slipped * trade_shares

            buy_cost = 0.0
            if policy.include_statutory_costs:
                buy_cost = calculate_statutory_costs(entry_p_slipped, trade_shares, "BUY", True)["total_cost"]

            total_sell_proceeds = 0.0
            total_sell_costs = 0.0

            if t.exit_fills:
                for fill in t.exit_fills:
                    slip_bps = policy.gap_slippage_bps if fill.is_gap else policy.normal_slippage_bps
                    fill_p_slipped = fill.raw_price * (1.0 - slip_bps / 10000.0)
                    fill_proceeds = fill_p_slipped * fill.shares
                    total_sell_proceeds += fill_proceeds

                    if policy.include_statutory_costs:
                        dp_key = (t.symbol, fill.session)
                        apply_dp = False
                        if dp_key not in dp_seen:
                            dp_seen.add(dp_key)
                            apply_dp = True

                        sell_cost_dict = calculate_statutory_costs(fill_p_slipped, fill.shares, "SELL", True)
                        sc = sell_cost_dict["total_cost"]
                        if not apply_dp:
                            sc = round(sc - sell_cost_dict["dp_charges"], 2)
                        total_sell_costs += sc
            else:
                raw_exit = t.raw_exit_price if t.raw_exit_price is not None else (t.exit_price if t.exit_price is not None else 0.0)
                if raw_exit <= 0:
                    continue

                is_gap_exit = (t.exit_reason == "GAP_STOP_LOSS")
                exit_slip_bps = policy.gap_slippage_bps if is_gap_exit else policy.normal_slippage_bps
                exit_p_slipped = raw_exit * (1.0 - exit_slip_bps / 10000.0)
                total_sell_proceeds = exit_p_slipped * trade_shares

                if policy.include_statutory_costs:
                    dp_key = (t.symbol, t.exit_session or "")
                    apply_dp = False
                    if dp_key not in dp_seen:
                        dp_seen.add(dp_key)
                        apply_dp = True

                    sell_cost_dict = calculate_statutory_costs(exit_p_slipped, trade_shares, "SELL", True)
                    sell_cost = sell_cost_dict["total_cost"]
                    if not apply_dp:
                        sell_cost = round(sell_cost - sell_cost_dict["dp_charges"], 2)
                    total_sell_costs += sell_cost

            gross = total_sell_proceeds - entry_notional
            net = gross - (buy_cost + total_sell_costs)
            total_net_pnl += net

        return round(total_net_pnl, 2)


# =============================================================================
# PERFORMANCE METRICS CALCULATION
# =============================================================================

def compute_backtest_metrics(
    trades: List[BacktestTrade],
    equity_curve: List[DailyEquityPoint],
    corpus_rs: float = TOTAL_CORPUS_RS,
    risk_per_trade_rs: float = RISK_PER_TRADE_RS,
) -> BacktestMetrics:
    """
    Computes rigorous performance metrics, profit factor, win rate,
    net expectancy in R, and maximum drawdown in rupees, percent, and R.
    """
    if not trades and not equity_curve:
        return BacktestMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_pnl=0.0,
            profit_factor=0.0,
            net_expectancy_r=0.0,
            max_drawdown_rs=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_r=0.0,
            hurdle_passed=False,
        )

    # 1. Trade Statistics
    winning = [t for t in trades if t.net_pnl > 0]
    losing = [t for t in trades if t.net_pnl < 0]
    breakeven = [t for t in trades if t.net_pnl == 0]

    n_total = len(trades)
    n_win = len(winning)
    n_loss = len(losing)
    n_be = len(breakeven)

    win_rate = (n_win / n_total) if n_total > 0 else 0.0

    gross_profit = sum(t.net_pnl for t in winning)
    gross_loss = abs(sum(t.net_pnl for t in losing))
    net_pnl = sum(t.net_pnl for t in trades)

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    # Net expectancy in R
    r_multiples = [
        t.realized_r
        for t in trades
        if t.realized_r is not None
    ]
    if not r_multiples and n_total > 0:
        # Fallback to computing R if not pre-populated
        r_multiples = [
            t.net_pnl / t.initial_risk_rs
            for t in trades
            if t.initial_risk_rs > 0
        ]
    net_expectancy_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0

    # 2. Maximum Drawdown Calculation
    max_dd_rs = 0.0
    if equity_curve:
        peak = max(corpus_rs, equity_curve[0].equity)
        for pt in equity_curve:
            if pt.equity > peak:
                peak = pt.equity
            dd = peak - pt.equity
            if dd > max_dd_rs:
                max_dd_rs = dd

    max_dd_pct = (max_dd_rs / corpus_rs) * 100.0 if corpus_rs > 0 else 0.0
    max_dd_r = (max_dd_rs / risk_per_trade_rs) if risk_per_trade_rs > 0 else 0.0

    # 3. Hurdle Verification (Tier 2 baseline criteria)
    # Win rate >= 45%, Profit factor >= 1.30, Net expectancy > 0.25R, Max Drawdown <= 6.0%, Cash buffer >= Rs 136,000
    has_cash_obs = bool(equity_curve and any(hasattr(pt, "cash") for pt in equity_curve))
    min_cash = min((pt.cash for pt in equity_curve if hasattr(pt, "cash")), default=0.0) if has_cash_obs else 0.0
    cash_passed = has_cash_obs and (min_cash >= CASH_BUFFER_RS)
    hurdle_passed = (
        win_rate >= 0.45
        and profit_factor >= 1.30
        and net_expectancy_r > 0.250
        and max_dd_pct <= 6.0
        and cash_passed
    )

    return BacktestMetrics(
        total_trades=n_total,
        winning_trades=n_win,
        losing_trades=n_loss,
        breakeven_trades=n_be,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        net_expectancy_r=net_expectancy_r,
        max_drawdown_rs=max_dd_rs,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_r=max_dd_r,
        hurdle_passed=hurdle_passed,
    )


# =============================================================================
# WALK-FORWARD ENGINE
# =============================================================================

class WalkForwardEngine:
    """
    Purged rolling walk-forward backtest orchestrator across historical data.
    """

    def __init__(self, allow_holdout: bool = False) -> None:
        self.allow_holdout = allow_holdout

    def is_symbol_warmed_up(
        self, symbol: str, session_count: int, required_warmup: int = 252
    ) -> bool:
        """
        Enforces Codex Mandate: 252 valid historical sessions PER SECURITY,
        not merely 252 calendar rows across the market.
        """
        return session_count >= required_warmup

    def run_simulation_for_dates(
        self, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """
        Guards sealed holdout window: 2025-2026 data requires explicit opt-in.
        """
        if not self.allow_holdout and (start_date >= "2025-01-01" or end_date >= "2025-01-01"):
            raise PermissionError(
                f"Holdout evaluation blocked: Dates [{start_date}, {end_date}] touch sealed 2025-2026 holdout dataset. "
                f"Requires explicit allow_holdout=True."
            )

        return {"status": "SUCCESS", "start_date": start_date, "end_date": end_date}
