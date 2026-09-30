"""
feed_validity.py - single authoritative gate for live market-data snapshots.

Why this exists
---------------
shared/live_depth.json (and its Track 2 twin) had four consumers with four
different notions of "is this feed usable":

    multi_stock_radar.py   no check at all - read watchlist LTPs and stamped
                           them KITE_LIVE
    track2_live_radar.py   no check at all
    track2_kite_bridge.py  no check at all
    live_signal_engine.py  checked is_stale / is_tab_hidden /
                           STALE_TAB_BACKGROUNDED and timestamp age, but not
                           data_valid and not STALE_DATA_FROZEN

A producer-side fail-closed gate only protects consumers that happen to read
the fields it nulls. Anything reading a field the gate misses - watchlist was
exactly that case - gets stale prices wearing a live label. The gate belongs in
one place every consumer calls, so adding a new stale status or a new snapshot
field cannot silently bypass a subset of readers.

Fails closed: anything unparseable, unexpected or missing is unusable.
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Statuses the producer sets when the DOM read cannot be trusted.
STALE_STATUSES = frozenset({
    "STALE_TAB_BACKGROUNDED",
    "STALE_DATA_FROZEN",
    "CONNECTED_NO_DATA",
})

# A snapshot older than this is dead regardless of its flags: the producer may
# have crashed, leaving the last good file on disk looking perfectly healthy.
MAX_SNAPSHOT_AGE_SEC = 120.0

# Maximum allowed clock-skew for a snapshot claiming a future timestamp.
# Any snapshot further in the future than this tolerance fails closed.
MAX_FUTURE_SKEW_SEC = 5.0

SNAPSHOT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def snapshot_age_seconds(
    snapshot: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Age of the snapshot in seconds (positive if in the past, negative if in the future),
    or None if it cannot be determined."""
    ts_str = snapshot.get("local_write_time")
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        ts = datetime.strptime(ts_str.strip(), SNAPSHOT_TIME_FORMAT)
    except (ValueError, TypeError):
        return None
    return ((now or datetime.now()) - ts).total_seconds()


def check_feed(
    snapshot: Any,
    now: Optional[datetime] = None,
    max_age_sec: float = MAX_SNAPSHOT_AGE_SEC,
    max_future_skew_sec: float = MAX_FUTURE_SKEW_SEC,
) -> Tuple[bool, Optional[str]]:
    """Return (usable, reason_if_not).

    Usable means every market-data field in the snapshot may be treated as a
    live quote. Not usable means none of them may be - including watchlist
    LTPs, which is the field that leaked.
    Fails closed on missing or non-boolean data_valid, unhashable/non-string status,
    or stale timestamps.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False, "NO_SNAPSHOT"

    # data_valid must be exact Boolean True
    data_valid = snapshot.get("data_valid")
    if data_valid is not True:
        if data_valid is False:
            return False, snapshot.get("invalid_reason") or "DATA_INVALID"
        return False, "DATA_VALID_NOT_BOOLEAN_TRUE"

    if snapshot.get("is_tab_hidden") is True:
        return False, "TAB_HIDDEN"

    if snapshot.get("is_stale") is True:
        return False, "STALE"

    status = snapshot.get("status")
    if status is not None:
        if not isinstance(status, str):
            return False, "INVALID_STATUS_TYPE"
        if status in STALE_STATUSES:
            return False, status

    age = snapshot_age_seconds(snapshot, now=now)
    if age is None:
        return False, "NO_TIMESTAMP"
    if age < -max_future_skew_sec:
        return False, f"FUTURE_TIMESTAMP_{abs(age):.0f}S_EXCEEDS_TOLERANCE_{max_future_skew_sec:.0f}S"
    if age > max_age_sec:
        return False, f"SNAPSHOT_AGE_{age:.0f}S_EXCEEDS_{max_age_sec:.0f}S"

    return True, None


def usable_watchlist(
    snapshot: Any,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Watchlist entries, or [] when the snapshot is not usable.

    multi_stock_radar and track2_live_radar both read watchlist LTPs and label
    them live; routing them through this stops a stale price acquiring a
    KITE_LIVE stamp. Validates that symbols are non-empty strings and LTPs
    are finite, positive numbers.
    """
    ok, _ = check_feed(snapshot, now=now)
    if not ok:
        return []
    wl = snapshot.get("watchlist")
    if not isinstance(wl, list):
        return []

    sanitized = []
    for item in wl:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol")
        if not sym or not isinstance(sym, str) or not sym.strip():
            continue
        ltp = item.get("ltp")
        try:
            ltp_float = float(ltp)
            if math.isnan(ltp_float) or math.isinf(ltp_float) or ltp_float <= 0:
                continue
        except (ValueError, TypeError):
            continue
        sanitized.append(item)
    return sanitized

