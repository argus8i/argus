"""
volume_climax_detector.py - Detects Healthy Continuation vs. Operator Distribution Churn
Part of the Project Swing Trades framework.
"""

import math
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple


@dataclass
class DailySession:
    day_number: int
    close_price: float
    volume: int
    is_upper_circuit: bool
    is_lower_circuit: bool
    bid_depth: int
    offer_depth: int


class VolumeClimaxDetector:
    """Analyzes daily volume profile across consecutive circuit days to identify safe exit windows and distribution churn."""

    @staticmethod
    def evaluate_session_progression(
        sessions: List[DailySession],
        target_profit_pct: float = 20.0,
    ) -> Dict[str, Any]:
        if not sessions:
            return {"action": "NO_DATA", "details": "No sessions provided."}

        current_day = sessions[-1].day_number
        entry_price = sessions[0].close_price
        current_price = sessions[-1].close_price

        if (
            entry_price is None
            or not isinstance(entry_price, (int, float))
            or not math.isfinite(entry_price)
            or entry_price <= 0
            or current_price is None
            or not isinstance(current_price, (int, float))
            or not math.isfinite(current_price)
            or current_price <= 0
        ):
            return {
                "day": current_day,
                "gain_pct": 0.0,
                "status": "DATA_INVALID",
                "recommended_action": "NO_ACTION",
                "urgency": "NONE",
                "reason": "DATA_INVALID / FAIL-CLOSED: Session entry price and current price must be positive finite numbers.",
            }

        gain_pct = round(((current_price - entry_price) / entry_price) * 100, 2)

        # Baseline volume from ignition day (Day 1)
        day1_volume = sessions[0].volume
        current_volume = sessions[-1].volume
        current_bid_depth = sessions[-1].bid_depth

        # Check for Lower Circuit or Zero Bid Depth
        if sessions[-1].is_lower_circuit or current_bid_depth <= 0:
            if current_bid_depth <= 0:
                status = "ZERO_BID_LIQUIDITY_LOCKOUT"
                reason = (
                    f"Stock has zero resting bids ({current_bid_depth} shares). "
                    "Counterparty liquidity is 0%. Stop loss cannot execute continuously. Queue order subject to FIFO allocation."
                )
            else:
                status = "LOWER_CIRCUIT_TRAP"
                reason = (
                    f"Stock is locked at Lower Circuit with {current_bid_depth:,} resting bids. "
                    "Stop loss cannot execute continuously without adverse selection. Queue order subject to FIFO allocation."
                )

            return {
                "day": current_day,
                "gain_pct": gain_pct,
                "status": status,
                "recommended_action": "EMERGENCY_AMO_EXIT",
                "urgency": "MAXIMUM",
                "reason": reason,
            }

        # Check Target Achievement (15% to 20% window on Day 4+, requires active resting bids)
        if gain_pct >= target_profit_pct or (current_day >= 4 and gain_pct >= 15.0):
            return {
                "day": current_day,
                "gain_pct": gain_pct,
                "status": "TARGET_PROFIT_ACHIEVED",
                "recommended_action": "EXIT_INTO_BUY_QUEUE",
                "urgency": "HIGH",
                "reason": (
                    f"Target gain (+{gain_pct}%) reached on Day {current_day}. "
                    f"Resting buyer queue observed ({current_bid_depth:,} shares). "
                    "Submit sell limit order to interact with resting Upper Circuit buyer depth (fill subject to FIFO priority)."
                ),
            }

        # Check for Volume Churn (Distribution Warning)
        # If Day 3+ volume exceeds Day 1 ignition volume while locked, operator is dumping into retail bids!
        if current_day >= 3 and current_volume > (day1_volume * 1.5):
            return {
                "day": current_day,
                "gain_pct": gain_pct,
                "status": "DISTRIBUTION_CHURN_DETECTED",
                "recommended_action": "EARLY_EXIT_INTO_UC",
                "urgency": "CRITICAL",
                "reason": (
                    f"Abnormal volume surge ({current_volume:,} vs Day 1: {day1_volume:,}). "
                    "Operator is actively unloading inventory into the buyer queue. Exit immediately before circuit breaks."
                ),
            }

        # Healthy continuation (low volume lock: sellers are holding, buyers dominate)
        if sessions[-1].is_upper_circuit and current_day < 4:
            return {
                "day": current_day,
                "gain_pct": gain_pct,
                "status": "HEALTHY_CIRCUIT_MOMENTUM",
                "recommended_action": "HOLD_FOR_TARGET",
                "urgency": "LOW",
                "reason": f"Day {current_day} locked at UC (+{gain_pct}%). Volume is orderly. Hold for Day 4 target.",
            }

        return {
            "day": current_day,
            "gain_pct": gain_pct,
            "status": "MONITORING",
            "recommended_action": "HOLD",
            "urgency": "NORMAL",
            "reason": "Stock moving in range. Keep watching order book depth.",
        }


if __name__ == "__main__":
    print("--- VOLUME CLIMAX & 20% TARGET EVALUATOR ---")

    # Simulation 1: Ideal 4-Day 20% Run (like CCDL or early CROPSTER)
    healthy_cycle = [
        DailySession(day_number=1, close_price=10.00, volume=20000000, is_upper_circuit=True, is_lower_circuit=False, bid_depth=50000000, offer_depth=100000),
        DailySession(day_number=2, close_price=10.50, volume=3000000,  is_upper_circuit=True, is_lower_circuit=False, bid_depth=60000000, offer_depth=50000),
        DailySession(day_number=3, close_price=11.02, volume=2500000,  is_upper_circuit=True, is_lower_circuit=False, bid_depth=55000000, offer_depth=80000),
        DailySession(day_number=4, close_price=11.57, volume=5000000,  is_upper_circuit=True, is_lower_circuit=False, bid_depth=45000000, offer_depth=120000),
    ]

    result = VolumeClimaxDetector.evaluate_session_progression(healthy_cycle)
    print(f"\nResult Day 4 Evaluation:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    # Simulation 2: Distribution Churn on Day 3 (Operator Dumping)
    churn_cycle = [
        DailySession(day_number=1, close_price=10.00, volume=10000000, is_upper_circuit=True, is_lower_circuit=False, bid_depth=30000000, offer_depth=100000),
        DailySession(day_number=2, close_price=10.50, volume=2000000,  is_upper_circuit=True, is_lower_circuit=False, bid_depth=35000000, offer_depth=50000),
        DailySession(day_number=3, close_price=11.02, volume=25000000, is_upper_circuit=True, is_lower_circuit=False, bid_depth=10000000, offer_depth=5000000),
    ]
    churn_result = VolumeClimaxDetector.evaluate_session_progression(churn_cycle)
    print(f"\nResult Day 3 Churn Warning:")
    for k, v in churn_result.items():
        print(f"  {k}: {v}")
