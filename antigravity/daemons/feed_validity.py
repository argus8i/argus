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

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Statuses the producer sets when the DOM read cannot be trusted.
STALE_STATUSES = frozenset({
    "STALE_TAB_BACKGROUNDED",
    "STALE_DATA_FROZEN",
})

# A snapshot older than this is dead regardless of its flags: the producer may
# have crashed, leaving the last good file on disk looking perfectly healthy.
MAX_SNAPSHOT_AGE_SEC = 120.0

SNAPSHOT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def snapshot_age_seconds(
    snapshot: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Age of the snapshot in seconds, or None if it cannot be determined."""
    ts_str = snapshot.get("local_write_time")
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        ts = datetime.strptime(ts_str.strip(), SNAPSHOT_TIME_FORMAT)
    except (ValueError, TypeError):
        return None
    return abs(((now or datetime.now()) - ts).total_seconds())


def check_feed(
    snapshot: Any,
    now: Optional[datetime] = None,
    max_age_sec: float = MAX_SNAPSHOT_AGE_SEC,
) -> Tuple[bool, Optional[str]]:
    """Return (usable, reason_if_not).

    Usable means every market-data field in the snapshot may be treated as a
    live quote. Not usable means none of them may be - including watchlist
    LTPs, which is the field that leaked.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False, "NO_SNAPSHOT"

    # data_valid is authoritative when present. Absent means an older producer
    # wrote the file, so fall through to the legacy flags rather than assume OK.
    if snapshot.get("data_valid") is False:
        return False, snapshot.get("invalid_reason") or "DATA_INVALID"

    if snapshot.get("is_tab_hidden") is True:
        return False, "TAB_HIDDEN"

    if snapshot.get("is_stale") is True:
        return False, "STALE"

    status = snapshot.get("status")
    if status in STALE_STATUSES:
        return False, status

    age = snapshot_age_seconds(snapshot, now=now)
    if age is None:
        return False, "NO_TIMESTAMP"
    if age > max_age_sec:
        return False, f"SNAPSHOT_AGE_{age:.0f}S_EXCEEDS_{max_age_sec:.0f}S"

    return True, None


def usable_watchlist(
    snapshot: Any,
    now: Optional[datetime] = None,
) -> List[Any]:
    """Watchlist entries, or [] when the snapshot is not usable.

    multi_stock_radar and track2_live_radar both read watchlist LTPs and label
    them live; routing them through this stops a stale price acquiring a
    KITE_LIVE stamp.
    """
    ok, _ = check_feed(snapshot, now=now)
    if not ok:
        return []
    wl = snapshot.get("watchlist")
    return wl if isinstance(wl, list) else []
