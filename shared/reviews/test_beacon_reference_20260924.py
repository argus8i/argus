"""Isolated reference tests; deliberately not a production approval suite."""
from datetime import datetime, timedelta, timezone
from dataclasses import FrozenInstanceError, replace
import json
import pytest

from shared.reviews.beacon_reference_20260924 import (
    Provenance, Depth, MultiLevelOFICalculator, DerivativesAlphaSnapshot,
    DerivativesOverlayEngine, VIXRegimeClassification, UnifiedEnsembleSignal,
    CompassIntegrationBridge, QueueState, DiscreteExecutionSimulator as Sim,
)

NOW = datetime(2026, 9, 24, 5, 0, tzinfo=timezone.utc)


def provenance(seconds=0):
    return Provenance("TEST:SYMBOL", NOW+timedelta(seconds=seconds),
                      NOW+timedelta(seconds=seconds), "a"*64)


def depth(seconds=0, ask_shift=0, ask_qty=100):
    return Depth(provenance(seconds), tuple((100.-i, 100., 102.+i+ask_shift, ask_qty) for i in range(5)))


def derivatives(**updates):
    initial = DerivativesAlphaSnapshot(provenance(), "TEST", "2026-09-29",
        101., 95., 99., 1., 1., -.5, 100., None, .01)
    return replace(initial, **updates)


def signal(**updates):
    initial = UnifiedEnsembleSignal(provenance(), "TEST", "ORB", "TESTSECTOR",
        "BUY", .5, 100., 95., ((50, 107.5), (50, 115.)), 100, 20., 20.)
    return replace(initial, **updates)


def test_ask_improvement_is_negative_ofi():
    value = MultiLevelOFICalculator.calculate(depth(), depth(1, -.5), NOW+timedelta(seconds=1))
    assert value.normalized_ofi < 0
    assert 100 <= value.microprice <= 101.5
    assert value.iceberg_detected is None


def test_ask_withdrawal_is_positive_ofi():
    assert MultiLevelOFICalculator.calculate(depth(), depth(1, .5), NOW+timedelta(seconds=1)).normalized_ofi > 0


def test_more_ask_size_is_negative_ofi():
    assert MultiLevelOFICalculator.calculate(depth(), depth(1, ask_qty=200), NOW+timedelta(seconds=1)).normalized_ofi < 0


def test_stale_and_reordered_snapshots_reject():
    with pytest.raises(ValueError):
        MultiLevelOFICalculator.calculate(depth(), depth(1), NOW+timedelta(seconds=5))
    with pytest.raises(ValueError):
        MultiLevelOFICalculator.calculate(depth(1), depth(), NOW+timedelta(seconds=1))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -1, "100"])
def test_numeric_pollution_rejects(bad):
    with pytest.raises(ValueError):
        signal(entry=bad)


def test_frozen_nested_targets_and_json():
    value = signal()
    assert json.loads(value.to_json())["side"] == "BUY"
    with pytest.raises(FrozenInstanceError):
        value.shares = 200
    with pytest.raises(ValueError):
        signal(targets=[(100, 110.)])


def test_side_target_and_risk_bounds():
    with pytest.raises(ValueError):
        signal(side="SELL")
    with pytest.raises(ValueError):
        signal(shares=400, targets=((400, 110.),))
    assert signal(side="SELL", stop=105., targets=((100, 90.),)).side == "SELL"


def test_unknown_dealer_inventory_does_not_become_gex():
    with pytest.raises(ValueError):
        derivatives(dealer_gex_rs_per_1pct=1000.)
    result = DerivativesOverlayEngine.evaluate(derivatives(), 100., 4., NOW)
    assert result["live_approved"] is False
    assert result["dealer_gex"] is None
    assert "NEAR_CALL_OI_WALL_NOT_PROOF_OF_WRITING" in result["warnings"]


def test_future_expired_and_stale_data_reject():
    with pytest.raises(ValueError):
        Provenance("x", NOW+timedelta(seconds=1), NOW, "a"*64)
    with pytest.raises(ValueError):
        DerivativesOverlayEngine.evaluate(derivatives(expiry="2026-09-01"), 100., 4., NOW)
    with pytest.raises(ValueError):
        DerivativesOverlayEngine.evaluate(derivatives(), 100., 4., NOW+timedelta(minutes=2))


def test_crisis_never_auto_enables_recoil():
    with pytest.raises(ValueError):
        VIXRegimeClassification(provenance(), 23., "CRISIS", (("RECOIL", 1.),))
    assert VIXRegimeClassification(provenance(), 23., "CRISIS", (("RECOIL", 0.),)).tier == "CRISIS"


def test_quote_touch_cannot_fill_queue():
    assert Sim.step(QueueState(100, 20), "quote").filled == 0


def test_partial_then_full_and_replay_rejection():
    initial = Sim.step(QueueState(100, 20), "a", 110)
    assert (initial.state, initial.filled) == ("PARTIAL", 10)
    with pytest.raises(ValueError):
        Sim.step(initial, "a", 10)
    final = Sim.step(initial, "b", 10)
    assert final.state == "FILLED"
    assert Sim.evidence(final)["qualifies"] is False


def test_known_cancellation_reduces_queue():
    final = Sim.step(QueueState(100, 20), "a", 50, 70)
    assert final.state == "FILLED"


def test_lock_preserves_partial_inventory():
    partial = Sim.step(QueueState(0, 20), "a", 10)
    locked = Sim.step(partial, "b", contra_available=False)
    assert (locked.state, locked.filled) == ("LOCKED_NO_BID", 10)
    assert Sim.step(locked, "c", 10).state == "FILLED"


def test_contradictory_and_boolean_queue_inputs_reject():
    with pytest.raises(ValueError):
        Sim.step(QueueState(100, 20), "a", 10, contra_available=False)
    with pytest.raises(ValueError):
        QueueState(True, 20)


def test_compass_adapter_passes_peers_market_and_beta():
    seen = {}
    class Evaluator:
        def evaluate(self, **kwargs):
            seen.update(kwargs)
            return "evaluated"
    candidate = {"symbol": "A", "sector": "X", "candles_15m": [1]*5,
                 "stock_sector_beta": 1.2, "bucket_median_vol": 100., "current_ask": 100.}
    calls = []
    result = CompassIntegrationBridge.evaluate_portfolio_candidates(
        (candidate,), {"X": {s: [1]*5 for s in "ABCD"}}, [1]*5,
        Evaluator(), lambda *args: calls.append(args))
    assert result == ("evaluated",) and len(calls) == 1
    assert set(seen["sector_constituents_candles"]) == set("BCD")
    assert seen["stock_sector_beta"] == 1.2
    with pytest.raises(ValueError):
        CompassIntegrationBridge.evaluate_portfolio_candidates(
            (candidate,), {"X": {}}, [], Evaluator(), lambda *args: None)
