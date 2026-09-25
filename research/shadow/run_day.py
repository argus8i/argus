"""
research/shadow/run_day.py
==========================
P8.2 shadow runner. Paper/research only (AGENTS.md rule 1): it never places, modifies or cancels an order.

    python -m research.shadow.run_day tick   [--date D] [--now ISO]    # at every bar close + 60 s
    python -m research.shadow.run_day close  [--date D]                # after the session
    python -m research.shadow.run_day replay --date D                  # a past session, bar by bar

tick   Loads the live bar file, keeps closed bars from strategy-eligible sources, runs ORB_PROD and (from a
       locked pre-registration only) RESID_REV for day D through BacktestEngine, and appends every NEW
       signal to a hash-chained journal stamped with the wall-clock time it was written. Every tick also
       writes a heartbeat line, so a missed bar is visible.
close  Runs the day on the final bars, computes the E1 outcomes and writes one ledger row per signal
       (mode SHADOW), then the day report. A signal is prospective only if the journal holds it with the
       same side and prices, written no later than JOURNAL_LATENESS after its decision time; otherwise it is
       written as SHADOW_LATE / SHADOW_CHANGED / SHADOW_UNJOURNALED with evidence E1_CF (never
       admissible). A journal signal that the final run no longer produces (the feed revised a bar) is
       reported as withdrawn.
replay Builds the live file of a past session from the history, ticks it bar by bar with a simulated
       clock, and checks the journal against a one-shot engine run of the same day.

Fail-closed inputs:
- Live bars (shared/track2_liquid/live_candles_track2.json, written by antigravity/daemons/dhan_feed_bridge.py).
  A file for another session is refused. A symbol whose source_url host is not a strategy-eligible
  provenance class is dropped: kite.zerodha.com is KITE_WEB_SESSION (plan rule 1.2.11). A symbol with any
  malformed, off-grid, duplicate or inconsistent bar is dropped for the day. A bar that ends after
  now - 60 s is still forming and is not used.
- History: only post-CAS sessions (from 2026-08-03) strictly before D. The holdout stays hidden, and the
  24-bar post-CAS session shape is never mixed with the 25-bar one in a calibration window. The last
  history session must be the weekday before D; a gap is refused unless each gap day is declared a
  holiday (--holiday), which the journal records. (shared/track2_liquid/trading_calendar.json lists five
  2026 exchange holidays as trading days, MEASURED 26 Sep 2026, so it is not used.)
- Universe for D: universe_build rules with the F&O membership of the latest F&O session before D (D's own
  F&O bhavcopy is published after the close; flag FNO_MEMBERSHIP_PREV_SESSION), the ban list for D (a
  missing list makes every stock FO_BAN_UNKNOWN), and the wrong-company exclusions.
- RESID_REV runs only when resid_rev_v1.lock matches the YAML (P7.3). Until then it is not run and the
  heartbeat says why.
Known limitation: live bars and history can come from different vendors (Dhan feed, Upstox history); the
cross-vendor tolerance measured in P3 (max(2 ticks, 0.20%)) applies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

import numpy as np

from research.backtest.bars import Bar, DailyBar, is_index_symbol
from research.data import paths, provenance
from research.data.indices import canonical_index

IST = timezone(timedelta(hours=5, minutes=30))
POST_CAS_START = date(2026, 8, 3)
BAR = timedelta(minutes=15)
DECISION_DELAY = timedelta(seconds=60)
JOURNAL_LATENESS = timedelta(minutes=5)
FIRST_BAR = time(9, 15)
LIVE_FILE = "live_candles_track2.json"
SOURCE_HOSTS = {
    "api.dhan.co": provenance.DHAN,
    "api.upstox.com": provenance.UPSTOX,
    "kite.zerodha.com": provenance.KITE,
    "api.kite.trade": provenance.KITE,
}
Clock = Callable[[], datetime]


class ShadowError(RuntimeError):
    """The runner refuses to produce decisions (reason in the message)."""


def wall_clock() -> datetime:
    return datetime.now(IST)


def shadow_root() -> Path:
    return paths.shared_input("shadow")


def source_class(url: str) -> str:
    return SOURCE_HOSTS.get((urlparse(url or "").hostname or "").lower(), provenance.UNKNOWN)


# ---------------------------------------------------------------------------------------------- live bars
@dataclass
class LiveDay:
    day: date
    now: datetime
    bars: Dict[str, List[Bar]]
    file_sha256: str
    sources: Dict[str, str]
    dropped: Dict[str, str]
    issues: Counter = field(default_factory=Counter)


def _parse_bar(sym: str, b: Mapping[str, Any], day: date) -> Tuple[Optional[Bar], Optional[str]]:
    try:
        st = datetime.fromisoformat(str(b["timestamp"]))
    except (KeyError, ValueError):
        return None, "BAR_TIMESTAMP"
    if st.tzinfo is None:
        return None, "BAR_TIMESTAMP_NAIVE"
    st = st.astimezone(IST)
    if st.date() != day:
        return None, "BAR_OTHER_SESSION"
    minutes = st.hour * 60 + st.minute - (FIRST_BAR.hour * 60 + FIRST_BAR.minute)
    if minutes < 0 or minutes % 15 or st.second or st.microsecond:
        return None, "BAR_OFF_GRID"
    try:
        bar = Bar(sym, st, 15, float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]),
                  int(b.get("volume") or 0))
    except (KeyError, TypeError, ValueError):       # Bar rejects non-positive prices, inconsistent OHLC,
        return None, "BAR_INVALID"                  # negative volume
    return bar, None


def load_live(raw: bytes, day: date, now: datetime) -> LiveDay:
    """Validated closed bars of day D from the live file's bytes (see the module docstring)."""
    doc = json.loads(raw)
    if str(doc.get("session_date")) != day.isoformat():
        raise ShadowError(f"LIVE_FILE_WRONG_SESSION: file is {doc.get('session_date')}, run is {day}")
    if str(doc.get("interval")) not in ("15minute", "15m", "15"):
        raise ShadowError(f"LIVE_FILE_INTERVAL: {doc.get('interval')!r}")
    if doc.get("data_valid") is False:
        raise ShadowError("LIVE_FILE_MARKED_INVALID")
    cutoff = now.astimezone(IST) - DECISION_DELAY
    out = LiveDay(day, now, {}, hashlib.sha256(raw).hexdigest(), {}, {})
    for name, rec in sorted((doc.get("symbols") or {}).items()):
        sym = canonical_index(name) or str(name).strip().upper()
        cls = source_class(str(rec.get("source_url", "")))
        out.sources[sym] = cls
        if not provenance.strategy_eligible(cls):
            out.dropped[sym] = f"SOURCE_NOT_ALLOWED:{cls}"
            continue
        bars: List[Bar] = []
        seen, bad = set(), None
        for b in rec.get("bars") or []:
            bar, why = _parse_bar(sym, b, day)
            if why:
                bad = why
                break
            if bar.start in seen:
                bad = "BAR_DUPLICATE"
                break
            seen.add(bar.start)
            if bar.start + BAR > cutoff:
                out.issues["FORMING_BAR_NOT_USED"] += 1
                continue
            bars.append(bar)
        if bad:
            out.dropped[sym] = bad
            continue
        out.bars[sym] = sorted(bars, key=lambda x: x.start)
    return out


# ---------------------------------------------------------------------------------------------- store
class LiveDayStore:
    """History strictly before the live day and not before `history_from`, plus the live day's bars."""

    def __init__(self, history: Any, live: LiveDay, history_from: date = POST_CAS_START) -> None:
        self._h, self.live, self.day, self.first = history, live, live.day, history_from
        self._hist_symbols = set(history.symbols)
        self.mode = getattr(history, "mode", "STRATEGY")

    def _ok(self, d: date) -> bool:
        return self.first <= d < self.day

    @property
    def symbols(self) -> List[str]:
        return sorted(self._hist_symbols | set(self.live.bars))

    def kind(self, symbol: str) -> str:
        return "INDEX" if is_index_symbol(symbol) else "TRADABLE"

    def sessions(self, symbol: str) -> List[date]:
        hist = [d for d in self._h.sessions(symbol) if self._ok(d)] if symbol in self._hist_symbols else []
        return hist + ([self.day] if self.live.bars.get(symbol) else [])

    def bars(self, symbol: str, day: date) -> List[Bar]:
        if day == self.day:
            return list(self.live.bars.get(symbol, []))
        return self._h.bars(symbol, day) if self._ok(day) and symbol in self._hist_symbols else []

    @staticmethod
    def _arrays(bs: Sequence[Bar]) -> Optional[Dict[str, np.ndarray]]:
        if not bs:
            return None
        return {"start_epoch": np.array([int(b.start.timestamp()) for b in bs], dtype=np.int64),
                "open": np.array([b.open for b in bs]), "high": np.array([b.high for b in bs]),
                "low": np.array([b.low for b in bs]), "close": np.array([b.close for b in bs]),
                "volume": np.array([b.volume for b in bs], dtype=np.int64)}

    def session_arrays(self, symbol: str, day: date) -> Optional[Dict[str, np.ndarray]]:
        if day == self.day:
            return self._arrays(self.live.bars.get(symbol, []))
        if not self._ok(day) or symbol not in self._hist_symbols:
            return None
        if hasattr(self._h, "session_arrays"):
            return self._h.session_arrays(symbol, day)
        return self._arrays(self._h.bars(symbol, day))

    def daily(self, symbol: str) -> List[DailyBar]:
        """Every daily bar strictly before the live day. The post-CAS cutoff applies to intraday sessions only:
        daily closes do not depend on the session shape, and the VIX multiplier needs 120 of them (a cutoff
        here left ~40 and made m = 0 on every live day)."""
        if symbol not in self._hist_symbols:
            return []
        return [x for x in self._h.daily(symbol) if x.day < self.day]

    def daily_before(self, symbol: str, day: date) -> List[DailyBar]:
        return [x for x in self.daily(symbol) if x.day < day]


def check_history_fresh(history: Any, day: date, holidays: Iterable[date] = (),
                        reference: str = "IDX:NIFTY50") -> date:
    """The last history session before D; refuses a weekday gap that is not a declared holiday."""
    prior = [d for d in history.sessions(reference) if d < day]
    if not prior:
        raise ShadowError(f"HISTORY_EMPTY: no {reference} session before {day}")
    last = prior[-1]
    declared = set(holidays)
    d = last + timedelta(days=1)
    while d < day:
        if d.weekday() < 5 and d not in declared:
            raise ShadowError(f"HISTORY_STALE: history ends {last}; {d} is a weekday before {day} with no bars "
                              "(refresh the history, or pass --holiday for an exchange holiday)")
        d += timedelta(days=1)
    return last


# ---------------------------------------------------------------------------------------------- universe
class _OneDayView:
    """What universe_build.build needs for one live day: that day as the only session of each tradable
    with live bars, and daily bars strictly before it."""

    def __init__(self, store: LiveDayStore) -> None:
        self._s = store

    @property
    def symbols(self) -> List[str]:
        return self._s.symbols

    def kind(self, symbol: str) -> str:
        return self._s.kind(symbol)

    def sessions(self, symbol: str) -> List[date]:
        return [self._s.day] if self._s.live.bars.get(symbol) else []

    def daily(self, symbol: str) -> List[DailyBar]:
        return self._s.daily(symbol)


def live_universe(store: LiveDayStore, membership: Mapping[str, Iterable[date]],
                  banned: Mapping[str, Iterable[date]], ban_known: Iterable[date],
                  excluded: Mapping[str, str], series_keys: Optional[Mapping[str, str]] = None):
    from research.data import universe_build as ub

    day = store.day
    prev = max((d for ds in membership.values() for d in ds if d < day), default=None)
    if prev is None:
        raise ShadowError(f"FNO_MEMBERSHIP_MISSING: no F&O session before {day}")
    members = {s for s, ds in membership.items() if prev in set(ds)}
    rows, flags = ub.build(_OneDayView(store), [], banned={k: set(v) for k, v in banned.items()},
                           ban_known=set(ban_known), membership={s: {day} for s in members},
                           excluded=dict(excluded), series_keys=series_keys)
    table = {(str(r["symbol"]), r["session"]): (bool(r["eligible"]), str(r["reason"])) for r in rows}
    return ub.TableUniverse(table=table, flags=tuple(flags) + ("FNO_MEMBERSHIP_PREV_SESSION",),
                            note=f"live day {day}; F&O membership as of {prev}")


def default_universe_inputs(history_root: Optional[Path] = None) -> Dict[str, Any]:
    from research.data import nse_events, universe_build as ub

    h = Path(history_root or paths.history_dir())
    banned, known = nse_events.load_fo_ban(h)
    excluded = dict(ub.WRONG_COMPANY_SERIES)
    res = h / "bhavcopy" / "historical_fno_upstox_resolution.json"
    if res.exists():
        excluded.update(ub.wrong_company_from_resolution(res))
    keys: Dict[str, str] = {}
    man = h / "raw" / "upstox" / "manifest.jsonl"
    if man.exists():
        for line in man.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                keys.setdefault(str(rec["symbol"]), str(rec["instrument_key"]))
    return {"membership": ub.load_fno_membership(h / "bhavcopy" / "fno_point_in_time_2022_2026.parquet"),
            "banned": banned, "ban_known": known, "excluded": excluded, "series_keys": keys}


# ---------------------------------------------------------------------------------------------- journal
def _canon(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


class Journal:
    """Append-only, hash-chained JSONL: hash = sha256(prev_hash + canonical entry without its hash)."""

    GENESIS = "0" * 64

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def verify(self) -> List[Dict[str, Any]]:
        prev = self.GENESIS
        rows = self.entries()
        for i, e in enumerate(rows):
            body = {k: v for k, v in e.items() if k != "hash"}
            if e.get("seq") != i or e.get("prev_hash") != prev or \
                    hashlib.sha256(prev.encode() + _canon(body)).hexdigest() != e.get("hash"):
                raise ShadowError(f"JOURNAL_BROKEN at line {i + 1} of {self.path}")
            prev = e["hash"]
        return rows

    def append(self, entries: Sequence[Mapping[str, Any]]) -> None:
        rows = self.verify()
        prev = rows[-1]["hash"] if rows else self.GENESIS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            for k, e in enumerate(entries):
                body = {**e, "seq": len(rows) + k, "prev_hash": prev}
                body["hash"] = hashlib.sha256(prev.encode() + _canon(body)).hexdigest()
                fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")
                prev = body["hash"]


# ---------------------------------------------------------------------------------------------- runs
def signal_key(s: Any) -> Tuple[str, str, str]:
    return (s.strategy, s.symbol, s.signal_time.astimezone(IST).isoformat())


def signal_record(s: Any) -> Dict[str, Any]:
    return {"strategy": s.strategy, "symbol": s.symbol, "decision_ts": s.signal_time.astimezone(IST).isoformat(),
            "side": s.side, "entry_ref": s.entry_ref, "stop": s.stop_loss,
            "targets": [[float(p), float(f)] for p, f in s.targets], "max_bars": s.max_bars}


@dataclass
class DayInputs:
    """Everything one evaluation of day D needs except the live bars."""
    history: Any
    universe_inputs: Mapping[str, Any]
    adapters: Callable[[], List[Any]]          # fresh adapters per run (they hold per-day state)
    calibration: Callable[[Any], Any]          # store -> CalibrationProvider (or None)
    events: Any = None
    universe: Optional[Any] = None             # a fixed universe (replay); else built from universe_inputs
    notes: Dict[str, str] = field(default_factory=dict)
    history_from: date = POST_CAS_START
    # allocation at close (research/shadow/runner.py); None keeps the engine's own allocation rows
    runner_config: Optional[Any] = None
    sectors: Mapping[str, str] = field(default_factory=dict)
    clusters: Optional[Callable[[Any, date], Mapping[str, str]]] = None     # (store, day) -> symbol -> cluster


def evaluate_day(inp: DayInputs, live: LiveDay) -> Tuple[Any, Any, LiveDayStore]:
    from research.backtest.engine import BacktestEngine, EngineConfig

    store = LiveDayStore(inp.history, live, inp.history_from)
    universe = inp.universe or live_universe(store, **inp.universe_inputs)
    cfg = EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis="stop_limit", stop_limit_offset_pct=0.005)
    eng = BacktestEngine(store, universe, inp.adapters(), cfg, calibration_provider=inp.calibration(store),
                         events_provider=inp.events)
    return eng.run(only_dates=[live.day]), universe, store


def tick(inp: DayInputs, live: LiveDay, journal: Journal, holidays: Iterable[date] = (),
         clock: Clock = wall_clock) -> Dict[str, Any]:
    """Journal every signal not journaled yet; always write a heartbeat. `clock` stamps written_at: the
    wall clock in production, a simulated clock only in replay."""
    hb: Dict[str, Any] = {"kind": "TICK", "session": live.day.isoformat(), "now": live.now.isoformat(),
                          "live_file_sha256": live.file_sha256, "symbols_used": len(live.bars),
                          "dropped": live.dropped, "declared_holidays": sorted(str(h) for h in holidays),
                          "notes": inp.notes,
                          "bars_through": max((b[-1].start + BAR).isoformat() for b in live.bars.values() if b)
                          if any(live.bars.values()) else None}
    try:
        res, universe, _ = evaluate_day(inp, live)
    except ShadowError as exc:
        journal.append([{**hb, "written_at": clock().isoformat(), "refused": str(exc)}])
        raise
    written_at = clock().isoformat()
    done = {(e["strategy"], e["symbol"], e["decision_ts"]) for e in journal.entries() if e.get("kind") == "SIGNAL"}
    new = [{"kind": "SIGNAL", "written_at": written_at, "session": live.day.isoformat(), **signal_record(s)}
           for s in sorted(res.signals, key=signal_key) if signal_key(s) not in done]
    hb.update({"written_at": written_at, "new_signals": len(new),
               "eligible": sum(1 for sym in live.bars if not is_index_symbol(sym) and universe.check(sym, live.day)[0]),
               "universe_flags": list(getattr(universe, "flags", ())), "decision_counts": res.decision_counts})
    journal.append(new + [hb])
    return hb


def reconcile(signals: Sequence[Any], journal_rows: Sequence[Mapping[str, Any]],
              lateness: timedelta = JOURNAL_LATENESS) -> Tuple[Dict[Tuple[str, str, str], str], List[Dict[str, Any]]]:
    """Status per final signal (PROSPECTIVE / SHADOW_LATE / SHADOW_CHANGED / SHADOW_UNJOURNALED) and the
    journal signals the final run no longer produces (withdrawn)."""
    first: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    for e in journal_rows:
        if e.get("kind") == "SIGNAL":
            first.setdefault((e["strategy"], e["symbol"], e["decision_ts"]), e)
    status: Dict[Tuple[str, str, str], str] = {}
    for s in signals:
        k = signal_key(s)
        e = first.get(k)
        if e is None:
            status[k] = "SHADOW_UNJOURNALED"
            continue
        rec = signal_record(s)
        same = all(rec[f] == e.get(f) for f in ("side", "entry_ref", "stop", "targets", "max_bars"))
        late = datetime.fromisoformat(e["written_at"]) - datetime.fromisoformat(e["decision_ts"]) > lateness
        status[k] = "SHADOW_CHANGED" if not same else ("SHADOW_LATE" if late else "PROSPECTIVE")
    final = {signal_key(s) for s in signals}
    withdrawn = [dict(e) for k, e in first.items() if k not in final]
    return status, withdrawn


def close_day(inp: DayInputs, live: LiveDay, journal: Journal, ledger_path: Path,
              strategy_version: Mapping[str, str], code_commit: Optional[str] = None,
              clock: Clock = wall_clock) -> Dict[str, Any]:
    from research.decision.ledger import Ledger, rows_from_engine

    run_id = f"shadow_{live.day.isoformat()}"
    ledger = Ledger(ledger_path)
    if any(r["run_id"] == run_id for r in ledger.rows(mode="SHADOW")):
        raise ShadowError(f"ALREADY_CLOSED: {run_id} is in {ledger_path}")
    res, universe, store = evaluate_day(inp, live)
    status, withdrawn = reconcile(res.signals, journal.verify())
    session_report: Optional[Dict[str, Any]] = None
    if inp.runner_config is not None:
        from research.shadow.runner import ShadowRunner, ledger_rows

        cl = inp.clusters(store, live.day) if inp.clusters else {}
        runner = ShadowRunner(store, inp.sectors, config=inp.runner_config, clusters=lambda d: cl)
        srep, rrows = runner.process_session(live.day, res.signals)
        session_report = srep.as_dict()
        rows = ledger_rows(rrows, run_id=run_id, mode="SHADOW", strategy_version=strategy_version,
                           data_hash=live.file_sha256, code_commit=code_commit)
    else:
        rows = rows_from_engine(res, run_id=run_id, mode="SHADOW", strategy_version=strategy_version,
                                data_hash=live.file_sha256, code_commit=code_commit)
    for row in rows:
        ts = row["decision_ts"]
        st = status[(row["strategy_id"], row["symbol"], (ts if isinstance(ts, datetime) else
                                                           datetime.fromisoformat(str(ts))).astimezone(IST).isoformat())]
        if st != "PROSPECTIVE":                  # not proven prospective: never admissible
            row["disposition"] = f"{st}:{row['disposition']}"
            row["evidence_class"] = "E1_CF"
    if rows:
        ledger.append(rows)
    summary = {"session": live.day.isoformat(), "signals": len(rows), "status": dict(Counter(status.values())),
               "withdrawn": len(withdrawn), "universe_flags": list(getattr(universe, "flags", ())),
               "decision_counts": res.decision_counts, "dropped": live.dropped, "allocation": session_report}
    journal.append([{"kind": "CLOSE", "written_at": clock().isoformat(), "live_file_sha256": live.file_sha256,
                     **summary}])
    return {**summary, "rows": rows, "withdrawn_rows": withdrawn}


def write_report(summary: Mapping[str, Any], path: Path) -> Path:
    rows = summary.get("rows", [])
    dropped = Counter(str(v).split(":")[0] for v in summary["dropped"].values())
    lines = [f"# Shadow report {summary['session']}", "",
             "Paper/research only. E1 = simulated on the day's bars; E1_CF = counterfactual, or not proven "
             "prospective (never admissible).", "",
             f"- Signals: {summary['signals']}; status: {summary['status']}; withdrawn: {summary['withdrawn']}",
             f"- Universe flags: {', '.join(summary['universe_flags'])}",
             f"- Symbols dropped from the live file: {len(summary['dropped'])} {dict(dropped)}",
             "", "| Strategy | Symbol | Decision | Side | Disposition | Evidence | Exit | Net R |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        nr = r.get("net_r")
        lines.append(f"| {r['strategy_id']} | {r['symbol']} | {str(r['decision_ts'])[11:16]} | {r['side']} | "
                     f"{r['disposition']} | {r['evidence_class']} | {r.get('exit_reason') or ''} | "
                     f"{'' if nr is None else f'{float(nr):+.3f}'} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------- replay
def live_file_from_history(history: Any, day: date, symbols: Sequence[str], source_url: str) -> bytes:
    """The live-file bytes a feed would have written for a past session (replay tests)."""
    doc: Dict[str, Any] = {"session_date": day.isoformat(), "interval": "15minute", "data_valid": True,
                           "credential_serialized": False, "symbols": {}}
    for s in symbols:
        bs = history.bars(s, day)
        if bs:
            doc["symbols"][s] = {"source_url": source_url, "bars": [
                {"timestamp": b.start.astimezone(IST).isoformat(), "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume} for b in bs]}
    return json.dumps(doc).encode("utf-8")


def replay(inp: DayInputs, raw: bytes, day: date, journal: Journal) -> Dict[str, Any]:
    """Tick at every bar close + 60 s with a simulated clock, then compare the journal with a one-shot run
    on the full day. Any difference is look-ahead or a forming-bar leak."""
    end_of_day = datetime.combine(day, time(23, 59), IST)
    ends = sorted({b.start + BAR for bs in load_live(raw, day, end_of_day).bars.values() for b in bs})
    for end in ends:
        at = end + DECISION_DELAY
        tick(inp, load_live(raw, day, at), journal, clock=lambda at=at: at)
    full, _, _ = evaluate_day(inp, load_live(raw, day, end_of_day))
    status, withdrawn = reconcile(full.signals, journal.verify(), lateness=DECISION_DELAY)
    return {"ticks": len(ends), "signals": len(full.signals), "status": dict(Counter(status.values())),
            "withdrawn": withdrawn, "identical": all(v == "PROSPECTIVE" for v in status.values()) and not withdrawn}


# ---------------------------------------------------------------------------------------------- CLI
def production_inputs() -> Tuple[DayInputs, Dict[str, str]]:
    from research.data.sector_map import load_sector_map
    from research.data.store_parquet import ParquetCandleStore
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.strategies.orb_prod import OrbProdAdapter
    from research.studies import prereg_io

    history = ParquetCandleStore()                    # STRATEGY mode: the holdout stays hidden
    spec_path = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    lock = prereg_io.verify_lock(spec_path)
    spec = prereg_io.load(spec_path)
    versions = {"ORB_PROD": "orb_prod_v1:" + hashlib.sha256(_canon(OrbProdAdapter().manifest())).hexdigest()[:16]}
    notes = {"RESID_REV": "RUN" if lock.valid else f"NOT_RUN_PREREG_NOT_FROZEN:{lock.reason}"}

    def adapters() -> List[Any]:
        out: List[Any] = [OrbProdAdapter(is_shadow=True)]
        if lock.valid:
            from research.strategies.resid_rev import ResidRevAdapter

            out.append(ResidRevAdapter(spec, variant=str(spec.get("holdout_variant") or "RESID_REV"), mode="EMIT",
                                       is_shadow=True))
        return out

    events: Any = NoEventsData()
    cal: Callable[[Any], Any] = lambda store: None
    from research.data.sector_map import load_sector_map
    if lock.valid:
        from research.data.nse_events import load_table_events

        versions["RESID_REV"] = "resid_rev_v1:" + str(lock.lock["yaml_sha256"])[:16]
        versions["RESID_REV_NF"] = versions["RESID_REV"]
        sector_map = load_sector_map()
        cal = lambda store: CalibrationProvider(store, sector_map, CalibrationConfig.from_prereg(spec))
        if str(spec.get("holdout_variant") or "RESID_REV") == "RESID_REV":
            events = load_table_events()
    from research.decision.clusters import weekly_clusters
    from research.shadow.runner import RunnerConfig

    def clusters(store: Any, day: date) -> Mapping[str, str]:
        tradables = [s for s in store.symbols if store.kind(s) == "TRADABLE"]
        return weekly_clusters(store, tradables, [day]).get(day, {})

    return DayInputs(history, default_universe_inputs(), adapters, cal, events, notes=notes,
                     runner_config=RunnerConfig(), sectors=load_sector_map(), clusters=clusters), versions


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P8.2 shadow runner (paper/research only)")
    ap.add_argument("cmd", choices=["tick", "close", "replay"])
    ap.add_argument("--date", default=None, help="session (default: today IST)")
    ap.add_argument("--now", default=None, help="evaluate as of this time, ISO with offset (never later than "
                                                 "the wall clock; written_at is always the wall clock)")
    ap.add_argument("--holiday", action="append", default=[], help="declare an exchange holiday (repeatable)")
    ap.add_argument("--live-file", default=str(paths.shared_input(LIVE_FILE)))
    args = ap.parse_args(argv)
    wall = wall_clock()
    now = datetime.fromisoformat(args.now) if args.now else wall
    if now > wall:
        print("REFUSED: --now is later than the wall clock")
        return 2
    day = date.fromisoformat(args.date) if args.date else now.astimezone(IST).date()
    holidays = [date.fromisoformat(h) for h in args.holiday]
    inp, versions = production_inputs()
    root = shadow_root()
    try:
        if args.cmd == "replay":
            syms = [s for s in inp.history.symbols if day in inp.history.sessions(s)]
            raw = live_file_from_history(inp.history, day, syms, "https://api.upstox.com/v2/historical-candle")
            out = replay(inp, raw, day, Journal(root / "replay" / f"{day.isoformat()}_{wall:%Y%m%d_%H%M%S}.jsonl"))
            print(json.dumps({k: v for k, v in out.items() if k != "withdrawn"}, indent=1, default=str))
            return 0 if out["identical"] else 1
        check_history_fresh(inp.history, day, holidays)
        raw = Path(args.live_file).read_bytes()
        journal = Journal(root / "journal" / f"{day.isoformat()}.jsonl")
        if args.cmd == "tick":
            print(json.dumps(tick(inp, load_live(raw, day, now), journal, holidays), indent=1, default=str))
        else:
            summ = close_day(inp, load_live(raw, day, now), journal, root / "shadow_ledger.db", versions)
            rep = write_report(summ, paths.shared_input("shadow_reports") / f"{day.isoformat()}.md")
            (rep.with_suffix(".json")).write_text(json.dumps({k: v for k, v in summ.items() if k != "rows"},
                                                             indent=1, default=str), encoding="utf-8")
            print(json.dumps({k: v for k, v in summ.items() if k not in ("rows", "withdrawn_rows")}, indent=1,
                             default=str))
            print(f"report: {rep}")
    except ShadowError as exc:
        print(f"REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
