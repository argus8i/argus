"""
tests/test_feed_validity.py
===========================
Tests for the shared live-feed validity gate.

Context: shared/live_depth.json had four consumers with four different (or
absent) notions of "is this usable". multi_stock_radar read watchlist LTPs and
stamped them KITE_LIVE with no check at all; live_signal_engine checked
is_stale/is_tab_hidden/STALE_TAB_BACKGROUNDED but not data_valid and not
STALE_DATA_FROZEN. These pin the single gate they now all call.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from antigravity.daemons.feed_validity import (
    MAX_SNAPSHOT_AGE_SEC,
    check_feed,
    snapshot_age_seconds,
    usable_watchlist,
)

NOW = datetime(2026, 9, 17, 14, 30, 0)


def snap(**over):
    base = {
        "data_valid": True,
        "is_stale": False,
        "is_tab_hidden": False,
        "status": "LIVE_STREAMING",
        "local_write_time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "watchlist": [{"symbol": "MOBIKWIK", "ltp": 202.91}],
    }
    base.update(over)
    return base


def test_healthy_snapshot_is_usable():
    ok, reason = check_feed(snap(), now=NOW)
    assert ok is True and reason is None


@pytest.mark.parametrize("over,expected", [
    ({"data_valid": False, "invalid_reason": "TAB_HIDDEN"}, "TAB_HIDDEN"),
    ({"data_valid": False}, "DATA_INVALID"),
    ({"is_tab_hidden": True}, "TAB_HIDDEN"),
    ({"is_stale": True}, "STALE"),
    ({"status": "STALE_TAB_BACKGROUNDED"}, "STALE_TAB_BACKGROUNDED"),
    ({"status": "STALE_DATA_FROZEN"}, "STALE_DATA_FROZEN"),
])
def test_unusable_states_are_rejected_with_reason(over, expected):
    ok, reason = check_feed(snap(**over), now=NOW)
    assert ok is False
    assert reason == expected


def test_stale_data_frozen_is_rejected():
    """live_signal_engine's old inline check missed this status entirely."""
    ok, reason = check_feed(snap(status="STALE_DATA_FROZEN"), now=NOW)
    assert ok is False and reason == "STALE_DATA_FROZEN"


def test_old_snapshot_is_dead_even_when_flags_look_healthy():
    """A crashed producer leaves a healthy-looking file on disk forever."""
    old = NOW - timedelta(seconds=MAX_SNAPSHOT_AGE_SEC + 30)
    ok, reason = check_feed(
        snap(local_write_time=old.strftime("%Y-%m-%d %H:%M:%S")), now=NOW
    )
    assert ok is False
    assert "EXCEEDS" in reason


def test_snapshot_just_inside_the_age_limit_is_usable():
    fresh = NOW - timedelta(seconds=MAX_SNAPSHOT_AGE_SEC - 1)
    ok, _ = check_feed(
        snap(local_write_time=fresh.strftime("%Y-%m-%d %H:%M:%S")), now=NOW
    )
    assert ok is True


@pytest.mark.parametrize("bad", [None, {}, [], "text", 42])
def test_fails_closed_on_junk(bad):
    ok, reason = check_feed(bad, now=NOW)
    assert ok is False and reason == "NO_SNAPSHOT"


@pytest.mark.parametrize("ts", [None, "", "not-a-date", 12345, "17/09/2026 14:30"])
def test_fails_closed_on_unparseable_timestamp(ts):
    ok, reason = check_feed(snap(local_write_time=ts), now=NOW)
    assert ok is False and reason == "NO_TIMESTAMP"


def test_missing_data_valid_fails_closed():
    """data_valid must be exact Boolean True; absence or non-bool is rejected."""
    s = snap()
    del s["data_valid"]
    ok, reason = check_feed(s, now=NOW)
    assert ok is False
    assert reason == "DATA_VALID_NOT_BOOLEAN_TRUE"

    # String "false" or "true" or None must also fail closed
    assert check_feed(snap(data_valid="false"), now=NOW)[0] is False
    assert check_feed(snap(data_valid="true"), now=NOW)[0] is False
    assert check_feed(snap(data_valid=None), now=NOW)[0] is False


def test_connected_no_data_is_rejected():
    """CONNECTED_NO_DATA must be rejected as unusable."""
    ok, reason = check_feed(snap(status="CONNECTED_NO_DATA"), now=NOW)
    assert ok is False
    assert reason == "CONNECTED_NO_DATA"


def test_unhashable_status_fails_closed():
    """List or dict status must not raise TypeError; fails closed with INVALID_STATUS_TYPE."""
    ok, reason = check_feed(snap(status=["STALE_DATA_FROZEN"]), now=NOW)
    assert ok is False
    assert reason == "INVALID_STATUS_TYPE"

    ok2, reason2 = check_feed(snap(status={"status": "LIVE"}), now=NOW)
    assert ok2 is False
    assert reason2 == "INVALID_STATUS_TYPE"


# --------------------------------------------------------------------------
# usable_watchlist - the field that actually leaked
# --------------------------------------------------------------------------

def test_watchlist_returned_when_feed_is_healthy():
    wl = usable_watchlist(snap(), now=NOW)
    assert len(wl) == 1 and wl[0]["symbol"] == "MOBIKWIK"


@pytest.mark.parametrize("over", [
    {"data_valid": False},
    {"data_valid": "false"},
    {"is_tab_hidden": True},
    {"is_stale": True},
    {"status": "STALE_DATA_FROZEN"},
    {"status": "CONNECTED_NO_DATA"},
])
def test_watchlist_is_empty_whenever_feed_is_unusable(over):
    """multi_stock_radar stamps these LTPs KITE_LIVE; a stale one must not
    reach that stamp."""
    assert usable_watchlist(snap(**over), now=NOW) == []


def test_watchlist_sanitizes_malformed_items():
    """Rejects items with non-string symbol, zero/negative LTP, NaN or infinity."""
    malformed_wl = [
        {"symbol": "GOOD", "ltp": 125.50},
        {"symbol": "ZERO_PRICE", "ltp": 0.0},
        {"symbol": "NEG_PRICE", "ltp": -10.0},
        {"symbol": "NAN_PRICE", "ltp": float("nan")},
        {"symbol": "INF_PRICE", "ltp": float("inf")},
        {"symbol": "", "ltp": 50.0},
        {"symbol": 12345, "ltp": 50.0},
        "not-a-dict",
        {"symbol": "BAD_PRICE_STR", "ltp": "garbage"},
    ]
    wl = usable_watchlist(snap(watchlist=malformed_wl), now=NOW)
    assert len(wl) == 1
    assert wl[0]["symbol"] == "GOOD"
    assert wl[0]["ltp"] == 125.50


def test_watchlist_empty_for_stale_timestamp():
    old = NOW - timedelta(seconds=MAX_SNAPSHOT_AGE_SEC + 1)
    assert usable_watchlist(
        snap(local_write_time=old.strftime("%Y-%m-%d %H:%M:%S")), now=NOW
    ) == []


def test_watchlist_tolerates_wrong_type():
    assert usable_watchlist(snap(watchlist=None), now=NOW) == []
    assert usable_watchlist(snap(watchlist="oops"), now=NOW) == []


def test_snapshot_age_seconds_basic():
    older = NOW - timedelta(seconds=45)
    age = snapshot_age_seconds(
        snap(local_write_time=older.strftime("%Y-%m-%d %H:%M:%S")), now=NOW
    )
    assert age == pytest.approx(45.0)
    assert snapshot_age_seconds({"local_write_time": "junk"}, now=NOW) is None
