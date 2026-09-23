"""
caliber_performance_analytics.py - CALIBER Performance & Expectancy Analytics Engine
=====================================================================================
Part of Project Swing Trades // ARGUS 8i // BEACON Quantitative Platform.

Mandate:
  1. Computes institutional quantitative performance metrics:
     - Win Rate, Profit Factor, Expectancy per trade (E in R-multiples and Rupees).
     - Cumulative Rupee P&L and Equity Curve calibrated to Rs 2,50,000 capital.
     - Maximum Drawdown (MDD in Rs and %) and Annualized Sharpe Ratio.
  2. Enforces Rule 1 Mandatory Paper-Trading Gate:
     - Minimum 60 prospective trading sessions.
     - Minimum 20 realistically fillable (E3) entries.
     - Zero real capital deployment until positive net expectancy is verified.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_TRACK2_DIR = REPO_ROOT / "shared" / "track2_liquid"
PAPER_ORDERS_PATH = SHARED_TRACK2_DIR / "paper_orders.jsonl"
DEFAULT_BASELINE_CORPUS_RS = 250000.0


@dataclass(frozen=True)
class EquityPoint:
    trade_number: int
    timestamp: str
    symbol: str
    r_multiple: float
    net_pnl_rs: float
    cumulative_pnl_rs: float
    portfolio_equity_rs: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Rule1GateStatus:
    required_sessions: int
    completed_sessions: int
    sessions_progress_pct: float
    required_e3_fills: int
    verified_e3_fills: int
    fills_progress_pct: float
    is_live_trading_permitted: bool
    verdict: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceSummary:
    timestamp: str
    capital_base_rs: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    loss_rate_pct: float
    avg_win_rs: float
    avg_loss_rs: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    net_expectancy_r: float
    net_expectancy_rs: float
    gross_pnl_rs: float
    net_pnl_rs: float
    total_charges_rs: float
    max_drawdown_rs: float
    max_drawdown_pct: float
    sharpe_ratio: float
    current_equity_rs: float
    rule_1_gate: Rule1GateStatus
    equity_curve: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CaliberPerformanceAnalytics:
    """
    CALIBER: Computes real-time performance analytics and audits adherence to Rule 1 gates.
    """

    def __init__(
        self,
        capital_base_rs: float = DEFAULT_BASELINE_CORPUS_RS,
        risk_per_trade_rs: float = 1500.0,
        orders_log_path: Path | str = PAPER_ORDERS_PATH,
    ):
        self.capital_base_rs = float(capital_base_rs)
        self.risk_per_trade_rs = float(risk_per_trade_rs)
        self.orders_log_path = Path(orders_log_path)

    def calculate_metrics(
        self,
        trades: Sequence[Dict[str, Any]],
        completed_sessions: int = 1,
        verified_e3_fills: int = 0,
    ) -> PerformanceSummary:
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

        if not trades:
            # Baseline state before closed trades
            gate = Rule1GateStatus(
                required_sessions=60,
                completed_sessions=completed_sessions,
                sessions_progress_pct=round((completed_sessions / 60.0) * 100.0, 1),
                required_e3_fills=20,
                verified_e3_fills=verified_e3_fills,
                fills_progress_pct=round((verified_e3_fills / 20.0) * 100.0, 1),
                is_live_trading_permitted=False,
                verdict="OBSERVATION_ONLY_GATED",
                reason="Mandatory paper gate active (0/60 sessions, 0/20 fills completed). Zero real capital permitted.",
            )
            return PerformanceSummary(
                timestamp=now_str,
                capital_base_rs=self.capital_base_rs,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                breakeven_trades=0,
                win_rate_pct=0.0,
                loss_rate_pct=0.0,
                avg_win_rs=0.0,
                avg_loss_rs=0.0,
                avg_win_r=0.0,
                avg_loss_r=0.0,
                profit_factor=0.0,
                net_expectancy_r=0.0,
                net_expectancy_rs=0.0,
                gross_pnl_rs=0.0,
                net_pnl_rs=0.0,
                total_charges_rs=0.0,
                max_drawdown_rs=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                current_equity_rs=self.capital_base_rs,
                rule_1_gate=gate,
                equity_curve=[],
            )

        wins_gross: List[float] = []
        losses_gross: List[float] = []
        wins_net: List[float] = []
        losses_net: List[float] = []
        charges: List[float] = []
        r_multiples: List[float] = []
        equity_curve: List[Dict[str, Any]] = []

        cumulative_pnl = 0.0
        peak_equity = self.capital_base_rs
        max_drawdown_rs = 0.0
        max_drawdown_pct = 0.0

        for i, t in enumerate(trades, 1):
            net_pnl = float(t.get("net_pnl_rs", t.get("pnl_rs", 0.0)))
            charge = float(t.get("charges_rs", 0.0))
            gross_pnl = float(t.get("gross_pnl_rs", t.get("gross_pnl", net_pnl + charge)))
            r_mult = float(t.get("r_multiple", net_pnl / self.risk_per_trade_rs))
            sym = str(t.get("symbol", "UNKNOWN"))
            trade_time = str(t.get("timestamp", now_str))

            cumulative_pnl += net_pnl
            current_equity = self.capital_base_rs + cumulative_pnl
            if current_equity > peak_equity:
                peak_equity = current_equity

            dd = peak_equity - current_equity
            if dd > max_drawdown_rs:
                max_drawdown_rs = dd
            dd_pct = (dd / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct

            charges.append(charge)
            r_multiples.append(r_mult)

            if gross_pnl > 0.0:
                wins_gross.append(gross_pnl)
            elif gross_pnl < 0.0:
                losses_gross.append(gross_pnl)

            if net_pnl > 0.0:
                wins_net.append(net_pnl)
            elif net_pnl < 0.0:
                losses_net.append(net_pnl)

            equity_curve.append(
                EquityPoint(
                    trade_number=i,
                    timestamp=trade_time,
                    symbol=sym,
                    r_multiple=round(r_mult, 2),
                    net_pnl_rs=round(net_pnl, 2),
                    cumulative_pnl_rs=round(cumulative_pnl, 2),
                    portfolio_equity_rs=round(current_equity, 2),
                ).to_dict()
            )

        total_trades = len(trades)
        winning_count = len(wins_net)
        losing_count = len(losses_net)
        breakeven_count = total_trades - (winning_count + losing_count)

        win_rate = (winning_count / total_trades) if total_trades > 0 else 0.0
        loss_rate = (losing_count / total_trades) if total_trades > 0 else 0.0

        avg_win_rs = (sum(wins_net) / winning_count) if winning_count > 0 else 0.0
        avg_loss_rs = (sum(losses_net) / losing_count) if losing_count > 0 else 0.0

        win_r_list = [r for r in r_multiples if r > 0]
        loss_r_list = [r for r in r_multiples if r < 0]

        avg_win_r = (sum(win_r_list) / len(win_r_list)) if win_r_list else 0.0
        avg_loss_r = (sum(loss_r_list) / len(loss_r_list)) if loss_r_list else 0.0

        gross_profit = sum(wins_gross)
        gross_loss = abs(sum(losses_gross))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        # Net Expectancy E = (W * avg_win_R) - (L * |avg_loss_R|)
        net_expectancy_r = (win_rate * avg_win_r) - (loss_rate * abs(avg_loss_r))
        net_expectancy_rs = net_expectancy_r * self.risk_per_trade_rs

        # Sharpe ratio approximation based on trade returns (annualized ~ 250 sessions)
        if len(r_multiples) >= 5:
            mean_r = sum(r_multiples) / len(r_multiples)
            var_r = sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 1.0
            sharpe_ratio = round((mean_r / std_r) * math.sqrt(250 * 1.5), 2)
        else:
            sharpe_ratio = 0.0

        # Rule 1 Gate
        is_permitted = False
        gate_verdict = "OBSERVATION_ONLY_GATED"
        gate_reason = (
            f"Rule 1 in force: {completed_sessions}/60 sessions, {verified_e3_fills}/20 fills completed. "
            "Real capital deployment strictly prohibited."
        )

        gate = Rule1GateStatus(
            required_sessions=60,
            completed_sessions=completed_sessions,
            sessions_progress_pct=round((completed_sessions / 60.0) * 100.0, 1),
            required_e3_fills=20,
            verified_e3_fills=verified_e3_fills,
            fills_progress_pct=round((verified_e3_fills / 20.0) * 100.0, 1),
            is_live_trading_permitted=is_permitted,
            verdict=gate_verdict,
            reason=gate_reason,
        )

        return PerformanceSummary(
            timestamp=now_str,
            capital_base_rs=self.capital_base_rs,
            total_trades=total_trades,
            winning_trades=winning_count,
            losing_trades=losing_count,
            breakeven_trades=breakeven_count,
            win_rate_pct=round(win_rate * 100.0, 1),
            loss_rate_pct=round(loss_rate * 100.0, 1),
            avg_win_rs=round(avg_win_rs, 2),
            avg_loss_rs=round(avg_loss_rs, 2),
            avg_win_r=round(avg_win_r, 2),
            avg_loss_r=round(avg_loss_r, 2),
            profit_factor=round(profit_factor, 2),
            net_expectancy_r=round(net_expectancy_r, 2),
            net_expectancy_rs=round(net_expectancy_rs, 2),
            gross_pnl_rs=round(gross_profit - gross_loss, 2),
            net_pnl_rs=round(cumulative_pnl, 2),
            total_charges_rs=round(sum(charges), 2),
            max_drawdown_rs=round(max_drawdown_rs, 2),
            max_drawdown_pct=round(max_drawdown_pct, 1),
            sharpe_ratio=sharpe_ratio,
            current_equity_rs=round(self.capital_base_rs + cumulative_pnl, 2),
            rule_1_gate=gate,
            equity_curve=equity_curve,
        )

    def load_from_orders_log(
        self,
        orders_path: Optional[Path | str] = None,
        completed_sessions: int = 1,
        verified_e3_fills: int = 0,
    ) -> PerformanceSummary:
        path = Path(orders_path or self.orders_log_path)
        trades: List[Dict[str, Any]] = []

        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        status = str(rec.get("status", "")).upper()
                        # Only verified closed orders with explicit realized net PnL are included
                        if (
                            status in {"CLOSED", "SQUARED_OFF", "STOPPED_OUT", "TARGET_FILLED", "EMERGENCY_EXIT"}
                            or "net_pnl_rs" in rec
                        ):
                            if "net_pnl_rs" in rec and math.isfinite(float(rec["net_pnl_rs"])):
                                trades.append(rec)
            except Exception:
                trades = []

        return self.calculate_metrics(
            trades=trades,
            completed_sessions=completed_sessions,
            verified_e3_fills=verified_e3_fills,
        )

    @staticmethod
    def calculate_breakeven_win_rate(
        avg_gross_win_r: float = 0.75,
        avg_gross_loss_r: float = -1.0,
        friction_rs: float = 258.10,
        risk_per_trade_rs: float = 1500.0,
    ) -> float:
        """
        Calculates the mathematically exact breakeven win rate accounting for
        dual-tranche payoffs and statutory delivery frictions (Codex & Claude Audit 2026-09-23).
        
        Formula:
          friction_r = friction_rs / risk_per_trade_rs
          net_win_r = avg_gross_win_r - friction_r
          net_loss_r = abs(avg_gross_loss_r) + friction_r
          p_BE = net_loss_r / (net_win_r + net_loss_r)
        """
        friction_r = friction_rs / risk_per_trade_rs
        net_win_r = avg_gross_win_r - friction_r
        net_loss_r = abs(avg_gross_loss_r) + friction_r
        if (net_win_r + net_loss_r) <= 0:
            return 100.0
        return round(net_loss_r / (net_win_r + net_loss_r) * 100.0, 2)
