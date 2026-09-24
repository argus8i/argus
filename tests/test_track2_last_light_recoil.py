"""
test_track2_last_light_recoil.py - Tests for LAST LIGHT and RECOIL Alpha Strategies
===================================================================================
Part of Project Swing Trades (Track 2 Multi-Strategy Quantitative Desk).
"""

from datetime import time as dtime
import pytest

from antigravity.models.track2_last_light_strategy import LastLightStrategy, LastLightSignal
from antigravity.models.track2_recoil_strategy import RecoilStrategy, RecoilSignal
from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine, StrategyType


def create_15m_bar(bar_idx, o, h, l, c, v, ts=""):
    return {
        "bar_index": bar_idx,
        "timestamp": ts or f"2026-09-24T14:{15 * (bar_idx % 4):02d}:00+05:30",
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": int(v),
    }


def test_last_light_strategy_signal():
    """Verifies pre-close breakout setup between 14:00 and 14:30 IST."""
    strategy = LastLightStrategy(risk_budget_rs=1500.0, min_er8=0.35, min_rvol=1.20)

    # 4 bars of trend, 3 bars of afternoon consolidation, 1 breakout candle
    candles = [
        create_15m_bar(0, 100.0, 101.0, 99.8, 100.8, 10000, "2026-09-24T12:00:00+05:30"),
        create_15m_bar(1, 100.8, 102.0, 100.5, 101.8, 11000, "2026-09-24T12:15:00+05:30"),
        create_15m_bar(2, 101.8, 102.8, 101.6, 102.6, 12000, "2026-09-24T12:30:00+05:30"),
        create_15m_bar(3, 102.6, 103.5, 102.4, 103.4, 11500, "2026-09-24T12:45:00+05:30"),
        # Afternoon consolidation: range [103.0, 103.8] (range 0.8)
        create_15m_bar(4, 103.4, 103.7, 103.1, 103.5, 8000, "2026-09-24T13:00:00+05:30"),
        create_15m_bar(5, 103.5, 103.8, 103.2, 103.6, 7500, "2026-09-24T13:15:00+05:30"),
        create_15m_bar(6, 103.6, 103.7, 103.0, 103.4, 7000, "2026-09-24T13:30:00+05:30"),
        create_15m_bar(7, 103.4, 103.8, 103.1, 103.7, 8500, "2026-09-24T13:45:00+05:30"),
        # Breakout bar at 14:15 IST: breaks above 103.8 to close at 104.8 on 25,000 volume
        create_15m_bar(8, 103.7, 105.0, 103.6, 104.8, 25000, "2026-09-24T14:15:00+05:30"),
    ]

    res = strategy.evaluate_setup(
        symbol="RELIANCE",
        candles_15m=candles,
        bucket_median_vol=10000.0,
        current_time_ist=dtime(14, 15),
    )

    assert res.passed_all_gates is True
    assert res.decision == "SIGNAL_BUY"
    assert res.entry_price == 104.80
    assert res.stop_price < res.entry_price
    assert res.target_tranche1 > res.entry_price
    assert res.target_tranche2 > res.target_tranche1
    assert res.shares > 0
    assert res.hard_flat_time == "15:10:00"


def test_last_light_time_window_blocked():
    """Verifies that outside 14:00-14:30 IST, LAST LIGHT fails closed."""
    strategy = LastLightStrategy()
    candles = [create_15m_bar(i, 100.0 + i, 101.0 + i, 99.5 + i, 100.5 + i, 10000) for i in range(10)]

    res = strategy.evaluate_setup(
        symbol="TCS",
        candles_15m=candles,
        bucket_median_vol=10000.0,
        current_time_ist=dtime(11, 30),  # Morning time -> blocked
    )
    assert res.passed_all_gates is False
    assert res.decision == "TIME_WINDOW_BLOCKED"


def test_recoil_panic_capitulation_buy():
    """Verifies buying climax/capitulation reversal buy below VWAP."""
    strategy = RecoilStrategy(risk_budget_rs=1500.0, max_er8=0.45, min_rvol=2.0, min_stretch_atr=1.2)

    # Establish baseline VWAP near 1000.0 with choppy price action
    candles = [
        create_15m_bar(0, 1000.0, 1008.0, 992.0, 1002.0, 10000),
        create_15m_bar(1, 1002.0, 1010.0, 995.0, 998.0, 10000),
        create_15m_bar(2, 998.0, 1005.0, 992.0, 1001.0, 10000),
        create_15m_bar(3, 1001.0, 1009.0, 994.0, 999.0, 10000),
        create_15m_bar(4, 999.0, 1007.0, 993.0, 1003.0, 10000),
        create_15m_bar(5, 1003.0, 1010.0, 996.0, 1000.0, 10000),
        create_15m_bar(6, 1000.0, 1006.0, 993.0, 997.0, 10000),
        # Capitulation spike: opens 990.0, plunges to 975.0, bounces to close at 988.0 on 30,000 volume
        # Lower wick: (min(990.0, 988.0) - 975.0) = 13.0 / 15.0 = 86.7% wick!
        create_15m_bar(7, 990.0, 990.0, 975.0, 988.0, 30000),
    ]

    res = strategy.evaluate_setup(
        symbol="INFY",
        candles_15m=candles,
        bucket_median_vol=10000.0,
    )

    assert res.passed_all_gates is True
    assert res.decision == "SIGNAL_BUY"
    assert res.side == "BUY"
    assert res.entry_price == 988.00
    assert res.stop_price < res.entry_price
    assert res.target_price > res.entry_price  # Target is snapback to VWAP (~1000.0)
    assert res.shares > 0


def test_recoil_buying_climax_sell_fade():
    """Verifies selling climax fade above VWAP (MIS intraday short)."""
    strategy = RecoilStrategy(risk_budget_rs=1500.0, max_er8=0.45, min_rvol=2.0, min_stretch_atr=1.2)

    candles = [
        create_15m_bar(0, 1000.0, 1008.0, 992.0, 1002.0, 10000),
        create_15m_bar(1, 1002.0, 1010.0, 995.0, 998.0, 10000),
        create_15m_bar(2, 998.0, 1005.0, 992.0, 1001.0, 10000),
        create_15m_bar(3, 1001.0, 1009.0, 994.0, 999.0, 10000),
        create_15m_bar(4, 999.0, 1007.0, 993.0, 1003.0, 10000),
        create_15m_bar(5, 1003.0, 1010.0, 996.0, 1000.0, 10000),
        create_15m_bar(6, 1000.0, 1006.0, 993.0, 1001.0, 10000),
        # Buying climax: opens 1010.0, spikes to 1025.0, rejects back down to close at 1012.0 on 30,000 volume
        # Upper wick: (1025.0 - max(1010.0, 1012.0)) = 13.0 / 15.0 = 86.7% wick!
        create_15m_bar(7, 1010.0, 1025.0, 1008.0, 1012.0, 30000),
    ]

    res = strategy.evaluate_setup(
        symbol="SBIN",
        candles_15m=candles,
        bucket_median_vol=10000.0,
    )

    assert res.passed_all_gates is True
    assert res.decision == "SIGNAL_SELL"
    assert res.side == "SELL"
    assert res.entry_price == 1012.00
    assert res.stop_price > res.entry_price  # Stop above climax high
    assert res.target_price < res.entry_price  # Target below entry towards VWAP
    assert res.shares > 0


def test_multi_strategy_engine_includes_all_strategy_types():
    """Verifies StrategyType enum has all registered strategies."""
    engine = MultiStrategyEngine()
    registered_types = {s.value for s in StrategyType}
    expected_types = {
        "ORB_MOMENTUM",
        "VWAP_RECLAIM",
        "VOLATILITY_SQUEEZE",
        "TRAPDOOR",
        "COMPASS",
        "LAST_LIGHT",
        "RECOIL",
    }
    assert expected_types.issubset(registered_types)
    assert engine.last_light_engine is not None
    assert engine.recoil_engine is not None
    assert engine.trapdoor_engine is not None
