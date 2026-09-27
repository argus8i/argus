"""
research/backtest/bars.py
=========================
OHLCV bar containers, the NSE equity tick-size table, resampling and the loader for
shared/track2_liquid/historical_candles_track2.json.

Tick table (equity, effective 15-Apr-2025, as recorded in the 23-Sep audit; verify against the
current NSE circular before relying on it). The repo's own 32-session history is consistent with it:
stocks below Rs 250 print in Rs 0.01 steps and Rs 1,000-5,000 stocks in Rs 0.10 steps.

    price <= 250        0.01
    250 < p <= 1,000    0.05
    1,000 < p <= 5,000  0.10
    5,000 < p <= 10,000 0.50
    10,000 < p <= 20,000 1.00
    p > 20,000          5.00

Index series (NIFTY*) are computed values, not traded prices, and are excluded from tick checks.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN = time(9, 15)

_TICK_TABLE = ((250.0, 0.01), (1000.0, 0.05), (5000.0, 0.10), (10000.0, 0.50), (20000.0, 1.00),
               (math.inf, 5.00))


def is_index_symbol(symbol: str) -> bool:
    """True only for registered indices or IDX:-prefixed names (plan D5). The old substring test
    ("NIFTY" in name) treated INDIA VIX as a stock."""
    from research.data.indices import is_index
    return is_index(symbol)


def tick_size(price: float) -> float:
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError(f"tick_size needs a positive finite price, got {price!r}")
    for ceiling, tick in _TICK_TABLE:
        if price <= ceiling:
            return tick
    raise AssertionError("unreachable")


def round_to_tick(price: float, direction: str = "nearest") -> float:
    """Round onto the NSE grid. 'down' for buy limits, 'up' for sell limits."""
    tick = tick_size(price)
    steps = price / tick
    if direction == "down":
        n = math.floor(steps + 1e-9)
    elif direction == "up":
        n = math.ceil(steps - 1e-9)
    elif direction == "nearest":
        n = math.floor(steps + 0.5)
    else:
        raise ValueError(f"direction must be down/up/nearest, got {direction!r}")
    return round(n * tick, 2)


def on_tick_grid(price: float, tol: float = 1e-6) -> bool:
    tick = tick_size(price)
    steps = price / tick
    return abs(steps - round(steps)) < tol * max(1.0, steps)


@dataclass(frozen=True)
class Bar:
    symbol: str
    start: datetime          # bar START, timezone-aware
    minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: int

    def __post_init__(self) -> None:
        prices = (self.open, self.high, self.low, self.close)
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0
               for p in prices):
            raise ValueError(f"{self.symbol} {self.start}: non-positive or non-finite price")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError(f"{self.symbol} {self.start}: inconsistent OHLC {prices}")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
            raise ValueError(f"{self.symbol} {self.start}: negative or non-integer volume")
        if self.minutes <= 0:
            raise ValueError("bar length must be positive")
        if self.start.tzinfo is None:
            raise ValueError("bar start must be timezone-aware")

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def hm(self) -> str:
        return self.start.astimezone(IST).strftime("%H:%M")

    @property
    def session_date(self) -> date:
        return self.start.astimezone(IST).date()

    def as_mapping(self) -> Dict[str, object]:
        """Shape expected by the antigravity strategy classes."""
        return {"timestamp": self.start.astimezone(IST).isoformat(), "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int

    def as_mapping(self) -> Dict[str, object]:
        return {"timestamp": self.day.isoformat(), "open": self.open, "high": self.high, "low": self.low,
                "close": self.close, "volume": self.volume}


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value!r}")
    return ts.astimezone(IST)


def resample(bars: Sequence[Bar], minutes: int) -> List[Bar]:
    """Aggregate finer bars into `minutes` buckets aligned to the 09:15 session open."""
    buckets: Dict[datetime, List[Bar]] = defaultdict(list)
    for b in sorted(bars, key=lambda x: x.start):
        start = b.start.astimezone(IST)
        anchor = datetime.combine(start.date(), SESSION_OPEN, IST)
        k = int((start - anchor).total_seconds() // (minutes * 60))
        buckets[anchor + timedelta(minutes=k * minutes)].append(b)
    out = []
    for start, group in sorted(buckets.items()):
        out.append(Bar(group[0].symbol, start, minutes, group[0].open, max(x.high for x in group),
                       min(x.low for x in group), group[-1].close, sum(x.volume for x in group)))
    return out


class CandleStore:
    """Session-indexed 15m bars and daily bars per symbol."""

    def __init__(self, intraday: Mapping[str, Mapping[date, List[Bar]]], daily: Mapping[str, List[DailyBar]],
                 issues: Mapping[str, int] | None = None, mode: str = "STRATEGY"):
        self._intraday = {s: dict(v) for s, v in intraday.items()}
        self._daily = {s: list(v) for s, v in daily.items()}
        self._issues = dict(issues or {})
        self.mode = mode          # "QA" stores are refused by the engine (research/data/holdout.py)

    @classmethod
    def from_historical_json(cls, path: Path | str, guard=None, mode: str = "STRATEGY") -> "CandleStore":
        """Load the canonical candle JSON. Every load passes through a HoldoutGuard (plan P3.7): in
        STRATEGY mode, holdout-dated bars are dropped unless the pre-registration is locked; mode="QA"
        keeps everything and marks the store so strategy code refuses it."""
        from research.data.holdout import default_guard

        guard = guard or default_guard(mode=mode)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        symbols = raw.get("symbols")
        if not isinstance(symbols, dict) or not symbols:
            raise ValueError(f"{path}: no 'symbols' mapping")
        intraday: Dict[str, Dict[date, List[Bar]]] = {}
        daily: Dict[str, List[DailyBar]] = {}
        issues = {"invalid_bars": 0, "duplicate_bars": 0}
        for sym, entry in symbols.items():
            per_day: Dict[date, Dict[datetime, Bar]] = defaultdict(dict)
            for rec in entry.get("bars") or []:
                try:
                    start = _parse_ts(rec["timestamp"])
                    bar = Bar(sym, start, 15, float(rec["open"]), float(rec["high"]), float(rec["low"]),
                              float(rec["close"]), int(rec["volume"]))
                except (KeyError, TypeError, ValueError):
                    issues["invalid_bars"] += 1
                    continue
                if start in per_day[bar.session_date]:
                    issues["duplicate_bars"] += 1
                    continue
                per_day[bar.session_date][start] = bar
            intraday[sym] = {d: [v[k] for k in sorted(v)] for d, v in per_day.items()}
            dl = []
            for rec in entry.get("daily_bars") or []:
                try:
                    dl.append(DailyBar(sym, _parse_ts(rec["timestamp"]).date(), float(rec["open"]), float(rec["high"]),
                                       float(rec["low"]), float(rec["close"]), int(rec["volume"])))
                except (KeyError, TypeError, ValueError):
                    issues["invalid_bars"] += 1
            daily[sym] = sorted(dl, key=lambda x: x.day)
        hidden = {d for v in intraday.values() for d in v if guard.hidden(d)}
        if hidden:
            intraday = {s: {d: b for d, b in v.items() if not guard.hidden(d)} for s, v in intraday.items()}
        daily = {s: [d for d in v if not guard.hidden(d.day)] for s, v in daily.items()}
        issues["holdout_hidden_sessions"] = len(hidden)
        return cls(intraday, daily, issues, mode=guard.mode)

    @property
    def symbols(self) -> List[str]:
        return sorted(self._intraday)

    def kind(self, symbol: str) -> str:
        """INDEX or TRADABLE (plan D5). The engine never evaluates strategies on an INDEX series."""
        return "INDEX" if is_index_symbol(symbol) else "TRADABLE"

    def sessions(self, symbol: str) -> List[date]:
        return sorted(self._intraday.get(symbol, {}))

    def bars(self, symbol: str, day: date) -> List[Bar]:
        return list(self._intraday.get(symbol, {}).get(day, []))

    def daily(self, symbol: str) -> List[DailyBar]:
        return list(self._daily.get(symbol, []))

    def daily_before(self, symbol: str, day: date) -> List[DailyBar]:
        """Point-in-time: completed daily bars strictly before `day`."""
        return [d for d in self._daily.get(symbol, []) if d.day < day]

    def quality_report(self) -> Dict[str, object]:
        off_tick = 0
        prices = 0
        zero_volume = 0
        bars_per_session: Dict[str, Dict[int, int]] = {}
        for sym, days in self._intraday.items():
            counts: Dict[int, int] = defaultdict(int)
            for bars in days.values():
                counts[len(bars)] += 1
                for b in bars:
                    zero_volume += b.volume == 0
                    if is_index_symbol(sym):
                        continue
                    for p in (b.open, b.high, b.low, b.close):
                        prices += 1
                        off_tick += not on_tick_grid(p)
            bars_per_session[sym] = dict(counts)
        return {"invalid_bars": self._issues.get("invalid_bars", 0),
                "duplicate_bars": self._issues.get("duplicate_bars", 0),
                "off_tick_prices": off_tick, "prices_checked": prices, "zero_volume_bars": zero_volume,
                "bars_per_session": bars_per_session}
