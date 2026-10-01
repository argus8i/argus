"""
tests/test_day3_strategies.py
=============================
Rigorous adversarial and compliance test suite for Sprint Day 3 Quantitative Alpha Strategies.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Verifies:
1. SignalEvent & ExitSignalEvent immutable contracts, Rule 2 price floor, and order-level pricing invariants.
2. Mathematical indicator calculation accuracy (TR, ATR, SMA, EMA, RSI) and fail-closed edge cases.
3. Sleeve A: Institutional Delivery Accumulation setup and exit logic.
4. Sleeve B: 52-Week High Anchoring Momentum setup and exit logic.
5. Sleeve C: Post-Expiry Relief & Gamma Pin Mean Reversion setup and exit logic.
6. Pre-registered YAML specification loading and deterministic priority sorting.
"""

import math
from pathlib import Path
import pytest

from antigravity.strategies.base_strategy import (
    BaseSwingStrategy,
    SignalEvent,
    ExitSignalEvent,
)
from antigravity.strategies.delivery_accumulation import DeliveryAccumulationStrategy
from antigravity.strategies.high52_momentum import High52MomentumStrategy
from antigravity.strategies.expiry_relief import ExpiryReliefStrategy


# =============================================================================
# 1. SignalEvent & ExitSignalEvent Immutable Contract Tests
# =============================================================================

def test_signal_event_valid_instantiation():
    event = SignalEvent(
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SBIN",
        session_date="2026-10-01",
        entry_session="2026-10-02",
        reference_price=800.0,
        stop_loss_price=776.0,
        target_price=848.0,
        priority_score=3.5,
        trace={"test": 123},
    )
    assert event.strategy_id == "DELIVERY_ACCUMULATION"
    assert event.symbol == "SBIN"
    assert event.risk_per_share == 24.0
    assert event.target_gain_per_share == 48.0
    assert event.reward_risk_ratio == 2.0
    assert event.created_at != ""


def test_signal_event_frozen_immutability():
    event = SignalEvent(
        strategy_id="HIGH52_MOMENTUM",
        symbol="RELIANCE",
        session_date="2026-10-01",
        entry_session="2026-10-02",
        reference_price=3000.0,
        stop_loss_price=2900.0,
        target_price=3200.0,
    )
    with pytest.raises(Exception):
        event.reference_price = 3050.0  # Cannot mutate frozen instance


def test_signal_event_rule2_price_floor_rejection():
    # AGENTS.md Rule 2: Sub-Rs 10.00 immediately rejected
    with pytest.raises(ValueError, match="violates Rule 2 floor"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="PENNY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=9.95,
            stop_loss_price=8.50,
            target_price=12.00,
        )


def test_signal_event_inverted_stop_loss_rejection():
    # Stop loss >= reference price rejected fail-closed
    with pytest.raises(ValueError, match="must be strictly less than"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=1500.0,
            stop_loss_price=1520.0,
            target_price=1600.0,
        )


def test_signal_event_invalid_target_rejection():
    # Target price <= reference price rejected fail-closed
    with pytest.raises(ValueError, match="must be strictly greater than"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=1500.0,
            stop_loss_price=1450.0,
            target_price=1490.0,
        )


def test_signal_event_nan_inf_boolean_rejections():
    with pytest.raises(ValueError, match="must be a finite float"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=float("nan"),
            stop_loss_price=1450.0,
            target_price=1600.0,
        )
    with pytest.raises(ValueError, match="must be a finite float"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=1500.0,
            stop_loss_price=1450.0,
            target_price=float("inf"),
        )
    with pytest.raises(ValueError, match="must be a finite float"):
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            session_date="2026-10-01",
            entry_session="2026-10-02",
            reference_price=True,  # boolean rejected
            stop_loss_price=1450.0,
            target_price=1600.0,
        )


def test_exit_signal_event_validation():
    exit_event = ExitSignalEvent(
        strategy_id="EXPIRY_RELIEF",
        symbol="TATASTEEL",
        session_date="2026-10-01",
        position_id="POS_001",
        reason="TARGET_HIT",
        exit_price=160.0,
        shares_to_exit=200,
    )
    assert exit_event.exit_price == 160.0
    assert exit_event.shares_to_exit == 200
    assert exit_event.to_dict()["reason"] == "TARGET_HIT"

    with pytest.raises(ValueError, match="shares_to_exit must be a positive integer"):
        ExitSignalEvent(
            strategy_id="EXPIRY_RELIEF",
            symbol="TATASTEEL",
            session_date="2026-10-01",
            position_id="POS_001",
            reason="TARGET_HIT",
            exit_price=160.0,
            shares_to_exit=0,
        )


# =============================================================================
# 2. Pure Indicator Calculations
# =============================================================================

def test_true_range_calculation():
    # Normal bar
    assert BaseSwingStrategy.calculate_true_range(105.0, 95.0, 100.0) == 10.0
    # Gap up: High-PrevClose is largest
    assert BaseSwingStrategy.calculate_true_range(120.0, 110.0, 100.0) == 20.0
    # Gap down: PrevClose-Low is largest
    assert BaseSwingStrategy.calculate_true_range(95.0, 80.0, 100.0) == 20.0


def test_atr_calculation():
    highs = [100.0 + i for i in range(25)]
    lows = [90.0 + i for i in range(25)]
    closes = [95.0 + i for i in range(25)]

    # 14-period ATR
    atr = BaseSwingStrategy.calculate_atr(highs, lows, closes, period=14)
    assert atr is not None
    assert 9.0 <= atr <= 11.0

    # Insufficient bars returns None fail-closed
    assert BaseSwingStrategy.calculate_atr(highs[:10], lows[:10], closes[:10], period=14) is None
    # Non-finite values return None
    assert BaseSwingStrategy.calculate_atr([float("nan")] * 20, lows[:20], closes[:20], period=14) is None


def test_sma_and_ema_calculation():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert BaseSwingStrategy.calculate_sma(vals, 5) == 30.0
    assert BaseSwingStrategy.calculate_sma(vals, 10) is None

    # EMA test
    prices = [100.0] * 30
    ema = BaseSwingStrategy.calculate_ema(prices, 20)
    assert ema == 100.0


def test_rsi_calculation():
    # Constant up prices: RSI should be 100
    up_prices = [100.0 + i * 2 for i in range(20)]
    assert BaseSwingStrategy.calculate_rsi(up_prices, 14) == 100.0

    # Constant down prices: RSI should be 0
    down_prices = [200.0 - i * 2 for i in range(20)]
    assert BaseSwingStrategy.calculate_rsi(down_prices, 14) == 0.0

    # Mixed alternating prices
    alt_prices = [100.0, 102.0, 101.0, 103.0, 102.0] * 5
    rsi = BaseSwingStrategy.calculate_rsi(alt_prices, 14)
    assert rsi is not None and 30.0 < rsi < 70.0


# =============================================================================
# 3. Sleeve A: Delivery Accumulation Tests
# =============================================================================

def make_delivery_bars(n=25, base_price=500.0, compressed=True, vol_expansion=True, deliv_expansion=True):
    bars = []
    # Earlier bars: establish wider ATR (~5.0)
    for i in range(n - 6):
        high = base_price + 3.0
        low = base_price - 3.0
        close = base_price
        vol = 1_000_000.0
        deliv = 20.0
        bars.append({
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "delivery_pct": deliv,
            "session_date": f"2026-09-{i+1:02d}",
        })

    # 5 compression bars preceding the trigger bar
    for i in range(n - 6, n - 1):
        if compressed:
            high = base_price + 1.0
            low = base_price - 1.0
        else:
            high = base_price + 20.0
            low = base_price - 20.0
        close = base_price
        vol = 1_000_000.0
        deliv = 20.0
        bars.append({
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "delivery_pct": deliv,
            "session_date": f"2026-09-{i+1:02d}",
        })

    # Trigger bar (last bar): breakout above 5-day high (501.0)
    deliv = 50.0 if deliv_expansion else 20.0
    vol = 2_000_000.0 if vol_expansion else 1_000_000.0
    high = base_price + 4.0
    close = base_price + 3.0
    low = base_price - 1.0
    bars.append({
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
        "delivery_pct": deliv,
        "session_date": f"2026-09-{n:02d}",
    })
    return bars


def test_delivery_accumulation_successful_signal():
    strat = DeliveryAccumulationStrategy()
    bars = make_delivery_bars(n=25, base_price=500.0)

    market_data = {
        "HDFCBANK": {
            "metadata": {
                "is_fno_underlying": True,
                "is_surveillance": False,
                "series": "EQ",
            },
            "bars": bars,
        }
    }

    signals = strat.generate_signals("2026-09-25", market_data, context={"next_session": "2026-09-26"})
    assert len(signals) == 1
    sig = signals[0]
    assert sig.symbol == "HDFCBANK"
    assert sig.strategy_id == "DELIVERY_ACCUMULATION"
    assert sig.reference_price == 503.0
    assert sig.stop_loss_price < 503.0
    assert sig.target_price > 503.0
    assert sig.priority_score > 0.0


def test_delivery_accumulation_rejections():
    strat = DeliveryAccumulationStrategy()

    # 1. Non-FNO underlying rejected
    bars = make_delivery_bars(n=25)
    data = {"NON_FNO": {"metadata": {"is_fno_underlying": False, "is_surveillance": False, "series": "EQ"}, "bars": bars}}
    assert len(strat.generate_signals("2026-09-25", data)) == 0

    # 2. Surveillance scrip rejected
    data = {"SURV": {"metadata": {"is_fno_underlying": True, "is_surveillance": True, "series": "EQ"}, "bars": bars}}
    assert len(strat.generate_signals("2026-09-25", data)) == 0

    # 3. Non-EQ series rejected
    data = {"BE_SERIES": {"metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "BE"}, "bars": bars}}
    assert len(strat.generate_signals("2026-09-25", data)) == 0

    # 4. Sub-Rs 10 price rejected (Rule 2)
    penny_bars = make_delivery_bars(n=25, base_price=8.0)
    data = {"PENNY": {"metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"}, "bars": penny_bars}}
    assert len(strat.generate_signals("2026-09-25", data)) == 0

    # 5. Uncompressed range rejected
    uncomp_bars = make_delivery_bars(n=25, compressed=False)
    data = {"UNCOMP": {"metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"}, "bars": uncomp_bars}}
    assert len(strat.generate_signals("2026-09-25", data)) == 0


def test_delivery_accumulation_exits():
    strat = DeliveryAccumulationStrategy()
    open_pos = [
        {
            "strategy_id": "DELIVERY_ACCUMULATION",
            "symbol": "HDFCBANK",
            "position_id": "POS_01",
            "entry_price": 500.0,
            "stop_price": 490.0,
            "target_price": 520.0,
            "shares": 50,
            "holding_sessions": 2,
        }
    ]

    # Target hit
    bars = {"HDFCBANK": {"high": 522.0, "low": 505.0, "close": 518.0, "open": 506.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-28")
    assert len(exits) == 1
    assert exits[0].reason == "TARGET_HIT"

    # Stop hit
    bars = {"HDFCBANK": {"high": 495.0, "low": 488.0, "close": 490.0, "open": 492.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-28")
    assert len(exits) == 1
    assert exits[0].reason == "STOP_LOSS"

    # Time stop (session 7)
    open_pos[0]["holding_sessions"] = 7
    bars = {"HDFCBANK": {"high": 510.0, "low": 495.0, "close": 505.0, "open": 500.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-28")
    assert len(exits) == 1
    assert exits[0].reason == "TIME_STOP"

    # Trailing exit after session 3 when close < EMA5
    open_pos[0]["holding_sessions"] = 4
    bars = {"HDFCBANK": {"high": 510.0, "low": 495.0, "close": 498.0, "open": 500.0, "ema5": 502.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-28")
    assert len(exits) == 1
    assert exits[0].reason == "TRAILING_STOP"


# =============================================================================
# 4. Sleeve B: 52-Week High Momentum Tests
# =============================================================================

def make_high52_bars(n=100, base_high=1000.0, near_52w=True, breaking_out=True):
    bars = []
    for i in range(n):
        high = base_high * 0.90 + i * 0.5
        low = high - 10.0
        close = high - 2.0
        vol = 500_000.0
        bars.append({"high": high, "low": low, "close": close, "volume": vol})

    # Set 52w high at index 20
    bars[20]["high"] = base_high

    if near_52w:
        # Last bar is within 2% of base_high
        bars[-1]["high"] = base_high * 0.99
        bars[-1]["close"] = base_high * 0.985
    else:
        bars[-1]["high"] = base_high * 0.85
        bars[-1]["close"] = base_high * 0.84

    if breaking_out:
        # Prior 20 bars capped below breakout close
        for b in bars[-21:-1]:
            b["high"] = min(b["high"], base_high * 0.975)
            b["close"] = min(b["close"], base_high * 0.97)
        if near_52w:
            bars[-1]["close"] = base_high * 0.985
        else:
            bars[-1]["high"] = base_high * 0.85
            bars[-1]["close"] = base_high * 0.84
    else:
        # Not breaking out: prior 20d high is above current close
        bars[-5]["high"] = base_high * 0.995

    return bars


def test_high52_momentum_signal():
    strat = High52MomentumStrategy()
    bars = make_high52_bars(n=100, near_52w=True, breaking_out=True)

    market_data = {
        "RELIANCE": {
            "metadata": {
                "is_fno_underlying": True,
                "is_surveillance": False,
                "series": "EQ",
            },
            "bars": bars,
        }
    }

    signals = strat.generate_signals("2026-10-01", market_data)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.symbol == "RELIANCE"
    assert sig.strategy_id == "HIGH52_MOMENTUM"
    assert sig.reference_price == 985.0
    assert sig.stop_loss_price < 985.0
    assert sig.target_price > 985.0
    assert sig.priority_score >= 0.97


def test_high52_momentum_rejections():
    strat = High52MomentumStrategy()

    # Further than 3% from 52w high
    bars = make_high52_bars(n=100, near_52w=False)
    data = {"FAR_FROM_HIGH": {"metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"}, "bars": bars}}
    assert len(strat.generate_signals("2026-10-01", data)) == 0

    # Not breaking out of 20d high
    bars2 = make_high52_bars(n=100, near_52w=True, breaking_out=False)
    data2 = {"NO_BREAKOUT": {"metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"}, "bars": bars2}}
    assert len(strat.generate_signals("2026-10-01", data2)) == 0


def test_high52_momentum_exits():
    strat = High52MomentumStrategy()
    open_pos = [
        {
            "strategy_id": "HIGH52_MOMENTUM",
            "symbol": "RELIANCE",
            "position_id": "POS_MOM",
            "entry_price": 980.0,
            "stop_price": 950.0,
            "target_price": 1040.0,
            "shares": 30,
            "holding_sessions": 2,
        }
    ]

    # Target hit
    bars = {"RELIANCE": {"high": 1045.0, "low": 990.0, "close": 1030.0, "open": 995.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-10-02")
    assert len(exits) == 1
    assert exits[0].reason == "TARGET_HIT"

    # Time stop on session 10
    open_pos[0]["holding_sessions"] = 10
    bars = {"RELIANCE": {"high": 1010.0, "low": 970.0, "close": 990.0, "open": 985.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-10-02")
    assert len(exits) == 1
    assert exits[0].reason == "TIME_STOP"

    # Trailing exit after session 3 if close < 20 EMA
    open_pos[0]["holding_sessions"] = 5
    bars = {"RELIANCE": {"high": 1010.0, "low": 965.0, "close": 968.0, "open": 975.0, "ema20": 974.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-10-02")
    assert len(exits) == 1
    assert exits[0].reason == "TRAILING_STOP"


# =============================================================================
# 5. Sleeve C: Post-Expiry Relief Tests
# =============================================================================

def make_expiry_bars(n=30, base_price=100.0, cycle_decline_pct=10.0, oversold=True):
    bars = []
    for i in range(n):
        p = base_price - (i * 0.4)
        bars.append({"high": p + 1.0, "low": p - 1.0, "close": p, "volume": 4_000_000.0})

    # Ensure last bar is heavily down from bar 20 sessions ago
    start_p = bars[-21]["close"]
    bars[-1]["close"] = start_p * (1.0 - (cycle_decline_pct / 100.0))
    bars[-1]["high"] = bars[-1]["close"] + 0.5
    bars[-1]["low"] = bars[-1]["close"] - 1.0
    bars[-1]["volume"] = 4_000_000.0

    return bars


def test_expiry_relief_signals():
    strat = ExpiryReliefStrategy()
    bars = make_expiry_bars(n=30, cycle_decline_pct=10.0)

    market_data = {
        "TATASTEEL": {
            "metadata": {
                "is_fno_underlying": True,
                "is_surveillance": False,
                "series": "EQ",
                "nearest_fut_expiry": "2026-09-24",
            },
            "bars": bars,
        }
    }

    # On non-expiry date: zero signals
    no_sig = strat.generate_signals("2026-09-23", market_data, context={"is_expiry_session": False})
    assert len(no_sig) == 0

    # On expiry date: signal fires
    sigs = strat.generate_signals(
        "2026-09-24",
        market_data,
        context={"is_expiry_session": True, "next_session": "2026-09-25"}
    )
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.symbol == "TATASTEEL"
    assert sig.strategy_id == "EXPIRY_RELIEF"
    assert sig.reference_price > 10.0
    assert sig.stop_loss_price < sig.reference_price
    assert sig.target_price > sig.reference_price
    assert sig.priority_score > 0.0


def test_expiry_relief_rejections():
    strat = ExpiryReliefStrategy()

    # Decline only 4% (less than min 8.0%)
    mild_bars = make_expiry_bars(n=30, cycle_decline_pct=4.0)
    data = {
        "MILD_DROP": {
            "metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"},
            "bars": mild_bars,
        }
    }
    sigs = strat.generate_signals("2026-09-24", data, context={"is_expiry_session": True})
    assert len(sigs) == 0


def test_expiry_relief_exits():
    strat = ExpiryReliefStrategy()
    open_pos = [
        {
            "strategy_id": "EXPIRY_RELIEF",
            "symbol": "TATASTEEL",
            "position_id": "POS_EXP",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 104.0,
            "shares": 100,
            "holding_sessions": 2,
        }
    ]

    # Target hit
    bars = {"TATASTEEL": {"high": 105.0, "low": 99.0, "close": 103.5, "open": 99.5}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-26")
    assert len(exits) == 1
    assert exits[0].reason == "TARGET_HIT"

    # 5-day Time Stop
    open_pos[0]["holding_sessions"] = 5
    bars = {"TATASTEEL": {"high": 102.0, "low": 98.0, "close": 101.0, "open": 99.0}}
    exits = strat.evaluate_exits(open_pos, bars, "2026-09-26")
    assert len(exits) == 1
    assert exits[0].reason == "TIME_STOP"


# =============================================================================
# 6. YAML Spec Loading & Deterministic Ranking Tests
# =============================================================================

def test_yaml_spec_loading():
    spec_dir = Path("shared/track2_liquid/strategies/specs")
    strat_a = DeliveryAccumulationStrategy(spec_path=spec_dir / "delivery_accumulation_v1.yaml")
    assert strat_a.strategy_id == "DELIVERY_ACCUMULATION"
    assert strat_a.min_price == 10.00
    assert strat_a.delivery_exp_ratio == 2.0

    strat_b = High52MomentumStrategy(spec_path=spec_dir / "high52_momentum_v1.yaml")
    assert strat_b.strategy_id == "HIGH52_MOMENTUM"
    assert strat_b.lookback_52w == 252

    strat_c = ExpiryReliefStrategy(spec_path=spec_dir / "expiry_relief_v1.yaml")
    assert strat_c.strategy_id == "EXPIRY_RELIEF"
    assert strat_c.min_cycle_decline_pct == 8.0


def test_simultaneous_signals_deterministic_sorting():
    strat = DeliveryAccumulationStrategy()
    bars1 = make_delivery_bars(n=25, base_price=500.0)
    bars1[-1]["volume"] = 2_000_000.0
    bars1[-1]["delivery_pct"] = 45.0

    bars2 = make_delivery_bars(n=25, base_price=600.0)
    bars2[-1]["volume"] = 3_000_000.0
    bars2[-1]["delivery_pct"] = 65.0  # Higher priority

    market_data = {
        "LOWER_SCORE": {
            "metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"},
            "bars": bars1,
        },
        "HIGHER_SCORE": {
            "metadata": {"is_fno_underlying": True, "is_surveillance": False, "series": "EQ"},
            "bars": bars2,
        },
    }

    signals = strat.generate_signals("2026-09-25", market_data)
    assert len(signals) == 2
    # HIGHER_SCORE must be ranked first
    assert signals[0].symbol == "HIGHER_SCORE"
    assert signals[1].symbol == "LOWER_SCORE"
    assert signals[0].priority_score > signals[1].priority_score
