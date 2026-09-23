from antigravity.models.market_regime_filter import MarketRegimeSnapshot, MarketRegimeState
from antigravity.models.track2_orb_signal_adapter import evaluate_symbol


RULES = {"strategy": "15M_ORB", "risk_rs": 1500, "min_volume_multiple": 2.5}
REGIME = MarketRegimeSnapshot(
    MarketRegimeState.BULLISH_EXPANSION, 25000, 24900, 24800, 1.5, 300, 200,
    "verified bullish", True, 2.5,
)
BASELINE = {"historical_bucket_volume_median": 1000, "atr14_points": 4, "dtv_med20_cr": 100}


def _record(close=103, volume=3000):
    return {"raw_sha256": "a" * 64, "bars": [
        {"timestamp": "2026-09-22T09:15:00+05:30", "high": 102, "low": 99, "close": 101, "volume": 1000},
        {"timestamp": "2026-09-22T09:30:00+05:30", "high": close, "low": 101, "close": close, "volume": volume},
    ]}


def test_direct_bars_create_rule_bound_paper_instruction_without_fill_claim():
    result = evaluate_symbol(symbol="CDSL", candle_record=_record(), baseline=BASELINE, regime=REGIME, order_rules=RULES)
    assert result["decision"] == "PAPER_SIGNAL"
    assert result["paper_instruction"]["fill_claimed"] is False
    assert result["paper_instruction"]["evidence_class"] == "E1_BAR_POSSIBLE"
    assert len(result["paper_instruction"]["strategy_rules_sha256"]) == 64


def test_insufficient_volume_emits_no_instruction():
    result = evaluate_symbol(symbol="CDSL", candle_record=_record(volume=100), baseline=BASELINE, regime=REGIME, order_rules=RULES)
    assert result["paper_instruction"] is None
    assert result["decision"] == "HOLD_REJECT_FALSE_BREAKOUT"


def test_missing_baseline_fails_closed():
    result = evaluate_symbol(symbol="CDSL", candle_record=_record(), baseline={}, regime=REGIME, order_rules=RULES)
    assert result == {"symbol": "CDSL", "decision": "DATA_INVALID", "paper_instruction": None}


def test_missing_opening_bar_fails_closed():
    record = _record()
    record["bars"][0]["timestamp"] = "2026-09-22T09:00:00+05:30"
    result = evaluate_symbol(symbol="CDSL", candle_record=record, baseline=BASELINE, regime=REGIME, order_rules=RULES)
    assert result["decision"] == "DATA_INVALID"
