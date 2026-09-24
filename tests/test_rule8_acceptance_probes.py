"""
tests/test_rule8_acceptance_probes.py
======================================
Rule 8 Red-Team Acceptance Test Suite (Claude Audit 2026-09-24).
Encodes probes R1-R6 as permanent regression gates.
"""

from datetime import time as dtime
import pytest

from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine, UnifiedTradeSignal
from antigravity.models.track2_alpha_engine import MultiTimeframeAlphaEngine
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor
from antigravity.models.track2_paper_execution import BracketOrderManager


def generate_synthetic_session(n: int, px: float = 100.0, vol: int = 20000):
    bars = []
    for i in range(n):
        hh, mm = divmod(9 * 60 + 15 + 15 * i, 60)
        bars.append({
            "timestamp": f"2026-09-24T{hh:02d}:{mm:02d}:00+05:30",
            "open": px,
            "high": px + 1.0,
            "low": px - 0.8,
            "close": px + 0.4,
            "volume": vol,
        })
        px += 0.4
    return bars


def test_r1_compass_inputs_supplied_and_breadth_failclosed():
    """R1: COMPASS branch executes without AttributeError and fails closed when breadth is missing."""
    eng = MultiStrategyEngine()
    bars = generate_synthetic_session(8)
    base = dict(
        symbol="TEST", sector="X", daily_candles=None, hist_median_volume_15m=10000.0,
        daily_ema20=90.0, daily_ema50=85.0, atr14_points=3.0
    )

    # 1. When sector_breadth is not passed (default None), COMPASS is safely skipped
    out_no_breadth = eng.evaluate_symbol(
        candles_15m=bars, sector_candle_now=bars[-1], sector_candle_4_bars_ago=bars[-5],
        market_candle_now=bars[-1], market_candle_4_bars_ago=bars[-5], **base
    )
    assert not any(s.strategy_type == "COMPASS" for s in out_no_breadth)

    # 2. When sector_breadth is passed explicitly, evaluate_setup is invoked without AttributeError
    out_with_breadth = eng.evaluate_symbol(
        candles_15m=bars, sector_candle_now=bars[-1], sector_candle_4_bars_ago=bars[-5],
        market_candle_now=bars[-1], market_candle_4_bars_ago=bars[-5], sector_breadth=0.65, **base
    )
    assert isinstance(out_with_breadth, list)


def test_r2_orb_volume_gate_missing_baseline_fails_closed():
    """R2: Missing historical volume baseline (median <= 0) must fail closed with VOLUME_INSUFFICIENT."""
    bars = generate_synthetic_session(3, vol=20000)
    bars[-1]["close"] = bars[0]["high"] + 0.5  # Breakout price

    res = MultiTimeframeAlphaEngine.evaluate_candidate(
        symbol="TEST",
        candles_15m=bars,
        hist_median_volume_15m=0.0,
        daily_ema20=90.0,
        daily_ema50=85.0,
        atr14_points=3.0,
    )
    assert res.passed_all_gates is False
    assert res.decision == "VOLUME_INSUFFICIENT"
    assert res.volume_multiple == 0.0


def test_r3_shadow_isolation_default_in_rank_and_allocate():
    """R3: Default rank_and_allocate must exclude shadow signals and allocate only active baseline."""
    eng = MultiStrategyEngine()
    mk = dict(
        entry_price=100.0, stop_price=99.0, target_tranche1=101.5, target_tranche2=103.0,
        shares=50, notional_value_rs=5000.0, actual_risk_rs=50.0, risk_pct=1.0,
        volume_multiple=2.0, details={}
    )
    orb = UnifiedTradeSignal(symbol="A", strategy_type="ORB_MOMENTUM", conviction_score=0.5, sector="S1", is_shadow=False, **mk)
    vwap = UnifiedTradeSignal(symbol="B", strategy_type="VWAP_RECLAIM", conviction_score=0.9, sector="S2", is_shadow=True, **mk)

    # Default call (allow_shadow=False)
    allocated = eng.rank_and_allocate([orb, vwap])
    assert len(allocated) == 1
    assert allocated[0].strategy_type == "ORB_MOMENTUM"
    assert allocated[0].is_shadow is False


def test_r4_per_slot_cap_enforced_by_default():
    """R4: calibrate_for_corpus() must enforce the Rs 58,333 slot cap by default."""
    gov = PortfolioRiskGovernor.calibrate_for_corpus()
    res = gov.assess_candidate(
        symbol="RVNL",
        entry_price=400.0,
        stop_price=396.0,
        quantity=375,  # Rs 1,50,000 notional
        active_positions=[],
        var_elm_rate=0.20,
    )
    assert res.is_approved is False
    assert "SLOT_CAP_EXCEEDED" in res.rejection_reason


def test_r5_dhan_bracket_mis_cutoff_at_1508():
    """R5: All MIS brackets must force CLOSED_MIS_SQUAREOFF at or after 15:08 IST."""
    for test_time in (dtime(15, 9), dtime(15, 11)):
        for is_cas in (False, True):
            bracket = BracketOrderManager.create_bracket("r5", "TEST", 100.0, 95.0, 100, product_type="MIS")
            state = BracketOrderManager.update_bracket_quote(
                bracket=bracket,
                ltp=101.0,
                current_time_ist=test_time,
                is_cas_eligible=is_cas,
            )
            assert state.is_eod_squared_off is True
            assert state.terminal_state == "CLOSED_MIS_SQUAREOFF"
