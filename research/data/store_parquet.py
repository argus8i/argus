"""
research/data/store_parquet.py
==============================
ParquetCandleStore (plan P3.7): the CandleStore interface over the parquet history written by
research/data/ingest_json.py (or, later, the Dhan downloader in the same layout).

- Symbols are discovered from the per-symbol files; the symbol stored inside each file is authoritative.
- Loading is lazy: a symbol's arrays are read on first use. Bar objects are built per (symbol, session)
  and kept in an LRU cache sized for a rolling 60-session window across the universe.
- Index series carry canonical IDX: names (ingest canonicalises them).
- Provenance (research/data/provenance.py): every file carries a `source` class. In STRATEGY mode the
  store refuses to open a history that contains any QA_ONLY source (Yahoo, Kite, HF/Upstox mirror,
  unlabelled); such data opens only in QA mode.
- Every read passes through a HoldoutGuard. In STRATEGY mode holdout-dated bars are invisible unless the
  pre-registration is locked; mode="QA" serves everything, logs every read, and marks the store so the
  engine refuses it.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from research.backtest.bars import Bar, DailyBar, is_index_symbol
from research.data import paths, provenance
from research.data.holdout import HoldoutGuard, default_guard

IST = timezone(timedelta(hours=5, minutes=30))


class _SymbolData:
    __slots__ = ("sessions", "ranges", "start_epoch", "o", "h", "l", "c", "v", "daily")

    def __init__(self) -> None:
        self.sessions: List[date] = []
        self.ranges: Dict[date, Tuple[int, int]] = {}
        self.daily: List[DailyBar] = []


class ParquetCandleStore:
    def __init__(self, root: Optional[Path | str] = None, guard: Optional[HoldoutGuard] = None,
                 mode: str = "STRATEGY", cache_sessions: int = 60, cache_symbols: int = 250) -> None:
        self.root = Path(root) if root else paths.history_dir()
        self.guard = guard or default_guard(mode=mode)
        self.mode = self.guard.mode
        self.sources: Dict[str, str] = {}
        self._files: Dict[str, Dict[str, Path]] = self._discover()
        blocked = sorted(s for s, c in self.sources.items() if not provenance.strategy_eligible(c))
        if self.mode != "QA" and blocked:
            by_class: Dict[str, int] = {}
            for s in blocked:
                by_class[self.sources[s]] = by_class.get(self.sources[s], 0) + 1
            raise provenance.SourceNotAllowedError(
                f"{self.root}: {len(blocked)} symbols come from QA_ONLY sources {by_class} "
                f"(e.g. {blocked[:3]}); open with mode='QA' for validation only")
        self._data: Dict[str, _SymbolData] = {}
        self._bar_cache: "OrderedDict[Tuple[str, date], List[Bar]]" = OrderedDict()
        self._cache_max = max(1, cache_sessions * cache_symbols)

    # ------------------------------------------------------------------ discovery and loading
    def _discover(self) -> Dict[str, Dict[str, Path]]:
        import pyarrow.parquet as pq

        found: Dict[str, Dict[str, Path]] = {}
        for sub in ("bars_15m", "daily"):
            d = self.root / sub
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.parquet")):
                names = pq.read_schema(f).names
                cols = ["symbol"] + (["source"] if "source" in names else [])
                t = pq.read_table(f, columns=cols)
                if t.num_rows == 0:
                    continue
                sym = str(t.column("symbol")[0].as_py())
                found.setdefault(sym, {})[sub] = f
                # a file without a source column predates provenance stamping: UNKNOWN, so QA_ONLY
                classes = set(t.column("source").to_pylist()) if "source" in names else {provenance.UNKNOWN}
                for c in classes:
                    prev = self.sources.get(sym)
                    if prev is None or provenance.strategy_eligible(prev):
                        self.sources[sym] = str(c)       # any QA_ONLY class wins for the symbol
        if not found:
            raise FileNotFoundError(f"no parquet history under {self.root}")
        return found

    def _load(self, symbol: str) -> _SymbolData:
        if symbol in self._data:
            return self._data[symbol]
        import pyarrow.parquet as pq

        sd = _SymbolData()
        files = self._files.get(symbol, {})
        if "bars_15m" in files:
            t = pq.read_table(files["bars_15m"]).to_pydict()
            order = np.argsort(np.asarray(t["start_epoch"], dtype=np.int64), kind="stable")
            sd.start_epoch = np.asarray(t["start_epoch"], dtype=np.int64)[order]
            sd.o, sd.h, sd.l, sd.c = (np.asarray(t[k], dtype=float)[order] for k in ("open", "high", "low", "close"))
            sd.v = np.asarray(t["volume"], dtype=np.int64)[order]
            sess = np.asarray(t["session"])[order]
            i0 = 0
            for i in range(1, len(sess) + 1):
                if i == len(sess) or sess[i] != sess[i0]:
                    day = date.fromisoformat(str(sess[i0]))
                    if not self.guard.hidden(day):
                        sd.ranges[day] = (i0, i)
                        sd.sessions.append(day)
                    i0 = i
        else:
            sd.start_epoch = np.zeros(0, dtype=np.int64)
            sd.o = sd.h = sd.l = sd.c = np.zeros(0)
            sd.v = np.zeros(0, dtype=np.int64)
        if "daily" in files:
            t = pq.read_table(files["daily"]).to_pydict()
            rows = []
            for k in range(len(t["day"])):
                day = date.fromisoformat(str(t["day"][k]))
                if self.guard.hidden(day):
                    continue
                vol = int(t["volume"][k])
                rows.append(DailyBar(symbol, day, float(t["open"][k]), float(t["high"][k]), float(t["low"][k]),
                                     float(t["close"][k]), max(vol, 0)))
            sd.daily = sorted(rows, key=lambda x: x.day)
        self._data[symbol] = sd
        return sd

    # ------------------------------------------------------------------ CandleStore interface
    @property
    def symbols(self) -> List[str]:
        return sorted(self._files)

    def kind(self, symbol: str) -> str:
        return "INDEX" if is_index_symbol(symbol) else "TRADABLE"

    def sessions(self, symbol: str) -> List[date]:
        if symbol not in self._files:
            return []
        return list(self._load(symbol).sessions)

    def bars(self, symbol: str, day: date) -> List[Bar]:
        if symbol not in self._files:
            return []
        self.guard.check(symbol, day)
        key = (symbol, day)
        if key in self._bar_cache:
            self._bar_cache.move_to_end(key)
            return list(self._bar_cache[key])
        sd = self._load(symbol)
        rng = sd.ranges.get(day)
        out: List[Bar] = []
        if rng:
            for k in range(*rng):
                vol = int(sd.v[k])
                if vol < 0:          # volume missing at source: keep the bar, validate.py flags the session
                    vol = 0
                out.append(Bar(symbol, datetime.fromtimestamp(int(sd.start_epoch[k]), IST), 15, float(sd.o[k]),
                               float(sd.h[k]), float(sd.l[k]), float(sd.c[k]), vol))
        self._bar_cache[key] = out
        if len(self._bar_cache) > self._cache_max:
            self._bar_cache.popitem(last=False)
        return list(out)

    def daily(self, symbol: str) -> List[DailyBar]:
        if symbol not in self._files:
            return []
        return list(self._load(symbol).daily)

    def daily_before(self, symbol: str, day: date) -> List[DailyBar]:
        import bisect

        rows = self.daily(symbol)
        k = bisect.bisect_left([d.day for d in rows], day)
        return rows[:k]

    # ------------------------------------------------------------------ fast paths for features/
    def session_arrays(self, symbol: str, day: date) -> Optional[Dict[str, np.ndarray]]:
        """Numpy views of one session (no Bar objects); None if absent. Used by research/features."""
        if symbol not in self._files:
            return None
        self.guard.check(symbol, day)
        sd = self._load(symbol)
        rng = sd.ranges.get(day)
        if not rng:
            return None
        a, b = rng
        return {"start_epoch": sd.start_epoch[a:b], "open": sd.o[a:b], "high": sd.h[a:b], "low": sd.l[a:b],
                "close": sd.c[a:b], "volume": sd.v[a:b]}
