"""
tests/test_track2_multi_strategy_engine.py
=========================================
Unit tests for VWAPReclaimStrategy, VolatilitySqueezeStrategy, and MultiStrategyEngine.
"""

import pytest
from antigravity.models.track2_vwap_reclaim_strategy import VWAPReclaimStrategy, VWAPSnapshot
from antigravity.models.track2_volatility_squeeze_strategy import VolatilitySqueezeStrategy, DailyCompressionProfile
from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine, StrategyType, UnifiedTradeSignal


def test_vwap_calculation_and_reclaim_signal():
    """Verifies intraday rolling VWAP calculation and valid reclaim trigger."""
    strategy = VWAPReclaimStrategy(risk_budget_rs=1500.0)

    # 3 bars: Bar 0 establishes high, Bar 1 dips to VWAP, Bar 2 reclaims above VWAP
    candles = [
        {"open": 100.0, "high": 105.0, "low": 99.5, "close": 104.0, "volume": 10000},
        {"open": 104.0, "high": 104.5, "low": 101.5, "close": 101.8, "volume": 8000},  # pulls back to VWAP
        {"open": 102.0, "high": 105.5, "low": 101.8, "close": 105.0, "volume": 25000}, # reclaims with surge
    ]

    vwap_snap = strategy.calculate_intraday_vwap(candles)
    assert vwap_snap is not None
    assert vwap_snap.vwap > 100.0
    assert vwap_snap.upper_band > vwap_snap.vwap > vwap_snap.lower_band

    # Evaluate signal
    signal = strategy.evaluate(
        symbol="TEST_STOCK",
        candles_15m=candles,
        hist_median_volume_15m=10000.0,
        daily_ema20=95.0,
        daily_ema50=90.0,
        atr14_points=3.5,
    )

    assert signal.passed_all_gates is True
    assert signal.decision == "SIGNAL_BUY"
    assert signal.entry_price == 105.0
    assert signal.stop_price is not None
    assert signal.stop_price < 105.0
    assert signal.shares is not None and signal.shares > 0
    assert signal.target_tranche1 is not None and signal.target_tranche1 > signal.entry_price
    assert signal.target_tranche2 is not None and signal.target_tranche2 > signal.target_tranche1


def test_vwap_reclaim_fails_on_bearish_trend():
    """Verifies VWAP reclaim strictly fails closed if daily trend is bearish."""
    strategy = VWAPReclaimStrategy()
    candles = [
        {"open": 100.0, "high": 105.0, "low": 99.5, "close": 104.0, "volume": 10000},
        {"open": 104.0, "high": 104.5, "low": 101.5, "close": 101.8, "volume": 8000},
        {"open": 102.0, "high": 105.5, "low": 101.8, "close": 105.0, "volume": 25000},
    ]

    signal = strategy.evaluate(
        symbol="TEST_STOCK",
        candles_15m=candles,
        hist_median_volume_15m=10000.0,
        daily_ema20=110.0,  # Price 105 < EMA20 110 (bearish)
        daily_ema50=115.0,
        atr14_points=3.5,
    )

    assert signal.passed_all_gates is False
    assert signal.decision == "TREND_BEARISH"


def test_volatility_squeeze_nr7_detection_and_breakout():
    """Verifies NR7 detection on daily bars and subsequent 15m expansion breakout."""
    strategy = VolatilitySqueezeStrategy(risk_budget_rs=1500.0)

    # 7 daily candles where Day 6 has the narrowest range (100 to 101 = range 1.0)
    daily_candles = [
        {"open": 95.0, "high": 99.0, "low": 94.0, "close": 98.0, "volume": 50000},   # range 5.0
        {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0, "volume": 60000}, # range 5.0
        {"open": 101.0, "high": 104.0, "low": 100.0, "close": 103.0, "volume": 40000},# range 4.0
        {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 35000},# range 3.0
        {"open": 104.0, "high": 105.5, "low": 103.0, "close": 104.5, "volume": 30000},# range 2.5
        {"open": 104.5, "high": 105.2, "low": 103.8, "close": 104.8, "volume": 25000},# range 1.4
        {"open": 104.8, "high": 105.1, "low": 104.2, "close": 105.0, "volume": 20000},# range 0.9 (NR7!)
    ]

    profile = strategy.detect_daily_compression(daily_candles)
    assert profile is not None
    assert profile.is_nr7 is True
    assert profile.compression_score >= 0.45

    # 15m candle breaking out above 105.1 with volume expansion
    intraday_candles = [
        {"open": 105.0, "high": 106.5, "low": 104.8, "close": 106.2, "volume": 30000}
    ]

    signal = strategy.evaluate(
        symbol="SQUEEZE_STOCK",
        daily_candles=daily_candles,
        candles_15m=intraday_candles,
        hist_median_volume_15m=10000.0,
        daily_ema20=98.0,
        daily_ema50=92.0,
    )

    assert signal.passed_all_gates is True
    assert signal.decision == "SIGNAL_BUY"
    assert signal.entry_price == 106.2
    assert signal.stop_price == profile.compression_midpoint
    assert signal.shares is not None and signal.shares > 0


def test_multi_strategy_engine_ranking_and_sector_capping():
    """Verifies MultiStrategyEngine ranks multiple signals and caps sector exposure."""
    engine = MultiStrategyEngine(risk_budget_rs=1500.0, max_portfolio_slots=3, max_per_sector=2)

    # Mock signals across different sectors and conviction scores
    s1 = UnifiedTradeSignal(
        symbol="METAL_A", strategy_type="VWAP_RECLAIM", conviction_score=0.92,
        entry_price=500.0, stop_price=495.0, target_tranche1=507.5, target_tranche2=515.0,
        shares=300, notional_value_rs=150000.0, actual_risk_rs=1500.0, risk_pct=1.0,
        volume_multiple=2.8, sector="Metals", details={}
    )
    s2 = UnifiedTradeSignal(
        symbol="METAL_B", strategy_type="ORB_MOMENTUM", conviction_score=0.88,
        entry_price=400.0, stop_price=390.0, target_tranche1=415.0, target_tranche2=430.0,
        shares=150, notional_value_rs=60000.0, actual_risk_rs=1500.0, risk_pct=2.5,
        volume_multiple=2.6, sector="Metals", details={}
    )
    s3 = UnifiedTradeSignal(
        symbol="METAL_C", strategy_type="VOLATILITY_SQUEEZE", conviction_score=0.85,
        entry_price=300.0, stop_price=295.0, target_tranche1=307.5, target_tranche2=315.0,
        shares=300, notional_value_rs=90000.0, actual_risk_rs=1500.0, risk_pct=1.67,
        volume_multiple=2.2, sector="Metals", details={}
    )
    s4 = UnifiedTradeSignal(
        symbol="AUTO_A", strategy_type="VWAP_RECLAIM", conviction_score=0.80,
        entry_price=1200.0, stop_price=1180.0, target_tranche1=1230.0, target_tranche2=1260.0,
        shares=75, notional_value_rs=90000.0, actual_risk_rs=1500.0, risk_pct=1.67,
        volume_multiple=2.1, sector="Automobile", details={}
    )

    # Total 4 signals. Metals has 3 signals. Max slots = 3. Max per sector = 2.
    # Expected selection: METAL_A (0.92), METAL_B (0.88), AUTO_A (0.80) -> METAL_C rejected due to sector cap.
    allocated = engine.rank_and_allocate([s1, s2, s3, s4])

    assert len(allocated) == 3
    assert allocated[0].symbol == "METAL_A"
    assert allocated[1].symbol == "METAL_B"
    assert allocated[2].symbol == "AUTO_A"
    assert "METAL_C" not in [s.symbol for s in allocated]


def test_shadow_mode_isolation_and_filtering():
    """
    Verifies Tri-Agent Consensus Mandate (ChatGPT / Codex & Claude Audit 2026-09-24):
    - ORB_MOMENTUM is the active control baseline (is_shadow=False).
    - Unproven strategies (VWAP, SQUEEZE, etc.) are quarantined to shadow mode (is_shadow=True).
    - When allow_shadow=False, rank_and_allocate strictly allocates capital only to active baseline signals.
    """
    engine = MultiStrategyEngine(risk_budget_rs=1500.0, max_portfolio_slots=3, max_per_sector=2)

    active_orb = UnifiedTradeSignal(
        symbol="SBIN", strategy_type="ORB_MOMENTUM", conviction_score=0.82,
        entry_price=800.0, stop_price=790.0, target_tranche1=815.0, target_tranche2=830.0,
        shares=150, notional_value_rs=120000.0, actual_risk_rs=1500.0, risk_pct=1.25,
        volume_multiple=2.2, sector="Banking", details={}, is_shadow=False
    )
    shadow_vwap = UnifiedTradeSignal(
        symbol="TATASTEEL", strategy_type="VWAP_RECLAIM", conviction_score=0.95,
        entry_price=150.0, stop_price=147.0, target_tranche1=154.5, target_tranche2=159.0,
        shares=500, notional_value_rs=75000.0, actual_risk_rs=1500.0, risk_pct=2.0,
        volume_multiple=3.0, sector="Metals", details={}, is_shadow=True
    )
    shadow_recoil = UnifiedTradeSignal(
        symbol="INFY", strategy_type="RECOIL", conviction_score=0.90,
        entry_price=1900.0, stop_price=1870.0, target_tranche1=1945.0, target_tranche2=1990.0,
        shares=50, notional_value_rs=95000.0, actual_risk_rs=1500.0, risk_pct=1.58,
        volume_multiple=2.5, sector="IT", details={}, is_shadow=True
    )

    # In Shadow Logging Mode (allow_shadow=True), all signals are visible in rankings
    all_signals = engine.rank_and_allocate([active_orb, shadow_vwap, shadow_recoil], allow_shadow=True)
    assert len(all_signals) == 3
    assert all_signals[0].symbol == "TATASTEEL"

    # In Production Execution Mode (allow_shadow=False), only verified baseline signals compete for capital
    prod_allocated = engine.rank_and_allocate([active_orb, shadow_vwap, shadow_recoil], allow_shadow=False)
    assert len(prod_allocated) == 1
    assert prod_allocated[0].symbol == "SBIN"
    assert prod_allocated[0].is_shadow is False
