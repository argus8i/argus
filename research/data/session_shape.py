"""
research/data/session_shape.py
==============================
Expected shape of an NSE cash session in 15-minute bars (plan D10, P3.8).

- Bars are stamped at their START, IST. Slot 0 starts 09:15; slot k starts 09:15 + 15k minutes.
- Before the closing auction session (CAS) went live on 3 Aug 2026, stocks traded continuously until
  15:30, giving 25 bars (the last starting 15:15).
- From 3 Aug 2026 continuous trading in stocks ends at 15:15 and 15:15-15:35 is the CAS. A 15:15 bar in
  a stock after that date is an auction print, not continuous trading, so stocks have 24 bars.
- Index series keep 25 bars on both sides of the change (checked on the Kite file: NIFTY has 25).

Strategies and calibrations use returns 1..23 only (slot 0 carries the overnight gap; slot 24 does
not exist for post-CAS stocks), which makes both shapes comparable.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List

CAS_START = date(2026, 8, 3)
FIRST_BAR = time(9, 15)
BAR_MINUTES = 15
CALIBRATION_SLOTS = range(1, 24)          # returns r[1..23]


def slot_start(slot: int) -> time:
    base = datetime.combine(date(2000, 1, 1), FIRST_BAR)
    return (base + timedelta(minutes=BAR_MINUTES * slot)).time()


def slot_of(t: time) -> int | None:
    """Slot index of a bar start, or None when it is not on the 15-minute grid from 09:15."""
    minutes = (t.hour * 60 + t.minute) - (FIRST_BAR.hour * 60 + FIRST_BAR.minute)
    if t.second or t.microsecond or minutes < 0 or minutes % BAR_MINUTES:
        return None
    slot = minutes // BAR_MINUTES
    return slot if slot <= 24 else None


def expected_bar_count(kind: str, day: date) -> int:
    """kind is INDEX or TRADABLE (CandleStore.kind)."""
    if kind == "INDEX":
        return 25
    return 25 if day < CAS_START else 24


def expected_slots(kind: str, day: date) -> List[int]:
    return list(range(expected_bar_count(kind, day)))


def is_cas_auction_bar(kind: str, day: date, start: time) -> bool:
    return kind != "INDEX" and day >= CAS_START and start == slot_start(24)
