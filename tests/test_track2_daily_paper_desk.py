from datetime import datetime, timedelta

import pytest

from antigravity.daemons.track2_daily_paper_desk import append_unique_paper_orders, baseline_from_history
from antigravity.models.session_manifest import IST


def _history(days=20, minute=30):
    bars = []
    start = datetime(2026, 8, 1, 9, 15, tzinfo=IST)
    for day in range(days):
        stamp = start + timedelta(days=day)
        for bucket in range(25):
            bars.append({
                "timestamp": (stamp + timedelta(minutes=15 * bucket)).isoformat(),
                "open": 100, "high": 104, "low": 98, "close": 102, "volume": 1000 + day,
            })
    return bars


def _daily_history(days=20):
    start = datetime(2026, 8, 1, 0, 0, tzinfo=IST)
    return [{
        "timestamp": (start + timedelta(days=day)).isoformat(),
        "open": 100, "high": 104, "low": 98, "close": 102, "volume": 25000 + day,
    } for day in range(days)]


def test_baseline_uses_prior_same_bucket_and_derives_positive_risk_inputs():
    result = baseline_from_history(
        _history(), daily_bars=_daily_history(),
        decision_timestamp="2026-09-22T09:30:00+05:30",
    )
    assert result["historical_bucket_volume_median"] > 0
    assert result["atr14_points"] == 6
    assert result["dtv_med20_cr"] > 0


def test_baseline_rejects_missing_history():
    with pytest.raises(ValueError, match="twenty"):
        baseline_from_history([], daily_bars=[], decision_timestamp="2026-09-22T09:30:00+05:30")


def test_baseline_rejects_too_few_same_bucket_observations():
    with pytest.raises(ValueError, match="twenty complete"):
        baseline_from_history(
            _history(days=4), daily_bars=_daily_history(days=4),
            decision_timestamp="2026-09-22T09:30:00+05:30",
        )


def test_baseline_accepts_days_without_unrelated_1515_bucket():
    result = baseline_from_history(
        _history(), daily_bars=_daily_history(),
        decision_timestamp="2026-09-22T09:30:00+05:30",
    )
    assert result["daily_sessions"] == result["same_bucket_sessions"] == 20


def test_baseline_rejects_missing_matching_bucket():
    intraday = [bar for bar in _history() if not bar["timestamp"].startswith("2026-08-20T09:30")]
    with pytest.raises(ValueError, match="twenty prior same-bucket"):
        baseline_from_history(
            intraday, daily_bars=_daily_history(),
            decision_timestamp="2026-09-22T09:30:00+05:30",
        )


def test_paper_orders_append_once_without_claiming_fill(tmp_path):
    signal = {
        "symbol": "CDSL", "signal_timestamp": "2026-09-22T09:30:00+05:30",
        "limit_price": 100.0, "fill_claimed": False,
    }
    path = tmp_path / "orders.jsonl"
    assert append_unique_paper_orders(path, [signal]) == 1
    assert append_unique_paper_orders(path, [signal]) == 0
    assert "QUEUED_UNVERIFIED" in path.read_text(encoding="utf-8")
