"""
tests/test_track2_new_strategies.py
===================================
Unit tests for TRAPDOOR (Failed Breakdown Reversal) and COMPASS (Sector Residual Strength).
"""

import pytest
from antigravity.models.track2_shared_features import SharedFeatureEngine
from antigravity.models.track2_trapdoor_strategy import TrapdoorStrategy
from antigravity.models.track2_compass_strategy import CompassStrategy


def test_shared_feature_engine():
    """Verifies typical price, ATR20, RVOL, and Kaufman ER8 calculations."""
    candles = [
        {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10000},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.5, "volume": 12000},
        {"open": 102.5, "high": 104.0, "low": 102.0, "close": 103.5, "volume": 15000},
    ]

    typ = SharedFeatureEngine.calculate_typical_price(104.0, 102.0, 103.5)
    assert typ == 103.17

    atr = SharedFeatureEngine.calculate_atr20(candles)
    assert atr > 0

    rvol = SharedFeatureEngine.calculate_rvol(15000, 10000)
    assert rvol == 1.50

    er8 = SharedFeatureEngine.calculate_er8(candles)
    assert 0.0 <= er8 <= 1.0


def test_trapdoor_strategy_signal():
    """Verifies TRAPDOOR pattern: Mother -> Inside -> Failed Breakdown -> Confirmation in a chop regime."""
    strategy = TrapdoorStrategy(risk_budget_rs=1500.0, max_notional_rs=58333.0)

    # 6 bars in a choppy/oscillating range (ER < 0.35, ATR ~ 1.2):
    # Bar 0: Baseline oscillating
    # Bar 1: Mother Bar (H=101.2, L=100.0, Range=1.2 <= 1.2*ATR)
    # Bar 2: Inside Bar (H=100.9, L=100.3)
    # Bar 3: Failed Breakdown (H=100.8, L=99.9 < Inside Low 100.3, Close=100.4 > 100.3)
    # Bar 4: Consolidation (H=100.7, L=100.2, Close=100.4)
    # Bar 5: Confirmation Bar (Close=101.1 > Inside High 100.9, Vol=25000)
    candles = [
        {"open": 100.5, "high": 101.5, "low": 99.8, "close": 100.4, "volume": 10000},
        {"open": 100.4, "high": 101.1, "low": 100.0, "close": 100.8, "volume": 10000},  # Mother (range 1.1)
        {"open": 100.8, "high": 100.9, "low": 100.3, "close": 100.6, "volume": 8000},   # Inside
        {"open": 100.6, "high": 100.8, "low": 99.9, "close": 100.4, "volume": 12000},  # Failed probe
        {"open": 100.4, "high": 100.7, "low": 100.2, "close": 100.4, "volume": 9000},
        {"open": 100.4, "high": 101.3, "low": 100.3, "close": 101.1, "volume": 25000},  # Confirm
    ]

    signal = strategy.evaluate(
        symbol="CHOP_STOCK",
        candles_15m=candles,
        bucket_median_vol=10000.0,
        market_breadth=0.50,
    )

    assert signal.passed_all_gates is True
    assert signal.decision == "SIGNAL_BUY"
    assert signal.entry_price == 101.1
    assert signal.stop_price is not None and signal.stop_price < 101.1
    assert signal.shares is not None and signal.shares > 0
    assert signal.target_price is not None and signal.target_price > signal.entry_price


def test_compass_strategy_signal():
    """Verifies COMPASS strategy: Sector leadership with stock residual outperformance."""
    strategy = CompassStrategy(risk_budget_rs=1500.0, max_notional_rs=58333.0)

    # 5 bars for candidate stock (closing at 101.8, up from 100.0 = +1.78% 1h return)
    # Stop level: previous 3-bar low is 100.6 (risk distance = 1.2 / 101.8 = 1.18%, ideal!)
    stock_candles = [
        {"open": 99.8, "high": 100.3, "low": 99.5, "close": 100.0, "volume": 10000},
        {"open": 100.0, "high": 100.8, "low": 99.8, "close": 100.5, "volume": 11000},
        {"open": 100.5, "high": 101.0, "low": 100.2, "close": 100.8, "volume": 12000},
        {"open": 100.8, "high": 101.3, "low": 100.6, "close": 101.1, "volume": 14000},
        {"open": 101.1, "high": 102.0, "low": 100.9, "close": 101.8, "volume": 25000}, # Breakout
    ]

    # Sector peer candles (up modestly, from 100.0 to 100.6 = +0.60%)
    peer_candles = [
        {"open": 100.0, "high": 100.3, "low": 99.8, "close": 100.0, "volume": 10000},
        {"open": 100.0, "high": 100.4, "low": 99.8, "close": 100.2, "volume": 10000},
        {"open": 100.2, "high": 100.5, "low": 100.0, "close": 100.3, "volume": 10000},
        {"open": 100.3, "high": 100.6, "low": 100.1, "close": 100.4, "volume": 10000},
        {"open": 100.4, "high": 100.8, "low": 100.2, "close": 100.6, "volume": 12000},
    ]

    # Broad market (flat, from 100.0 to 100.1 = +0.10%)
    market_candles = [
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 50000},
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 50000},
        {"open": 100.0, "high": 100.3, "low": 99.9, "close": 100.1, "volume": 50000},
        {"open": 100.1, "high": 100.3, "low": 99.9, "close": 100.0, "volume": 50000},
        {"open": 100.0, "high": 100.3, "low": 99.9, "close": 100.1, "volume": 50000},
    ]

    signal = strategy.evaluate(
        symbol="TATASTEEL",
        sector="Metals",
        candles_15m=stock_candles,
        sector_constituents_candles={"TATASTEEL": stock_candles, "JSWSTEEL": peer_candles},
        market_candles_15m=market_candles,
        bucket_median_vol=10000.0,
        stock_sector_beta=1.1,
    )

    assert signal.passed_all_gates is True
    assert signal.decision == "SIGNAL_BUY"
    assert signal.entry_price == 101.8
    assert signal.residual_strength is not None and signal.residual_strength > 0
    assert signal.stop_price is not None and signal.stop_price < 101.8
    assert signal.shares is not None and signal.shares > 0
    assert signal.target_price is not None and signal.target_price > signal.entry_price
