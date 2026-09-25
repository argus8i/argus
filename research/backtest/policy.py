"""
research/backtest/policy.py
===========================
One session policy object for Track 2 MIS on Dhan. Every time-based decision in the backtester
reads from here, so there is a single clock (audit finding: the 15:10 rule used to live in four places).

Dhan squares off cash intraday positions at 15:10 once the closing auction applies (dhan.co support
page, fetched 24-Sep-2026). The desk must therefore be flat and reconciled before that, not at it:

    09:30  first entry allowed (after the 09:15-09:30 opening range completes)
    14:50  freeze: no new entries
    15:00  cancel all pending entries
    15:05  bounded exits (passive/limit)
    15:08  hard escalation (marketable exits) - flat deadline
    15:10  broker RMS square-off (Rs 20 + GST per order) if anything is still open
    15:15  continuous trading ends, closing auction begins
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import Enum


class SessionPhase(str, Enum):
    PRE_ENTRY = "PRE_ENTRY"
    ENTRIES_OPEN = "ENTRIES_OPEN"
    FROZEN = "FROZEN"                 # no new entries, pending entries still live
    CANCEL_PENDING = "CANCEL_PENDING"
    BOUNDED_EXIT = "BOUNDED_EXIT"
    HARD_EXIT = "HARD_EXIT"
    BROKER_RMS = "BROKER_RMS"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class SessionPolicy:
    entry_start: time = time(9, 30)
    freeze_entries: time = time(14, 50)
    cancel_pending: time = time(15, 0)
    bounded_exit: time = time(15, 5)
    hard_escalation: time = time(15, 8)
    broker_rms: time = time(15, 10)
    continuous_close: time = time(15, 15)
    broker: str = "DHAN"

    def __post_init__(self) -> None:
        order = [self.entry_start, self.freeze_entries, self.cancel_pending, self.bounded_exit,
                 self.hard_escalation, self.broker_rms, self.continuous_close]
        if any(a >= b for a, b in zip(order, order[1:])):
            raise ValueError(f"session policy times must be strictly increasing: {order}")

    def entries_allowed(self, t: time) -> bool:
        return self.entry_start <= t < self.freeze_entries

    def phase_at(self, t: time) -> SessionPhase:
        if t < self.entry_start:
            return SessionPhase.PRE_ENTRY
        if t < self.freeze_entries:
            return SessionPhase.ENTRIES_OPEN
        if t < self.cancel_pending:
            return SessionPhase.FROZEN
        if t < self.bounded_exit:
            return SessionPhase.CANCEL_PENDING
        if t < self.hard_escalation:
            return SessionPhase.BOUNDED_EXIT
        if t < self.broker_rms:
            return SessionPhase.HARD_EXIT
        if t < self.continuous_close:
            return SessionPhase.BROKER_RMS
        return SessionPhase.CLOSED
