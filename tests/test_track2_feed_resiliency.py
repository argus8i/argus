"""
test_track2_feed_resiliency.py - Tests for Feed Latency Resiliency & Clock Drift Compensation
=============================================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON).
"""

from datetime import datetime, timezone, timedelta
import pytest
from antigravity.daemons.track2_daily_paper_desk import (
    completed_bars,
    load_current_candles,
    CLOCK_SKEW_TOLERANCE_SECONDS,
    IST,
)


def test_completed_bars_clock_drift_compensation():
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=IST)
    
    # 1. Slight future-dated request due to clock drift (-1.5s skew) -> Must be accepted
    drift_requested = now + timedelta(seconds=1.5)
    record = {
        "requested_at": drift_requested.isoformat(),
        "bars": [
            {
                "timestamp": "2026-09-22T09:15:00+05:30",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 50000,
            }
        ],
    }
    bars = completed_bars(record, start="2026-09-22", end="2026-09-22", now=now, max_age=90.0)
    assert len(bars) == 1
    assert bars[0]["open"] == 100.0


def test_completed_bars_genuine_stale_rejection():
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=IST)
    
    # 2. Genuine stale request (> 90s stale) -> Must fail closed
    stale_requested = now - timedelta(seconds=120.0)
    record = {
        "requested_at": stale_requested.isoformat(),
        "bars": [
            {
                "timestamp": "2026-09-22T09:15:00+05:30",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 50000,
            }
        ],
    }
    with pytest.raises(ValueError, match="stale or future-dated"):
        completed_bars(record, start="2026-09-22", end="2026-09-22", now=now, max_age=90.0)


def test_completed_bars_excessive_future_dated_rejection():
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=IST)
    
    # 3. Excessive future-dated request (> 5s tolerance) -> Must fail closed
    excessive_future = now + timedelta(seconds=10.0)
    record = {
        "requested_at": excessive_future.isoformat(),
        "bars": [
            {
                "timestamp": "2026-09-22T09:15:00+05:30",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 50000,
            }
        ],
    }
    with pytest.raises(ValueError, match="stale or future-dated"):
        completed_bars(record, start="2026-09-22", end="2026-09-22", now=now, max_age=90.0)
