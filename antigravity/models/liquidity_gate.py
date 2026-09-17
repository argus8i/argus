"""
liquidity_gate.py - Calm-Market Liquidity Participation & Sizing Gate (AGENTS.md Rule 9)
Derived from Claude's specification (claude/models/liquidity_gate.py).

CRITICAL RISK FRAMING (Fix Claude F4 / F5):
Rule 9 is a CALM-MARKET participation filter, NOT a tail-risk stop.
In an unbroken lower-circuit run (Rule 5 crisis), daily volume V -> 0.
When V -> 0, 0.15 * V -> 0, meaning clearable capacity completely evaporates.
Therefore, Rule 9 CANNOT protect against lower-circuit tail risk.
Tail risk is strictly governed by Rule 5 (10-Day Lower-Circuit Risk Calibration at 0.401).
Rule 9 functions exclusively to prevent the strategy from dominating normal two-sided trading volume (>15%).
"""

from typing import Tuple, Dict, Any
import math

MAX_PARTICIPATION: float = 0.15   # Maximum realistic fraction of a session's volume
                                  # an order can represent without moving the price.
DEFAULT_CLEARABLE_DAYS: float = 2.0  # Maximum days permitted to clear the entire position.


def daily_fill_fraction(
    position_shares: int,
    daily_volume: int,
    max_participation: float = MAX_PARTICIPATION
) -> float:
    """Fraction of the position realistically clearable in ONE session."""
    if daily_volume <= 0 or position_shares <= 0:
        return 0.0
    return min(1.0, (max_participation * daily_volume) / position_shares)


def sessions_to_exit(
    position_shares: int,
    daily_volume: int,
    max_participation: float = MAX_PARTICIPATION
) -> float:
    """Calculates the number of sessions required to fully liquidate the position."""
    f = daily_fill_fraction(position_shares, daily_volume, max_participation)
    if f <= 0:
        return float("inf")
    return 1.0 / f


def max_position_for_exit_in(
    days: float = DEFAULT_CLEARABLE_DAYS,
    daily_volume: int = 0,
    max_participation: float = MAX_PARTICIPATION
) -> int:
    """Calculates the maximum share quantity exitable within `days` sessions.
    
    THE SIZING RULE:
        Max Shares = days * max_participation * daily_volume
    """
    return int(days * max_participation * daily_volume)


def evaluate_liquidity_gate(
    position_shares: int,
    daily_volume: int,
    circuit_band_pct: float = 5.0,
    max_days: float = DEFAULT_CLEARABLE_DAYS,
    avg_20d_volume: int = None,
    is_locked_circuit: bool = False,
    resting_bids: int = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluates Rule 9 Pre-Trade Liquidity Gate with explicit circuit and volume collapse safeguards.
    
    Returns:
        (passed, reason, metrics_dict)
    """
    # Guard: Non-finite or non-numeric inputs fail closed
    if (
        not isinstance(position_shares, (int, float))
        or not math.isfinite(position_shares)
        or position_shares <= 0
    ):
        return False, "FAILED: Invalid or non-positive position_shares (fail-closed).", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": 0.0,
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": True
        }

    if (
        not isinstance(daily_volume, (int, float))
        or not math.isfinite(daily_volume)
    ):
        return False, "FAILED: Invalid or non-finite daily_volume (fail-closed).", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": 0.0,
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": True
        }

    if (
        not isinstance(circuit_band_pct, (int, float))
        or not math.isfinite(circuit_band_pct)
        or circuit_band_pct <= 0
    ):
        return False, "FAILED: Invalid or non-positive circuit_band_pct (fail-closed).", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": 0.0,
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": True
        }

    if is_locked_circuit:
        return False, "DISQUALIFIED BY RULE 9: Stock is locked at circuit. Rule 9 participation capacity is completely evaporated; exit safety cannot be assumed under zero-counterparty locks. Governed strictly by Rule 5 outright loss calibration.", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": 1.0,
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": True,
            "is_volume_collapsed": True
        }

    if daily_volume <= 0:
        return False, "FAILED: Zero daily volume (completely illiquid)", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": 1.0,
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": True
        }

    # Safeguard against volume collapse relative to 20-day baseline (Claude Condition Check)
    if avg_20d_volume is not None and avg_20d_volume > 0 and daily_volume < 0.10 * avg_20d_volume:
        return False, f"DISQUALIFIED BY RULE 9: Severe volume evaporation ({daily_volume:,} < 10% of 20d avg {avg_20d_volume:,}). Participation capacity is inert during liquidity freeze.", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": round((position_shares / daily_volume) * 100, 2),
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": True
        }

    # Safeguard against operator bid spoofing (Claude Directive 1 Recommendation)
    if resting_bids is not None and daily_volume > 0 and (resting_bids / daily_volume) > 3.0:
        fragility = round(resting_bids / daily_volume, 2)
        return False, f"DISQUALIFIED BY RULE 9 (SPOOF RISK GATE): Displayed bids ({resting_bids:,}) exceed {fragility:.1f}x daily volume ({daily_volume:,}). Suspected non-executable phantom depth.", {
            "sessions_to_exit": float("inf"),
            "daily_fill_fraction": 0.0,
            "participation_pct": round((position_shares / daily_volume) * 100, 2),
            "worst_case_cost_pct": 1.0,
            "is_circuit_locked": False,
            "is_volume_collapsed": False,
            "fragility_ratio": fragility
        }

    participation_pct = position_shares / daily_volume
    f = daily_fill_fraction(position_shares, daily_volume)
    s = sessions_to_exit(position_shares, daily_volume)
    
    # Calculate cumulative slippage/loss if trapped for s sessions at circuit band
    # Note (Fix Claude F5): This geometric decay is a calm-market slippage heuristic.
    # In an LC lockout, volume evaporates and true losses are governed by Rule 5 (40.1%).
    band_dec = circuit_band_pct / 100.0
    worst_case_cost = (1.0 - (1.0 - band_dec) ** s) if s != float("inf") else 1.0

    passed = (s <= max_days)
    max_allowed = max_position_for_exit_in(max_days, daily_volume)

    metrics = {
        "position_shares": position_shares,
        "daily_volume": daily_volume,
        "participation_pct": round(participation_pct * 100, 2),
        "daily_fill_fraction": round(f * 100, 2),
        "sessions_to_exit": round(s, 2),
        "max_allowed_shares": max_allowed,
        "worst_case_exit_cost_pct": round(worst_case_cost * 100, 2),
    }

    if not passed:
        reason = (
            f"DISQUALIFIED BY RULE 9 (LIQUIDITY GATE): Position ({position_shares:,} sh) is "
            f"{participation_pct:.1%} of daily volume ({daily_volume:,} sh). "
            f"Requires {s:.1f} sessions to clear (limit: {max_days:.1f}). Max allowed: {max_allowed:,} sh."
        )
    else:
        reason = (
            f"PASSED RULE 9 (LIQUIDITY GATE): Position is {participation_pct:.2%} of daily volume. "
            f"Clearable in {s:.1f} sessions (within {max_days:.1f} session limit)."
        )

    return passed, reason, metrics


if __name__ == "__main__":
    print("=== LIQUIDITY GATE VALIDATION SUITE ===")
    test_cases = [
        ("CROPSTER", 15_735_454, 12_560, 5.0),
        ("CCDL",     29_339_415, 30_000, 5.0),
        ("GATECH",      268_977, 30_000, 5.0),
        ("CHANDRIMA",     6_355,  4_500, 2.0),
    ]

    for name, vol, pos, band in test_cases:
        ok, r, m = evaluate_liquidity_gate(pos, vol, circuit_band_pct=band)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name:<10} | Pos: {pos:>6,} | Vol: {vol:>10,} | Partic: {m['participation_pct']:>6.2f}% | Sess: {m['sessions_to_exit']:>5.1f} | MaxAllowed: {m['max_allowed_shares']:>7,}")

    # Specific Assertion on CHANDRIMA (Must FAIL: 70.8% participation)
    ok_chand, _, m_chand = evaluate_liquidity_gate(4500, 6355, 2.0)
    assert not ok_chand, "CHANDRIMA 4,500 shares must FAIL liquidity gate!"
    assert m_chand["max_allowed_shares"] == 1906, f"Expected 1906 max shares, got {m_chand['max_allowed_shares']}"

    # Specific Assertion on CROPSTER (Must PASS: 0.08% participation)
    ok_crop, _, _ = evaluate_liquidity_gate(12560, 15735454, 5.0)
    assert ok_crop, "CROPSTER 12,560 shares must PASS liquidity gate!"

    # Test 3: Circuit Lock Safeguard (Claude Condition)
    ok_lock, r_lock, _ = evaluate_liquidity_gate(1000, 50000, 5.0, is_locked_circuit=True)
    assert not ok_lock, "Locked circuit must FAIL liquidity gate!"
    assert "locked at circuit" in r_lock
    print(f"[PASS] Circuit Lock Safeguard Verified: {r_lock[:60]}...")

    # Test 4: Severe Volume Evaporation Safeguard (Claude Condition)
    # Day volume 5,000 vs 20d avg 100,000 (5% of normal -> collapsed!)
    ok_evap, r_evap, _ = evaluate_liquidity_gate(100, 5000, 5.0, avg_20d_volume=100000)
    assert not ok_evap, "Severe volume evaporation must FAIL liquidity gate!"
    assert "volume evaporation" in r_evap
    print(f"[PASS] Volume Evaporation Safeguard Verified: {r_evap[:60]}...")

    print("\nALL LIQUIDITY GATE TESTS PASSED 100%!")
