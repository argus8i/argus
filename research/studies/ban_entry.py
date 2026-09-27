"""
research/studies/ban_entry.py
=============================
BAN_ENTRY_SHORT, design-set evaluation (DRAFT idea from the 26 Sep exploratory scan, T0062-T0075).

Idea: on the FIRST session of an F&O-ban spell (MWPL > 95%: no new F&O positions; the list for day D is
published by NSE the evening before), the stock falls from the open (design scan: -0.88% market-adjusted
open->close, t -6.5, every year negative). Trade: intraday MIS SHORT at the open, covered at the 15:05 policy exit.

Model (the shared research simulator, signal_sim.simulate_signal, with the engine's own fill rules):
- the decision is made BEFORE the session (the ban list is known the evening before; the pre-open equilibrium
  price is published at 09:08). A synthetic 09:00-09:15 bar priced at the 09:15 open is placed before the
  day's bars, so the simulator enters at the 09:15 open with the normal slippage (1 tick) and clamp;
- stop: SL-limit at entry x (1 + s) for s in {2%, 3%} (two pre-declared variants, both registered);
  r_basis stop_limit; no targets; exit at the 15:05 policy exit;
- size: Adjusted A1 (Rs 1,500 risk, Rs 38,000 at the worst admissible entry); MIS fees (DhanFeeEngine).
Eligibility: the stock was ELIGIBLE in the universe table on the previous session (F&O member, price >= Rs 10,
DTV20 >= Rs 30 Cr) and is not in the ban on the previous session (first day of the spell).

NOT modelled, to be confirmed before any live use: whether the broker permits MIS shorts in F&O-ban stocks, and
whether an auction-open fill is achievable (pre-open order entry 09:00-09:08).
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


def ban_entries(snap: Path, start: str, end: str, counts: Optional[Dict[str, int]] = None) -> pd.DataFrame:
    """(symbol, day) of the first session D of every ban spell (in the list for D, not for D-1). Fail closed:
    both D and D-1 must be sessions with a ban list on disk (events/fo_ban_days); the stock must be ELIGIBLE on
    D-1 and on D (point-in-time F&O member, lagged price >= Rs 10 and DTV20 >= Rs 30 Cr, right company/series;
    the universe never excludes a stock for being in the ban, so ban status is the only D-day condition not
    applied). No ASM/GSM history exists on disk: that check is a stated, unenforced limitation. `counts`, when
    given, receives the number of events dropped by each rule."""
    b = pd.read_parquet(snap / "events" / "fo_ban.parquet")
    b["day"] = b.trade_date.astype(str)
    b = b[["symbol", "day"]].drop_duplicates()
    u = pd.read_parquet(snap / "reference" / "universe_daily.parquet")
    days = sorted(u.session.astype(str).unique())
    idx = {d: i for i, d in enumerate(days)}
    b["i"] = b.day.map(idx)
    b = b.dropna(subset=["i"]).sort_values(["symbol", "i"])
    first = b[b.groupby("symbol")["i"].diff() != 1].copy()
    first["prev"] = [days[int(i) - 1] if int(i) > 0 else None for i in first.i]
    first = first[(first.day >= start) & (first.day <= end)]
    cov = set(pd.read_parquet(snap / "events" / "fo_ban_days.parquet").trade_date.astype(str))
    elig = set(zip(u[u.eligible].symbol, u[u.eligible].session.astype(str)))
    ok_d = u.eligible | (u.reason == "FO_BAN") if "reason" in u else u.eligible       # the ban: the only D-day pass
    elig_d = set(zip(u[ok_d].symbol, u[ok_d].session.astype(str)))
    c = {"candidates": len(first)}
    keep = [(d in cov) and (p in cov) for d, p in zip(first.day, first.prev)]
    c["dropped_unknown_coverage"] = len(first) - sum(keep)
    first = first[keep]
    keep = [(s, p) in elig for s, p in zip(first.symbol, first.prev)]
    c["dropped_prev_ineligible"] = len(first) - sum(keep)
    first = first[keep]
    keep = [(s, d) in elig_d for s, d in zip(first.symbol, first.day)]
    c["dropped_event_day_ineligible"] = len(first) - sum(keep)
    first = first[keep]
    c["events"] = len(first)
    if counts is not None:
        counts.update(c)
    return first[["symbol", "day"]].reset_index(drop=True)


ENTRY_MODELS = ("open", "first_minute_low", "vwap_5min")


class FirstMinutes:
    """The 09:15-09:19 one-minute candles of a symbol-day from the raw Upstox files (hash-verified history;
    the same vendor series as the 15-minute bars, so the same split/bonus scale)."""

    def __init__(self, raw_root: Path) -> None:
        import json as _json

        self.root = raw_root
        self.files: Dict[str, List[tuple]] = {}
        for line in (raw_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = _json.loads(line)
                if r.get("interval") == "1minute" and r.get("status") == 200:
                    a, b = r["window"].split("_")
                    self.files.setdefault(r["symbol"], []).append((a, b, r["file"]))
        self._cache: Dict[str, Any] = {}

    def minutes(self, symbol: str, day: str) -> List[List[Any]]:
        import gzip
        import json as _json

        for a, b, f in self.files.get(symbol, []):
            if a <= day <= b:
                if f not in self._cache:
                    raw = (self.root / f).read_bytes()
                    self._cache = {f: _json.loads(gzip.decompress(raw))["data"]["candles"]}
                rows = [c for c in self._cache[f] if c[0][:10] == day and "09:15" <= c[0][11:16] <= "09:19"]
                return sorted(rows, key=lambda c: c[0])
        return []


def entry_price(model: str, open_0915: float, minutes: List[List[Any]]) -> Optional[float]:
    """The pre-slippage short-entry price: the 09:15 open, the LOW of the 09:15 minute (the worst price a short
    could have sold at in the first minute), or the 09:15-09:19 VWAP (typical price x volume)."""
    if model == "open":
        return open_0915
    if not minutes:
        return None
    if model == "first_minute_low":
        return float(minutes[0][3]) if minutes[0][0][11:16] == "09:15" else None
    vol = sum(float(m[5]) for m in minutes)
    if vol <= 0:
        return None
    return sum((float(m[2]) + float(m[3]) + float(m[4])) / 3 * float(m[5]) for m in minutes) / vol


# ------------------------------------------------------------------------------------ one-minute path (v1 primary)
class MinuteDataRefused(RuntimeError):
    pass


class VerifiedMinutes:
    """1-minute Upstox candles of a symbol-day from the raw files named in a SEALED snapshot's manifest (Codex,
    26 Sep). Use VerifiedMinutes.sealed(snapshot, raw_root) for a run: the snapshot is verified (and its content
    hash compared when one is expected) before its manifest is read. Every file is checked against the
    manifest's body_sha256 (SHA-256 of the decompressed body) before it is parsed. Unsafe paths, conflicting
    records for one (symbol, window), or ambiguous windows refuse. `verified` maps each file read to its hash."""

    def __init__(self, records: Dict[tuple, Dict[str, Any]], raw_root: Path) -> None:
        self.records, self.root = records, raw_root
        self.verified: Dict[str, str] = {}
        self._sym: Optional[str] = None
        self._files: Dict[str, Dict[str, list]] = {}

    @classmethod
    def from_manifest(cls, manifest: Path, raw_root: Path) -> "VerifiedMinutes":
        recs: Dict[tuple, Dict[str, Any]] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("interval") != "1minute" or r.get("status") != 200:
                continue
            f = str(r.get("file", ""))
            if not f or ".." in Path(f).parts or Path(f).is_absolute() or ":" in f:
                raise MinuteDataRefused(f"unsafe file path in manifest: {f!r}")
            if not r.get("body_sha256"):
                raise MinuteDataRefused(f"manifest record without body_sha256: {f}")
            key = (r["symbol"], r["window"])
            if key in recs and (recs[key]["file"], recs[key]["body_sha256"]) != (f, r["body_sha256"]):
                raise MinuteDataRefused(f"conflicting manifest records for {key}")
            recs[key] = {"file": f, "body_sha256": r["body_sha256"]}
        return cls(recs, raw_root)

    @classmethod
    def sealed(cls, snapshot_path: Path, raw_root: Path, expected_content_sha256: Optional[str] = None
               ) -> "VerifiedMinutes":
        from research.data import snapshot

        info = snapshot.verify(snapshot_path)
        if expected_content_sha256 and info["content_sha256"] != expected_content_sha256:
            raise MinuteDataRefused(f"snapshot {snapshot_path.name} content hash {info['content_sha256'][:12]} is "
                                    f"not the expected {expected_content_sha256[:12]}")
        vm = cls.from_manifest(snapshot_path / "raw" / "upstox" / "manifest.jsonl", raw_root)
        vm.snapshot_content_sha256 = info["content_sha256"]
        return vm

    def _record(self, symbol: str, day: str) -> Optional[Dict[str, Any]]:
        hits = [r for (s, w), r in self.records.items() if s == symbol and w.split("_")[0] <= day <= w.split("_")[1]]
        if len({(h["file"], h["body_sha256"]) for h in hits}) > 1:
            raise MinuteDataRefused(f"ambiguous windows for {symbol} {day}")
        return hits[0] if hits else None

    def day(self, symbol: str, day: str) -> List[List[Any]]:
        import gzip
        import hashlib

        rec = self._record(symbol, day)
        if rec is None:
            return []
        if symbol != self._sym:
            self._sym, self._files = symbol, {}
        f = rec["file"]
        if f not in self._files:
            body = gzip.decompress((self.root / f).read_bytes())
            sha = hashlib.sha256(body).hexdigest()
            if sha != rec["body_sha256"]:
                raise MinuteDataRefused(f"{f}: sha256 {sha[:12]} does not match the sealed manifest")
            by_day: Dict[str, list] = {}
            for c in json.loads(body)["data"]["candles"]:
                by_day.setdefault(c[0][:10], []).append(c)
            for v in by_day.values():
                v.sort(key=lambda c: c[0])
            self._files[f] = by_day
            self.verified[f] = sha
        return self._files[f].get(day, [])


MINUTE_ENTRY_MODELS = ("vwap_0915_0919", "auction_open", "first_minute_low")
PARTICIPATION = 0.10            # a fill may take at most 10% of the entry window's traded volume
BAND_STRESS = 0.095             # a stop fill in a minute reaching prev_close x 1.095 (near the initial 10% dynamic
                                # band of an F&O stock) is filled at that minute's HIGH + slippage: a worst-case
                                # bound for a cover into a runaway / no-offer market (Codex 26 Sep)


def _hm(c: List[Any]) -> str:
    return c[0][11:16]


def simulate_short_minutes(minutes: List[List[Any]], entry_model: str, stop_pct: float, prev_close: float,
                           slippage_ticks: int = 1, participation: float = PARTICIPATION,
                           stop_limit_offset_pct: float = 0.005) -> Dict[str, Any]:
    """One intraday MIS SHORT on one-minute candles [ts, o, h, l, c, v, oi].

    Entry (reference price, then `slippage_ticks` worse):
      vwap_0915_0919    typical-price VWAP proxy of 09:15-09:19, known at 09:20: filled at 09:20, exposed to
                        prices from the 09:20 minute only (the window's own prices are never post-entry);
      auction_open      the 09:15 open (the pre-open auction price): exposed from the 09:15 minute;
      first_minute_low  the 09:15 minute's low (stress): exposed from the 09:15 minute (pessimistic).
    Quantity: Adjusted A1 (Rs 1,500 to the SL-limit, Rs 38,000 at the worst admissible entry), capped at
    `participation` x the entry window's volume (09:15-09:19; the 09:15 minute for the other models):
    PARTIAL below plan, UNFILLED_VOLUME under one share.
    Stop: SL-limit BUY, trigger round_up(ref x (1 + stop_pct)), limit trigger x (1 + offset). A minute whose high
    reaches the trigger fills at max(trigger, open) + slippage, capped at the limit; a minute OPENING above the
    limit leaves the order unfilled and the short is covered at the next minute's open + slippage
    (STOP_LIMIT_GAP_MARKET); a trigger in a minute reaching prev_close x (1 + BAND_STRESS) fills at that minute's
    high + slippage (BAND_STRESS). Time exit: the 15:05 minute's open + slippage (TIME_1505).
    R = qty x (limit - reference); fees DhanFeeEngine MIS on both legs; gross_r at slippage-free prices."""
    from research.backtest.bars import round_to_tick, tick_size
    from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
    from research.backtest.engine import EngineConfig
    from research.studies.signal_sim import size_qty

    if entry_model not in MINUTE_ENTRY_MODELS:
        raise ValueError(f"entry_model must be one of {MINUTE_ENTRY_MODELS}")
    day = sorted((c for c in minutes if "09:15" <= _hm(c) <= "15:29"), key=lambda c: c[0])
    if not day or _hm(day[0]) != "09:15":
        return {"disposition": "NO_MINUTES"}
    win = [c for c in day if _hm(c) <= "09:19"]
    if entry_model == "vwap_0915_0919":
        vol = sum(float(c[5]) for c in win)
        if vol <= 0:
            return {"disposition": "NO_ENTRY_PRICE"}
        ref = sum((float(c[2]) + float(c[3]) + float(c[4])) / 3 * float(c[5]) for c in win) / vol
        start = next((k for k, c in enumerate(day) if _hm(c) >= "09:20"), None)
        if start is None:
            return {"disposition": "NO_MINUTES"}
        window_vol, entry_time = vol, "09:20"
    elif entry_model == "auction_open":
        ref, start, window_vol, entry_time = float(day[0][1]), 0, float(day[0][5]), "09:15"
    else:
        ref, start, window_vol, entry_time = float(day[0][3]), 0, float(day[0][5]), "09:15"
    if not (ref > 0 and math.isfinite(ref)):
        return {"disposition": "NO_ENTRY_PRICE"}
    ecfg = EngineConfig()
    ref = round_to_tick(ref, "down")                     # a short's price, rounded against us
    tick = tick_size(ref)
    entry = ref - slippage_ticks * tick
    trigger = round_to_tick(ref * (1 + stop_pct), "up")
    limit = round_to_tick(trigger * (1 + stop_limit_offset_pct), "up")
    cap_px = round_to_tick(ref * (1 + ecfg.clamp_pct), "up") + slippage_ticks * tick
    planned = size_qty(ref, trigger, "SELL", ecfg.risk_budget_rs, ecfg.slot_cap_rs, stop_limit_offset_pct,
                       notional_px=cap_px)
    if planned <= 0:
        return {"disposition": "ZERO_QTY"}
    cap_qty = int(participation * window_vol)
    if cap_qty < 1:
        return {"disposition": "UNFILLED_VOLUME", "planned_qty": planned}
    qty = min(planned, cap_qty)
    band = prev_close * (1 + BAND_STRESS) if prev_close and prev_close > 0 else math.inf
    pre_touch = any(float(c[2]) >= trigger for c in day[:start])
    exit_px = exit_ideal = None
    reason = exit_time = None
    band_touch, pending = False, False
    for c in day[start:]:
        hm, o, h = _hm(c), float(c[1]), float(c[2])
        band_touch = band_touch or h >= band
        if pending:
            exit_ideal, exit_px, reason, exit_time = o, o + slippage_ticks * tick, "STOP_LIMIT_GAP_MARKET", hm
            break
        if hm >= "15:05":
            exit_ideal, exit_px, exit_time = o, o + slippage_ticks * tick, hm
            reason = "TIME_1505" if hm == "15:05" else "TIME_AFTER_1505"
            break
        if h >= trigger:
            if h >= band:
                exit_ideal, exit_px, reason, exit_time = trigger, h + slippage_ticks * tick, "BAND_STRESS", hm
                break
            px = max(trigger, o)
            if px > limit:
                pending = True
                continue
            exit_ideal, exit_px, reason, exit_time = trigger, min(px + slippage_ticks * tick, limit), "STOP", hm
            break
    if exit_px is None:
        last = day[-1]
        exit_ideal, exit_px, reason, exit_time = float(last[4]), float(last[4]) + slippage_ticks * tick, \
            "LAST_MINUTE", _hm(last)
    fees = (DhanFeeEngine.calculate_order(OrderSide.SELL, [(entry, qty)], ProductType.MIS).total_charges
            + DhanFeeEngine.calculate_order(OrderSide.BUY, [(exit_px, qty)], ProductType.MIS).total_charges)
    risk_rs = qty * (limit - ref)
    gross = (entry - exit_px) * qty
    gross_ideal = (ref - exit_ideal) * qty
    net = gross - fees
    return {"disposition": "FILLED" if qty == planned else "PARTIAL", "planned_qty": planned, "qty": qty,
            "entry_ref": ref, "entry": entry, "entry_time": entry_time, "trigger": trigger, "limit": limit,
            "exit": round(exit_px, 2), "exit_time": exit_time, "exit_reason": reason, "band_touch": band_touch,
            "pre_entry_stop_touch": pre_touch, "risk_rs": round(risk_rs, 2), "fees_rs": round(fees, 2),
            "net_pnl_rs": round(net, 2), "gross_r": gross_ideal / risk_rs, "slip_r": (gross_ideal - gross) / risk_rs,
            "fee_r": fees / risk_rs, "net_r": net / risk_rs}


def daily_prev_close(snap: Path, symbols: List[str]) -> Dict[tuple, float]:
    """(symbol, day) -> the previous session's close, from the snapshot's daily bars (same vendor scale)."""
    out: Dict[tuple, float] = {}
    want = set(symbols)
    for f in sorted((snap / "daily").glob("*.parquet")):
        d = pd.read_parquet(f, columns=["symbol", "day", "close"])
        if d.empty or d.symbol.iloc[0] not in want:
            continue
        d = d.sort_values("day")
        for s, day, pc in zip(d.symbol, d.day.astype(str), d.close.shift(1)):
            out[(s, day)] = float(pc)
    return out


def simulate_minutes(vm: VerifiedMinutes, events: pd.DataFrame, prev_close: Dict[tuple, float], stop_pct: float,
                     entry_model: str, slippage_ticks: int = 1) -> List[Dict[str, Any]]:
    out = []
    for ev in events.sort_values(["symbol", "day"]).itertuples():
        mins = vm.day(ev.symbol, ev.day)
        r = simulate_short_minutes(mins, entry_model, stop_pct, prev_close.get((ev.symbol, ev.day), math.nan),
                                   slippage_ticks=slippage_ticks)
        out.append({"symbol": ev.symbol, "day": ev.day, **r})
    return out


def simulate(store: Any, events: pd.DataFrame, stop_pct: float, slippage_ticks: int = 1,
             entry_model: str = "open", first_minutes: Optional[FirstMinutes] = None) -> List[Dict[str, Any]]:
    import dataclasses

    from research.backtest.bars import Bar, round_to_tick, tick_size
    from research.backtest.engine import EngineConfig
    from research.studies.signal_sim import simulate_signal, size_qty

    if entry_model not in ENTRY_MODELS:
        raise ValueError(f"entry_model must be one of {ENTRY_MODELS}")
    ecfg = EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis="stop_limit", stop_limit_offset_pct=0.005,
                        entry_slippage_ticks=slippage_ticks, stop_slippage_ticks=slippage_ticks,
                        exit_slippage_ticks=slippage_ticks)
    cfg = ecfg.sim_config()
    out = []
    for ev in events.itertuples():
        d = date.fromisoformat(ev.day)
        bars = store.bars(ev.symbol, d)
        if len(bars) < 20:
            out.append({"symbol": ev.symbol, "day": ev.day, "disposition": "NO_BARS"})
            continue
        mins = first_minutes.minutes(ev.symbol, ev.day) if (first_minutes and entry_model != "open") else []
        p = entry_price(entry_model, bars[0].open, mins)
        if p is None or not (p > 0):
            out.append({"symbol": ev.symbol, "day": ev.day, "disposition": "NO_ENTRY_PRICE"})
            continue
        p = round_to_tick(p, "down")                        # a short's price, rounded against us
        b0 = bars[0]
        bars = [dataclasses.replace(b0, open=p, high=max(b0.high, p), low=min(b0.low, p))] + list(bars[1:])
        o = bars[0].open
        pre = Bar(ev.symbol, bars[0].start - timedelta(minutes=15), 15, o, o, o, o, 0)      # pre-open decision bar
        stop = round_to_tick(o * (1 + stop_pct), "up")
        worst = o * (1 + ecfg.clamp_pct)
        cap_px = round_to_tick(worst, "up") + ecfg.entry_slippage_ticks * tick_size(worst)
        qty = size_qty(o, stop, "SELL", ecfg.risk_budget_rs, ecfg.slot_cap_rs, ecfg.stop_limit_offset_pct, notional_px=cap_px)
        sim = simulate_signal([pre] + list(bars), 0, "SELL", o, stop, [], None, qty, cfg, d)
        out.append({"symbol": ev.symbol, "day": ev.day, "disposition": sim.disposition, "qty": qty,
                    "entry": sim.entry_price, "exit_reason": sim.exit_reason, "net_r": sim.net_r, "gross_r": sim.gross_r,
                    "fee_r": sim.fee_r, "slip_r": sim.slip_r, "net_pnl_rs": sim.net_pnl_rs, "risk_rs": sim.risk_rs})
    return out


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The primary estimand: equal-weighted mean net R over FILLED and PARTIAL signals (R on the filled
    quantity), SE clustered by trading session (CR1); every other disposition is counted, not averaged."""
    from research.decision.stats import clustered_se

    df = pd.DataFrame(rows)
    f = df[df.disposition.isin(["FILLED", "PARTIAL"]) & np.isfinite(df.net_r.astype(float))] if "net_r" in df \
        else df.iloc[:0]
    net = f.net_r.astype(float).to_numpy()
    se = clustered_se(list(net), list(f.day)) if len(net) > 1 else math.nan
    by_year = {}
    for y, g in f.groupby(f.day.str[:4]):
        v = g.net_r.astype(float).to_numpy()
        s = clustered_se(list(v), list(g.day)) if len(v) > 1 else math.nan
        by_year[y] = {"n": len(v), "mean_net_r": round(float(v.mean()), 4),
                      "t": round(float(v.mean() / s), 2) if math.isfinite(s) and s > 0 else None}
    return {"events": len(df), "filled": len(f), "dispositions": df.disposition.value_counts().to_dict(),
            "mean_net_r": float(net.mean()) if len(net) else None, "se_cluster_by_day": se,
            "t_cluster": float(net.mean() / se) if len(net) > 1 and math.isfinite(se) and se > 0 else None,
            "mean_gross_r": float(f.gross_r.astype(float).mean()) if len(f) else None,
            "mean_fee_r": float(f.fee_r.astype(float).mean()) if len(f) else None,
            "mean_slip_r": float(f.slip_r.astype(float).mean()) if len(f) else None,
            "mean_net_pnl_rs": float(f.net_pnl_rs.astype(float).mean()) if len(f) else None,
            "total_net_pnl_rs": float(f.net_pnl_rs.astype(float).sum()) if len(f) else None,
            "exit_reasons": f.exit_reason.value_counts().to_dict() if len(f) else {}, "by_year": by_year,
            "partial_fills": int((f.disposition == "PARTIAL").sum()) if len(f) else 0,
            "band_touch": int(f.band_touch.sum()) if "band_touch" in f and len(f) else None,
            "pre_entry_stop_touch": int(f.pre_entry_stop_touch.sum()) if "pre_entry_stop_touch" in f and len(f)
            else None}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths
    from research.data.store_parquet import ParquetCandleStore

    ap = argparse.ArgumentParser(description="BAN_ENTRY_SHORT design-set evaluation")
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--end", default="2024-09-30")
    ap.add_argument("--stops", default="0.02,0.03")
    ap.add_argument("--sim", choices=["minutes", "bars15"], default="minutes",
                    help="minutes: the v1 one-minute simulator (Codex 26 Sep); bars15: the T0076-T0081 path")
    ap.add_argument("--entry-model", default=None, help=f"minutes: {MINUTE_ENTRY_MODELS}; bars15: {ENTRY_MODELS}")
    ap.add_argument("--slippage-ticks", type=int, default=1)
    ap.add_argument("--snapshot", default=None, help="sealed snapshot (required for --sim minutes)")
    ap.add_argument("--raw-upstox", default=None, help="raw/upstox root (default: <main history>/raw/upstox)")
    args = ap.parse_args(argv)
    if args.end > "2024-09-30":
        print("REFUSED: design window only (the holdout is read once, after a locked pre-registration)")
        return 2
    if args.sim == "minutes":
        return _main_minutes(args)
    args.entry_model = args.entry_model or "open"
    snap = paths.history_dir()
    store = ParquetCandleStore()
    ev = ban_entries(snap, args.start, args.end)
    fm = None
    if args.entry_model != "open":
        raw = Path(args.raw_upstox) if args.raw_upstox else \
            paths.main_checkout() / "shared" / "track2_liquid" / "history" / "raw" / "upstox"
        fm = FirstMinutes(raw)
    res = {"window": [args.start, args.end], "events": len(ev), "entry_model": args.entry_model, "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "ban_entry" /
                           f"{datetime.now(IST):%Y%m%d_%H%M%S}_{args.entry_model}")
    for s in (float(x) for x in args.stops.split(",")):
        rows = simulate(store, ev, s, entry_model=args.entry_model, first_minutes=fm)
        res["variants"][f"stop_{s:.0%}"] = summarise(rows)
        pd.DataFrame(rows).to_parquet(out_dir / f"signals_stop{int(s * 100)}.parquet", index=False)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


def _main_minutes(args: argparse.Namespace) -> int:
    from research.data import paths

    if not args.snapshot:
        print("REFUSED: --sim minutes reads a sealed snapshot (--snapshot)")
        return 2
    model = args.entry_model or "vwap_0915_0919"
    if model not in MINUTE_ENTRY_MODELS:
        print(f"REFUSED: entry model {model!r} not in {MINUTE_ENTRY_MODELS}")
        return 2
    from research.studies import prereg_io

    snap = Path(args.snapshot)
    pin = prereg_io.load(prereg_io.PREREG_DIR / "ban_entry_short_v1.yaml")["data"]["snapshot"]
    if snap.name != pin["name"]:
        print(f"REFUSED: the design reads the pinned snapshot {pin['name']}, not {snap.name}")
        return 2
    raw = Path(args.raw_upstox) if args.raw_upstox else \
        paths.main_checkout() / "shared" / "track2_liquid" / "history" / "raw" / "upstox"
    try:
        vm = VerifiedMinutes.sealed(snap, raw, expected_content_sha256=pin["content_sha256"])
    except MinuteDataRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    counts: Dict[str, int] = {}
    ev = ban_entries(snap, args.start, args.end, counts=counts)
    pc = daily_prev_close(snap, sorted(set(ev.symbol)))
    res: Dict[str, Any] = {"window": [args.start, args.end], "sim": "minutes", "entry_model": model,
                           "slippage_ticks": args.slippage_ticks, "event_counts": counts,
                           "snapshot": snap.name, "snapshot_content_sha256": vm.snapshot_content_sha256, "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "ban_entry" /
                           f"{datetime.now(IST):%Y%m%d_%H%M%S}_minutes_{model}_s{args.slippage_ticks}")
    for s in (float(x) for x in args.stops.split(",")):
        rows = simulate_minutes(vm, ev, pc, s, model, slippage_ticks=args.slippage_ticks)
        res["variants"][f"stop_{s:.0%}"] = summarise(rows)
        pd.DataFrame(rows).to_parquet(out_dir / f"signals_stop{int(round(s * 100))}.parquet", index=False)
    res["verified_files"] = len(vm.verified)
    (out_dir / "verified_files.json").write_text(json.dumps(vm.verified, indent=0), encoding="utf-8")
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
