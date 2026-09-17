"""
queue_model.py - Limit Order Book Queue Drain & Fill Probability Model
Part of the Project Swing Trades framework co-developed with Claude & ChatGPT.
Calibrated on verified micro-cap circuit runs (25-Aug & 27-Aug CROPSTER observations).
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, time


@dataclass
class QueueDrainResult:
    rho: float                                  # Standing queue ratio R / V
    rho_total: float                            # Total volume capacity ratio (R + Q) / V
    estimated_fill_shares: int                  # Strictly bounded by max(0, min(Q, V - R))
    fill_state: str                             # Discrete structural capacity state
    drain_time_hours: Optional[float]           # Hours within 6.25h session, or None if unfillable in session
    rationale: str
    fill_probability: Optional[float] = None    # DEPRECATED: Uncalibrated probabilities removed per red-team audit


class QueueDrainModel:
    """
    Diagnostic Queue Drain & Structural Capacity Estimator (Secondary Diagnostic Only).
    
    ARCHITECTURE NOTE (Fix Claude F1 / Codex R5):
    This class is NOT the authoritative execution state machine.
    The SINGLE AUTHORITATIVE execution state machine is CircuitRuleEngine.estimate_execution_state
    in antigravity/models/circuit_rules.py, which strictly executes discrete point-in-time contra-volume
    turnover under AGENTS.md Rule 4.
    
    QueueDrainModel serves solely as a secondary diagnostic capacity estimator to determine whether
    resting queue + order quantity exceeds session turnover limits prior to order submission.
    Subjective fill probabilities (0%/100%) and multi-thousand-hour drain times are strictly removed.
    
    Formulas:
        rho_queue = R / V
        rho_total = (R + Q) / V
        max_fillable = max(0, min(Q, V - R))
        where:
          R = Resting quantity ahead in queue at order placement
          Q = Desired order quantity
          V = Forecasted/realized daily contra-volume at limit price
    """

    # Baseline structural thresholds
    RHO_FULL_FILL_CEILING = 0.30  # Low queue congestion anchor
    RHO_NO_FILL_FLOOR = 1.00      # Queue saturation cutoff
    SESSION_DURATION_HOURS = 6.25 # Standard NSE/BSE trading day (09:15 to 15:30)

    # Intraday cumulative volume CDF for Indian equity markets (U-shaped intraday curve)
    # Calibrated from Krishnan & Mishra (2013) and Sampath & Gopalaswamy (2020)
    # Format: minute_from_open (0 to 375): cumulative_fraction
    INTRADAY_CDF = [
        (0, 0.00),    # 09:15
        (15, 0.12),   # 09:30 (Opening rush)
        (30, 0.22),   # 09:45
        (45, 0.30),   # 10:00
        (75, 0.38),   # 10:30
        (105, 0.44),  # 11:00
        (135, 0.50),  # 11:30
        (165, 0.55),  # 12:00 (Midday trough)
        (195, 0.60),  # 12:30
        (225, 0.65),  # 13:00
        (255, 0.70),  # 13:30
        (285, 0.75),  # 14:00
        (315, 0.82),  # 14:30 (Closing rush begins)
        (345, 0.92),  # 15:00
        (375, 1.00),  # 15:30 (Market close)
    ]

    @classmethod
    def get_intraday_volume_cdf(cls, elapsed_minutes: int) -> float:
        """Returns the expected cumulative volume fraction at a given minute after 09:15 IST."""
        if elapsed_minutes <= 0:
            return 0.01
        if elapsed_minutes >= 375:
            return 1.00

        for i in range(len(cls.INTRADAY_CDF) - 1):
            t1, f1 = cls.INTRADAY_CDF[i]
            t2, f2 = cls.INTRADAY_CDF[i + 1]
            if t1 <= elapsed_minutes <= t2:
                ratio = (elapsed_minutes - t1) / (t2 - t1)
                return f1 + ratio * (f2 - f1)

        return 1.00

    @classmethod
    def forecast_session_volume(
        cls,
        cumulative_volume: int,
        observed_time: Optional[time] = None,
        baseline_20d_median: Optional[int] = None
    ) -> int:
        """
        Forecasts full-day volume using current cumulative volume and elapsed intraday time.
        If before open (<= 09:15), returns the uncontaminated baseline volume.
        """
        if observed_time is None:
            observed_time = datetime.now().time()

        open_time = time(9, 15)
        close_time = time(15, 30)

        if observed_time < open_time:
            return baseline_20d_median if baseline_20d_median and baseline_20d_median > 0 else cumulative_volume

        if observed_time >= close_time:
            return cumulative_volume

        mins = (observed_time.hour - 9) * 60 + (observed_time.minute - 15)
        cdf = cls.get_intraday_volume_cdf(mins)

        if cdf <= 0.02:
            return baseline_20d_median if baseline_20d_median else cumulative_volume

        projected = int(cumulative_volume / cdf)
        return max(projected, cumulative_volume)

    @classmethod
    def evaluate_queue(
        cls,
        resting_queue_ahead: int,
        order_qty: int,
        expected_volume: int
    ) -> QueueDrainResult:
        """
        Evaluates structural capacity strictly against queue and volume constraints.
        Enforces volume capacity: fillable shares cannot exceed max(0, min(order_qty, expected_volume - resting_queue_ahead)).
        """
        if resting_queue_ahead is None or not math.isfinite(resting_queue_ahead) or resting_queue_ahead < 0:
            return QueueDrainResult(
                rho=float("nan"),
                rho_total=float("nan"),
                estimated_fill_shares=0,
                fill_state="INVALID_QUEUE_RANK",
                drain_time_hours=None,
                rationale="Resting queue ahead must be a non-negative finite number.",
                fill_probability=None
            )

        if expected_volume is None or not math.isfinite(expected_volume) or expected_volume <= 0:
            return QueueDrainResult(
                rho=float("inf"),
                rho_total=float("inf"),
                estimated_fill_shares=0,
                fill_state="ZERO_VOLUME_LOCKED" if (expected_volume is not None and expected_volume == 0) else "INVALID_EXPECTED_VOLUME",
                drain_time_hours=None,
                rationale="Expected volume must be a positive finite number.",
                fill_probability=None
            )

        if order_qty is None or not math.isfinite(order_qty) or order_qty <= 0:
            return QueueDrainResult(
                rho=round(resting_queue_ahead / expected_volume, 4),
                rho_total=round(resting_queue_ahead / expected_volume, 4),
                estimated_fill_shares=0,
                fill_state="INVALID_ORDER_QTY",
                drain_time_hours=0.0,
                rationale="Order quantity must be positive.",
                fill_probability=None
            )

        rho = round(resting_queue_ahead / expected_volume, 4)
        total_needed = resting_queue_ahead + order_qty
        rho_total = round(total_needed / expected_volume, 4)

        # Maximum shares that can physically be filled from expected session turnover
        shares_available_for_order = max(0, expected_volume - resting_queue_ahead)
        fillable_shares = min(order_qty, shares_available_for_order)

        # 1. Queue Saturated: Quantity ahead exhausts entire session turnover
        if resting_queue_ahead >= expected_volume:
            return QueueDrainResult(
                rho=rho,
                rho_total=rho_total,
                estimated_fill_shares=0,
                fill_state="QUEUE_SATURATED_NO_FILL",
                drain_time_hours=None,
                rationale=(
                    f"Queue ratio rho={rho:.2f} >= 1.0. Quantity ahead ({resting_queue_ahead:,}) "
                    f"exhausts expected volume ({expected_volume:,}). Zero shares fillable."
                ),
                fill_probability=None
            )

        # 2. Volume Deficit: Queue advances to order, but total volume is insufficient to fill complete order
        if total_needed > expected_volume:
            return QueueDrainResult(
                rho=rho,
                rho_total=rho_total,
                estimated_fill_shares=int(fillable_shares),
                fill_state="VOLUME_CAPACITY_EXCEEDED_PARTIAL",
                drain_time_hours=None,
                rationale=(
                    f"Total requirement (Queue {resting_queue_ahead:,} + Order {order_qty:,} = {total_needed:,}) "
                    f"exceeds expected volume ({expected_volume:,}). Capped at {fillable_shares:,} shares. "
                    "Cannot complete fill within session duration."
                ),
                fill_probability=None
            )

        # 3. Potentially Clearable: Total requirement <= Expected volume
        drain_fraction = total_needed / expected_volume
        est_hours = round(drain_fraction * cls.SESSION_DURATION_HOURS, 2)
        est_hours = min(est_hours, cls.SESSION_DURATION_HOURS)

        state = "POTENTIALLY_CLEARABLE_IN_SESSION" if rho <= cls.RHO_FULL_FILL_CEILING else "DEEP_QUEUE_CLEARABLE"

        return QueueDrainResult(
            rho=rho,
            rho_total=rho_total,
            estimated_fill_shares=int(order_qty),
            fill_state=state,
            drain_time_hours=est_hours,
            rationale=(
                f"Queue ratio rho={rho:.2f} (Total rho_total={rho_total:.2f}). "
                f"Required turnover ({total_needed:,}) within expected volume ({expected_volume:,}). "
                f"Estimated queue clearance time ~{est_hours}h."
            ),
            fill_probability=None
        )


if __name__ == "__main__":
    print("=== QUEUE DRAIN (RHO = R / V) MODEL VERIFICATION ===")

    # Hard Observation 1: 25-Aug 10:28 CROPSTER
    # Resting queue R = 4,646,100, Average daily volume V = 4,448,328
    # Realized fill: 0 shares (rho = 1.04) -> MATCH
    res1 = QueueDrainModel.evaluate_queue(
        resting_queue_ahead=4646100,
        order_qty=12560,
        expected_volume=4448328
    )
    print(f"\nTest 1 (25-Aug Observation): rho = {res1.rho:.2f} | State = {res1.fill_state}")
    print(f"  Rationale: {res1.rationale}")
    assert res1.rho >= 1.0, "25-Aug rho should be >= 1.0!"
    assert res1.fill_state == "QUEUE_SATURATED_NO_FILL"
    assert res1.estimated_fill_shares == 0, "25-Aug filled shares should be 0!"

    # Hard Observation 2: 27-Aug Day 3 CROPSTER
    # Resting queue R = 4,646,100, Volume V = 15,735,454
    # Realized fill: 12,560 shares in 1 hour (rho = 0.30) -> MATCH
    res2 = QueueDrainModel.evaluate_queue(
        resting_queue_ahead=4646100,
        order_qty=12560,
        expected_volume=15735454
    )
    print(f"\nTest 2 (27-Aug Observation): rho = {res2.rho:.2f} | State = {res2.fill_state}")
    print(f"  Rationale: {res2.rationale}")
    assert res2.rho <= 0.30, "27-Aug rho should be <= 0.30!"
    assert res2.fill_state == "POTENTIALLY_CLEARABLE_IN_SESSION"
    assert res2.estimated_fill_shares == 12560, "27-Aug filled shares should be 100%!"

    # Test 3: Intraday U-Curve Volume Forecasting at 11:00 AM (elapsed = 105 mins)
    # CDF at 11:00 AM is 0.44. If cumulative volume is 2,200,000, projected is 2.2M / 0.44 = 5.0M
    forecast_vol = QueueDrainModel.forecast_session_volume(
        cumulative_volume=2200000,
        observed_time=time(11, 0)
    )
    print(f"\nTest 3 (Intraday U-Curve Forecast at 11:00 AM): Cumulative = 2.2M -> Projected Full Day = {forecast_vol:,}")
    assert 4900000 <= forecast_vol <= 5100000, "11:00 AM projection should be ~5.0M!"

    # Test 4: Red-Team Probe: 1,000,000-share order against 1,000 forecast volume
    # Must cap fillable shares at 1,000 (never 100%), and drain_time_hours cannot be 6,250h!
    res4 = QueueDrainModel.evaluate_queue(
        resting_queue_ahead=0,
        order_qty=1000000,
        expected_volume=1000
    )
    print(f"\nTest 4 (1M Share Order against 1K Volume): Fillable = {res4.estimated_fill_shares} | State = {res4.fill_state}")
    print(f"  Rationale: {res4.rationale}")
    assert res4.estimated_fill_shares == 1000, f"Expected 1000 shares, got {res4.estimated_fill_shares}"
    assert res4.fill_state == "VOLUME_CAPACITY_EXCEEDED_PARTIAL"
    assert res4.drain_time_hours is None, f"Expected None for drain time, got {res4.drain_time_hours}"

    print("\nALL QUEUE DRAIN TESTS PASSED 100%!")
