"""
research/shadow/expiry_desk.py
==============================
Paper desk for EXPIRY_RELIEF_LONG v2 (research/studies/prereg/expiry_relief_long_v2.yaml). PAPER ONLY (AGENTS.md
Rule 1): it never places, modifies or cancels an order, and it has no broker code at all.

This file holds the strategy's own rules (ExpiryReliefV2, a research/framework plug-in). Everything shared by all
paper strategies (journal, 09:00 prospective rule, duplicates, pre-registration and code checks, missing-session
checks, scoring once, evaluation) lives in research/framework/desk.py. The daily command runs every strategy:

    python -m research.framework.daily --date 2026-09-29

This module's own command still works for this one strategy:
    python -m research.shadow.expiry_desk plan      --date 2026-09-29   # expiry evening, after the files land
    python -m research.shadow.expiry_desk reconcile --date 2026-10-07   # any evening: scores finished trades
    python -m research.shadow.expiry_desk replay    --date 2026-08-25   # a past expiry, separate journal, never evidence
    python -m research.shadow.expiry_desk summary

Rules (from the pre-registration): 20-session split-safe return <= -5% into a monthly stock-futures expiry;
eligibility on entry; buy the entry open + 1 tick; 5% stop; no target; exit the close of the 5th session held; A1
sizing; Dhan CNC fees; simulated by research/studies/expiry_relief.simulate_long (the design and holdout code).
The 20 sessions must chain (no missing file); a corporate action inside the hold (previous close not equal to the
prior close within 1%) VOIDs the trade.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from research.framework import desk
from research.framework.market import (NUMERIC, MarketFiles, ban_path, cm_path, fo_path, read_udiff,  # noqa: F401
                                       sha256_file)
from research.framework.strategy import PaperStrategy, paper_root
from research.shadow.run_day import Journal  # noqa: F401  (tests and callers import it from here)

IST = timezone(timedelta(hours=5, minutes=30))
STRATEGY = "EXPIRY_RELIEF_LONG_v2"
LOOKBACK, DROP_PCT, HOLD, STOP_PCT = 20, -5.0, 5, 0.05
MIN_PRICE, MIN_TURNOVER_CR = 10.0, 30.0
CA_TOLERANCE = 0.01


def prereg_path() -> Path:
    from research.studies import prereg_io

    return prereg_io.PREREG_DIR / "expiry_relief_long_v2.yaml"


def is_monthly_expiry(fo: pd.DataFrame, day: date) -> bool:
    if "FinInstrmTp" not in fo:
        return False
    stf = fo[fo.FinInstrmTp == "STF"]
    if stf.empty:
        return False
    nearest = stf.groupby("TckrSymb")["XpryDt"].min()
    return bool((nearest == day.isoformat()).mean() > 0.5)


class _Panel:
    """The minimal panel research/studies/expiry_relief.simulate_long reads: O, H, L, C arrays of (sessions, 1)."""

    def __init__(self, sym: str, rows: List[Dict[str, float]], days: List[str]) -> None:
        a = np.array([[r["OpnPric"], r["HghPric"], r["LwPric"], r["ClsPric"]] for r in rows], dtype=float)
        self.O, self.H, self.L, self.C = (a[:, k:k + 1] for k in range(4))
        self.j = {sym: 0}
        self.days = days


class ExpiryReliefV2(PaperStrategy):
    id = STRATEGY
    hold_sessions = HOLD

    def __init__(self, prereg: Optional[Path] = None, journal_dir: Optional[Path] = None) -> None:
        self._prereg = Path(prereg) if prereg else None
        self._journal_dir = Path(journal_dir) if journal_dir else None

    def prereg_path(self) -> Path:
        return self._prereg or prereg_path()

    def journal_path(self, replay: bool = False) -> Path:
        base = self._journal_dir or paper_root() / "expiry_relief_v2"
        return base / ("replay_journal.jsonl" if replay else "journal.jsonl")

    @property
    def evaluation(self) -> Dict[str, Any]:                       # type: ignore[override]
        from research.studies import prereg_io

        ev = prereg_io.load(self.prereg_path())["evaluation"]
        return {"review_after": int(ev["review_after_expiries"]), "futility_after": int(ev["futility_after_expiries"]),
                "t_pass": 2.0}

    # ------------------------------------------------------------------------------------------ plan
    def is_plan_day(self, md: MarketFiles, day: date) -> bool:
        """An expiry session. Without the F&O file it cannot be known, so it is planned (and records NO_DATA)."""
        if not md.fo_path(day).exists():
            return day.weekday() < 5
        return is_monthly_expiry(md.fo(day), day)

    def entry_ban(self, md: MarketFiles, entry: date) -> tuple:
        """(ban set, assumptions). Where the source has ban lists, a missing one is None (the plan blocks). Before
        ban lists existed on file (the 2005-2021 archive), no stock is excluded for a ban and the plan says so."""
        ban = md.ban(entry)
        if ban is None and not getattr(md, "ban_lists", True):
            return set(), ["NO_BAN_LIST_ERA"]
        return ban, []

    def build_plan(self, md: MarketFiles, expiry: date, entry: Optional[date]) -> Dict[str, Any]:
        from research.data import universe_build as ub

        base: Dict[str, Any] = {"expiry": expiry.isoformat(), "entry_session": entry.isoformat() if entry else None}
        ss = md.sessions()
        if expiry not in ss or not md.fo_path(expiry).exists():
            return {**base, "status": "NO_DATA", "reason": f"no CM/F&O file for {expiry}"}
        fo = md.fo(expiry)
        if not is_monthly_expiry(fo, expiry):
            return {**base, "status": "NOT_AN_EXPIRY", "reason": f"{expiry} is not a monthly stock-futures expiry"}
        ban, assumptions = self.entry_ban(md, entry) if entry else (None, [])
        if ban is None:
            return {**base, "status": "BLOCKED", "reason": f"no ban list for the entry session {entry} (fail closed)"}
        base["assumptions"] = assumptions
        surv = md.surveillance(expiry)                   # ASM/GSM as fetched on the plan day (decision A, 27 Sep)
        if surv is None:
            if md.surveillance_required(expiry):
                return {**base, "status": "BLOCKED", "reason": f"no ASM/GSM list for {expiry} (fail closed)"}
            surv = set()
            assumptions.append("NO_SURVEILLANCE_LIST_ERA")
        i = ss.index(expiry)
        if i < LOOKBACK - 1:
            return {**base, "status": "INSUFFICIENT_HISTORY", "reason": f"fewer than {LOOKBACK} sessions to {expiry}"}
        window = ss[i - LOOKBACK + 1: i + 1]
        ch = md.chain_window(window)
        if ch["ok"] is not True:
            return {**base, "status": "DATA_GAP", "reason": f"the {LOOKBACK} sessions to {expiry} do not chain "
                                                            f"(a file is missing or unverifiable): "
                                                            f"{ch['breaks'] or ch['unknown']}"}
        frames, files = [], {}
        for d in window:
            frames.append(md.cm_eq(d).assign(_d=d))
            files[md.rel(md.cm_path(d))] = sha256_file(md.cm_path(d))
        files[md.rel(md.fo_path(expiry))] = sha256_file(md.fo_path(expiry))
        if md.ban_path(entry).exists():
            files[md.rel(md.ban_path(entry))] = sha256_file(md.ban_path(entry))
        for kind in ("asm", "gsm"):
            sp = md.surveillance_path(expiry, kind)
            if sp.exists():
                files[md.rel(sp)] = sha256_file(sp)
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
            elif sym in surv:
                excluded[sym] = "ASM_GSM"
            elif sym in ban:
                excluded[sym] = "FO_BAN_ON_ENTRY"
            elif r20 <= DROP_PCT:
                signals.append({"symbol": sym, "side": "BUY", "r20_pct": r20, "close_expiry": close_e,
                                "turnover_cr": round(turnover_cr, 2)})
        signals.sort(key=lambda s: (s["r20_pct"], s["symbol"]))
        book = [s["symbol"] for s in signals[:3]]
        return {**base, "status": "OK", "signals": signals, "excluded": excluded, "futures_stocks": len(futures),
                "book": book, "book_top3": book, "data_files": files}

    # ------------------------------------------------------------------------------------------ score
    def score(self, md: MarketFiles, plan_row: Dict[str, Any], s: Dict[str, Any], hold: List[date]) -> Dict[str, Any]:
        from research.studies.expiry_relief import simulate_long

        sym = s["symbol"]
        out: Dict[str, Any] = {"expiry": plan_row.get("expiry")}
        rs: List[Optional[Dict[str, float]]] = []
        for d in hold:
            m = md.cm_eq(d)
            m = m[m.TckrSymb == sym]
            rs.append(None if m.empty else {c: float(m.iloc[0][c]) for c in NUMERIC})
        if any(r is None for r in rs):
            return {**out, "exit_reason": "VOID_NO_DATA", "net_r": None}
        prev = [s["close_expiry"]] + [r["ClsPric"] for r in rs[:-1]]          # type: ignore[index]
        if any(abs(r["PrvsClsgPric"] / p - 1) > CA_TOLERANCE for r, p in zip(rs, prev)):   # type: ignore[index]
            return {**out, "exit_reason": "VOID_CORPORATE_ACTION", "net_r": None}
        panel = _Panel(sym, rs, [d.isoformat() for d in hold])                   # type: ignore[arg-type]
        sig = pd.DataFrame({"symbol": [sym], "expiry": [plan_row.get("expiry")], "signal": [0], "entry": [0],
                            "exit": [HOLD - 1], "r20": [s["r20_pct"]]})
        r = simulate_long(panel, sig, stop_pct=STOP_PCT, target_r=None).iloc[0]
        if r.disposition != "FILLED":
            return {**out, "exit_reason": f"VOID_{r.disposition}", "net_r": None}
        return {**out, "exit_reason": r.exit_reason, "qty": int(r.qty), "entry_px": float(r.entry_px),
                "stop_px": float(r.stop_px), "net_r": float(r.net_r), "gross_r": float(r.gross_r),
                "fee_r": float(r.fee_r), "net_pnl_rs": round(float(r.net_pnl_rs), 2)}


# ---------------------------------------------------------------------------------------------- module API
def sessions(h: Path) -> List[date]:
    return MarketFiles(h).sessions()


def ban_for(h: Path, d: date) -> Optional[Set[str]]:
    return MarketFiles(h).ban(d)


def entry_session(h: Path, expiry: date) -> Optional[date]:
    return MarketFiles(h).entry_session(expiry)[0]


def build_plan(h: Path, expiry: date, entry: Optional[date]) -> Dict[str, Any]:
    return ExpiryReliefV2().build_plan(MarketFiles(h), expiry, entry)


def _strategy_for(journal_path: Path, prereg: Optional[Path] = None) -> ExpiryReliefV2:
    return ExpiryReliefV2(prereg=prereg, journal_dir=Path(journal_path).parent)


def plan(h: Path, expiry: date, journal_path: Path, now: Optional[datetime] = None,
         prereg: Optional[Path] = None, kind: str = "PLAN", code: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return desk.plan(_strategy_for(journal_path, prereg), MarketFiles(h), expiry, Path(journal_path), now=now,
                     kind=kind, code=code)


def reconcile(h: Path, journal_path: Path, as_of: date) -> Dict[str, Any]:
    return desk.reconcile(_strategy_for(journal_path), MarketFiles(h), Path(journal_path), as_of=as_of)


def summary(journal_path: Path) -> Dict[str, Any]:
    return desk.summary(_strategy_for(journal_path), Path(journal_path))


# ---------------------------------------------------------------------------------------------- cli
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

    from research.framework.strategy import history_root

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="EXPIRY_RELIEF_LONG v2 paper desk (paper only)")
    ap.add_argument("cmd", choices=["plan", "reconcile", "replay", "summary"])
    ap.add_argument("--date", required=False)
    args = ap.parse_args(argv)
    bad = _prereg_clean()
    if bad:
        print(f"REFUSED: {bad}")
        return 2
    code = desk.code_state()
    if args.cmd == "plan" and code["dirty"]:
        print(f"REFUSED: research code has uncommitted changes {code['dirty'][:5]} (the plan could never count)")
        return 2
    s, md = ExpiryReliefV2(), MarketFiles(history_root())
    if args.cmd == "summary":
        for replay in (False, True):
            if s.journal_path(replay).exists():
                print(("REPLAY " if replay else "LIVE ") + json.dumps(desk.summary(s, replay=replay), indent=1,
                                                                        default=str))
        return 0
    if not args.date:
        print("REFUSED: --date is required")
        return 2
    d = date.fromisoformat(args.date)
    if args.cmd == "plan":
        rec = desk.plan(s, md, d)                 # the desk reads the real clock and code state itself (A4)
    elif args.cmd == "replay":
        rec = desk.plan(s, md, d, kind="PLAN_REPLAY")
        rec = {**rec, "reconcile": desk.reconcile(s, md, as_of=date.today(), replay=True)}
    else:
        rec = desk.reconcile(s, md, as_of=d)
    print(json.dumps({k: v for k, v in rec.items() if k not in ("data_files", "excluded")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
