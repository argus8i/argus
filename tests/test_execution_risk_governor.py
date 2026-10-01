"""
tests/test_execution_risk_governor.py
======================================
Adversarial & Invariant Unit Tests for Sprint Day 2 Deliverables:
- ExecutionFrictionEngine / ExecutionSimulator (antigravity/engine/execution_simulator.py)
  * Statutory transaction friction (STT, NSE, SEBI, Stamp Duty, GST, flat DP charges, zero delivery brokerage).
  * Adverse slippage model (base 7.5 bps, gap stress 25 bps).
  * Discrete execution states (QUEUED, FILLED, PARTIAL, LOCKED_NO_OFFER, LOCKED_NO_BID, REJECTED).
  * Gap-open mechanics & stop-loss gap slippage (>1R realized loss).
  * Claude Rule 9: 15% volume participation cap.
  * Rule 2: Absolute ₹10.00 price floor.
- PortfolioRiskGovernor (antigravity/engine/risk_governor.py)
  * Pinned Adjusted A1 capacity limits (3 slots, ₹38,000 slot cap, ₹1,500 risk budget, ₹114,000 exposure ceiling).
  * Unencumbered cash buffer (₹136,000 inviolable).
  * Fail-closed missing ATR sizing (= 0 shares).
  * Deterministic simultaneous signal priority ranking & tie-breaking.
  * Lower-circuit exit lockout handling (preserves slot & margin).
  * Gap-down loss reconciliation without state corruption.
"""

from decimal import Decimal
import math
import pytest

from antigravity.engine.execution_simulator import (
    DailyBar,
    EntryOrder,
    ExecutionFrictionEngine,
    ExecutionReport,
    ExecutionSimulator,
    ExecutionState,
    OrderSide,
    OrderType,
    SwingPosition,
    calculate_statutory_costs,
)
from antigravity.engine.risk_governor import (
    AGGREGATE_EXPOSURE_CAP_RS,
    AGGREGATE_RISK_CAP_RS,
    CASH_BUFFER_RS,
    DEFAULT_SECTOR_MAP,
    MAX_SLOTS,
    RISK_PER_TRADE_RS,
    SLOT_CAP_RS,
    TOTAL_CORPUS_RS,
    CandidateSignal,
    PortfolioRiskGovernor,
    RiskAssessmentVerdict,
    compute_position_size,
)


# ============================================================================
# PART 1: STATUTORY TRANSACTION COSTS & SLIPPAGE ADVERSARIAL TESTS
# ============================================================================

def test_statutory_costs_exact_breakdown_buy():
    """
    Test exact itemized statutory costs for a delivery equity BUY order:
    Turnover = 30,000 (30 shares @ 1,000)
    - Brokerage: 0.00 (Zerodha delivery is free)
    - STT: 0.1% = 30.00
    - NSE Exchange fee: 0.00297% = 0.89
    - SEBI fee: 0.0001% = 0.03
    - Stamp Duty: 0.015% = 4.50
    - DP Charges: 0.00 (Buy has no DP charge)
    - GST: 18% on (Brokerage + NSE + SEBI) = 18% of (0.89 + 0.03) = 0.17
    - Total Cost: 30.00 + 0.89 + 0.03 + 4.50 + 0.17 = 35.59
    """
    costs = calculate_statutory_costs(price=1000.0, quantity=30, side="BUY", is_delivery=True)
    assert costs["turnover"] == 30000.00
    assert costs["brokerage"] == 0.00
    assert costs["stt"] == 30.00
    assert costs["exchange_charges"] == 0.89
    assert costs["sebi_charges"] == 0.03
    assert costs["stamp_duty"] == 4.50
    assert costs["dp_charges"] == 0.00
    assert costs["gst"] == 0.17
    assert costs["total_cost"] == 35.59


def test_statutory_costs_exact_breakdown_sell():
    """
    Test exact itemized statutory costs for a delivery equity SELL order:
    Turnover = 30,000 (30 shares @ 1,000)
    - Brokerage: 0.00
    - STT: 0.1% = 30.00
    - NSE Exchange fee: 0.00297% = 0.89
    - SEBI fee: 0.0001% = 0.03
    - Stamp Duty: 0.00 (No stamp duty on sell)
    - DP Charges: flat 15.93 (Codex Mandate 2: Rs 15.93 flat per delivery scrip sale)
    - GST: 18% on (Brokerage + NSE + SEBI) = 0.17
    - Total Cost: 30.00 + 0.89 + 0.03 + 0.00 + 15.93 + 0.17 = 47.02
    """
    costs = calculate_statutory_costs(price=1000.0, quantity=30, side="SELL", is_delivery=True)
    assert costs["turnover"] == 30000.00
    assert costs["brokerage"] == 0.00
    assert costs["stt"] == 30.00
    assert costs["exchange_charges"] == 0.89
    assert costs["sebi_charges"] == 0.03
    assert costs["stamp_duty"] == 0.00
    assert costs["dp_charges"] == 15.93
    assert costs["gst"] == 0.17
    assert costs["total_cost"] == 47.02


def test_statutory_costs_fail_closed_inputs():
    """Rejects zero, negative, NaN, inf, boolean, or non-integral quantities fail-closed."""
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=0.0, quantity=10, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=-100.0, quantity=10, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=float("nan"), quantity=10, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=float("inf"), quantity=10, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=True, quantity=10, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=100.0, quantity=0, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=100.0, quantity=-5, side="BUY")
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        calculate_statutory_costs(price=100.0, quantity=10.5, side="BUY")


def test_slippage_base_and_stress():
    """Verify adverse slippage calculation for buy and sell under base and stress conditions."""
    engine = ExecutionFrictionEngine(base_slippage_bps=7.5, gap_stress_slippage_bps=25.0)

    # Normal BUY: 1,000 * (1 + 0.00075) = 1000.75
    buy_normal = engine.apply_slippage(1000.0, side=OrderSide.BUY, is_gap=False)
    assert buy_normal == 1000.75

    # Gap BUY: 1,000 * (1 + 0.0025) = 1002.50
    buy_gap = engine.apply_slippage(1000.0, side=OrderSide.BUY, is_gap=True)
    assert buy_gap == 1002.50

    # Normal SELL: 1,000 * (1 - 0.00075) = 999.25
    sell_normal = engine.apply_slippage(1000.0, side=OrderSide.SELL, is_gap=False)
    assert sell_normal == 999.25

    # Gap SELL: 1,000 * (1 - 0.0025) = 997.50
    sell_gap = engine.apply_slippage(1000.0, side=OrderSide.SELL, is_gap=True)
    assert sell_gap == 997.50


# ============================================================================
# PART 2: DISCRETE EXECUTION SIMULATOR MECHANICS
# ============================================================================

def test_sub_10_floor_rejection():
    """Rule 2: Absolute ₹10.00 price floor immediately rejects securities trading below ₹10."""
    engine = ExecutionSimulator()
    order = EntryOrder(
        symbol="PENNY",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=9.50,
        stop_price=8.50,
        quantity=100,
    )
    bar = DailyBar(
        symbol="PENNY",
        open=9.50,
        high=9.80,
        low=9.40,
        close=9.70,
        volume=50000,
        upper_circuit=10.45,
        lower_circuit=8.55,
    )
    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.REJECTED
    assert "RULE_2_PRICE_FLOOR_VIOLATION" in report.rejection_reason
    assert report.filled_quantity == 0


def test_inverted_stop_loss_rejection():
    """Stop loss >= Entry price must be rejected immediately."""
    engine = ExecutionSimulator()
    order = EntryOrder(
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=2500.0,
        stop_price=2505.0,  # Inverted stop
        quantity=15,
    )
    bar = DailyBar(
        symbol="RELIANCE",
        open=2500.0,
        high=2520.0,
        low=2490.0,
        close=2510.0,
        volume=1000000,
        upper_circuit=2750.0,
        lower_circuit=2250.0,
    )
    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.REJECTED
    assert "INVERTED_STOP" in report.rejection_reason


def test_locked_upper_circuit_buy_no_offer():
    """
    Rule 3: Prohibition of Locked-Circuit Chasing.
    When stock is locked at Upper Circuit on Buy attempt, order must result in
    LOCKED_NO_OFFER with fill probability 0%.
    """
    engine = ExecutionSimulator()
    order = EntryOrder(
        symbol="SUZLON",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET_ON_OPEN,
        limit_price=65.0,
        stop_price=60.0,
        quantity=500,
    )
    # Locked at Upper Circuit: open == high == low == close == upper_circuit
    bar = DailyBar(
        symbol="SUZLON",
        open=65.0,
        high=65.0,
        low=65.0,
        close=65.0,
        volume=1000,
        upper_circuit=65.0,
        lower_circuit=55.0,
        is_upper_circuit_locked=True,
    )
    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.LOCKED_NO_OFFER
    assert report.fill_probability == 0.0
    assert report.filled_quantity == 0
    assert report.actual_fill_price == 0.0


def test_locked_lower_circuit_exit_no_bid():
    """
    Rule 4 & 5: Lower-Circuit Exit Lockout.
    When stock is locked at Lower Circuit on Exit attempt, exit CANNOT execute.
    Must return LOCKED_NO_BID, position remains open and unresolved.
    """
    engine = ExecutionSimulator()
    position = SwingPosition(
        symbol="SUZLON",
        shares=500,
        entry_price=60.0,
        stop_price=57.0,
        sector="GREEN_ENERGY_POWER",
    )
    # Locked at Lower Circuit: open == high == low == close == lower_circuit
    bar = DailyBar(
        symbol="SUZLON",
        open=54.0,
        high=54.0,
        low=54.0,
        close=54.0,
        volume=500,
        upper_circuit=66.0,
        lower_circuit=54.0,
        is_lower_circuit_locked=True,
    )
    report = engine.simulate_exit(position, bar, trigger_reason="STOP_LOSS")
    assert report.state == ExecutionState.LOCKED_NO_BID
    assert report.fill_probability == 0.0
    assert report.filled_quantity == 0
    assert report.position_remains_open is True


def test_gap_up_open_entry_execution():
    """
    Gap-up open on entry: execution fills at Open price + slippage, NOT at limit/signal price.
    """
    engine = ExecutionSimulator(base_slippage_bps=10.0, gap_stress_slippage_bps=10.0)
    order = EntryOrder(
        symbol="TCS",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET_ON_OPEN,
        limit_price=3500.0,
        stop_price=3450.0,
        quantity=10,
    )
    # Gaps up to 3550 at open
    bar = DailyBar(
        symbol="TCS",
        open=3550.0,
        high=3580.0,
        low=3540.0,
        close=3570.0,
        volume=500000,
        upper_circuit=3850.0,
        lower_circuit=3150.0,
    )
    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.FILLED
    # Fill price is open (3550) + slippage (10 bps = 3.55) -> 3553.55
    assert report.actual_fill_price == 3553.55
    assert report.actual_fill_price > order.limit_price


def test_gap_down_open_stop_loss_actual_loss_exceeds_1r():
    """
    Gap-down past stop price: Stop is at 980, planned risk was (1000 - 980) * 75 = 1,500 Rs.
    Market opens gap down at 940!
    Exit fills at Open (940) - slippage, NOT at 980.
    Actual realized loss must strictly exceed the 1,500 Rs planned risk budget.
    """
    engine = ExecutionSimulator(gap_stress_slippage_bps=25.0)
    position = SwingPosition(
        symbol="CDSL",
        shares=75,
        entry_price=1000.0,
        stop_price=980.0,
        sector="CAPITAL_MARKETS_FINTECH",
    )
    # Market gaps down to 940 at open
    bar = DailyBar(
        symbol="CDSL",
        open=940.0,
        high=945.0,
        low=930.0,
        close=935.0,
        volume=200000,
        upper_circuit=1100.0,
        lower_circuit=900.0,
    )
    report = engine.simulate_exit(position, bar, trigger_reason="STOP_LOSS")
    assert report.state == ExecutionState.FILLED
    assert report.is_gap_exit is True
    # Fill price = 940 * (1 - 0.0025) = 937.65
    assert report.actual_fill_price == 937.65
    # Realized loss before friction = 75 * (1000 - 937.65) = 4,676.25 Rs
    assert report.gross_realized_loss > 1500.0
    assert report.net_realized_loss > 1500.0
    assert report.realized_r_multiple < -1.0


def test_volume_participation_cap_partial_fill():
    """
    Claude Rule 9: Max 15% volume participation.
    If order quantity is 2,000 shares but day volume is only 10,000 shares:
    Max fill = 0.15 * 10,000 = 1,500 shares.
    State must be PARTIAL with 1,500 shares filled and 500 unfilled.
    """
    engine = ExecutionSimulator()
    order = EntryOrder(
        symbol="ILLIQUID_FNO",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET_ON_OPEN,
        limit_price=100.0,
        stop_price=95.0,
        quantity=2000,
    )
    bar = DailyBar(
        symbol="ILLIQUID_FNO",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=10000,
        upper_circuit=110.0,
        lower_circuit=90.0,
    )
    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.PARTIAL
    assert report.filled_quantity == 1500
    assert report.unfilled_quantity == 500


# ============================================================================
# PART 3: PORTFOLIO RISK GOVERNOR & ADJUSTED A1 CAPACITY TESTS
# ============================================================================

def test_governor_single_slot_cap_rs_38000():
    """Candidate exceeding ₹38,000 slot cap is rejected with SLOT_CAP_EXCEEDED."""
    gov = PortfolioRiskGovernor()
    # 39 shares @ 1,000 = 39,000 Rs > 38,000 Rs
    res = gov.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=970.0,
        quantity=39,
    )
    assert res.is_approved is False
    assert "SLOT_CAP_EXCEEDED" in res.rejection_reason


def test_governor_single_trade_risk_rs_1500():
    """Candidate exceeding ₹1,500 risk budget is rejected with SINGLE_TRADE_RISK_EXCEEDED."""
    gov = PortfolioRiskGovernor()
    # 30 shares @ 1,000, stop 940 -> risk = 30 * 60 = 1,800 Rs > 1,500 Rs
    res = gov.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=940.0,
        quantity=30,
    )
    assert res.is_approved is False
    assert "SINGLE_TRADE_RISK_EXCEEDED" in res.rejection_reason


def test_governor_max_concurrent_positions_3_slots():
    """Portfolio already with 3 active/pending slots rejects a 4th slot."""
    gov = PortfolioRiskGovernor()
    # Fill 3 slots
    gov.confirm_fill("POS1", 30, 1000.0, 950.0, "IT_SERVICES")
    gov.confirm_fill("POS2", 30, 1000.0, 950.0, "DEFENSE_AEROSPACE")
    gov.confirm_fill("POS3", 30, 1000.0, 950.0, "METALS_MINING")

    # 4th slot candidate
    res = gov.assess_candidate(
        symbol="POS4",
        entry_price=1000.0,
        stop_price=950.0,
        quantity=30,
        custom_sector="CHEMICALS_SPECIALTY",
    )
    assert res.is_approved is False
    assert "MAX_CONCURRENT_POSITIONS_REACHED" in res.rejection_reason


def test_governor_aggregate_exposure_cap_rs_114000():
    """Aggregate deployable capital cannot exceed ₹114,000."""
    gov = PortfolioRiskGovernor()
    # 2 positions taking 38,000 + 38,000 = 76,000
    gov.confirm_fill("POS1", 38, 1000.0, 960.0, "IT_SERVICES")
    gov.confirm_fill("POS2", 38, 1000.0, 960.0, "DEFENSE_AEROSPACE")

    # 3rd position proposed with 39,000 -> Total = 115,000 > 114,000
    # Stop at 962 ensures risk = 39 * (1000 - 962) = 1482 Rs <= 1500 Rs
    res = gov.assess_candidate(
        symbol="POS3",
        entry_price=1000.0,
        stop_price=962.0,
        quantity=39,
        custom_sector="METALS_MINING",
    )
    assert res.is_approved is False
    # Will fail either slot cap or total capital cap
    assert any(code in res.rejection_reason for code in ["SLOT_CAP_EXCEEDED", "TOTAL_CAPITAL_EXCEEDED"])


def test_governor_aggregate_open_risk_cap_rs_4500():
    """Aggregate open risk cannot exceed ₹4,500."""
    gov = PortfolioRiskGovernor()
    # 2 positions with 1,500 each = 3,000 Rs open risk
    gov.confirm_fill("POS1", 30, 1000.0, 950.0, "IT_SERVICES")  # risk = 1,500
    gov.confirm_fill("POS2", 30, 1000.0, 950.0, "DEFENSE_AEROSPACE")  # risk = 1,500

    # 3rd proposed trade with 1,550 risk -> Total = 4,550 > 4,500
    res = gov.assess_candidate(
        symbol="POS3",
        entry_price=1000.0,
        stop_price=948.0,
        quantity=30,  # risk = 30 * 52 = 1,560 Rs
        custom_sector="METALS_MINING",
    )
    assert res.is_approved is False
    assert any(code in res.rejection_reason for code in ["SINGLE_TRADE_RISK_EXCEEDED", "AGGREGATE_PORTFOLIO_RISK_EXCEEDED"])


def test_governor_cash_buffer_preservation_rs_136000():
    """Unencumbered cash buffer of ₹136,000 is never breached even under full 3-slot deployment."""
    gov = PortfolioRiskGovernor()
    assert gov.cash_rs == TOTAL_CORPUS_RS
    assert gov.cash_buffer_rs == CASH_BUFFER_RS

    # Deploy 3 full slots @ 38,000 = 114,000 Rs
    gov.confirm_fill("POS1", 38, 1000.0, 960.0, "IT_SERVICES")
    gov.confirm_fill("POS2", 38, 1000.0, 960.0, "DEFENSE_AEROSPACE")
    gov.confirm_fill("POS3", 38, 1000.0, 960.0, "METALS_MINING")

    # Remaining cash must be >= 136,000 Rs (exact inviolable cash buffer)
    assert gov.cash_rs >= CASH_BUFFER_RS


def test_governor_missing_atr_fails_closed():
    """compute_position_size fails closed to 0 shares if ATR is missing, None, NaN, or non-positive."""
    assert compute_position_size(entry_price=1000.0, stop_price=960.0, atr=None) == 0
    assert compute_position_size(entry_price=1000.0, stop_price=960.0, atr=0.0) == 0
    assert compute_position_size(entry_price=1000.0, stop_price=960.0, atr=-5.0) == 0
    assert compute_position_size(entry_price=1000.0, stop_price=960.0, atr=float("nan")) == 0
    assert compute_position_size(entry_price=1000.0, stop_price=960.0, atr=float("inf")) == 0


def test_governor_sizing_exact_floor():
    """
    Position sizing uses mathematical floor (ROUND_FLOOR / int) without boundary round-up.
    Formula: min(floor(38000 / price), floor(1500 / (price - stop))).
    """
    # Price = 750, Stop = 720 (Diff = 30)
    # Risk shares: floor(1500 / 30) = 50
    # Slot shares: floor(38000 / 750) = 50.666... -> 50
    shares = compute_position_size(entry_price=750.0, stop_price=720.0, atr=25.0)
    assert shares == 50

    # Price = 1000, Stop = 975 (Diff = 25)
    # Risk shares: floor(1500 / 25) = 60
    # Slot shares: floor(38000 / 1000) = 38
    # Min(60, 38) = 38
    shares2 = compute_position_size(entry_price=1000.0, stop_price=975.0, atr=20.0)
    assert shares2 == 38


def test_governor_unmapped_sector_fails_closed():
    """Symbol without a recognized sector in mapping is rejected fail-closed."""
    gov = PortfolioRiskGovernor()
    res = gov.assess_candidate(
        symbol="UNKNOWN_XYZ",
        entry_price=1000.0,
        stop_price=960.0,
        quantity=25,
    )
    assert res.is_approved is False
    assert "UNMAPPED_SECTOR" in res.rejection_reason


def test_governor_sector_concentration_max_2():
    """Maximum 2 positions per sector: 3rd in same sector is rejected."""
    gov = PortfolioRiskGovernor()
    # 2 positions in CAPITAL_MARKETS_FINTECH (CDSL, ANGELONE)
    gov.confirm_fill("CDSL", 30, 1000.0, 960.0, "CAPITAL_MARKETS_FINTECH")
    gov.confirm_fill("ANGELONE", 30, 1000.0, 960.0, "CAPITAL_MARKETS_FINTECH")

    # 3rd position in CAPITAL_MARKETS_FINTECH (POLICYBZR)
    res = gov.assess_candidate(
        symbol="POLICYBZR",
        entry_price=1000.0,
        stop_price=960.0,
        quantity=25,
    )
    assert res.is_approved is False
    assert "SECTOR_CONCENTRATION_EXCEEDED" in res.rejection_reason


def test_governor_duplicate_symbol_prohibited():
    """Duplicate position in already open symbol is prohibited."""
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 30, 1000.0, 960.0, "CAPITAL_MARKETS_FINTECH")

    # Second entry in CDSL
    res = gov.assess_candidate(
        symbol="CDSL",
        entry_price=1020.0,
        stop_price=980.0,
        quantity=20,
    )
    assert res.is_approved is False
    assert "DUPLICATE_SYMBOL_POSITION" in res.rejection_reason


def test_governor_simultaneous_signals_deterministic_priority():
    """
    When 5 valid signals trigger simultaneously on a morning, but only 2 slots are free:
    Deterministic ranking selects the top 2 candidates based on priority score,
    and cleanly rejects the remaining 3 with PORTFOLIO_CAPACITY_EXCEEDED.
    """
    gov = PortfolioRiskGovernor()
    # 1 slot already occupied
    gov.confirm_fill("SUZLON", 350, 100.0, 96.0, "GREEN_ENERGY_POWER")
    assert gov.available_slots == 2

    candidates = [
        CandidateSignal(symbol="NATIONALUM", entry_price=180.0, stop_price=172.0, atr=6.0, priority_score=1.85, sector="METALS_MINING"),
        CandidateSignal(symbol="BDL", entry_price=1200.0, stop_price=1150.0, atr=40.0, priority_score=2.40, sector="DEFENSE_AEROSPACE"),
        CandidateSignal(symbol="CDSL", entry_price=1000.0, stop_price=960.0, atr=30.0, priority_score=3.10, sector="CAPITAL_MARKETS_FINTECH"),
        CandidateSignal(symbol="TATACHEM", entry_price=900.0, stop_price=860.0, atr=25.0, priority_score=1.20, sector="CHEMICALS_SPECIALTY"),
        CandidateSignal(symbol="DIXON", entry_price=8000.0, stop_price=7700.0, atr=200.0, priority_score=2.90, sector="ELECTRONICS_EMS"),
    ]

    allocation = gov.rank_and_allocate_signals(candidates)
    assert len(allocation.approved_signals) == 2
    assert len(allocation.rejected_signals) == 3

    # Top 2 by priority_score: CDSL (3.10) and DIXON (2.90)
    approved_syms = [s.symbol for s in allocation.approved_signals]
    assert approved_syms == ["CDSL", "DIXON"]

    # Remaining 3 rejected
    rejected_syms = [s.symbol for s, reason in allocation.rejected_signals]
    assert "BDL" in rejected_syms
    assert "NATIONALUM" in rejected_syms
    assert "TATACHEM" in rejected_syms


def test_governor_circuit_lock_preserves_slot_and_exposure():
    """
    When an exit attempt is locked at Lower Circuit (LOCKED_NO_BID):
    The position remains open, slot count is NOT released, and margin is held.
    Subsequent new orders cannot steal the locked slot.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("POS1", 30, 1000.0, 950.0, "IT_SERVICES")
    gov.confirm_fill("POS2", 30, 1000.0, 950.0, "DEFENSE_AEROSPACE")
    gov.confirm_fill("POS3", 30, 1000.0, 950.0, "METALS_MINING")
    assert gov.available_slots == 0

    # Attempt to exit POS1, but circuit lock occurs
    gov.record_circuit_locked_exit("POS1")
    assert "POS1" in gov.unresolved_exits
    assert gov.available_slots == 0  # Still 0!

    # Attempting to add a new trade must fail because 3 slots are still occupied
    res = gov.assess_candidate(
        symbol="NEW_STOCK",
        entry_price=500.0,
        stop_price=480.0,
        quantity=50,
        custom_sector="CHEMICALS_SPECIALTY",
    )
    assert res.is_approved is False
    assert "MAX_CONCURRENT_POSITIONS_REACHED" in res.rejection_reason


def test_governor_gap_down_loss_reconciliation():
    """
    Reconciles an actual gap-down exit loss that exceeded 1R (e.g. -2,400 Rs vs planned -1,500 Rs).
    The governor ledger reflects the loss truthfully in cash and equity without state corruption.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 30, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH")
    initial_cash = gov.cash_rs

    # Exit filled at gap down price 920 (Loss = 30 * 80 = 2,400 Rs)
    gov.reconcile_exit(
        symbol="CDSL",
        exit_price=920.0,
        shares=30,
        transaction_costs=45.0,
        exit_event_id="EVT_GAP_DOWN_001",
    )
    assert "CDSL" not in gov.active_positions
    assert gov.available_slots == 3

    # Proceeds = 30 * 920 - 45 = 27,600 - 45 = 27,555
    # Capital returned to cash cleanly
    assert gov.cash_rs == initial_cash + 27555.00
    assert gov.current_open_risk == 0.0


def test_full_position_lifecycle_state_machine():
    """
    Full end-to-end lifecycle verification:
    1. Candidate assessment -> Approved.
    2. Pending reservation created.
    3. Fill confirmed -> Active position created, reservation cleared, cash deducted.
    4. Exit attempted on Day 2 -> Locked at LC, position carried forward.
    5. Exit attempted on Day 3 -> Unlocked, fill confirmed, proceeds credited, slot freed.
    """
    gov = PortfolioRiskGovernor()
    assert gov.available_slots == 3

    # Step 1 & 2: Assess & Reserve
    res = gov.assess_candidate("TATACHEM", 950.0, 910.0, 35, custom_sector="CHEMICALS_SPECIALTY")
    assert res.is_approved is True
    gov.reserve_slot("TATACHEM", 35, 950.0, 910.0, "CHEMICALS_SPECIALTY")
    assert gov.available_slots == 2
    assert "TATACHEM" in gov.pending_reservations

    # Step 3: Confirm Fill
    gov.confirm_fill_from_reservation("TATACHEM", actual_fill_price=951.0, transaction_costs=40.0)
    assert "TATACHEM" not in gov.pending_reservations
    assert "TATACHEM" in gov.active_positions
    assert gov.available_slots == 2

    # Step 4: Circuit lock on exit attempt
    gov.record_circuit_locked_exit("TATACHEM")
    assert "TATACHEM" in gov.unresolved_exits
    assert gov.available_slots == 2  # slot still occupied!

    # Step 5: Unlocked fill on next session
    gov.reconcile_exit("TATACHEM", exit_price=980.0, shares=35, transaction_costs=48.0, exit_event_id="EVT_LC_UNLOCKED_001")
    assert "TATACHEM" not in gov.active_positions
    assert "TATACHEM" not in gov.unresolved_exits
    assert gov.available_slots == 3  # slot completely freed


# ============================================================================
# PART 4: CODEX REVIEW REGRESSION TESTS (FINDINGS 1 - 8)
# ============================================================================

def test_codex_finding_1_exit_touch_and_volume_participation():
    """
    Finding 1: Exits fabricate executable liquidity.
    - Stop at 95, session low 108: Stop was not touched -> state UNTOUCHED, filled=0.
    - Zero volume session -> filled=0, position remains open.
    - Position 100 shares, session volume 100 -> Rule 9 limits fill to 15 shares (PARTIAL), 85 remain open.
    """
    engine = ExecutionSimulator()
    pos = SwingPosition(symbol="CDSL", shares=100, entry_price=100.0, stop_price=95.0, sector="CAPITAL_MARKETS_FINTECH")

    # Case A: Stop not touched (Low = 108 > 95)
    bar_untouched = DailyBar(symbol="CDSL", open=110.0, high=115.0, low=108.0, close=112.0, volume=50000)
    rep_untouched = engine.simulate_exit(pos, bar_untouched, trigger_reason="STOP_LOSS")
    assert rep_untouched.state == ExecutionState.QUEUED or rep_untouched.filled_quantity == 0
    assert rep_untouched.position_remains_open is True

    # Case B: Zero-volume session
    bar_zero_vol = DailyBar(symbol="CDSL", open=94.0, high=96.0, low=92.0, close=93.0, volume=0)
    rep_zero = engine.simulate_exit(pos, bar_zero_vol, trigger_reason="STOP_LOSS")
    assert rep_zero.filled_quantity == 0
    assert rep_zero.position_remains_open is True

    # Case C: 15% volume participation cap on exit (volume 100 -> max fill 15 shares)
    bar_low_vol = DailyBar(symbol="CDSL", open=94.0, high=96.0, low=92.0, close=93.0, volume=100)
    rep_part = engine.simulate_exit(pos, bar_low_vol, trigger_reason="STOP_LOSS")
    assert rep_part.state == ExecutionState.PARTIAL
    assert rep_part.filled_quantity == 15
    assert rep_part.unfilled_quantity == 85
    assert rep_part.position_remains_open is True


def test_codex_finding_2_partial_exit_and_duplicate_credit():
    """
    Finding 2: Exit reconciliation corrupts remaining holdings and permits duplicate cash credits.
    - Selling 10 of 100 shares keeps 90 shares open, slot still occupied.
    - Selling invalid quantity (> position shares or <= 0) rejected.
    - Repeating exit on closed position rejected fail-closed.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 100, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH")
    assert gov.available_slots == 2

    # Partial exit: sell 10 shares
    gov.reconcile_exit(symbol="CDSL", exit_price=1050.0, shares=10, transaction_costs=15.0, exit_event_id="EVT_PARTIAL_EXIT_1")
    assert "CDSL" in gov.active_positions
    assert gov.active_positions["CDSL"]["shares"] == 90
    assert gov.available_slots == 2  # slot still occupied!

    # Selling 90 remaining shares completes exit
    gov.reconcile_exit(symbol="CDSL", exit_price=1050.0, shares=90, transaction_costs=15.0, exit_event_id="EVT_PARTIAL_EXIT_2")
    assert "CDSL" not in gov.active_positions
    assert gov.available_slots == 3  # slot now freed

    # Duplicate exit attempt rejected fail-closed
    with pytest.raises(KeyError, match="No active position found"):
        gov.reconcile_exit(symbol="CDSL", exit_price=1050.0, shares=10, exit_event_id="EVT_CLOSED_POSITION")


def test_codex_finding_3_actual_fill_reassessment():
    """
    Finding 3: Actual entry fills bypass risk ceilings.
    - Reservation for 38 shares at 1,000 (stop 970).
    - If actual fill slips/gaps to 1,100: notional is 41,800 > 38,000 cap, risk is 4,940 > 1,500.
    - Must reject or resize fail-closed rather than blindly admitting.
    """
    gov = PortfolioRiskGovernor()
    gov.reserve_slot("CDSL", quantity=38, entry_price=1000.0, stop_price=970.0, sector="CAPITAL_MARKETS_FINTECH")

    with pytest.raises(ValueError, match="EXPOSURE_OR_RISK_BREACH"):
        gov.confirm_fill_from_reservation("CDSL", actual_fill_price=1100.0, transaction_costs=50.0)


def test_codex_finding_4_strict_cash_buffer_enforcement():
    """
    Finding 4: Cash buffer is stored but not enforced (breached by fee friction or gap loss).
    - Deployable capital must strictly maintain cash >= 136,000 without exception.
    - If gap loss reduces cash below 136,000 + proposed notional + friction, reject candidate.
    """
    gov = PortfolioRiskGovernor()
    # Simulate a prior account drawdown/gap loss so available cash is 170,000 Rs
    # Deploying 38,000 Rs leaves 132,000 Rs < 136,000 Rs buffer
    gov.cash_rs = 170_000.0

    res = gov.assess_candidate("POS3", 1000.0, 961.0, 38, custom_sector="DEFENSE_AEROSPACE")
    assert res.is_approved is False
    assert "INSUFFICIENT_UNENCUMBERED_CASH" in res.rejection_reason or "BUFFER_BREACH" in res.rejection_reason


def test_codex_finding_5_sector_whitelist_and_unwidened_limit():
    """
    Finding 5: Sector restrictions can be overridden.
    - Unmapped custom_sector="invented" must be rejected.
    - max_positions_per_sector cannot be widened beyond 2.
    """
    gov = PortfolioRiskGovernor()
    res = gov.assess_candidate("UNKNOWN", 100.0, 95.0, 10, custom_sector="invented_sector")
    assert res.is_approved is False
    assert "UNMAPPED_SECTOR" in res.rejection_reason or "INVALID_SECTOR" in res.rejection_reason

    # Attempting to instantiate with max_positions_per_sector > 2 must be clamped or rejected
    gov_wide = PortfolioRiskGovernor(max_positions_per_sector=3)
    assert gov_wide.max_positions_per_sector <= 2


def test_codex_finding_6_exact_floor_no_premature_rounding():
    """
    Finding 6: Sizing rounds inputs before applying exact floor.
    - Price 1000.004 -> slot shares floor(38000 / 1000.004) = 37, NOT 38.
    - Price 9.999 is below 10.00 floor -> rejected, not rounded to 10.00.
    """
    # Stop at 970 -> diff = 30.004 -> risk shares floor(1500 / 30.004) = 49
    # Slot shares floor(38000 / 1000.004) = 37.9998... -> 37
    # Min(37, 49) = 37
    shares = compute_position_size(entry_price=1000.004, stop_price=970.0, atr=25.0)
    assert shares == 37

    # Price 9.999
    shares_penny = compute_position_size(entry_price=9.999, stop_price=9.0, atr=0.5)
    assert shares_penny == 0

    gov = PortfolioRiskGovernor()
    res = gov.assess_candidate("PENNY", 9.999, 9.0, 10, custom_sector="CAPITAL_MARKETS_FINTECH")
    assert res.is_approved is False
    assert "RULE_2_PRICE_FLOOR_VIOLATION" in res.rejection_reason


def test_codex_finding_7_buy_limit_ceiling_and_order_validation():
    """
    Finding 7: Buy limits cannot execute above their limit price.
    - Limit order at 100.00 with slippage cannot fill at 100.08. Max fill is limit_price.
    - Mismatching symbol or order side must fail closed.
    """
    engine = ExecutionSimulator(base_slippage_bps=7.5)
    order = EntryOrder(symbol="INFY", side=OrderSide.BUY, order_type=OrderType.LIMIT, limit_price=100.0, stop_price=95.0, quantity=100)
    bar = DailyBar(symbol="INFY", open=100.0, high=102.0, low=99.0, close=101.0, volume=50000)

    report = engine.simulate_entry(order, bar)
    assert report.state == ExecutionState.FILLED
    assert report.actual_fill_price <= order.limit_price  # Limit price is a ceiling!

    # Symbol mismatch
    bar_mismatch = DailyBar(symbol="WRONG", open=100.0, high=102.0, low=99.0, close=101.0, volume=50000)
    rep_mismatch = engine.simulate_entry(order, bar_mismatch)
    assert rep_mismatch.state == ExecutionState.REJECTED


def test_codex_finding_8_net_realized_pnl_includes_entry_and_exit_costs():
    """
    Finding 8: Reported net loss must include BOTH entry transaction friction and exit friction.
    """
    engine = ExecutionSimulator()
    pos = SwingPosition(symbol="CDSL", shares=50, entry_price=1000.0, stop_price=950.0, sector="CAPITAL_MARKETS_FINTECH")
    bar = DailyBar(symbol="CDSL", open=950.0, high=960.0, low=940.0, close=945.0, volume=50000)

    report = engine.simulate_exit(pos, bar, trigger_reason="STOP_LOSS", entry_transaction_costs=60.0)
    # Gross loss = 50 * (1000 - 950 - slippage)
    # Net loss must include entry costs (60) + exit costs
    assert report.net_realized_loss > report.gross_realized_loss + 59.0


# ============================================================================
# PART 5: CODEX ROUND 2 ACCEPTANCE REGRESSION TESTS
# ============================================================================

def test_codex_round2_cash_buffer_at_fill():
    """
    Round 2 Finding: Cash buffer not enforced at actual fill.
    An approved reservation for 38 shares at 999, with 174,020 cash,
    subsequently fills at 1,000 with calculated statutory costs of 45.08.
    Remaining cash becomes 135,974.92, below 136,000.
    Must raise ValueError('CASH_BUFFER_BREACH_AT_FILL') fail-closed.
    """
    gov = PortfolioRiskGovernor()
    gov.cash_rs = 174_020.00
    gov.reserve_slot("CDSL", quantity=38, entry_price=999.0, stop_price=961.0, sector="CAPITAL_MARKETS_FINTECH")

    with pytest.raises(ValueError, match="CASH_BUFFER_BREACH_AT_FILL"):
        gov.confirm_fill_from_reservation("CDSL", actual_fill_price=1000.0, transaction_costs=45.08)


def test_codex_round2_partial_fill_preserves_reservation():
    """
    Round 2 Finding: Partial fills discard outstanding reservations.
    Confirming 15 shares against a 100-share reservation must preserve
    the remaining 85 shares in pending_reservations.
    """
    gov = PortfolioRiskGovernor(slot_cap_rs=38000.0)
    # 100 shares @ 300 = 30,000 notional, stop 285 -> risk 1,500
    gov.reserve_slot("SUZLON", quantity=100, entry_price=300.0, stop_price=285.0, sector="GREEN_ENERGY_POWER")

    gov.confirm_fill_from_reservation("SUZLON", actual_fill_price=300.0, filled_quantity=15, transaction_costs=10.0)
    assert "SUZLON" in gov.active_positions
    assert gov.active_positions["SUZLON"]["shares"] == 15

    # 85 shares must remain in pending_reservations!
    assert "SUZLON" in gov.pending_reservations
    assert gov.pending_reservations["SUZLON"]["quantity"] == 85
    assert gov.pending_reservations["SUZLON"]["notional_rs"] == 85 * 300.0


def test_codex_round2_actual_fill_nan_rejected():
    """
    Round 2 Finding: Actual-fill validation fails open for NaN.
    Confirming a fill at float('nan') or invalid prices must raise fail-closed.
    """
    gov = PortfolioRiskGovernor()
    gov.reserve_slot("CDSL", quantity=30, entry_price=1000.0, stop_price=960.0, sector="CAPITAL_MARKETS_FINTECH")

    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        gov.confirm_fill_from_reservation("CDSL", actual_fill_price=float("nan"))

    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        gov.confirm_fill_from_reservation("CDSL", actual_fill_price=-100.0)

    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        gov.confirm_fill_from_reservation("CDSL", actual_fill_price=1000.0, filled_quantity=0)


def test_codex_round2_exit_event_idempotency():
    """
    Round 2 Finding: Replaying a partial exit credits cash again.
    reconcile_exit must require/track exit_event_id and prevent duplicate credits.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 100, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH")
    initial_cash = gov.cash_rs

    # First partial exit of 10 shares
    gov.reconcile_exit("CDSL", exit_price=1050.0, shares=10, transaction_costs=15.0, exit_event_id="EVT_001")
    cash_after = gov.cash_rs
    assert cash_after == initial_cash + (10 * 1050.0 - 15.0)

    # Replay of exact same exit event ID must raise duplicate event error
    with pytest.raises(ValueError, match="DUPLICATE_EXIT_EVENT"):
        gov.reconcile_exit("CDSL", exit_price=1050.0, shares=10, transaction_costs=15.0, exit_event_id="EVT_001")


def test_codex_round2_take_profit_touch_and_benchmark():
    """
    Round 2 Finding: Take profit bypasses touch verification and benchmarks against stop.
    - If trigger is TAKE_PROFIT, must verify bar.high >= target_price.
    - If bar.high < target_price, state must be QUEUED (unfilled).
    - If touched, execution benchmarks against target_price, NOT stop_price!
    """
    engine = ExecutionSimulator()
    pos = SwingPosition(symbol="CDSL", shares=50, entry_price=1000.0, stop_price=950.0, sector="CAPITAL_MARKETS_FINTECH")

    # Case A: Target at 1150, but Bar high is only 1120 -> untouched!
    bar_low = DailyBar(symbol="CDSL", open=1100.0, high=1120.0, low=1080.0, close=1110.0, volume=50000)
    rep_untouched = engine.simulate_exit(pos, bar_low, trigger_reason="TAKE_PROFIT", target_price=1150.0)
    assert rep_untouched.state == ExecutionState.QUEUED or rep_untouched.filled_quantity == 0
    assert rep_untouched.position_remains_open is True

    # Case B: Target at 1150, Bar high is 1160 -> touched!
    bar_touched = DailyBar(symbol="CDSL", open=1140.0, high=1160.0, low=1130.0, close=1155.0, volume=50000)
    rep_touched = engine.simulate_exit(pos, bar_touched, trigger_reason="TAKE_PROFIT", target_price=1150.0)
    assert rep_touched.state == ExecutionState.FILLED
    # Fill price benchmarks near 1150 (minus 7.5 bps slippage = 1149.14), NOT at 950!
    assert rep_touched.actual_fill_price > 1140.0
    assert rep_touched.realized_r_multiple > 0.0  # Profitable exit!


def test_codex_round2_exit_reconciliation_no_double_entry_deduction():
    """
    Round 2 Finding: total_cost combines previously paid entry costs with exit costs.
    reconcile_exit must deduct ONLY exit_transaction_costs from sale proceeds,
    preventing double deduction of entry costs from portfolio cash.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 50, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH", transaction_costs=60.0)
    cash_after_buy = gov.cash_rs
    # Entry costs (60) already deducted from cash!

    # Exit sold at 1,000. Exit transaction costs are 47.02.
    # Entry costs were 60.0.
    gov.reconcile_exit("CDSL", exit_price=1000.0, shares=50, exit_transaction_costs=47.02, exit_event_id="EVT_NO_DOUBLE_DEDUCTION")
    # Cash credited must be: 50 * 1000 - 47.02 = 49,952.98
    assert gov.cash_rs == cash_after_buy + 49952.98


# ============================================================================
# PART 6: CODEX ROUND 3 ACCEPTANCE REGRESSION TESTS
# ============================================================================

def test_codex_round3_partial_fills_cumulative_risk_and_entry_basis():
    """
    Round 3 Finding 1: Partial fills bypass cumulative risk and corrupt entry basis.
    - Initial fill of 50 shares @ 300, stop 285 (risk = 750, notional = 15,000, fee = 10).
    - Second fill of 50 shares @ 310, stop 285 (risk = 1250, notional = 15,500, fee = 20).
    - Cumulative risk is 750 + 1250 = 2,000 > 1,500 risk budget!
    - Must raise EXPOSURE_OR_RISK_BREACH fail-closed.
    - When second fill is within budget (50 @ 300, risk = 750):
      Combined notional = 30,000, risk = 1,500, weighted avg entry price = 300.0,
      total entry costs recorded = 10 + 20 = 30.
    """
    gov = PortfolioRiskGovernor()
    gov.reserve_slot("SUZLON", quantity=100, entry_price=300.0, stop_price=285.0, sector="GREEN_ENERGY_POWER")

    # First partial fill: 50 shares @ 300, fee 10
    gov.confirm_fill_from_reservation("SUZLON", actual_fill_price=300.0, filled_quantity=50, transaction_costs=10.0)
    assert gov.active_positions["SUZLON"]["shares"] == 50
    assert gov.active_positions["SUZLON"]["entry_costs"] == 10.0

    # Second partial fill at 310: 50 shares @ 310, fee 20 -> cumulative risk 2,000 breaches 1,500 budget!
    with pytest.raises(ValueError, match="EXPOSURE_OR_RISK_BREACH"):
        gov.confirm_fill_from_reservation("SUZLON", actual_fill_price=310.0, filled_quantity=50, transaction_costs=20.0)

    # Valid second fill at 300: 50 shares @ 300, fee 20 -> cumulative risk 1,500 <= 1,500
    gov.confirm_fill_from_reservation("SUZLON", actual_fill_price=300.0, filled_quantity=50, transaction_costs=20.0)
    pos = gov.active_positions["SUZLON"]
    assert pos["shares"] == 100
    assert pos["notional_rs"] == 30000.0
    assert pos["open_risk_rs"] == 1500.0
    assert pos["entry_costs"] == 30.0  # Fees aggregated!


def test_codex_round3_exit_idempotency_mandatory_and_atomic():
    """
    Round 3 Finding 2: Exit idempotency remains optional and non-atomic.
    - Reconcile exit requires exit_event_id fail-closed.
    - An invalid exit attempt (e.g. 11 shares against 10 open shares) must NOT consume the event ID.
    - Corrected retry with the same event ID must succeed.
    - Replaying after successful exit must raise DUPLICATE_EXIT_EVENT.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 10, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH")

    # Missing event ID fails closed
    with pytest.raises(ValueError, match="FAIL-CLOSED: exit_event_id is required"):
        gov.reconcile_exit("CDSL", exit_price=1050.0, shares=5)

    # Invalid exit: attempt to sell 11 shares (only 10 open) with event ID "EVT_ATOMIC"
    with pytest.raises(ValueError, match="Cannot sell 11 shares"):
        gov.reconcile_exit("CDSL", exit_price=1050.0, shares=11, exit_event_id="EVT_ATOMIC")

    # Event ID "EVT_ATOMIC" must NOT have been consumed!
    assert "EVT_ATOMIC" not in gov.processed_exit_events

    # Corrected retry with 5 shares using the same event ID "EVT_ATOMIC" succeeds!
    gov.reconcile_exit("CDSL", exit_price=1050.0, shares=5, exit_event_id="EVT_ATOMIC")
    assert "EVT_ATOMIC" in gov.processed_exit_events
    assert gov.active_positions["CDSL"]["shares"] == 5

    # Replay of exact same event ID raises duplicate error
    with pytest.raises(ValueError, match="DUPLICATE_EXIT_EVENT"):
        gov.reconcile_exit("CDSL", exit_price=1050.0, shares=5, exit_event_id="EVT_ATOMIC")


def test_codex_round3_partial_exit_prorates_entry_costs():
    """
    Round 3 Finding 3: Partial exits do not prorate remaining entry costs.
    Position of 100 shares with Rs 100 entry costs.
    Selling 40 shares must leave 60 shares with Rs 60 remaining entry costs basis.
    """
    gov = PortfolioRiskGovernor()
    gov.confirm_fill("CDSL", 100, 1000.0, 950.0, "CAPITAL_MARKETS_FINTECH", transaction_costs=100.0)
    assert gov.active_positions["CDSL"]["entry_costs"] == 100.0

    # Partial exit of 40 shares
    gov.reconcile_exit("CDSL", exit_price=1050.0, shares=40, exit_event_id="EVT_P40")
    pos = gov.active_positions["CDSL"]
    assert pos["shares"] == 60
    assert pos["entry_costs"] == 60.0  # Prorated from 100!

    # Second partial exit of 30 shares
    gov.reconcile_exit("CDSL", exit_price=1060.0, shares=30, exit_event_id="EVT_P30")
    pos = gov.active_positions["CDSL"]
    assert pos["shares"] == 30
    assert pos["entry_costs"] == 30.0  # Prorated from 60!


def test_codex_round3_target_validation_finite_positive():
    """
    Round 3 Finding 4: Target validation remains incomplete.
    Target of -1.0, 0.0, NaN, inf, or boolean must fail closed.
    """
    engine = ExecutionSimulator()
    pos = SwingPosition(symbol="CDSL", shares=50, entry_price=1000.0, stop_price=950.0, sector="CAPITAL_MARKETS_FINTECH")
    bar = DailyBar(symbol="CDSL", open=1000.0, high=1050.0, low=990.0, close=1020.0, volume=50000)

    for invalid_target in [-1.0, 0.0, float("nan"), float("inf"), float("-inf"), False, True]:
        with pytest.raises(ValueError, match="FAIL-CLOSED"):
            engine.simulate_exit(pos, bar, trigger_reason="TAKE_PROFIT", target_price=invalid_target)


# ============================================================================
# PART 7: CODEX ROUND 4 ACCEPTANCE REGRESSION TESTS
# ============================================================================

def test_codex_round4_partial_fill_precision_and_post_fill_risk_cap():
    """
    Round 4 Finding: confirm_fill rounded weighted entry to 4 decimals, causing
    recalculated risk to breach Rs 1,500.
    Must preserve full weighted-entry precision, compute risk consistently from
    cumulative notional and stop basis, and enforce risk cap on final recorded ledger.
    """
    gov = PortfolioRiskGovernor()
    stop = 10.000051 - 1500 / 3799
    gov.reserve_slot("SUZLON", 3799, 10.0, stop, "GREEN_ENERGY_POWER")
    gov.confirm_fill_from_reservation("SUZLON", 10.0, 1)
    gov.confirm_fill_from_reservation("SUZLON", 10.000051, 3798)

    pos = gov.active_positions["SUZLON"]
    assert pos["shares"] == 3799
    assert pos["open_risk_rs"] <= 1500.0 + 1e-4
    assert pos["notional_rs"] <= 38000.0 + 1e-4




