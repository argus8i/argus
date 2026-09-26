"""
research/shadow/expiry_desk.py
==============================
Paper desk for EXPIRY_RELIEF_LONG v2 (research/studies/prereg/expiry_relief_long_v2.yaml). PAPER ONLY (AGENTS.md
Rule 1): it never places, modifies or cancels an order, and it has no broker code at all.

    python -m research.shadow.expiry_desk plan      --date 2026-09-29   # expiry evening, after the files land
    python -m research.shadow.expiry_desk reconcile --date 2026-10-07   # any evening: scores finished trades
    python -m research.shadow.expiry_desk replay    --date 2026-08-25   # a past expiry, separate journal, never evidence

Data (official NSE daily files, UDiFF format, one per session, as the daily pipeline stores them):
  history/bhavcopy/raw/cm/<YYYY>/<YYYY-MM-DD>.csv.gz     equities: TckrSymb SctySrs OpnPric HghPric LwPric ClsPric
                                                          PrvsClsgPric TtlTrfVal
  history/bhavcopy/raw/fo/<YYYY>/<YYYY-MM-DD>.csv.gz     derivatives: FinInstrmTp (STF = stock futures), XpryDt
  history/raw/nse/fo_ban/<D>_<D>.csv.gz                  F&O ban list for session D (published the evening before)
Sessions are the dates that have a CM file. Future holidays are never assumed: the entry session is the first weekday
after the expiry whose ban list NSE has published; without it the plan is BLOCKED.

Rules (from the pre-registration): 20-session split-safe return <= -5% into a monthly stock-futures expiry;
eligibility on entry; buy the entry open + 1 tick; 5% stop; no target; exit the close of the 5th session held; A1
sizing; Dhan CNC fees; simulated by research/studies/expiry_relief.simulate_long (the design and holdout code).
A plan is PROSPECTIVE only if its journal entry was written before 09:00 IST on the entry session; otherwise LATE.
A corporate action inside the hold (previous close not equal to the prior close within 1%) VOIDs the trade.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from research.shadow.run_day import Journal

IST = timezone(timedelta(hours=5, minutes=30))
STRATEGY = "EXPIRY_RELIEF_LONG_v2"
LOOKBACK, DROP_PCT, HOLD, STOP_PCT = 20, -5.0, 5, 0.05
MIN_PRICE, MIN_TURNOVER_CR = 10.0, 30.0
CA_TOLERANCE = 0.01
NUMERIC = ["OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTrfVal"]


def prereg_path() -> Path:
    from research.studies import prereg_io

    return prereg_io.PREREG_DIR / "expiry_relief_long_v2.yaml"


# ---------------------------------------------------------------------------------------------- data
def _bytes(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def read_udiff(path: Path) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(_bytes(path)), dtype=str, keep_default_na=False)
    for c in NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def cm_path(h: Path, d: date) -> Path:
    return Path(h) / "bhavcopy" / "raw" / "cm" / str(d.year) / f"{d.isoformat()}.csv.gz"


def fo_path(h: Path, d: date) -> Path:
    return Path(h) / "bhavcopy" / "raw" / "fo" / str(d.year) / f"{d.isoformat()}.csv.gz"


def ban_path(h: Path, d: date) -> Path:
    return Path(h) / "raw" / "nse" / "fo_ban" / f"{d.isoformat()}_{d.isoformat()}.csv.gz"


def sessions(h: Path) -> List[date]:
    root = Path(h) / "bhavcopy" / "raw" / "cm"
    out = []
    for p in root.glob("*/*.csv.gz") if root.exists() else []:
        try:
            out.append(date.fromisoformat(p.name[:10]))
        except ValueError:
            continue
    return sorted(out)


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ban_for(h: Path, d: date) -> Optional[Set[str]]:
    """The ban list for session d, or None when it is absent or dated differently (fail closed)."""
    from research.data.nse_events import parse_ban_csv

    p = ban_path(h, d)
    if not p.exists():
        return None
    when, syms = parse_ban_csv(_bytes(p).decode("utf-8", errors="replace"))
    return set(syms) if when == d else None


def entry_session(h: Path, expiry: date) -> Optional[date]:
    d = expiry
    for _ in range(7):
        d += timedelta(days=1)
        if d.weekday() < 5 and ban_path(h, d).exists():
            return d
    return None


def is_monthly_expiry(fo: pd.DataFrame, day: date) -> bool:
    if "FinInstrmTp" not in fo:
        return False
    stf = fo[fo.FinInstrmTp == "STF"]
    if stf.empty:
        return False
    nearest = stf.groupby("TckrSymb")["XpryDt"].min()
    return bool((nearest == day.isoformat()).mean() > 0.5)


# ---------------------------------------------------------------------------------------------- plan
def build_plan(h: Path, expiry: date, entry: Optional[date]) -> Dict[str, Any]:
    from research.data import universe_build as ub

    base: Dict[str, Any] = {"expiry": expiry.isoformat(), "entry_session": entry.isoformat() if entry else None}
    ss = sessions(h)
    if expiry not in ss or not fo_path(h, expiry).exists():
        return {**base, "status": "NO_DATA", "reason": f"no CM/F&O file for {expiry}"}
    fo = read_udiff(fo_path(h, expiry))
    if not is_monthly_expiry(fo, expiry):
        return {**base, "status": "NOT_AN_EXPIRY", "reason": f"{expiry} is not a monthly stock-futures expiry"}
    ban = ban_for(h, entry) if entry else None
    if ban is None:
        return {**base, "status": "BLOCKED", "reason": f"no ban list for the entry session {entry} (fail closed)"}
    i = ss.index(expiry)
    if i < LOOKBACK - 1:
        return {**base, "status": "INSUFFICIENT_HISTORY", "reason": f"fewer than {LOOKBACK} sessions to {expiry}"}
    window = ss[i - LOOKBACK + 1: i + 1]
    frames, files = [], {}
    for d in window:
        p = cm_path(h, d)
        df = read_udiff(p)
        frames.append(df[df["SctySrs"] == "EQ"].assign(_d=d) if "SctySrs" in df else df.iloc[:0])
        files[p.relative_to(h).as_posix()] = _sha(p)
    files[fo_path(h, expiry).relative_to(h).as_posix()] = _sha(fo_path(h, expiry))
    files[ban_path(h, entry).relative_to(h).as_posix()] = _sha(ban_path(h, entry))
    cm = pd.concat(frames)
    futures = set(fo.loc[fo.FinInstrmTp == "STF", "TckrSymb"])
    wrong = set(ub.WRONG_COMPANY_SERIES)
    signals, excluded = [], {}
    for sym in sorted(futures):
        g = cm[cm.TckrSymb == sym].sort_values("_d")
        if sym in wrong:
            excluded[sym] = "WRONG_COMPANY_SERIES"
            continue
        if len(g) != LOOKBACK or g[["ClsPric", "PrvsClsgPric"]].isna().any().any() or (g.PrvsClsgPric <= 0).any():
            excluded[sym] = "NO_20_SESSIONS"
            continue
        r20 = (float(np.prod(g.ClsPric.to_numpy() / g.PrvsClsgPric.to_numpy())) - 1) * 100
        close_e = float(g.ClsPric.iloc[-1])
        turnover_cr = float(g.TtlTrfVal.mean()) / 1e7
        if close_e < MIN_PRICE:
            excluded[sym] = "PRICE_BELOW_10"
        elif turnover_cr < MIN_TURNOVER_CR:
            excluded[sym] = "TURNOVER_BELOW_30CR"
        elif sym in ban:
            excluded[sym] = "FO_BAN_ON_ENTRY"
        elif r20 <= DROP_PCT:
            signals.append({"symbol": sym, "r20_pct": r20, "close_expiry": close_e, "turnover_cr": round(turnover_cr, 2)})
    signals.sort(key=lambda s: (s["r20_pct"], s["symbol"]))
    return {**base, "status": "OK", "signals": signals, "excluded": excluded, "futures_stocks": len(futures),
            "book_top3": [s["symbol"] for s in signals[:3]], "data_files": files}


def _code_identity() -> str:
    try:
        from research.studies.run_holdout import code_state

        return code_state()["identity"]
    except Exception as exc:                 # recorded, never silently green
        return f"UNKNOWN ({type(exc).__name__})"


def plan(h: Path, expiry: date, journal_path: Path, now: Optional[datetime] = None,
         prereg: Optional[Path] = None, kind: str = "PLAN") -> Dict[str, Any]:
    from research.studies import prereg_io

    now = now or datetime.now(IST)
    entry = entry_session(h, expiry)
    p = build_plan(h, expiry, entry) if entry else {
        "expiry": expiry.isoformat(), "entry_session": None, "status": "BLOCKED",
        "reason": "no ban list published for any session in the 7 days after the expiry (fail closed)"}
    deadline = datetime.combine(entry, time(9, 0), IST) if entry else None
    admissible = bool(kind == "PLAN" and p["status"] == "OK" and deadline is not None and now < deadline)
    rec = {"kind": kind, "strategy": STRATEGY, "written_at": now.isoformat(timespec="seconds"),
           "prereg_sha256": prereg_io.normalised_sha256(prereg or prereg_path()), "admissible": admissible,
           "evidence": "PROSPECTIVE" if admissible else ("REPLAY" if kind != "PLAN" else "LATE"),
           "code_identity": _code_identity(), **p}
    Journal(journal_path).append([rec])
    return rec


# ---------------------------------------------------------------------------------------------- reconcile
class _Panel:
    """The minimal panel research/studies/expiry_relief.simulate_long reads: O, H, L, C arrays of (sessions, 1)."""

    def __init__(self, sym: str, rows: List[Dict[str, float]], days: List[str]) -> None:
        a = np.array([[r["OpnPric"], r["HghPric"], r["LwPric"], r["ClsPric"]] for r in rows], dtype=float)
        self.O, self.H, self.L, self.C = (a[:, k:k + 1] for k in range(4))
        self.j = {sym: 0}
        self.days = days


def reconcile(h: Path, journal_path: Path, as_of: date) -> Dict[str, int]:
    from research.studies.expiry_relief import simulate_long

    journal = Journal(journal_path)
    rows = journal.verify()
    done = {(r["plan_seq"], r["symbol"]) for r in rows if r.get("kind") == "RESULT"}
    ss = sessions(h)
    new: List[Dict[str, Any]] = []
    pending = 0
    for plan_row in rows:
        if plan_row.get("kind") not in ("PLAN", "PLAN_REPLAY") or plan_row.get("status") != "OK":
            continue
        entry = date.fromisoformat(plan_row["entry_session"])
        hold = [d for d in ss if d >= entry][:HOLD]
        todo = [s for s in plan_row["signals"] if (plan_row["seq"], s["symbol"]) not in done]
        if not todo:
            continue
        if len(hold) < HOLD or hold[-1] > as_of:
            pending += len(todo)
            continue
        day_frames = {d: read_udiff(cm_path(h, d)) for d in hold}
        for s in todo:
            sym = s["symbol"]
            out = {"kind": "RESULT", "strategy": STRATEGY, "plan_seq": plan_row["seq"], "symbol": sym,
                   "expiry": plan_row["expiry"], "entry_session": entry.isoformat(),
                   "exit_session": hold[-1].isoformat(), "evidence": plan_row["evidence"],
                   "in_book_top3": sym in plan_row.get("book_top3", []),
                   "scored_at": datetime.now(IST).isoformat(timespec="seconds")}
            rs: List[Optional[Dict[str, float]]] = []
            for d in hold:
                df = day_frames[d]
                m = df[(df.TckrSymb == sym) & (df["SctySrs"] == "EQ")] if "SctySrs" in df else df.iloc[:0]
                rs.append(None if m.empty else {c: float(m.iloc[0][c]) for c in NUMERIC})
            if any(r is None for r in rs):
                new.append({**out, "exit_reason": "VOID_NO_DATA", "net_r": None})
                continue
            prev = [s["close_expiry"]] + [r["ClsPric"] for r in rs[:-1]]
            if any(abs(r["PrvsClsgPric"] / p - 1) > CA_TOLERANCE for r, p in zip(rs, prev)):
                new.append({**out, "exit_reason": "VOID_CORPORATE_ACTION", "net_r": None})
                continue
            panel = _Panel(sym, rs, [d.isoformat() for d in hold])
            sig = pd.DataFrame({"symbol": [sym], "expiry": [plan_row["expiry"]], "signal": [0], "entry": [0],
                                "exit": [HOLD - 1], "r20": [s["r20_pct"]]})
            r = simulate_long(panel, sig, stop_pct=STOP_PCT, target_r=None).iloc[0]
            if r.disposition != "FILLED":
                new.append({**out, "exit_reason": f"VOID_{r.disposition}", "net_r": None})
                continue
            new.append({**out, "exit_reason": r.exit_reason, "qty": int(r.qty), "entry_px": float(r.entry_px),
                        "stop_px": float(r.stop_px), "net_r": float(r.net_r), "gross_r": float(r.gross_r),
                        "fee_r": float(r.fee_r), "net_pnl_rs": round(float(r.net_pnl_rs), 2)})
    if new:
        journal.append(new)
    return {"scored": len(new), "pending": pending}


def summary(journal_path: Path) -> Dict[str, Any]:
    rows = Journal(journal_path).verify()
    res = [r for r in rows if r.get("kind") == "RESULT" and r.get("net_r") is not None]
    out: Dict[str, Any] = {}
    for ev in ("PROSPECTIVE", "LATE", "REPLAY"):
        v = [r["net_r"] for r in res if r["evidence"] == ev]
        book = [r["net_r"] for r in res if r["evidence"] == ev and r.get("in_book_top3")]
        out[ev] = {"trades": len(v), "mean_net_r": round(float(np.mean(v)), 4) if v else None,
                   "book_top3_trades": len(book),
                   "book_top3_mean_net_r": round(float(np.mean(book)), 4) if book else None,
                   "expiries": len({r["expiry"] for r in res if r["evidence"] == ev})}
    out["void"] = sum(1 for r in rows if r.get("kind") == "RESULT" and r.get("net_r") is None)
    return out


# ---------------------------------------------------------------------------------------------- cli
def _history() -> Path:
    from research.data import paths

    return paths.main_checkout() / "shared" / "track2_liquid" / "history"


def _journal(replay: bool) -> Path:
    from research.data import paths

    base = paths.main_checkout() / "shared" / "track2_liquid" / "paper" / "expiry_relief_v2"
    return base / ("replay_journal.jsonl" if replay else "journal.jsonl")


def _prereg_clean() -> Optional[str]:
    from research.data import paths
    from research.studies import prereg_io

    p = prereg_path()
    if prereg_io.load(p).get("status") != "LOCKED_PROSPECTIVE":
        return "pre-registration status is not LOCKED_PROSPECTIVE"
    rel = p.relative_to(paths.repo_root()).as_posix()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=paths.repo_root(), capture_output=True,
                           text=True, timeout=30).stdout.strip()
    return f"{rel} has uncommitted changes" if dirty else None


def main(argv: Optional[List[str]] = None) -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="EXPIRY_RELIEF_LONG v2 paper desk (paper only)")
    ap.add_argument("cmd", choices=["plan", "reconcile", "replay", "summary"])
    ap.add_argument("--date", required=False)
    args = ap.parse_args(argv)
    bad = _prereg_clean()
    if bad:
        print(f"REFUSED: {bad}")
        return 2
    h = _history()
    if args.cmd == "summary":
        for replay in (False, True):
            if _journal(replay).exists():
                print(("REPLAY " if replay else "LIVE ") + json.dumps(summary(_journal(replay)), indent=1))
        return 0
    if not args.date:
        print("REFUSED: --date is required")
        return 2
    d = date.fromisoformat(args.date)
    if args.cmd == "plan":
        rec = plan(h, d, _journal(False))
    elif args.cmd == "replay":
        rec = plan(h, d, _journal(True), kind="PLAN_REPLAY")
        rec = {**rec, "reconcile": reconcile(h, _journal(True), as_of=date.today())}
    else:
        rec = reconcile(h, _journal(False), as_of=d)
    print(json.dumps({k: v for k, v in rec.items() if k not in ("data_files", "excluded")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
