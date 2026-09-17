"""
risk_calculator.py - Position Sizing, Circuit Growth, & Downside Trap Simulator
Part of the Project Swing Trades framework.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
import math


# Rule 1 Mandatory Invariant: Real capital deployment strictly prohibited
RULE_1_OBSERVATION_GATE_PASSED: bool = False

# Calibrated strictly to 10 consecutive 5% lower-circuit sessions: 1 - 0.95^10 = 0.401263... ~ 0.401
RULE_5_TEN_DAY_LC_DIVISOR: float = 0.401


@dataclass
class PortfolioConfig:
    total_capital: float = 100000.0        # Total trading capital in INR
    max_risk_pct_per_trade: float = 2.0     # Max total capital to lose on worst-case (2%)
    max_position_pct: float = 10.0          # Max portfolio allocation to a single penny stock (10%)
    target_monthly_return_pct: float = 20.0 # Target 20%


class CircuitRiskCalculator:
    """Quantitative risk and position management engine for Indian circuit trading."""

    @staticmethod
    def calculate_max_safe_position_by_10day_lc(
        rupees_willing_to_lose: float,
        stock_price: float,
        daily_volume: int,
    ) -> Dict[str, Any]:
        """Calculates maximum permitted position size strictly under AGENTS.md Rule 1, Rule 2, Rule 5 & Rule 9.
        
        Rule 1 Constraint:
            Observation Only. Live shares strictly 0 until 60 prospective sessions pass.
            
        Rule 2 Constraint:
            Absolute Rs 10.00 price floor.
            
        Rule 5 Constraint:
            Max Position Size (Rupees) = Rupees Willing to Lose Outright / 0.401
            
        Rule 9 Liquidity Constraint (Calm-Market Participation Filter):
            Max Exitable Shares = 2 sessions * 0.15 * Daily Volume
            
        Combined Sizing Rule:
            Max Shares = min(Capital Shares, Liquidity Shares)
        """
        # Guard: Non-positive or non-finite inputs
        if (
            rupees_willing_to_lose is None
            or not isinstance(rupees_willing_to_lose, (int, float))
            or not math.isfinite(rupees_willing_to_lose)
            or rupees_willing_to_lose <= 0
        ):
            return {
                "rupees_willing_to_lose": rupees_willing_to_lose,
                "stock_price": stock_price,
                "max_shares": 0,
                "paper_shares": 0,
                "live_shares": 0,
                "constrained_by": "INVALID_RISK_BUDGET",
                "error": "rupees_willing_to_lose must be a positive finite number.",
                "observation_gate_passed": RULE_1_OBSERVATION_GATE_PASSED,
            }

        if (
            stock_price is None
            or not isinstance(stock_price, (int, float))
            or not math.isfinite(stock_price)
            or stock_price <= 0
        ):
            return {
                "rupees_willing_to_lose": rupees_willing_to_lose,
                "stock_price": stock_price,
                "max_shares": 0,
                "paper_shares": 0,
                "live_shares": 0,
                "constrained_by": "INVALID_STOCK_PRICE",
                "error": "stock_price must be a positive finite number.",
                "observation_gate_passed": RULE_1_OBSERVATION_GATE_PASSED,
            }

        # Rule 2: Absolute Rs 10.00 Floor
        if stock_price < 10.00:
            return {
                "rupees_willing_to_lose": rupees_willing_to_lose,
                "stock_price": stock_price,
                "max_shares": 0,
                "paper_shares": 0,
                "live_shares": 0,
                "constrained_by": "RULE_2_DISQUALIFIED_SUB_10",
                "error": "Immediate disqualification: stock trades below Rs 10.00 floor.",
                "observation_gate_passed": RULE_1_OBSERVATION_GATE_PASSED,
            }

        # Rule 9: Mandatory daily volume check
        if (
            daily_volume is None
            or not isinstance(daily_volume, (int, float))
            or not math.isfinite(daily_volume)
            or daily_volume <= 0
        ):
            return {
                "rupees_willing_to_lose": rupees_willing_to_lose,
                "stock_price": stock_price,
                "max_shares": 0,
                "paper_shares": 0,
                "live_shares": 0,
                "constrained_by": "INVALID_DAILY_VOLUME",
                "error": "daily_volume is mandatory and must be a positive finite number under Rule 9.",
                "observation_gate_passed": RULE_1_OBSERVATION_GATE_PASSED,
            }

        max_position_rupees = rupees_willing_to_lose / RULE_5_TEN_DAY_LC_DIVISOR
        capital_max_shares = int(max_position_rupees // stock_price)
        
        liquidity_max_shares = int(2.0 * 0.15 * daily_volume)
        if liquidity_max_shares < capital_max_shares:
            constrained_by = "LIQUIDITY_GATE_RULE_9"
            max_shares = liquidity_max_shares
        else:
            constrained_by = "CAPITAL_RISK_RULE_5"
            max_shares = capital_max_shares

        actual_rupees = round(max_shares * stock_price, 2)
        worst_case_loss = round(actual_rupees * RULE_5_TEN_DAY_LC_DIVISOR, 2)

        # Rule 1 Enforcement: live shares are permanently 0 during observation phase
        live_shares = max_shares if RULE_1_OBSERVATION_GATE_PASSED else 0

        return {
            "rupees_willing_to_lose": rupees_willing_to_lose,
            "max_position_rupees": round(max_position_rupees, 2),
            "stock_price": stock_price,
            "max_shares": max_shares,
            "paper_shares": max_shares,
            "live_shares": live_shares,
            "capital_max_shares": capital_max_shares,
            "liquidity_max_shares": liquidity_max_shares,
            "constrained_by": constrained_by,
            "actual_capital_deployed": actual_rupees,
            "calibrated_worst_case_10d_loss": worst_case_loss,
            "observation_gate_passed": RULE_1_OBSERVATION_GATE_PASSED,
        }

    @classmethod
    def calculate_position_size(
        cls,
        total_capital: float = 100000.0,
        stock_price: float = 0.0,
        avg_daily_volume: int = 0,
        max_capital_allocation_pct: float = 10.0,
        max_pct_of_daily_volume: float = 1.0,
        rupees_willing_to_lose: Optional[float] = None,
        daily_volume: Optional[int] = None,
        circuit_band_pct: Optional[float] = 5.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Calculates position size. Supports both legacy capital-pct and Rule 5/9 risk-budget signatures."""
        # If caller provides rupees_willing_to_lose, delegate to the authoritative Rule 5/9 method
        vol = daily_volume if daily_volume is not None and daily_volume > 0 else avg_daily_volume
        if rupees_willing_to_lose is not None:
            res = cls.calculate_max_safe_position_by_10day_lc(
                rupees_willing_to_lose=rupees_willing_to_lose,
                stock_price=stock_price,
                daily_volume=vol
            )
            # Add compatibility keys
            res["recommended_shares"] = res["max_shares"]
            res["capital_deployed"] = res.get("actual_capital_deployed", 0.0)
            res["portfolio_allocation_pct"] = round((res["capital_deployed"] / max(total_capital, 1.0)) * 100, 2)
            return res

        # Fallback to total_capital budget
        risk_budget = total_capital * (max_capital_allocation_pct / 100.0) * (RULE_5_TEN_DAY_LC_DIVISOR)
        res = cls.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=risk_budget,
            stock_price=stock_price,
            daily_volume=vol
        )
        res["recommended_shares"] = res["max_shares"]
        res["capital_deployed"] = res.get("actual_capital_deployed", 0.0)
        res["portfolio_allocation_pct"] = round((res["capital_deployed"] / max(total_capital, 1.0)) * 100, 2)
        return res

    @staticmethod
    def simulate_circuit_run(
        entry_price: float,
        num_days: int = 4,
        circuit_step_pct: float = 5.0,
    ) -> Dict[str, Any]:
        """Calculates daily compounded price progression for a consecutive upper circuit run."""
        prices = [entry_price]
        current = entry_price
        for day in range(1, num_days + 1):
            current = round(current * (1 + circuit_step_pct / 100.0), 2)
            prices.append(current)

        total_gain_pct = round(((prices[-1] - entry_price) / entry_price) * 100, 2)

        return {
            "entry_price": entry_price,
            "days": num_days,
            "daily_prices": prices,
            "final_price": prices[-1],
            "total_gain_pct": total_gain_pct,
            "target_achieved": total_gain_pct >= 20.0,
        }

    @staticmethod
    def simulate_lower_circuit_lock_trap(
        entry_price: float,
        shares: int,
        consecutive_lc_days: int = 10,
        lc_step_pct: float = 5.0,
    ) -> Dict[str, Any]:
        """Simulates loss severity if caught in an unbroken Lower Circuit freeze where bids = 0.
        
        Per AGENTS.md Rule 5: Default calibration is 10 consecutive sessions (-40.1% loss).
        """
        capital_at_risk = entry_price * shares
        prices = [entry_price]
        current = entry_price

        for _ in range(consecutive_lc_days):
            current = round(current * (1 - lc_step_pct / 100.0), 2)
            prices.append(current)

        final_capital = current * shares
        loss_inr = round(capital_at_risk - final_capital, 2)
        loss_pct = round(((prices[-1] - entry_price) / entry_price) * 100, 2)

        return {
            "consecutive_lc_days": consecutive_lc_days,
            "start_capital": round(capital_at_risk, 2),
            "end_capital": round(final_capital, 2),
            "total_loss_inr": loss_inr,
            "loss_pct": loss_pct,
        }


if __name__ == "__main__":
    print("--- CIRCUIT RISK CALCULATOR DEMONSTRATION ---")

    # 1. Rule 5 Calibration: Willing to lose Rs 5,000 on a trade
    rule5_sizing = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
        rupees_willing_to_lose=5000.0,
        stock_price=25.00,
        daily_volume=100000,
    )
    print("Rule 5 (10-Day LC Worst-Case Sizing for Rs. 5,000 Risk):")
    for k, v in rule5_sizing.items():
        print(f"  {k}: {v}")

    # 2. Rule 2 Floor Check: CCDL at Rs 1.32 (should be rejected)
    sub10_check = CircuitRiskCalculator.calculate_position_size(
        total_capital=100000,
        stock_price=1.32,
        avg_daily_volume=20000000,
    )
    print("\nRule 2 Sub-Rs 10 Check for CCDL:")
    print(f"  Result: {sub10_check['constrained_by']} | Error: {sub10_check.get('error')}")

    # 3. Simulate unbroken 10-day LC lock (Rule 5 Calibration from CROPSTER)
    trap = CircuitRiskCalculator.simulate_lower_circuit_lock_trap(
        entry_price=4.77,
        shares=12560,
        consecutive_lc_days=10,
        lc_step_pct=5.0,
    )
    print(f"\nRule 5 10-Day Descent Simulation (CROPSTER):")
    print(f"  Capital at Entry: Rs. {trap['start_capital']}")
    print(f"  Capital after {trap['consecutive_lc_days']} LC days: Rs. {trap['end_capital']}")
    print(f"  Total Loss: -Rs. {trap['total_loss_inr']} ({trap['loss_pct']}%)")

    # 4. Combined Rule 5 & Rule 9 Check: CHANDRIMA (Rs 20,000 risk, but only 6,355 daily volume)
    chandrima_size = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
        rupees_willing_to_lose=20000.0,
        stock_price=12.23,
        daily_volume=6355,
    )
    print(f"\nRule 5 + Rule 9 Combined Sizing (CHANDRIMA):")
    print(f"  Capital limit: {chandrima_size['capital_max_shares']:,} sh | Liquidity limit (2d @ 15%): {chandrima_size['liquidity_max_shares']:,} sh")
    print(f"  Final Allowed: {chandrima_size['max_shares']:,} sh | Constrained by: {chandrima_size['constrained_by']}")
    assert chandrima_size['max_shares'] == 1906, f"Expected 1906 shares, got {chandrima_size['max_shares']}"
    assert chandrima_size['constrained_by'] == "LIQUIDITY_GATE_RULE_9"
