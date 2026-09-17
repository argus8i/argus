"""
accumulation_screener.py - Pre-Circuit Accumulation Screener
Implementation of screener_spec.md (Claude).
Build target: Antigravity.
Fulfills Q6 from shared/04_OPEN_QUESTIONS.md.
"""

import math
import os
import sys
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Set, Tuple
import numpy as np

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import (
    normalize_series,
    normalize_surveillance,
    SurveillanceStatus,
    SecuritySeries,
    calculate_bse_circuit_bands
)


@dataclass
class DailyCandle:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    delivery_pct: float
    spread_pct: Optional[float] = None  # Fail-closed: Must be explicitly verified (ChatGPT Loophole 4)
    bid_depth_ratio: Optional[float] = None  # resting bids / traded volume (Claude Stress Test C)


@dataclass
class TickerData:
    symbol: str
    series: str
    surveillance_flags: Set[str]
    history: List[DailyCandle]
    surveillance_verified: bool = False  # Fail-closed: Empty set cannot pass without explicit verification
    circuit_band_pct: float = 20.0       # Dynamic exchange circuit band (e.g. 2%, 5%, 10%, 20%)
    market_cap_cr: Optional[float] = None # Track 1 boundary: Mcap < Rs 500 Cr


class AccumulationScreener:
    """Screens for early accumulation & markup before the circuit phase begins."""

    def __init__(
        self,
        min_price: float = 10.0,
        min_turnover_20d: float = 5000000.0, # Rs 50 Lakh
        min_vol_ratio: float = 3.0,
        min_deliv_pct: float = 0.35,
        min_run_pct: float = 0.15,
        max_run_pct: float = 0.40,
    ):
        self.min_price = min_price
        self.min_turnover = min_turnover_20d
        self.min_vol_ratio = min_vol_ratio
        self.min_deliv_pct = min_deliv_pct
        self.min_run_pct = min_run_pct
        self.max_run_pct = max_run_pct

    def evaluate_ticker(
        self,
        ticker: TickerData,
        proposed_shares: Optional[int] = None,
        rupees_risk_budget: float = 5000.0
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        candles = ticker.history
        if len(candles) < 80:
            return False, "INSUFFICIENT_DATA (Need >= 80 sessions)", None

        latest = candles[-1]

        # Stage 1: Hard Disqualifiers
        # Track 1 Boundary: Market Cap < Rs 500 Cr (Fail-Closed: Mcap must be verified, positive finite, and < 500 Cr)
        if (
            ticker.market_cap_cr is None
            or not isinstance(ticker.market_cap_cr, (int, float))
            or not math.isfinite(ticker.market_cap_cr)
            or ticker.market_cap_cr <= 0
        ):
            return False, "FAILED: Market Cap unverified or missing (Track 1 requires verified Mcap < Rs 500 Cr)", None

        if ticker.market_cap_cr >= 500.0:
            return False, f"FAILED: Market Cap Rs {ticker.market_cap_cr:.1f} Cr exceeds Track 1 ceiling (<Rs 500 Cr)", None

        # Rule 2: Price Floor
        if latest.close < self.min_price:
            return False, f"FAILED: Price below Rs {self.min_price:.2f} floor ({latest.close:.2f})", None

        # Rule 6 & Broker Gate: Series normalization
        norm_series = normalize_series(ticker.series)
        if norm_series not in (SecuritySeries.EQ,):
            return False, f"FAILED: Restricted or unverified series ({ticker.series} -> {norm_series.value} != EQ)", None

        # Rule 6 Surveillance Gate: Fail-closed alias normalization & explicit verification
        if not ticker.surveillance_verified:
            return False, "FAILED: Surveillance status unverified (surveillance_verified=False). Fails closed.", None

        if ticker.surveillance_flags:
            for flag in ticker.surveillance_flags:
                norm_surv = normalize_surveillance(flag)
                if norm_surv != SurveillanceStatus.NONE:
                    return False, f"FAILED: Active surveillance ({flag} -> {norm_surv.value})", None

        # Rule 7 Setup Gate: Tight spread check (<1.0%, unified units)
        if latest.spread_pct is None or np.isnan(latest.spread_pct):
            return False, "FAILED: Spread data missing or NaN. Rule 7 requires verified tight spread < 1.0%", None

        # Unify units: if >= 0.10, treat as percentage (0.40% -> 0.004); if < 0.10, treat as decimal fraction
        spread_fraction = latest.spread_pct / 100.0 if latest.spread_pct >= 0.10 else latest.spread_pct
        if spread_fraction <= 0:
            return False, "FAILED: Non-positive bid-ask spread reported.", None
        if spread_fraction >= 0.01:
            return False, f"FAILED: Spread {spread_fraction:.2%} >= 1.0% (Rule 7 requires tight spread < 1.0%)", None

        # Rule 7 Pre-Circuit Proximity Gate: Reject if within 3 ticks or top 15% of circuit band
        if len(candles) >= 2:
            prev_close = candles[-2].close
            uc_prev, lc_prev = calculate_bse_circuit_bands(prev_close, ticker.circuit_band_pct)
            if uc_prev > 0:
                dist_ticks = round((uc_prev - latest.close) / 0.01, 2)
                band_width = max(uc_prev - lc_prev, 0.01)
                band_remaining = (uc_prev - latest.close) / band_width
                if latest.close >= uc_prev or dist_ticks < 3.0 or band_remaining < 0.15:
                    return False, (
                        f"FAILED: Price Rs {latest.close:.2f} is at or near Upper Circuit ceiling "
                        f"(UC Rs {uc_prev:.2f}, {dist_ticks:.0f} ticks away, {band_remaining:.1%} of band remaining). "
                        "Rule 7 permits entry only in pre-circuit accumulation."
                    ), None

        # Claude Stress Test C: Operator Bid Wall Spoofing Check
        if latest.bid_depth_ratio is not None and latest.bid_depth_ratio > 15.0:
            return False, f"FAILED: Operator bid wall spoofing risk ({latest.bid_depth_ratio:.1f}x resting bids vs volume)", None

        # Check for any locked day in last 5 sessions (high == low)
        last_5 = candles[-5:]
        if any(c.high == c.low for c in last_5):
            return False, "FAILED: Circuit lock detected in last 5 sessions (high == low)", None

        # Check median intraday range over last 5 sessions
        ranges = [(c.high - c.low) / max(c.close, 0.01) for c in last_5]
        if np.median(ranges) < 0.03:
            return False, f"FAILED: Low intraday volatility/range ({np.median(ranges):.2%})", None

        # Check 20-day average turnover
        turnovers = [c.close * c.volume for c in candles[-20:]]
        avg_turnover = np.mean(turnovers)
        if avg_turnover < self.min_turnover:
            return False, f"FAILED: Turnover < Rs 50 Lakh (Avg: Rs {avg_turnover:,.0f})", None

        # Stage 2: The Setup
        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        base_60d = np.median(closes[-65:-5])
        run_pct = (latest.close / base_60d) - 1.0

        if not (self.min_run_pct <= run_pct <= self.max_run_pct):
            return False, f"FAILED: Run percentage ({run_pct:.1%}) outside window [{self.min_run_pct:.0%}, {self.max_run_pct:.0%}]", None

        # Volume Expansion: Median of recent 20 vs uncontaminated base (sessions -80 to -20)
        vol_recent = np.median(volumes[-20:])
        vol_base = max(np.median(volumes[-80:-20]), 1.0)
        vol_ratio = vol_recent / vol_base

        if vol_ratio < self.min_vol_ratio:
            return False, f"FAILED: Volume ratio ({vol_ratio:.1f}x) < {self.min_vol_ratio:.1f}x", None

        # Delivery % Check: Fail-closed verification (Claude & ChatGPT Loophole)
        recent_delivs = [c.delivery_pct for c in candles[-10:]]
        base_delivs = [c.delivery_pct for c in candles[-40:-10]]

        if any(d is None or np.isnan(d) for d in recent_delivs) or any(d is None or np.isnan(d) for d in base_delivs):
            return False, "FAILED: Missing delivery % data. Official exchange delivery position archives required (fails closed).", None

        deliv_now = np.median(recent_delivs)
        deliv_before = np.median(base_delivs)

        if deliv_now < self.min_deliv_pct:
            return False, f"FAILED: Delivery % ({deliv_now:.1%}) < {self.min_deliv_pct:.1%}", None

        if deliv_now <= deliv_before:
            return False, f"FAILED: Delivery % not expanding ({deliv_now:.1%} <= {deliv_before:.1%})", None

        # Mandatory Sizing Evaluation (Rule 5 & Rule 9)
        from antigravity.models.risk_calculator import CircuitRiskCalculator
        sizing = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=rupees_risk_budget,
            stock_price=latest.close,
            daily_volume=latest.volume
        )
        if sizing["max_shares"] <= 0:
            return False, f"FAILED: Position sizing returned 0 shares ({sizing.get('constrained_by')}: {sizing.get('error')})", None

        if proposed_shares is not None and proposed_shares > sizing["max_shares"]:
            return False, f"FAILED: Proposed shares ({proposed_shares}) exceeds max safe shares ({sizing['max_shares']})", None

        # Qualified!
        metrics = {
            "symbol": ticker.symbol,
            "close": latest.close,
            "run_pct": round(run_pct * 100, 2),
            "vol_ratio": round(vol_ratio, 2),
            "deliv_now": round(deliv_now * 100, 2),
            "deliv_before": round(deliv_before * 100, 2),
            "avg_turnover": round(avg_turnover, 0),
            "spread_pct": round(spread_fraction * 100, 2),
            "max_safe_shares": sizing["max_shares"],
            "constrained_by": sizing["constrained_by"],
        }
        return True, "QUALIFIED: Pre-circuit accumulation breakout", metrics


if __name__ == "__main__":
    print("--- ACCUMULATION SCREENER VALIDATION SUITE ---")
    screener = AccumulationScreener()

    # Validation Case 1: CROPSTER (Must FAIL: Price < 10 & circuit locks)
    cropster_history = [
        DailyCandle(f"2026-08-{i:02d}", 4.5, 4.5, 4.5, 4.5, 100000, 0.40) for i in range(1, 85)
    ]
    cropster_ticker = TickerData("CROPSTER", "T", {"ESM_STAGE_1"}, cropster_history)
    pass1, reason1, _ = screener.evaluate_ticker(cropster_ticker)
    print(f"Validation 1: CROPSTER -> Pass={pass1} | Reason: {reason1}")
    assert not pass1, "CROPSTER should never pass!"

    # Validation Case 2: CCDL (Must FAIL: Price < 10)
    ccdl_history = [
        DailyCandle(f"2026-08-{i:02d}", 1.3, 1.32, 1.25, 1.32, 5000000, 0.45) for i in range(1, 85)
    ]
    ccdl_ticker = TickerData("CCDL", "XT", {"ESM_STAGE_1"}, ccdl_history)
    pass2, reason2, _ = screener.evaluate_ticker(ccdl_ticker)
    print(f"Validation 2: CCDL -> Pass={pass2} | Reason: {reason2}")
    assert not pass2, "CCDL should never pass!"

    # Validation Case 3: GATECH-BE (Must FAIL: BE Series & Price < 10)
    gatech_history = [
        DailyCandle(f"2026-08-{i:02d}", 0.8, 0.82, 0.79, 0.80, 20000, 0.50) for i in range(1, 85)
    ]
    gatech_ticker = TickerData("GATECH-BE", "BE", {"ESM_STAGE_1"}, gatech_history)
    pass3, reason3, _ = screener.evaluate_ticker(gatech_ticker)
    print(f"Validation 3: GATECH-BE -> Pass={pass3} | Reason: {reason3}")
    assert not pass3, "GATECH-BE should never pass!"

    # Validation Case 4: CHANDRIMA in late July / early Aug (Tradeable Window: 6.50 -> 8.00)
    # Note: When price reached Rs 10+ with liquid base, rising delivery, no surveillance
    chandrima_valid_history = []
    # 60 days of base around 10.00
    for i in range(65):
        chandrima_valid_history.append(DailyCandle(f"Day_{i}", 10.0, 10.5, 9.8, 10.0, 500000, 0.30, spread_pct=0.005))
    # 20 days of steady markup to 12.50 (+25%), expanding volume, rising delivery
    for i in range(65, 85):
        price = 10.0 + (i - 65) * 0.125
        chandrima_valid_history.append(DailyCandle(f"Day_{i}", price, price + 0.5, price - 0.2, price, 2000000, 0.48, spread_pct=0.005))

    chandrima_setup = TickerData("CHANDRIMA_SETUP", "EQ", set(), chandrima_valid_history, surveillance_verified=True, circuit_band_pct=20.0, market_cap_cr=120.0)
    pass4, reason4, met4 = screener.evaluate_ticker(chandrima_setup)
    print(f"Validation 4: CHANDRIMA (Accumulation Phase) -> Pass={pass4} | Reason: {reason4}")
    print(f"  Metrics: {met4}")
    assert pass4, f"Clean accumulation setup should pass! Got: {reason4}"

    # Validation Case 5: Wide Spread >= 1.0% (Must FAIL)
    wide_spread_history = list(chandrima_valid_history[:-1]) + [
        DailyCandle("Day_84", 12.375, 12.875, 12.175, 12.375, 2000000, 0.48, spread_pct=0.015)
    ]
    wide_spread_ticker = TickerData("WIDE_SPREAD", "EQ", set(), wide_spread_history, surveillance_verified=True, market_cap_cr=120.0)
    pass5, reason5, _ = screener.evaluate_ticker(wide_spread_ticker)
    print(f"Validation 5: Wide Spread (1.5%) -> Pass={pass5} | Reason: {reason5}")
    assert not pass5, "Wide spread setup must fail!"

    # Validation Case 6: Price within 1.0% of Upper Circuit (Must FAIL)
    # prev_close = 12.25 -> For 5% band, UC = 12.86. If close is 12.80, distance is (12.86 - 12.80)/12.80 = 0.46% <= 1.0%
    near_uc_history = list(chandrima_valid_history[:-1]) + [
        DailyCandle("Day_84", 12.5, 12.80, 12.4, 12.80, 2000000, 0.48, spread_pct=0.005)
    ]
    near_uc_ticker = TickerData("NEAR_UC", "EQ", set(), near_uc_history, surveillance_verified=True, circuit_band_pct=5.0, market_cap_cr=120.0)
    pass6, reason6, _ = screener.evaluate_ticker(near_uc_ticker)
    print(f"Validation 6: Near UC Ceiling -> Pass={pass6} | Reason: {reason6}")
    assert not pass6, "Near UC ceiling must fail!"

    # Validation Case 7: ESM Alias in Surveillance Flags (Must FAIL)
    esm_alias_ticker = TickerData("ESM_ALIAS", "EQ", {"esm_stage_1"}, chandrima_valid_history, surveillance_verified=True, market_cap_cr=120.0)
    pass7, reason7, _ = screener.evaluate_ticker(esm_alias_ticker)
    print(f"Validation 7: Surveillance Alias -> Pass={pass7} | Reason: {reason7}")
    assert not pass7, "Surveillance alias must fail!"

    # Validation Case 8: Operator Bid Wall Spoofing (Must FAIL)
    spoof_history = list(chandrima_valid_history[:-1]) + [
        DailyCandle("Day_84", 12.375, 12.50, 12.175, 12.375, 2000000, 0.48, spread_pct=0.005, bid_depth_ratio=24.0)
    ]
    spoof_ticker = TickerData("SPOOF_TICKER", "EQ", set(), spoof_history, surveillance_verified=True, market_cap_cr=120.0)
    pass8, reason8, _ = screener.evaluate_ticker(spoof_ticker)
    print(f"Validation 8: Operator Bid Wall Spoof -> Pass={pass8} | Reason: {reason8}")
    assert not pass8, "Operator bid wall spoof must fail!"

    # Validation Case 9: Surveillance Unverified (Empty Set but surveillance_verified=False) (Must FAIL)
    unverified_ticker = TickerData("UNVERIFIED", "EQ", set(), chandrima_valid_history, surveillance_verified=False, market_cap_cr=120.0)
    pass9, reason9, _ = screener.evaluate_ticker(unverified_ticker)
    print(f"Validation 9: Unverified Surveillance -> Pass={pass9} | Reason: {reason9}")
    assert not pass9, "Unverified surveillance must fail closed!"

    # Validation Case 10: Missing Spread on Latest Candle (spread_pct=None) (Must FAIL)
    missing_spread_history = list(chandrima_valid_history[:-1]) + [
        DailyCandle("Day_84", 12.375, 12.50, 12.175, 12.375, 2000000, 0.48, spread_pct=None)
    ]
    missing_spread_ticker = TickerData("MISSING_SPREAD", "EQ", set(), missing_spread_history, surveillance_verified=True, market_cap_cr=120.0)
    pass10, reason10, _ = screener.evaluate_ticker(missing_spread_ticker)
    print(f"Validation 10: Missing Spread (None) -> Pass={pass10} | Reason: {reason10}")
    assert not pass10, "Missing spread must fail closed!"

    # Validation Case 11: Missing Delivery % in Pipeline History (Must FAIL)
    missing_deliv_history = list(chandrima_valid_history[:-1]) + [
        DailyCandle("Day_84", 12.375, 12.50, 12.175, 12.375, 2000000, None, spread_pct=0.005)
    ]
    missing_deliv_ticker = TickerData("MISSING_DELIV", "EQ", set(), missing_deliv_history, surveillance_verified=True, market_cap_cr=120.0)
    pass11, reason11, _ = screener.evaluate_ticker(missing_deliv_ticker)
    print(f"Validation 11: Missing Delivery % -> Pass={pass11} | Reason: {reason11}")
    assert not pass11, "Missing delivery % must fail closed!"
    assert "Missing delivery %" in reason11

    print("\nALL 11 ACCUMULATION SCREENER TESTS PASSED 100%!")
