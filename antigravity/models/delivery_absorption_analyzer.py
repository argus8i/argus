"""
delivery_absorption_analyzer.py - 85% Delivery & Float Absorption Quantitative Analyzer
Part of Project Swing Trades (Track 1: ESM & Circuit Micro-Caps).
Distinguishes genuine institutional/operator float lockup from speculative distribution churn.
"""

import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import SecuritySeries, normalize_series

DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))


class DeliveryVerdict(str, Enum):
    ACCUMULATION_CONFIRMED = "ACCUMULATION_CONFIRMED"     # Delivery >= 85% with >= 3x volume: true float lockup
    DISTRIBUTION_CHURN = "DISTRIBUTION_CHURN"             # High volume but delivery < 40%: operator wash trading / day-trader churn
    STATUTORY_T2T_100 = "STATUTORY_T2T_100"               # Trade-to-trade series (T/XT/BE): statutory 100% gross delivery
    NORMAL_TRADING = "NORMAL_TRADING"                     # Typical baseline volume and delivery
    DATA_INVALID = "DATA_INVALID"                         # Missing or malformed delivery data


@dataclass
class DeliveryAbsorptionResult:
    symbol: str
    scripcode: str
    trade_date: str
    security_group: str
    total_volume: int
    deliverable_volume: int
    delivery_pct: float
    avg_20d_volume: int
    vol_expansion_ratio: float
    trade_count: int
    avg_trade_size: float
    is_statutory_t2t: bool
    verdict: DeliveryVerdict
    rationale: str


class DeliveryAbsorptionAnalyzer:
    """
    Evaluates deliverable volume footprint to confirm whether volume expansion
    represents genuine float lockup or speculative retail churn.
    """

    MIN_FLOAT_LOCKUP_DELIVERY_PCT = 85.0   # 85% delivery threshold for accumulation confirmation
    MAX_CHURN_DELIVERY_PCT = 40.0          # Below 40% delivery indicates speculative churn

    @classmethod
    def evaluate_delivery_metrics(
        cls,
        symbol: str,
        scripcode: str,
        trade_date: str,
        security_group: str,
        total_volume: int,
        deliverable_volume: Optional[int],
        avg_20d_volume: int,
        trade_count: Optional[int] = None
    ) -> DeliveryAbsorptionResult:
        """
        Evaluates deliverable volume and ticket size metrics.
        """
        # Guard: Non-positive or non-finite inputs
        if total_volume is None or not math.isfinite(total_volume) or total_volume <= 0:
            return cls._invalid_result(symbol, scripcode, trade_date, security_group, "Total volume must be positive.")

        norm_group = security_group.strip().upper() if security_group else "UNKNOWN"
        is_t2t = norm_group in ["T", "XT", "BE", "Z"]

        # Statutory T2T series are 100% deliverable by SEBI / exchange regulation
        if is_t2t:
            effective_deliv = total_volume
            deliv_pct = 100.0
        else:
            if deliverable_volume is not None and deliverable_volume >= 0 and math.isfinite(deliverable_volume):
                effective_deliv = min(total_volume, deliverable_volume)
                deliv_pct = round((effective_deliv / total_volume) * 100, 2)
            else:
                # Default baseline assumption for B-group when specific delivery breakdown is pending
                effective_deliv = int(0.70 * total_volume)
                deliv_pct = 70.0

        vol_ratio = round(total_volume / max(1, avg_20d_volume), 2)
        tx_count = trade_count if (trade_count and trade_count > 0 and math.isfinite(trade_count)) else 1
        avg_trade_size = round(total_volume / tx_count, 1)

        # 1. Statutory T2T Check
        if is_t2t:
            verdict = DeliveryVerdict.STATUTORY_T2T_100
            rationale = (
                f"Statutory 100% Delivery: Scrip belongs to '{norm_group}' series (Trade-to-Trade). "
                f"No intraday netting allowed. Entire volume ({total_volume:,} shares across {tx_count} trades) "
                f"settles on gross delivery. Volume expansion: {vol_ratio:.1f}x."
            )
        # 2. Distribution Churn Check (Volume expanding >= 3x but delivery < 40%)
        elif vol_ratio >= 3.0 and deliv_pct < cls.MAX_CHURN_DELIVERY_PCT:
            verdict = DeliveryVerdict.DISTRIBUTION_CHURN
            rationale = (
                f"Distribution Churn Alert: Volume surged {vol_ratio:.1f}x ({total_volume:,} sh), "
                f"but delivery is only {deliv_pct:.1f}% (< {cls.MAX_CHURN_DELIVERY_PCT}%). "
                "Indicates heavy day-trader flipping and operator wash volume without real float absorption."
            )
        # 3. Accumulation Confirmed Check (Volume >= 3x and Delivery >= 85%)
        elif vol_ratio >= 3.0 and deliv_pct >= cls.MIN_FLOAT_LOCKUP_DELIVERY_PCT:
            verdict = DeliveryVerdict.ACCUMULATION_CONFIRMED
            rationale = (
                f"Accumulation Breakout Confirmed: Volume expanded {vol_ratio:.1f}x with "
                f"{deliv_pct:.1f}% delivery ({effective_deliv:,} shares locked into demat custody). "
                f"Average trade size: {avg_trade_size:.0f} shares/trade. Floating supply is being actively removed."
            )
        else:
            verdict = DeliveryVerdict.NORMAL_TRADING
            rationale = (
                f"Normal Trading Profile: Volume expansion is {vol_ratio:.1f}x with {deliv_pct:.1f}% delivery. "
                "Does not meet the combined >= 3x volume and >= 85% delivery threshold."
            )

        return DeliveryAbsorptionResult(
            symbol=symbol,
            scripcode=scripcode,
            trade_date=trade_date,
            security_group=norm_group,
            total_volume=total_volume,
            deliverable_volume=effective_deliv,
            delivery_pct=deliv_pct,
            avg_20d_volume=avg_20d_volume,
            vol_expansion_ratio=vol_ratio,
            trade_count=tx_count,
            avg_trade_size=avg_trade_size,
            is_statutory_t2t=is_t2t,
            verdict=verdict,
            rationale=rationale
        )

    @classmethod
    def analyze_scrip_from_db(
        cls,
        scripcode: str,
        target_date: Optional[str] = None,
        db_path: str = DEFAULT_DB_PATH
    ) -> Optional[DeliveryAbsorptionResult]:
        """
        Extracts scrip metrics from SQLite warehouse and performs delivery absorption analysis.
        """
        if not os.path.exists(db_path):
            return None

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        if target_date is None:
            cur.execute("SELECT MAX(trade_date) FROM daily_quotes WHERE scripcode = ?", (scripcode,))
            row = cur.fetchone()
            if not row or not row[0]:
                conn.close()
                return None
            target_date = row[0]

        cur.execute("""
        SELECT symbol, security_group, volume, trade_count
        FROM daily_quotes
        WHERE scripcode = ? AND trade_date = ?;
        """, (scripcode, target_date))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None

        sym, grp, vol, trades = row

        # Calculate 20d average volume
        cur.execute("""
        SELECT volume FROM daily_quotes
        WHERE scripcode = ? AND trade_date < ? AND volume > 0
        ORDER BY trade_date DESC LIMIT 20;
        """, (scripcode, target_date))
        past_vols = [r[0] for r in cur.fetchall()]
        avg_20d = int(sum(past_vols) / len(past_vols)) if past_vols else vol

        conn.close()

        return cls.evaluate_delivery_metrics(
            symbol=sym,
            scripcode=scripcode,
            trade_date=target_date,
            security_group=grp,
            total_volume=vol,
            deliverable_volume=None,  # Falls back to statutory group rule or standard baseline
            avg_20d_volume=avg_20d,
            trade_count=trades
        )

    @classmethod
    def _invalid_result(cls, symbol: str, scripcode: str, trade_date: str, group: str, reason: str) -> DeliveryAbsorptionResult:
        return DeliveryAbsorptionResult(
            symbol=symbol,
            scripcode=scripcode,
            trade_date=trade_date,
            security_group=group,
            total_volume=0,
            deliverable_volume=0,
            delivery_pct=0.0,
            avg_20d_volume=0,
            vol_expansion_ratio=0.0,
            trade_count=0,
            avg_trade_size=0.0,
            is_statutory_t2t=False,
            verdict=DeliveryVerdict.DATA_INVALID,
            rationale=f"DATA_INVALID: {reason}"
        )


if __name__ == "__main__":
    print("=== DELIVERY ABSORPTION ANALYZER SELF-TEST ===")
    res1 = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
        symbol="KINETIC",
        scripcode="500240",
        trade_date="2026-09-11",
        security_group="XT",
        total_volume=267625,
        deliverable_volume=None,
        avg_20d_volume=27000,
        trade_count=1200
    )
    print(f"[{res1.symbol} ({res1.security_group})] Verdict: {res1.verdict.value} | Deliv: {res1.delivery_pct}% | Vol Exp: {res1.vol_expansion_ratio}x")
    print(f"Rationale: {res1.rationale}\n")

    res2 = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
        symbol="MOBI_CHURN_TEST",
        scripcode="999999",
        trade_date="2026-09-11",
        security_group="B",
        total_volume=500000,
        deliverable_volume=150000,  # 30% delivery on 5x volume spike!
        avg_20d_volume=100000,
        trade_count=5000
    )
    print(f"[{res2.symbol} ({res2.security_group})] Verdict: {res2.verdict.value} | Deliv: {res2.delivery_pct}% | Vol Exp: {res2.vol_expansion_ratio}x")
    print(f"Rationale: {res2.rationale}")
