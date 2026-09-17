"""
circuit_rules.py - Quantitative Circuit Cycle Classifier & Fail-Closed Signal Engine
Part of the Project Swing Trades framework.
Aligned with AGENTS.md ground rules, Claude's primary research, and ChatGPT red-team specifications.
"""

import os
import sys
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Tuple, Optional, List

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class CycleStage(str, Enum):
    STAGE_0_ACCUMULATION = "STAGE_0_ACCUMULATION"
    STAGE_1_BREAKOUT = "STAGE_1_BREAKOUT"
    STAGE_2_PARABOLIC = "STAGE_2_PARABOLIC"
    STAGE_3_DISTRIBUTION = "STAGE_3_DISTRIBUTION"
    STAGE_4_LC_LOCK = "STAGE_4_LC_LOCK"
    UNKNOWN = "UNKNOWN"


class TradeSignal(str, Enum):
    BUY_ACCUMULATION_BREAKOUT = "BUY_ACCUMULATION_BREAKOUT" # Pre-circuit Stage 0/1 breakout; two-sided liquid book (Rule 7)
    HOLD = "HOLD"                                           # Observing or managing existing position
    EXIT_PREEMPTIVE_INTO_UC = "EXIT_PREEMPTIVE_INTO_UC"     # Target (15-20%) reached; pre-emptive sell into UC buyer queue
    EMERGENCY_EXIT_ATTEMPT = "EMERGENCY_EXIT_ATTEMPT"       # Circuit broken or distribution detected; exit review
    CRITICAL_AVOID = "CRITICAL_AVOID"                       # Sub-Rs 10 floor, circuit ceiling, surveillance freeze (Rules 2, 3, 6)
    DATA_INVALID = "DATA_INVALID"                           # Missing fields, unverified surveillance, or malformed depth


class EntrySignal(str, Enum):
    BUY_ACCUMULATION_BREAKOUT = "BUY_ACCUMULATION_BREAKOUT" # Rule 7 qualified setup
    NO_ENTRY = "NO_ENTRY"                                   # Awaiting setup criteria (spread, volume, range, etc.)
    CRITICAL_AVOID = "CRITICAL_AVOID"                       # Disqualified by Rule 1, 2, 3, 6, 9
    DATA_INVALID = "DATA_INVALID"                           # Missing/unverified data


class PositionSignal(str, Enum):
    HOLD_POSITION = "HOLD_POSITION"                         # Normal holding within risk parameters
    EXIT_PREEMPTIVE_INTO_UC = "EXIT_PREEMPTIVE_INTO_UC"     # Target reached, exit into UC buyer queue
    MANDATORY_SURVEILLANCE_EXIT = "MANDATORY_SURVEILLANCE_EXIT" # Rule 6 / band revision: exit into earliest liquidity
    EMERGENCY_EXIT_ATTEMPT = "EMERGENCY_EXIT_ATTEMPT"       # Circuit cracked or distribution; exit review
    LC_EXIT_LOCKED = "LC_EXIT_LOCKED"                       # Bids = 0 at LC; exit locked out, zero counterparty liquidity
    DATA_INVALID = "DATA_INVALID"


class SurveillanceStatus(str, Enum):
    NONE = "NONE"
    ESM_STAGE_1 = "ESM_STAGE_1"
    ESM_STAGE_2 = "ESM_STAGE_2"
    GSM_STAGE_1 = "GSM_STAGE_1"
    GSM_STAGE_2 = "GSM_STAGE_2"
    GSM_STAGE_3 = "GSM_STAGE_3"
    GSM_STAGE_4 = "GSM_STAGE_4"
    ASM_SHORT_TERM = "ASM_SHORT_TERM"
    ASM_LONG_TERM = "ASM_LONG_TERM"
    UNKNOWN = "UNKNOWN"


class SecuritySeries(str, Enum):
    EQ = "EQ"
    BE = "BE"
    T = "T"
    XT = "XT"
    Z = "Z"
    UNKNOWN = "UNKNOWN"


class BrokerOrderState(str, Enum):
    """Broker RMS & Demat Authorization Lifecycle States (Decoupled from Market Execution)."""
    BROKER_INELIGIBLE = "BROKER_INELIGIBLE"                 # Broker RMS blocks order (e.g. BTST in T2T/ASM before demat credit)
    AUTH_REQUIRED = "AUTH_REQUIRED"                         # CDSL TPIN + OTP required (no DDPI on file)
    AUTH_ACTIVE = "AUTH_ACTIVE"                             # DDPI active or pre-auth completed for the trading day
    REJECTED = "REJECTED"                                   # Exchange or broker RMS rejected order entry
    ACCEPTED = "ACCEPTED"                                   # Order verified and placed onto exchange order book


class ExecutionState(str, Enum):
    """Authoritative Discrete 4-State Market Execution Model (AGENTS.md Rule 4)."""
    LOCKED_NO_BID = "LOCKED_NO_BID"                         # AGENTS.md Rule 4 canonical: 0 bids (on LC) or 0 offers (on UC). Contra fill prob = 0%
    QUEUED = "QUEUED"                                       # Waiting behind R resting shares in FIFO queue
    PARTIAL = "PARTIAL"                                     # Incoming turnover matches a portion of order quantity
    FILLED = "FILLED"                                       # Cumulative volume turnover exceeds queue rank + order size (V_cum >= R + Q_order)


# Backward-compatibility aliases for legacy code:
LOCKED_NO_COUNTERPARTY = ExecutionState.LOCKED_NO_BID


@dataclass
class BrokerOrderRequest:
    """Request payload for broker RMS and demat authorization evaluation."""
    ticker: str
    action: str                     # "BUY" or "SELL"
    shares: int
    holding_days: int = 0           # 0 = Day T (same day), 1 = Day T+1, etc.
    series: str = "EQ"
    surveillance_stage: str = "NONE"
    has_ddpi: bool = False
    is_authorized: bool = False
    available_margin: float = 100000.0
    required_margin: float = 0.0


def normalize_surveillance(val: Optional[str]) -> SurveillanceStatus:
    """Canonical alias normalizer for exchange surveillance categories. Fails closed to UNKNOWN."""
    if not val:
        return SurveillanceStatus.UNKNOWN
    v = val.strip().upper().replace("-", "_").replace(" ", "_").replace(":", "_")
    while "__" in v:
        v = v.replace("__", "_")
    if v in ["NONE", "NORMAL", "NIL", "CLEAN", "NO_SURVEILLANCE"]:
        return SurveillanceStatus.NONE
    if v in ["ESM_1", "ESM_STAGE_1", "ESM_STAGE1", "ESMSTAGE1", "STAGE_1"]:
        return SurveillanceStatus.ESM_STAGE_1
    if v in ["ESM_2", "ESM_STAGE_2", "ESM_STAGE2", "ESMSTAGE2", "STAGE_2"]:
        return SurveillanceStatus.ESM_STAGE_2
    if v in ["GSM_1", "GSM_STAGE_1", "GSM_STAGE1"]:
        return SurveillanceStatus.GSM_STAGE_1
    if v in ["GSM_2", "GSM_STAGE_2", "GSM_STAGE2"]:
        return SurveillanceStatus.GSM_STAGE_2
    if v in ["GSM_3", "GSM_STAGE_3", "GSM_STAGE3"]:
        return SurveillanceStatus.GSM_STAGE_3
    if v in ["GSM_4", "GSM_STAGE_4", "GSM_STAGE4"]:
        return SurveillanceStatus.GSM_STAGE_4
    if v in ["ASM_ST", "STASM", "ASM_SHORT_TERM", "SHORT_TERM_ASM"]:
        return SurveillanceStatus.ASM_SHORT_TERM
    if v in ["ASM_LT", "LTASM", "ASM_LONG_TERM", "LONG_TERM_ASM", "ASM"]:
        return SurveillanceStatus.ASM_LONG_TERM
    return SurveillanceStatus.UNKNOWN


def normalize_series(val: Optional[str]) -> SecuritySeries:
    """Canonical alias normalizer for settlement series. Fails closed to UNKNOWN."""
    if not val:
        return SecuritySeries.UNKNOWN
    v = val.strip().upper()
    if v in ["EQ", "BSE_A", "BSE_B", "BSE_X"]:
        return SecuritySeries.EQ
    if v in ["BE", "T2T"]:
        return SecuritySeries.BE
    if v in ["T"]:
        return SecuritySeries.T
    if v in ["XT", "BSE_XT"]:
        return SecuritySeries.XT
    if v in ["Z"]:
        return SecuritySeries.Z
    return SecuritySeries.UNKNOWN


@dataclass
class MarketDepthSnapshot:
    ticker: str
    price: float
    prev_close: float
    circuit_limit_pct: float
    total_bids: int
    total_offers: int
    day_volume: int
    avg_20d_volume: int
    high: Optional[float] = None
    low: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread_pct: Optional[float] = None
    consecutive_uc_days: int = 0
    series: Optional[str] = None
    surveillance_stage: Optional[str] = None
    data_quality_flags: List[str] = field(default_factory=list)


class CircuitRuleEngine:
    """Evaluates micro-cap circuit stocks and produces cycle classifications & fail-closed actionable signals."""

    PRICE_FLOOR_INR: float = 10.00  # Mandatory Rule 2
    TICK_SIZE_INR: float = 0.01

    @staticmethod
    def calculate_bse_bands(prev_close: float, band_pct: float) -> Tuple[float, float]:
        """Calculates exact BSE Upper and Lower circuit bands using inward tick truncation.
        
        Per BSE Consolidated Master Circular Equity Segment Item 1.6:
        - Lower price-band cannot be specified in negative (floored to 1 tick = Rs 0.01).
        - Limits are truncated inward to tick size (not nearest-tick rounded) to prevent exceeding band_pct.
        """
        raw_uc = prev_close * (1.0 + band_pct / 100.0)
        raw_lc = prev_close * (1.0 - band_pct / 100.0)

        # Inward truncation: UC truncates down to tick, LC truncates up to tick
        uc = math.floor(raw_uc * 100.0 + 1e-9) / 100.0
        lc = math.ceil(raw_lc * 100.0 - 1e-9) / 100.0

        # Floored at 1 tick (Rs 0.01)
        lc = max(0.01, lc)
        if band_pct > 0 and uc <= prev_close:
            uc = round(prev_close + CircuitRuleEngine.TICK_SIZE_INR, 2)

        return uc, lc

    @staticmethod
    def classify_stage(snap: MarketDepthSnapshot) -> CycleStage:
        """Classifies the market microstructure cycle stage."""
        if snap.prev_close <= 0 or snap.circuit_limit_pct <= 0:
            return CycleStage.UNKNOWN

        uc, lc = CircuitRuleEngine.calculate_bse_bands(snap.prev_close, snap.circuit_limit_pct)

        # Check Lower Circuit lock (price at LC and bids absent or negligible)
        if snap.price <= lc and snap.total_bids == 0:
            return CycleStage.STAGE_4_LC_LOCK

        # Check Upper Circuit lock (price at UC and offers absent or negligible)
        if snap.price >= uc:
            if snap.consecutive_uc_days <= 2:
                return CycleStage.STAGE_1_BREAKOUT
            elif 3 <= snap.consecutive_uc_days <= 4:
                return CycleStage.STAGE_2_PARABOLIC
            else:
                return CycleStage.STAGE_3_DISTRIBUTION

        # If previously in circuit run and now cracked
        if snap.consecutive_uc_days >= 3 and snap.price < uc:
            return CycleStage.STAGE_3_DISTRIBUTION

        return CycleStage.STAGE_0_ACCUMULATION

    @staticmethod
    def evaluate_signal(snap: MarketDepthSnapshot, proposed_shares: Optional[int] = None) -> Tuple[TradeSignal, str]:
        """Evaluates live market snapshot and returns actionable trade signal under fail-closed gates."""
        # -------------------------------------------------------------
        # GATE 0: Data Completeness & Sanity (Fail-Closed)
        # -------------------------------------------------------------
        # Gate 0: Strict Schema and Series / Surveillance Normalization
        if snap.price is None or snap.prev_close is None or snap.price <= 0 or snap.prev_close <= 0:
            return (TradeSignal.DATA_INVALID, "DATA_INVALID: Price and previous close must be positive numbers.")

        if snap.circuit_limit_pct is None or snap.circuit_limit_pct <= 0:
            return (TradeSignal.DATA_INVALID, "DATA_INVALID: Circuit limit percentage is missing or invalid.")

        norm_series = normalize_series(snap.series)
        norm_surv = normalize_surveillance(snap.surveillance_stage)

        if norm_series == SecuritySeries.UNKNOWN:
            return (TradeSignal.CRITICAL_AVOID, f"DATA_INVALID / FAIL-CLOSED: Unverified or missing security series ('{snap.series}').")

        if norm_surv == SurveillanceStatus.UNKNOWN:
            return (TradeSignal.CRITICAL_AVOID, f"DATA_INVALID / FAIL-CLOSED: Unverified or missing surveillance stage ('{snap.surveillance_stage}').")

        # -------------------------------------------------------------
        # GATE 1: Rule 2 Price Floor
        # -------------------------------------------------------------
        if snap.price < CircuitRuleEngine.PRICE_FLOOR_INR or snap.prev_close < CircuitRuleEngine.PRICE_FLOOR_INR:
            return (
                TradeSignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 2: Price Rs {snap.price:.2f} is below Rs 10.00 floor. Extreme tick distortion and surveillance risk.",
            )

        # -------------------------------------------------------------
        # GATE 2: Rule 6 Surveillance Pre-Emption & Series Restrictions
        # -------------------------------------------------------------
        if norm_series != SecuritySeries.EQ:
            return (
                TradeSignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 6: Restricted series ({norm_series.value}). Trade-to-trade settlement restricts intraday risk control.",
            )

        if norm_surv != SurveillanceStatus.NONE:
            return (
                TradeSignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 6: Active surveillance ({norm_surv.value}). Immediate freeze on new allocations.",
            )

        # -------------------------------------------------------------
        # GATE 2B: Rule 9 Liquidity & Participation Gate (Claude Spec)
        # -------------------------------------------------------------
        if proposed_shares is not None and proposed_shares > 0:
            from antigravity.models.liquidity_gate import evaluate_liquidity_gate
            ok_liq, reason_liq, _ = evaluate_liquidity_gate(
                position_shares=proposed_shares,
                daily_volume=snap.day_volume,
                circuit_band_pct=snap.circuit_limit_pct,
                max_days=2.0
            )
            if not ok_liq:
                return (TradeSignal.CRITICAL_AVOID, reason_liq)

        # Calculate exact exchange price bands
        uc, lc = CircuitRuleEngine.calculate_bse_bands(snap.prev_close, snap.circuit_limit_pct)

        # -------------------------------------------------------------
        # GATE 3: Pre-Circuit Entry Requirement & Circuit Ceiling Prohibition (Rule 3 & Rule 7)
        # -------------------------------------------------------------
        # If stock is locked at UC with 0 offers -> Rule 3 violation
        if snap.price >= uc and snap.total_offers == 0:
            return (
                TradeSignal.CRITICAL_AVOID,
                "DISQUALIFIED BY RULE 3: Stock locked at Upper Circuit with 0 offers. Adverse selection trap.",
            )

        # Rule 7 Setup requires PRE-CIRCUIT accumulation.
        # Expressed in ticks and band fraction (Claude 11-Sep refactor):
        # Disqualified if price >= UC, or < 3 ticks from UC, or within top 15% of the total circuit band width.
        dist_ticks = round((uc - snap.price) / CircuitRuleEngine.TICK_SIZE_INR, 2)
        band_width = max(uc - lc, CircuitRuleEngine.TICK_SIZE_INR)
        band_remaining = (uc - snap.price) / band_width

        if snap.price >= uc or dist_ticks < 3.0 or band_remaining < 0.15:
            return (
                TradeSignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 7: Price Rs {snap.price:.2f} is at or near Upper Circuit ceiling "
                f"(UC Rs {uc:.2f}, {dist_ticks:.0f} ticks away, {band_remaining:.1%} of band remaining). "
                "Rule 7 permits entries ONLY during pre-circuit accumulation bases, not on circuit ceiling test days.",
            )

        stage = CircuitRuleEngine.classify_stage(snap)

        # Trapped in Lower Circuit
        if stage == CycleStage.STAGE_4_LC_LOCK:
            return (
                TradeSignal.EMERGENCY_EXIT_ATTEMPT,
                f"LOWER CIRCUIT LOCK: 0 bids at Rs {lc:.2f}. Counterparty liquidity is 0%. Stop loss cannot execute. Exit review required; subject to broker T+1 unlock and call auction availability.",
            )

        # Distribution detected
        if stage == CycleStage.STAGE_3_DISTRIBUTION:
            return (
                TradeSignal.EXIT_PREEMPTIVE_INTO_UC,
                "DISTRIBUTION DETECTED: Circuit broken or excessive consecutive circuit days. Pre-emptive exit advised.",
            )

        # Pre-emptive profit taking window
        if stage == CycleStage.STAGE_2_PARABOLIC:
            if snap.consecutive_uc_days >= 3:
                return (
                    TradeSignal.EXIT_PREEMPTIVE_INTO_UC,
                    f"PRE-EMPTIVE PROFIT EXIT (Rule 7): Day {snap.consecutive_uc_days} of UC run. Target gain reached. "
                    "Submit sell order to interact with resting Upper Circuit buyer queue.",
                )
            return (
                TradeSignal.HOLD,
                f"STAGE 2 PARABOLIC: Day {snap.consecutive_uc_days}. Hold with trailing profit mindset.",
            )

        # -------------------------------------------------------------
        # GATE 4: Rule 7 Pre-Circuit Breakout Verification
        # -------------------------------------------------------------
        if stage in [CycleStage.STAGE_0_ACCUMULATION, CycleStage.STAGE_1_BREAKOUT]:
            # 1. Order book two-sided verification
            if snap.total_bids <= 0 or snap.total_offers <= 0:
                return (TradeSignal.HOLD, "Order book is one-sided or unpopulated (bids or offers == 0). Awaiting two-sided depth.")

            # Quote sanity check if best prices are available
            if snap.best_bid is not None and snap.best_ask is not None:
                if snap.best_bid <= 0 or snap.best_ask <= 0:
                    return (TradeSignal.DATA_INVALID, "DATA_INVALID: Best bid or ask is non-positive.")
                if snap.best_bid >= snap.best_ask:
                    return (TradeSignal.DATA_INVALID, f"DATA_INVALID: Crossed or locked book (Bid {snap.best_bid} >= Ask {snap.best_ask}).")

            # 2. Spread validation (must be finite, strictly positive and < 1.0%)
            calc_spread: Optional[float] = None
            if snap.best_bid is not None and snap.best_ask is not None:
                calc_spread = round(((snap.best_ask - snap.best_bid) / snap.best_bid) * 100.0, 3)
            elif snap.spread_pct is not None:
                calc_spread = snap.spread_pct

            if calc_spread is None or math.isnan(calc_spread) or math.isinf(calc_spread):
                return (TradeSignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Spread data unavailable or non-finite (<1% required).")
            if calc_spread <= 0:
                return (TradeSignal.DATA_INVALID, "DATA_INVALID: Non-positive bid-ask spread reported.")
            if calc_spread >= 1.0:
                return (TradeSignal.HOLD, f"Spread {calc_spread:.2f}% exceeds 1.0% setup ceiling. Awaiting tighter book.")

            # 3. Daily Range requirement (Rule 7 mandates daily range >= 3%, high/low mandatory)
            if snap.high is None or snap.low is None or math.isnan(snap.high) or math.isnan(snap.low):
                return (TradeSignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Daily high and low required to verify Rule 7 range.")
            if snap.low <= 0 or snap.high < snap.low:
                return (TradeSignal.DATA_INVALID, f"DATA_INVALID: Daily low ({snap.low}) must be positive and <= high ({snap.high}).")
            daily_range_pct = round(((snap.high - snap.low) / snap.low) * 100.0, 2)
            if daily_range_pct < 3.0:
                return (TradeSignal.HOLD, f"Daily range {daily_range_pct:.2f}% < 3.0% minimum setup requirement.")

            # 4. Volume expansion requirement (20d volume expanding >= 3x, 20d volume mandatory)
            if snap.avg_20d_volume is None or math.isnan(snap.avg_20d_volume) or snap.avg_20d_volume <= 0:
                return (TradeSignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Positive 20-day average volume required to verify volume expansion.")
            vol_expansion = snap.day_volume / snap.avg_20d_volume
            if vol_expansion < 3.0:
                return (TradeSignal.HOLD, f"Volume expansion {vol_expansion:.1f}x < 3.0x threshold. Awaiting volume ignition.")

            # 5. Operator Bid Wall / Spoofing check (Claude Stress Test C)
            if snap.day_volume > 0:
                bid_wall_ratio = snap.total_bids / snap.day_volume
                if bid_wall_ratio > 15.0:
                    return (
                        TradeSignal.HOLD,
                        f"OPERATOR_SPOOF_RISK: Resting bids ({snap.total_bids:,}) are {bid_wall_ratio:.1f}x daily volume. "
                        "Suspected operator bid wall / spoof manipulation.",
                    )

            # All gates passed: Qualified Rule 7 Setup
            return (
                TradeSignal.BUY_ACCUMULATION_BREAKOUT,
                f"QUALIFIED SETUP (Rule 7): Two-sided book verified, volume expanding {vol_expansion:.1f}x (>=3x), "
                f"spread {calc_spread:.2f}% (<1.0%), distance to UC {dist_ticks:.0f} ticks ({band_remaining:.1%} of band). Target +15% to +20% pre-emptive exit on Day 3/4.",
            )

        return (TradeSignal.HOLD, "Neutral state. Maintain observation protocol.")

    @staticmethod
    def evaluate_entry_signal(snap: MarketDepthSnapshot, proposed_shares: Optional[int] = None) -> Tuple[EntrySignal, str]:
        """Evaluates prospective entry candidacy under fail-closed gates (Rules 1, 2, 3, 6, 7, 9).
        
        Returns:
            (EntrySignal.BUY_ACCUMULATION_BREAKOUT, reason) if qualified.
            (EntrySignal.CRITICAL_AVOID, reason) if prohibited by hard rules.
            (EntrySignal.NO_ENTRY, reason) if setup criteria (spread, volume, range) not met.
            (EntrySignal.DATA_INVALID, reason) if inputs are missing, NaN, or unverified.
        """
        # Gate 0: Schema & Data Sanity
        if (
            snap.price is None
            or snap.prev_close is None
            or not math.isfinite(snap.price)
            or not math.isfinite(snap.prev_close)
            or snap.price <= 0
            or snap.prev_close <= 0
        ):
            return (EntrySignal.DATA_INVALID, "DATA_INVALID: Price and previous close must be positive finite numbers.")

        if snap.circuit_limit_pct is None or not math.isfinite(snap.circuit_limit_pct) or snap.circuit_limit_pct <= 0:
            return (EntrySignal.DATA_INVALID, "DATA_INVALID: Circuit limit percentage is missing or invalid.")

        norm_series = normalize_series(snap.series)
        norm_surv = normalize_surveillance(snap.surveillance_stage)

        if norm_series == SecuritySeries.UNKNOWN:
            return (EntrySignal.CRITICAL_AVOID, f"DATA_INVALID / FAIL-CLOSED: Unverified or missing security series ('{snap.series}').")

        if norm_surv == SurveillanceStatus.UNKNOWN:
            return (EntrySignal.CRITICAL_AVOID, f"DATA_INVALID / FAIL-CLOSED: Unverified or missing surveillance stage ('{snap.surveillance_stage}').")

        # Gate 1: Rule 2 Price Floor
        if not math.isfinite(snap.price) or snap.price < CircuitRuleEngine.PRICE_FLOOR_INR or snap.prev_close < CircuitRuleEngine.PRICE_FLOOR_INR:
            return (
                EntrySignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 2: Price Rs {snap.price:.2f} is below Rs 10.00 floor. Extreme tick distortion and surveillance risk.",
            )

        # Gate 2: Rule 6 Surveillance Pre-Emption & Series Restrictions
        if norm_series != SecuritySeries.EQ:
            return (
                EntrySignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 6: Restricted series ({norm_series.value}). Trade-to-trade settlement restricts intraday risk control.",
            )

        if norm_surv != SurveillanceStatus.NONE:
            return (
                EntrySignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 6: Active surveillance ({norm_surv.value}). Immediate freeze on new allocations.",
            )

        # Gate 2B: Rule 9 Liquidity & Participation Gate (MANDATORY FOR ENTRY)
        if proposed_shares is None or proposed_shares <= 0:
            return (
                EntrySignal.DATA_INVALID,
                "DATA_INVALID / FAIL-CLOSED: Missing or non-positive proposed_shares. Rule 9 liquidity evaluation is mandatory for entry."
            )

        from antigravity.models.liquidity_gate import evaluate_liquidity_gate
        ok_liq, reason_liq, _ = evaluate_liquidity_gate(
            position_shares=proposed_shares,
            daily_volume=snap.day_volume,
            circuit_band_pct=snap.circuit_limit_pct,
            max_days=2.0
        )
        if not ok_liq:
            return (EntrySignal.CRITICAL_AVOID, reason_liq)

        # Calculate exact exchange price bands
        uc, lc = CircuitRuleEngine.calculate_bse_bands(snap.prev_close, snap.circuit_limit_pct)

        # Gate 3: Rule 3 Locked UC & Rule 7 Pre-Circuit Proximity (Tick & Band-Fraction Scaled)
        if snap.price >= uc and snap.total_offers == 0:
            return (
                EntrySignal.CRITICAL_AVOID,
                "DISQUALIFIED BY RULE 3: Stock locked at Upper Circuit with 0 offers. Adverse selection trap.",
            )

        dist_ticks = round((uc - snap.price) / CircuitRuleEngine.TICK_SIZE_INR, 2)
        band_width = max(uc - lc, CircuitRuleEngine.TICK_SIZE_INR)
        band_remaining = (uc - snap.price) / band_width

        if snap.price >= uc or dist_ticks < 3.0 or band_remaining < 0.15:
            return (
                EntrySignal.CRITICAL_AVOID,
                f"DISQUALIFIED BY RULE 7: Price Rs {snap.price:.2f} is at or near Upper Circuit ceiling "
                f"(UC Rs {uc:.2f}, {dist_ticks:.0f} ticks away, {band_remaining:.1%} of band remaining). "
                "Rule 7 permits entries ONLY during pre-circuit accumulation bases.",
            )

        # Gate 4: Order Book Two-Sided Verification
        if snap.total_bids <= 0 or snap.total_offers <= 0:
            return (EntrySignal.NO_ENTRY, "NO_ENTRY: Order book is one-sided or unpopulated (bids or offers == 0). Awaiting two-sided depth.")

        if snap.best_bid is not None and snap.best_ask is not None:
            if snap.best_bid <= 0 or snap.best_ask <= 0:
                return (EntrySignal.DATA_INVALID, "DATA_INVALID: Best bid or ask is non-positive.")
            if snap.best_bid >= snap.best_ask:
                return (EntrySignal.DATA_INVALID, f"DATA_INVALID: Crossed or locked book (Bid {snap.best_bid} >= Ask {snap.best_ask}).")

        # Spread validation (< 1.0%, Mandatory)
        if snap.spread_pct is not None and (math.isnan(snap.spread_pct) or math.isinf(snap.spread_pct)):
            return (EntrySignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Spread data is NaN or Inf. Cannot verify tight-spread requirement (<1%).")

        calc_spread: Optional[float] = None
        if snap.best_bid is not None and snap.best_ask is not None:
            calc_spread = round(((snap.best_ask - snap.best_bid) / snap.best_bid) * 100.0, 3)
        elif snap.spread_pct is not None:
            calc_spread = snap.spread_pct

        if calc_spread is None or math.isnan(calc_spread) or math.isinf(calc_spread):
            return (EntrySignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Spread data unavailable or NaN/Inf. Cannot verify tight-spread requirement (<1%).")
        if calc_spread <= 0:
            return (EntrySignal.DATA_INVALID, "DATA_INVALID: Non-positive bid-ask spread reported.")
        if calc_spread >= 1.0:
            return (EntrySignal.NO_ENTRY, f"NO_ENTRY: Spread {calc_spread:.2f}% exceeds 1.0% setup ceiling. Awaiting tighter book.")

        # Daily Range requirement (>= 3.0%, Mandatory under Rule 7)
        if snap.high is None or snap.low is None or math.isnan(snap.high) or math.isnan(snap.low):
            return (EntrySignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Daily high/low missing or NaN. Range requirement (>=3.0%) is mandatory under Rule 7.")
        if snap.low <= 0 or snap.high < snap.low:
            return (EntrySignal.DATA_INVALID, f"DATA_INVALID: Daily low ({snap.low}) must be positive and <= high ({snap.high}).")
        daily_range_pct = round(((snap.high - snap.low) / snap.low) * 100.0, 2)
        if daily_range_pct < 3.0:
            return (EntrySignal.NO_ENTRY, f"NO_ENTRY: Daily range {daily_range_pct:.2f}% < 3.0% minimum setup requirement.")

        # Volume expansion requirement (>= 3x, Mandatory under Rule 7)
        if snap.avg_20d_volume is None or math.isnan(snap.avg_20d_volume) or snap.avg_20d_volume <= 0:
            return (EntrySignal.DATA_INVALID, "DATA_INVALID / FAIL-CLOSED: Positive 20-day average volume required to verify volume expansion (>=3x).")
        vol_expansion = snap.day_volume / snap.avg_20d_volume
        if vol_expansion < 3.0:
            return (EntrySignal.NO_ENTRY, f"NO_ENTRY: Volume expansion {vol_expansion:.1f}x < 3.0x threshold. Awaiting volume ignition.")

        # Operator Bid Wall / Spoofing check
        if snap.day_volume > 0:
            bid_wall_ratio = snap.total_bids / snap.day_volume
            if bid_wall_ratio > 15.0:
                return (
                    EntrySignal.NO_ENTRY,
                    f"NO_ENTRY (OPERATOR_SPOOF_RISK): Resting bids ({snap.total_bids:,}) are {bid_wall_ratio:.1f}x daily volume. "
                    "Suspected operator bid wall / spoof manipulation.",
                )

        return (
            EntrySignal.BUY_ACCUMULATION_BREAKOUT,
            f"QUALIFIED SETUP (Rule 7): Two-sided book verified, volume expanding {vol_expansion:.1f}x (>=3x), "
            f"spread {calc_spread:.2f}% (<1.0%), distance to UC {dist_ticks:.0f} ticks ({band_remaining:.1%} of band). Target +15% to +20% pre-emptive exit on Day 3/4.",
        )

    @staticmethod
    def evaluate_position_signal(
        snap: MarketDepthSnapshot,
        entry_price: float,
        holding_days: int,
        current_shares: int = 0
    ) -> Tuple[PositionSignal, str]:
        """Evaluates ongoing management and exit execution for an existing position under AGENTS.md Precedence Hierarchy.
        
        Rule 10 Hierarchy:
        1. Rule 1: Cash gate.
        2. Rule 6: Surveillance escalation or Band Tightening -> Mandatory Immediate Exit.
           STRICTLY OVERRIDES Rule 7 profit targets.
        3. Rule 2: Price < Rs 10 floor -> Mandatory exit.
        4. Lower Circuit Lock (Total Bids == 0) -> Exit locked out; stop loss cannot execute.
        5. Distribution cracked -> Emergency exit.
        6. Target +15% or Day 3/4 UC with positive gain -> Pre-emptive exit into UC buyer queue.
        7. Otherwise -> Hold position / Stop loss review.
        """
        if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
            return (PositionSignal.DATA_INVALID, "DATA_INVALID: entry_price must be a positive finite number.")

        if (
            snap.price is None
            or snap.prev_close is None
            or not math.isfinite(snap.price)
            or not math.isfinite(snap.prev_close)
            or snap.price <= 0
            or snap.prev_close <= 0
        ):
            return (PositionSignal.DATA_INVALID, "DATA_INVALID: Price and previous close must be positive finite numbers.")

        norm_surv = normalize_surveillance(snap.surveillance_stage)
        norm_series = normalize_series(snap.series)

        # Fail closed on UNKNOWN surveillance or UNKNOWN series in position evaluation
        if norm_surv == SurveillanceStatus.UNKNOWN:
            return (
                PositionSignal.DATA_INVALID,
                "DATA_INVALID / FAIL-CLOSED: Surveillance stage is UNKNOWN. Cannot verify exit safety."
            )

        if norm_series == SecuritySeries.UNKNOWN:
            return (
                PositionSignal.DATA_INVALID,
                "DATA_INVALID / FAIL-CLOSED: Security series is UNKNOWN. Cannot verify settlement rules."
            )

        # Rule 6 Precedence Override (Rule 10): Surveillance escalation or band cut
        if norm_surv not in [SurveillanceStatus.NONE]:
            return (
                PositionSignal.MANDATORY_SURVEILLANCE_EXIT,
                f"RULE 6 MANDATORY SURVEILLANCE EXIT: Security entered {norm_surv.value}. "
                "Strictly overrides Rule 7 hold targets. Exit immediately into earliest available liquidity.",
            )

        if norm_series not in [SecuritySeries.EQ]:
            return (
                PositionSignal.MANDATORY_SURVEILLANCE_EXIT,
                f"RULE 6 MANDATORY SURVEILLANCE EXIT: Security series changed to {norm_series.value}. "
                "Trade-to-trade settlement restricts intraday risk control. Exit immediately.",
            )

        # Calculate exact exchange price bands
        uc, lc = CircuitRuleEngine.calculate_bse_bands(snap.prev_close, snap.circuit_limit_pct)

        # Calculate gain percentage
        gain_pct = round(((snap.price - entry_price) / entry_price) * 100.0, 2)

        # Rule 2: Price Floor Violation during holding
        if snap.price < CircuitRuleEngine.PRICE_FLOOR_INR:
            return (
                PositionSignal.MANDATORY_SURVEILLANCE_EXIT,
                f"RULE 2 MANDATORY EXIT: Price Rs {snap.price:.2f} fell below Rs 10.00 floor. Extreme tick-size distortion risk.",
            )

        # Lower Circuit Lockout: 0 bids (stop loss cannot execute continuously)
        if snap.total_bids == 0 or snap.price <= lc:
            return (
                PositionSignal.LC_EXIT_LOCKED,
                f"LC_EXIT_LOCKED / LOWER CIRCUIT LOCKOUT: Total bids = 0 (or price Rs {snap.price:.2f} at LC Rs {lc:.2f}). "
                "Counterparty liquidity is 0%. Discrete 4-state execution: State = LOCKED_NO_BID. "
                "Stop losses cannot execute. Order queued behind resting sellers for next liquidity wave.",
            )

        # Target Achievement: Pre-Emptive Exit into Upper Circuit Buyer Queue
        # Pre-emptive profit exits into UC buyer queue strictly require positive gains (>= +10% on Day 3+ or >= +15%)
        if gain_pct >= 15.0 or (holding_days >= 3 and gain_pct >= 10.0):
            return (
                PositionSignal.EXIT_PREEMPTIVE_INTO_UC,
                f"PRE-EMPTIVE PROFIT EXIT (Rule 7): Gain +{gain_pct:.1f}% reached on Day {holding_days}. "
                f"Resting buyer queue available ({snap.total_bids:,} bids). "
                "Sell limit order submitted into buyer depth before operator distribution. Discrete State: QUEUED.",
            )

        # Negative returns on Day 3+ route to EMERGENCY_EXIT_ATTEMPT, NEVER pre-emptive profit exit
        if holding_days >= 3 and gain_pct < 0:
            return (
                PositionSignal.EMERGENCY_EXIT_ATTEMPT,
                f"STOP LOSS / EXIT REVIEW: Negative return ({gain_pct:.1f}%) on Day {holding_days}. "
                "Setup thesis invalidated. Attempt emergency exit into available two-sided liquidity.",
            )

        # Normal holding state
        return (
            PositionSignal.HOLD_POSITION,
            f"HOLD_POSITION: Position within normal operating parameters (Gain: +{gain_pct:.1f}%, Holding Day: {holding_days}).",
        )

    @staticmethod
    def estimate_execution_state(
        queue_rank: Optional[int],
        order_qty: int,
        contra_volume_at_limit: Optional[int],
        is_locked_zero_contra: bool = False
    ) -> Tuple[ExecutionState, Optional[int]]:
        """Authoritative implementation of AGENTS.md Rule 4 (Discrete 4-State Execution Modeling).
        
        This is the SINGLE AUTHORITATIVE execution state machine across the codebase.
        All other modules (such as queue_model.py) serve as diagnostic or predictive estimators.
        
        Evaluates FIFO execution state strictly using price-specific, post-order contra-volume:
          1. LOCKED_NO_BID: 0 bids (on LC) or 0 offers (on UC). Contra fill prob = 0%.
          2. QUEUED: Order accepted, waiting behind R resting shares in FIFO queue.
          3. PARTIAL: Incoming turnover matches a portion of order quantity.
          4. FILLED: Cumulative volume turnover exceeds queue rank + order size (V_cum >= R + Q_order).
        
        Args:
            queue_rank: Number of shares ahead in the queue at order placement.
            order_qty: Desired order quantity.
            contra_volume_at_limit: Executed contra-volume specifically at the limit price AFTER order acceptance.
            is_locked_zero_contra: True if the counterparty side of the book is completely empty.
            
        Returns:
            (ExecutionState, filled_shares)
        """
        if is_locked_zero_contra:
            return ExecutionState.LOCKED_NO_BID, 0

        if order_qty is None or order_qty <= 0:
            return ExecutionState.QUEUED, 0

        if queue_rank is None or contra_volume_at_limit is None:
            # Without queue rank and price-specific contra-volume, fill state is mathematically unknown
            return ExecutionState.QUEUED, None

        if (
            queue_rank < 0
            or contra_volume_at_limit < 0
            or not math.isfinite(queue_rank)
            or not math.isfinite(contra_volume_at_limit)
        ):
            return ExecutionState.QUEUED, 0

        if contra_volume_at_limit <= queue_rank:
            return ExecutionState.QUEUED, 0

        available_for_order = contra_volume_at_limit - queue_rank
        if available_for_order >= order_qty:
            return ExecutionState.FILLED, order_qty
        else:
            return ExecutionState.PARTIAL, int(available_for_order)

    @classmethod
    def evaluate_broker_order_state(cls, req: BrokerOrderRequest) -> Tuple[BrokerOrderState, str]:
        """
        Evaluates broker RMS eligibility and demat authorization lifecycle.
        Decoupled from exchange market execution states.
        Enforces Zerodha/SEBI settlement rules:
        - Action must be 'BUY' or 'SELL'.
        - Security series must not be UNKNOWN.
        - Shares must be positive.
        - Margins must be non-negative. BUY requires available >= required.
        - T2T/ESM/GSM/ASM scrips: Day T intraday sale is PROHIBITED (BROKER_INELIGIBLE).
        - Day T+1 sale permitted under delivery settlement, but requires CDSL TPIN (AUTH_REQUIRED) unless DDPI is active or pre-authorized.
        """
        if req.action.upper() not in ["BUY", "SELL"]:
            return (
                BrokerOrderState.REJECTED,
                f"REJECTED: Invalid order action '{req.action}'. Must be 'BUY' or 'SELL'."
            )

        norm_series = normalize_series(req.series)
        if norm_series == SecuritySeries.UNKNOWN:
            return (
                BrokerOrderState.REJECTED,
                f"REJECTED: Unknown or unverified security series '{req.series}'."
            )

        if req.shares <= 0:
            return (
                BrokerOrderState.REJECTED,
                f"REJECTED: Order shares ({req.shares}) must be positive."
            )

        if req.available_margin < 0 or req.required_margin < 0:
            return (
                BrokerOrderState.REJECTED,
                f"REJECTED: Negative margin values (Available: {req.available_margin}, Required: {req.required_margin}) are invalid."
            )

        if req.action.upper() == "BUY" and req.required_margin > req.available_margin:
            return (
                BrokerOrderState.REJECTED,
                f"REJECTED: Insufficient available margin (Required: {req.required_margin} > Available: {req.available_margin})."
            )

        norm_surv = normalize_surveillance(req.surveillance_stage)
        is_t2t = norm_series in [SecuritySeries.BE, SecuritySeries.T, SecuritySeries.XT] or norm_surv in [
            SurveillanceStatus.ESM_STAGE_1,
            SurveillanceStatus.ESM_STAGE_2,
            SurveillanceStatus.GSM_STAGE_1,
            SurveillanceStatus.GSM_STAGE_2,
            SurveillanceStatus.GSM_STAGE_3,
            SurveillanceStatus.GSM_STAGE_4,
        ]

        if req.action.upper() == "SELL":
            if is_t2t and req.holding_days == 0:
                return (
                    BrokerOrderState.BROKER_INELIGIBLE,
                    f"BROKER_INELIGIBLE: {req.ticker} is in Trade-to-Trade / Surveillance ({norm_series.value}/{norm_surv.value}). "
                    "Same-day intraday selling is prohibited by broker RMS."
                )
            if not req.has_ddpi and not req.is_authorized:
                return (
                    BrokerOrderState.AUTH_REQUIRED,
                    f"AUTH_REQUIRED: CDSL TPIN + OTP authorization required before order entry (no active DDPI)."
                )
        return (
            BrokerOrderState.ACCEPTED,
            f"ACCEPTED: Order meets broker RMS eligibility and demat authorization criteria."
        )

# Top-level module alias for external poller & screener imports
calculate_bse_circuit_bands = CircuitRuleEngine.calculate_bse_bands


if __name__ == "__main__":
    print("=== CIRCUIT RULE ENGINE RED-TEAM COMPLIANCE SUITE ===")

    # Test 1: BSE Inward Tick Truncation (CCDL Rs 1.32 close -> Rs 1.38 UC)
    uc_ccdl, lc_ccdl = CircuitRuleEngine.calculate_bse_bands(1.32, 5.0)
    print(f"Test 1 (CCDL Bands): UC = {uc_ccdl:.2f} (Expected 1.38), LC = {lc_ccdl:.2f} (Expected 1.26)")
    assert uc_ccdl == 1.38 and lc_ccdl == 1.26

    # Test 2: ChatGPT Counterexample 1 - ESM_STAGE_1 Alias Attack
    # Must FAIL CLOSED (CRITICAL_AVOID) even if spelled ESM_STAGE_1
    snap_esm_alias = MarketDepthSnapshot(
        ticker="SURV_ALIAS",
        price=20.00,
        prev_close=19.50,
        circuit_limit_pct=5.0,
        total_bids=50000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=100000,
        high=20.20,
        low=19.40,
        best_bid=19.95,
        best_ask=20.00,
        spread_pct=0.25,
        series="EQ",
        surveillance_stage="ESM_STAGE_1", # Alias attack
    )
    sig2, reason2 = CircuitRuleEngine.evaluate_signal(snap_esm_alias)
    print(f"Test 2 (ESM Alias Attack): Signal = {sig2.value} | Reason: {reason2}")
    assert sig2 == TradeSignal.CRITICAL_AVOID

    # Test 3: ChatGPT Counterexample 2 - Stock At Upper Circuit Ceiling with Nonzero Offers
    # Must NOT return BUY_ACCUMULATION_BREAKOUT (Rule 7 requires pre-circuit)
    snap_at_uc = MarketDepthSnapshot(
        ticker="AT_UC_STOCK",
        price=21.00, # UC for 20.00 @ 5% is 21.00
        prev_close=20.00,
        circuit_limit_pct=5.0,
        total_bids=500000,
        total_offers=10000, # Nonzero offers!
        day_volume=600000,
        avg_20d_volume=100000,
        high=21.00,
        low=20.00,
        best_bid=21.00,
        best_ask=21.00,
        spread_pct=0.0,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig3, reason3 = CircuitRuleEngine.evaluate_signal(snap_at_uc)
    print(f"Test 3 (At Upper Circuit): Signal = {sig3.value} | Reason: {reason3}")
    assert sig3 == TradeSignal.CRITICAL_AVOID

    # Test 4: Missing Surveillance Metadata (Fail-Closed)
    snap_missing_surv = MarketDepthSnapshot(
        ticker="MISSING_DATA",
        price=25.00,
        prev_close=24.00,
        circuit_limit_pct=10.0,
        total_bids=50000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=100000,
        series="EQ",
        surveillance_stage=None, # Missing!
    )
    sig4, reason4 = CircuitRuleEngine.evaluate_signal(snap_missing_surv)
    print(f"Test 4 (Missing Surveillance): Signal = {sig4.value} | Reason: {reason4}")
    assert sig4 == TradeSignal.CRITICAL_AVOID

    # Test 5: One-Sided Book (Offers > 0 but Bids == 0)
    snap_one_sided = MarketDepthSnapshot(
        ticker="ONE_SIDED",
        price=25.00,
        prev_close=24.00,
        circuit_limit_pct=10.0,
        total_bids=0, # Zero bids!
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=100000,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig5, reason5 = CircuitRuleEngine.evaluate_signal(snap_one_sided)
    print(f"Test 5 (One-Sided Book): Signal = {sig5.value} | Reason: {reason5}")
    assert sig5 == TradeSignal.HOLD

    # Test 6: Missing Spread (Must Not Default to Ideal Zero Spread)
    snap_missing_spread = MarketDepthSnapshot(
        ticker="NO_SPREAD",
        price=25.00,
        prev_close=24.00,
        circuit_limit_pct=10.0,
        total_bids=50000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=100000,
        high=25.20,
        low=24.20,
        spread_pct=None, # Missing!
        series="EQ",
        surveillance_stage="NONE",
    )
    sig6, reason6 = CircuitRuleEngine.evaluate_signal(snap_missing_spread)
    print(f"Test 6 (Missing Spread): Signal = {sig6.value} | Reason: {reason6}")
    assert sig6 == TradeSignal.DATA_INVALID

    # Test 7: Daily Range Too Narrow (<3.0%)
    snap_narrow_range = MarketDepthSnapshot(
        ticker="NARROW_RANGE",
        price=25.00,
        prev_close=24.80,
        circuit_limit_pct=10.0,
        total_bids=50000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=100000,
        high=25.10,
        low=24.90, # Range is (25.10 - 24.90) / 24.90 = 0.8% (<3%)
        best_bid=24.95,
        best_ask=25.05,
        spread_pct=0.40,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig7, reason7 = CircuitRuleEngine.evaluate_signal(snap_narrow_range)
    print(f"Test 7 (Narrow Range <3%): Signal = {sig7.value} | Reason: {reason7}")
    assert sig7 == TradeSignal.HOLD

    # Test 8: Valid Rule 7 Pre-Circuit Breakout (All Gates Passed)
    snap_valid = MarketDepthSnapshot(
        ticker="VALID_EQUITY",
        price=25.00,
        prev_close=24.20,
        circuit_limit_pct=10.0, # UC is 26.62; distance to UC is >1.0%
        total_bids=80000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=150000, # 4x expansion
        high=25.30,
        low=24.10, # Range = 4.97% (>3%)
        best_bid=24.95,
        best_ask=25.05, # Spread = 0.40% (<1.0%)
        series="EQ",
        surveillance_stage="NONE",
    )
    sig8, reason8 = CircuitRuleEngine.evaluate_signal(snap_valid)
    print(f"Test 8 (Valid Rule 7 Breakout): Signal = {sig8.value} | Reason: {reason8}")
    assert sig8 == TradeSignal.BUY_ACCUMULATION_BREAKOUT

    # Test 9: Realistic Queue Drain (Contra Volume at Price vs Unknown Bounds)
    q_state_unknown, q_fill_unknown = CircuitRuleEngine.estimate_execution_state(
        queue_rank=None,
        order_qty=10000,
        contra_volume_at_limit=None,
    )
    print(f"Test 9 (Unknown Queue Data): State = {q_state_unknown.value} | Fill = {q_fill_unknown}")
    assert q_state_unknown == ExecutionState.QUEUED and q_fill_unknown is None

    # Test 10: Rule 9 Liquidity & Participation Gate (Claude Loophole 1)
    # 4,500 shares in CHANDRIMA with 6,355 day volume = 70.8% participation -> MUST FAIL
    snap_chand_liq = MarketDepthSnapshot(
        ticker="CHANDRIMA_TEST",
        price=15.00,
        prev_close=14.50,
        circuit_limit_pct=5.0,
        total_bids=5000,
        total_offers=5000,
        day_volume=6355,
        avg_20d_volume=5000,
        high=15.10,
        low=14.50,
        best_bid=14.95,
        best_ask=15.05,
        spread_pct=0.67,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig10, reason10 = CircuitRuleEngine.evaluate_signal(snap_chand_liq, proposed_shares=4500)
    print(f"Test 10 (Rule 9 Liquidity Gate): Signal = {sig10.value} | Reason: {reason10}")
    assert sig10 == TradeSignal.CRITICAL_AVOID
    assert "RULE 9" in reason10

    # Test 11: Operator Bid Wall Spoof Check (Claude Stress Test C)
    # Resting bids 12,000,000 vs 500,000 daily volume = 24x -> MUST HOLD / REJECT
    snap_spoof = MarketDepthSnapshot(
        ticker="SPOOF_STOCK",
        price=25.00,
        prev_close=24.20,
        circuit_limit_pct=10.0,
        total_bids=12000000, # 24x daily volume!
        total_offers=50000,
        day_volume=500000,
        avg_20d_volume=100000,
        high=25.30,
        low=24.10,
        best_bid=24.95,
        best_ask=25.05,
        spread_pct=0.40,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig11, reason11 = CircuitRuleEngine.evaluate_signal(snap_spoof)
    print(f"Test 11 (Operator Bid Wall Spoof): Signal = {sig11.value} | Reason: {reason11}")
    assert sig11 == TradeSignal.HOLD
    assert "OPERATOR_SPOOF_RISK" in reason11

    # Test 12: evaluate_entry_signal on narrow range (ChatGPT Loophole 5)
    # Must return NO_ENTRY, NOT HOLD!
    entry_sig, entry_reason = CircuitRuleEngine.evaluate_entry_signal(snap_narrow_range, proposed_shares=1000)
    print(f"Test 12 (evaluate_entry_signal Narrow Range): Signal = {entry_sig.value} | Reason: {entry_reason}")
    assert entry_sig == EntrySignal.NO_ENTRY
    assert "NO_ENTRY" in entry_reason

    # Test 13: evaluate_position_signal with Rule 6 Precedence Override (Rule 10)
    # In position with +12% gain on Day 2, but stock placed in ESM_STAGE_1 -> MUST EXIT IMMEDIATELY
    snap_pos_surv = MarketDepthSnapshot(
        ticker="POS_ESM",
        price=22.40,
        prev_close=22.00,
        circuit_limit_pct=5.0,
        total_bids=10000,
        total_offers=5000,
        day_volume=200000,
        avg_20d_volume=50000,
        series="EQ",
        surveillance_stage="ESM_STAGE_1" # Escalation!
    )
    pos_sig13, pos_reason13 = CircuitRuleEngine.evaluate_position_signal(
        snap=snap_pos_surv,
        entry_price=20.00,
        holding_days=2,
        current_shares=1000
    )
    print(f"Test 13 (evaluate_position_signal Rule 6 Override): Signal = {pos_sig13.value} | Reason: {pos_reason13}")
    assert pos_sig13 == PositionSignal.MANDATORY_SURVEILLANCE_EXIT
    assert "MANDATORY SURVEILLANCE EXIT" in pos_reason13

    # Test 14: evaluate_position_signal Lower Circuit Lockout
    snap_pos_lc = MarketDepthSnapshot(
        ticker="POS_LC",
        price=19.00,
        prev_close=20.00,
        circuit_limit_pct=5.0, # LC is 19.00
        total_bids=0, # ZERO bids!
        total_offers=100000,
        day_volume=5000,
        avg_20d_volume=50000,
        series="EQ",
        surveillance_stage="NONE"
    )
    pos_sig14, pos_reason14 = CircuitRuleEngine.evaluate_position_signal(
        snap=snap_pos_lc,
        entry_price=20.00,
        holding_days=3,
        current_shares=1000
    )
    print(f"Test 14 (evaluate_position_signal LC Lockout): Signal = {pos_sig14.value} | Reason: {pos_reason14}")
    assert pos_sig14 == PositionSignal.LC_EXIT_LOCKED
    assert "LC_EXIT_LOCKED" in pos_reason14

    # Test 15: ExecutionState Purity & Exact Set Equality (AGENTS.md Rule 4)
    # Asserts that Broker RMS states (BROKER_INELIGIBLE, REJECTED, ACCEPTED) are completely pruned
    expected_market_states = {"LOCKED_NO_BID", "QUEUED", "PARTIAL", "FILLED"}
    actual_states = set(ExecutionState.__members__.keys())
    print(f"Test 15 (ExecutionState 4-State Conformance): {actual_states}")
    assert actual_states == expected_market_states, f"ExecutionState contains unauthorized states: {actual_states - expected_market_states}"

    # Test 16: Broker RMS T2T Day T Intraday Sale Rejection (Zerodha RMS Model)
    req_t0 = BrokerOrderRequest(
        ticker="T2T_STOCK",
        action="SELL",
        shares=500,
        holding_days=0,
        series="BE",
        surveillance_stage="NONE"
    )
    b_state16, b_reason16 = CircuitRuleEngine.evaluate_broker_order_state(req_t0)
    print(f"Test 16 (Broker RMS Day T Rejection): State = {b_state16.value} | Reason: {b_reason16}")
    assert b_state16 == BrokerOrderState.BROKER_INELIGIBLE
    assert "BROKER_INELIGIBLE" in b_reason16

    # Test 17: Broker RMS Day T+1 Auth Required (No DDPI / No TPIN Pre-Auth)
    req_t1_no_auth = BrokerOrderRequest(
        ticker="T2T_STOCK",
        action="SELL",
        shares=500,
        holding_days=1,
        series="BE",
        has_ddpi=False,
        is_authorized=False
    )
    b_state17, b_reason17 = CircuitRuleEngine.evaluate_broker_order_state(req_t1_no_auth)
    print(f"Test 17 (Broker RMS T+1 Auth Required): State = {b_state17.value} | Reason: {b_reason17}")
    assert b_state17 == BrokerOrderState.AUTH_REQUIRED
    assert "AUTH_REQUIRED" in b_reason17

    # Test 18: Broker RMS Day T+1 Accepted with DDPI Active
    req_t1_ddpi = BrokerOrderRequest(
        ticker="T2T_STOCK",
        action="SELL",
        shares=500,
        holding_days=1,
        series="BE",
        has_ddpi=True,
        is_authorized=False
    )
    b_state18, b_reason18 = CircuitRuleEngine.evaluate_broker_order_state(req_t1_ddpi)
    print(f"Test 18 (Broker RMS T+1 Accepted with DDPI): State = {b_state18.value} | Reason: {b_reason18}")
    assert b_state18 == BrokerOrderState.ACCEPTED
    assert "ACCEPTED" in b_reason18

    # Test 19: Missing / Zero Proposed Shares in Entry Signal -> DATA_INVALID
    sig19, reason19 = CircuitRuleEngine.evaluate_entry_signal(snap_valid, proposed_shares=None)
    print(f"Test 19 (Missing Proposed Shares): Signal = {sig19.value} | Reason: {reason19}")
    assert sig19 == EntrySignal.DATA_INVALID

    # Test 20: Missing High / Low in Entry Signal -> DATA_INVALID
    snap_no_hl = MarketDepthSnapshot(
        ticker="NO_HL",
        price=25.0,
        prev_close=24.2,
        circuit_limit_pct=10.0,
        total_bids=80000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=150000,
        high=None, # Missing!
        low=None,
        best_bid=24.95,
        best_ask=25.05,
        spread_pct=0.40,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig20, reason20 = CircuitRuleEngine.evaluate_entry_signal(snap_no_hl, proposed_shares=1000)
    print(f"Test 20 (Missing High/Low): Signal = {sig20.value} | Reason: {reason20}")
    assert sig20 == EntrySignal.DATA_INVALID

    # Test 21: NaN Spread in Entry Signal -> DATA_INVALID
    snap_nan_spread = MarketDepthSnapshot(
        ticker="NAN_SPREAD",
        price=25.0,
        prev_close=24.2,
        circuit_limit_pct=10.0,
        total_bids=80000,
        total_offers=50000,
        day_volume=600000,
        avg_20d_volume=150000,
        high=25.3,
        low=24.1,
        best_bid=None,
        best_ask=None,
        spread_pct=float("nan"), # NaN!
        series="EQ",
        surveillance_stage="NONE",
    )
    sig21, reason21 = CircuitRuleEngine.evaluate_entry_signal(snap_nan_spread, proposed_shares=1000)
    print(f"Test 21 (NaN Spread): Signal = {sig21.value} | Reason: {reason21}")
    assert sig21 == EntrySignal.DATA_INVALID

    # Test 22: Zero 20-Day Average Volume -> DATA_INVALID (no 1,000,000x expansion)
    snap_zero_20d = MarketDepthSnapshot(
        ticker="ZERO_20D",
        price=25.0,
        prev_close=24.2,
        circuit_limit_pct=10.0,
        total_bids=80000,
        total_offers=50000,
        day_volume=1000000,
        avg_20d_volume=0, # Zero!
        high=25.3,
        low=24.1,
        best_bid=24.95,
        best_ask=25.05,
        spread_pct=0.40,
        series="EQ",
        surveillance_stage="NONE",
    )
    sig22, reason22 = CircuitRuleEngine.evaluate_entry_signal(snap_zero_20d, proposed_shares=1000)
    print(f"Test 22 (Zero 20d Volume): Signal = {sig22.value} | Reason: {reason22}")
    assert sig22 == EntrySignal.DATA_INVALID

    # Test 23: Day 3 Down 10% -> EMERGENCY_EXIT_ATTEMPT (Never PRE-EMPTIVE PROFIT EXIT!)
    snap_day3_loss = MarketDepthSnapshot(
        ticker="LOSS_DAY3",
        price=18.0,
        prev_close=18.5,
        circuit_limit_pct=5.0,
        total_bids=10000,
        total_offers=5000,
        day_volume=50000,
        avg_20d_volume=40000,
        series="EQ",
        surveillance_stage="NONE",
    )
    pos_sig23, pos_reason23 = CircuitRuleEngine.evaluate_position_signal(
        snap=snap_day3_loss,
        entry_price=20.0, # Down -10%
        holding_days=3,
        current_shares=1000
    )
    print(f"Test 23 (Day 3 Loss Exit Review): Signal = {pos_sig23.value} | Reason: {pos_reason23}")
    assert pos_sig23 == PositionSignal.EMERGENCY_EXIT_ATTEMPT
    assert "STOP LOSS" in pos_reason23 or "EXIT REVIEW" in pos_reason23

    # Test 24: Unknown Surveillance in Position Monitoring -> DATA_INVALID
    snap_pos_unknown = MarketDepthSnapshot(
        ticker="POS_UNK",
        price=22.0,
        prev_close=20.0,
        circuit_limit_pct=5.0,
        total_bids=10000,
        total_offers=5000,
        day_volume=50000,
        avg_20d_volume=40000,
        series="EQ",
        surveillance_stage="UNKNOWN", # Unknown!
    )
    pos_sig24, pos_reason24 = CircuitRuleEngine.evaluate_position_signal(
        snap=snap_pos_unknown,
        entry_price=20.0,
        holding_days=1,
        current_shares=1000
    )
    print(f"Test 24 (Position Unknown Surveillance): Signal = {pos_sig24.value} | Reason: {pos_reason24}")
    assert pos_sig24 == PositionSignal.DATA_INVALID

    # Test 25: Broker Order State Invalid Action & Negative Margin Rejection
    req_fly = BrokerOrderRequest(ticker="TEST", action="FLY", shares=100, holding_days=1)
    b_state25a, b_reason25a = CircuitRuleEngine.evaluate_broker_order_state(req_fly)
    print(f"Test 25a (Broker Action FLY): State = {b_state25a.value} | Reason: {b_reason25a}")
    assert b_state25a == BrokerOrderState.REJECTED

    req_neg_margin = BrokerOrderRequest(ticker="TEST", action="BUY", shares=100, holding_days=0, available_margin=-500)
    b_state25b, b_reason25b = CircuitRuleEngine.evaluate_broker_order_state(req_neg_margin)
    print(f"Test 25b (Negative Margin): State = {b_state25b.value} | Reason: {b_reason25b}")
    assert b_state25b == BrokerOrderState.REJECTED

    req_unknown_series = BrokerOrderRequest(ticker="TEST", action="BUY", shares=100, series="UNKNOWN")
    b_state25c, b_reason25c = CircuitRuleEngine.evaluate_broker_order_state(req_unknown_series)
    print(f"Test 25c (Unknown Series): State = {b_state25c.value} | Reason: {b_reason25c}")
    assert b_state25c == BrokerOrderState.REJECTED

    print("\nALL 25 RED-TEAM COMPLIANCE TESTS PASSED 100%!")

