"""
research/features/events.py
===========================
Point-in-time corporate events (plan P4.3). Every query answers True, False or None; None means the
answer is unknown, and every strategy treats unknown as blocked.

Keys are the times the market learned of the event (plan P3.4):
- announcements: the exchange dissemination timestamp;
- scheduled results and board meetings: the intimation timestamp (not the meeting date);
- ex-dates: keyed by the corporate-action announcement timestamp.

Implementations
- NoEventsData: no history is available (the case today: plan decision 7 is open). Every answer is None,
  so RESID_REV blocks every signal; only the registered variant RESID_REV_NF, which has no news filter,
  can be evaluated.
- TableEvents: in-memory tables; the P3.4 loader (not built until decision 7) will fill them.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple


class EventsProvider:
    def news_since(self, symbol: str, from_ts: datetime, to_ts: datetime) -> Optional[bool]:
        raise NotImplementedError

    def scheduled_event(self, symbol: str, session: date, as_of_ts: datetime) -> Optional[bool]:
        raise NotImplementedError

    def ex_date(self, symbol: str, session: date, as_of_ts: datetime) -> Optional[bool]:
        raise NotImplementedError

    @property
    def coverage(self) -> str:
        return "NONE"


class NoEventsData(EventsProvider):
    def news_since(self, symbol, from_ts, to_ts):
        return None

    def scheduled_event(self, symbol, session, as_of_ts):
        return None

    def ex_date(self, symbol, session, as_of_ts):
        return None


@dataclass
class TableEvents(EventsProvider):
    """announcements: symbol -> sorted dissemination timestamps.
    scheduled: symbol -> list of (intimated_at, event_session) for results/board meetings.
    ex_dates: symbol -> list of (announced_at, ex_session).
    covered: (first, last) datetimes the tables are complete for; outside it every answer is None."""
    announcements: Dict[str, List[datetime]] = field(default_factory=dict)
    scheduled: Dict[str, List[Tuple[datetime, date]]] = field(default_factory=dict)
    ex_dates: Dict[str, List[Tuple[datetime, date]]] = field(default_factory=dict)
    covered: Optional[Tuple[datetime, datetime]] = None

    def _in_cover(self, *ts: datetime) -> bool:
        return self.covered is not None and all(self.covered[0] <= t <= self.covered[1] for t in ts)

    def news_since(self, symbol, from_ts, to_ts):
        if not self._in_cover(from_ts, to_ts):
            return None
        xs = sorted(self.announcements.get(symbol, []))
        i = bisect.bisect_right(xs, from_ts)          # strictly after from_ts
        return i < len(xs) and xs[i] <= to_ts

    def scheduled_event(self, symbol, session, as_of_ts):
        """True if a results/board meeting on `session` or the next trading day was intimated at or before
        as_of_ts. 'Next trading day' is approximated as the next calendar session in the table's events;
        the caller passes both sessions when it has a calendar (see resid_rev)."""
        if not self._in_cover(as_of_ts):
            return None
        return any(intim <= as_of_ts and ev == session for intim, ev in self.scheduled.get(symbol, []))

    def ex_date(self, symbol, session, as_of_ts):
        if not self._in_cover(as_of_ts):
            return None
        return any(ann <= as_of_ts and ex == session for ann, ex in self.ex_dates.get(symbol, []))

    @property
    def coverage(self) -> str:
        return "TABLE" if self.covered else "NONE"


# NSE moved equity-derivative expiries from Thursday to Tuesday from 1 Sep 2025 (as recorded in the desk
# mandate; verify against the NSE circular before this flag is used for anything but logging).
EXPIRY_TUESDAY_FROM = date(2025, 9, 1)


def is_monthly_stock_expiry(session: date, trading_days: Optional[Sequence[date]] = None) -> bool:
    """Diagnostic only (logged, never gated): stock options expire on the month's last Tuesday (last
    Thursday before EXPIRY_TUESDAY_FROM); if that is not a trading day, the previous trading day.
    Without a calendar, the nominal weekday is used."""
    import calendar

    weekday = 1 if session >= EXPIRY_TUESDAY_FROM else 3
    last = max(d for d in range(1, calendar.monthrange(session.year, session.month)[1] + 1)
               if date(session.year, session.month, d).weekday() == weekday)
    expiry = date(session.year, session.month, last)
    if trading_days:
        days = sorted(d for d in trading_days if d.year == session.year and d.month == session.month and d <= expiry)
        if days:
            expiry = days[-1]
    return session == expiry
