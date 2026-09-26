"""
research/studies/strategy_lab.py
================================
Design-window test bench for the multi-day and event strategies proposed on 26 Sep 2026, after P7 killed every
intraday strategy: Antigravity's eight-strategy blueprint (09:20 IST), Codex's results x gap interaction, and
Claude's earnings-announcement premium and 52-week-high momentum. DESIGN WINDOW ONLY: the daily panel is
truncated at 2024-09-30 when it is loaded, so no forward window can read the holdout.

Every test is ONE fixed specification, written here before the run (no parameter search), and one registered
exploratory look (trials registry, E0_EXPLORATORY). Returns are percent from the ENTRY price (a session's open) to
the exit close, with no stop (the pure drift; a stop simulation follows only for survivors). Per trade:
  raw   the stock's return
  mkt   raw minus NIFTY 50 over the same window
  badj  raw minus beta x NIFTY 50; beta from the 120 sessions up to the signal (min 60), clipped [0.3, 2.5]
'alpha' is the beta-adjusted mean. A long-only multi-day strategy that only earns beta in a rising market has none.
Cost hurdles: CNC 0.28% round trip, MIS 0.12%.
Inference: the SE is clustered where trades share risk: the entry date for 1-session trades, the entry WEEK for
2-10 session holds, the entry MONTH for 20-session holds, the expiry for expiry trades. Thousands of setups on the
same few dates are not thousands of independent observations.
Verdict: CANDIDATE if alpha - cost > 0 with clustered t >= 3.0 (about 15 looks) and at least 2 of the 3 years
positive; PROMISING if t >= 2.0; otherwise NO_EDGE.

L1  EXPIRY_RELIEF_RALLY (Antigravity): on each monthly stock-futures expiry session E, eligible stocks with
    close_E / close_E-20 - 1 <= -5% -> buy open E+1, exit close E+5. Placebo L1p: the same filter on every
    NON-expiry session (is it the expiry, or plain one-month reversal?).
L2  BAN_EXIT (Antigravity, without its look-ahead): first session X after an F&O-ban spell, close_X-1 > SMA20
    -> buy open X, exit close X+4. Antigravity's +2.00% was the subset that RE-ENTERED the ban within 5 days,
    known only afterwards; that split is reported for reference, never as a tradeable number.
L3  PEAD_DRIFT (Antigravity): 'Financial Result Updates'; day 0 = the first session in which it is public (after
    15:30 -> next session; otherwise that session). Day-0 mkt-adjusted close-to-close >= +3% and day-0 volume
    >= 2 x the 20-session average before it -> buy open d+1, exit close d+20 (diagnostics d+5, d+10).
L4  BULK_ACCUMULATION (Antigravity): NOT TESTABLE, no delivery-% or bulk/block-deal data on disk.
L5  TOTM_SIP (Antigravity): NIFTY 50, buy open of the last session of a month, exit close of the 5th session of
    the next month, versus every other 6-session open->close window.
L6  XSEC_MOM_TOP5 (Antigravity): first session of each month; eligible stocks with close > SMA50 and > SMA200 at
    the previous close, ranked by 60-session return minus NIFTY's; top 5 bought at the open, held 20 sessions;
    cash when NIFTY < its SMA200 or India VIX > 24 at the previous close. One observation per month.
L7  AUCTION_GAP_AND_GO (Antigravity): gap >= +1.5% and 09:15-09:19 volume >= 2 x its median over the 20
    previous sessions; from 09:20 to 15:00 buy a break of the 09:15-09:19 high + 1 tick (fill max(trigger,
    minute open) + 1 tick), stop the 09:15-09:19 low (fill min(stop, minute open) - 1 tick; a stop in the entry
    minute is assumed hit), exit at the 15:05 minute's open - 1 tick; MIS fees 0.106%. 1-minute Upstox data.
L8  CAPITULATION (Antigravity's idea, one spec): day-0 mkt-adjusted <= -5%, volume >= 3 x the 20-session average,
    close in the upper half of the day's range -> buy open d+1, exit close d+5 (diagnostic d+10).
C1  EARNINGS_ANNOUNCEMENT_PREMIUM (Frazzini-Lamont; Barber et al.): board meeting for financial results on day M
    (the first session on or after the meeting date), intimated at I. C1a: buy at the open of session M-5 (or the
    first session opening after I, if later), exit close M-1 (out before the result). C1b: buy open M-1 (I must
    precede it), exit close M+1 (through the result).
C2  RESULTS_X_GAP (Codex): results public before the open of day 0 and |mkt-adjusted gap| >= 3% -> day-0
    open->close in the gap direction (MIS, both sides); gap-up subset: open d0 -> close d0+4 (CNC long).
C3  HIGH52_TOP5 (George-Hwang): first session of each month, top 5 eligible stocks by close / 252-session high
    (ties by 60-session return), held 20 sessions. One observation per month.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
DESIGN = ("2022-01-03", "2024-09-30")
CNC, MIS = 0.28, 0.12
MIS_FEES = 0.106                    # % of notional, Dhan MIS round trip at Rs 38k (slippage modelled in ticks)
NIFTY, VIX = "IDX:NIFTY50", "IDX:INDIAVIX"
T_CANDIDATE, T_PROMISING = 3.0, 2.0


# --------------------------------------------------------------------------------------------------- panel
class Panel:
    """Daily OHLCV of every stock on the NIFTY 50 calendar, truncated at `end` (the design end by default)."""

    def __init__(self, snap: Path, end: str = DESIGN[1]) -> None:
        if end > DESIGN[1]:
            raise ValueError("design window only: the holdout is read once, after a locked pre-registration")
        frames = [pd.read_parquet(f, columns=["symbol", "day", "open", "high", "low", "close", "volume"])
                  for f in sorted((snap / "daily").glob("*.parquet"))]
        p = pd.concat(frames)
        p["day"] = p["day"].astype(str)
        p = p[p.day <= end].drop_duplicates(["symbol", "day"])
        wide = {k: p.pivot(index="day", columns="symbol", values=k) for k in ("open", "high", "low", "close", "volume")}
        cal = wide["close"][NIFTY].dropna().index.sort_values()
        self.days: List[str] = [str(d) for d in cal]
        self.i = {d: n for n, d in enumerate(self.days)}
        self.syms = sorted(c for c in wide["close"].columns if not str(c).startswith("IDX:"))
        self.j = {s: n for n, s in enumerate(self.syms)}
        frame = {k: wide[k].reindex(index=cal, columns=self.syms).astype(float) for k in wide}
        self.Cd, self.Vd = frame["close"], frame["volume"]
        self.O, self.H, self.L, self.C, self.V = (frame[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
        self.nO = wide["open"][NIFTY].reindex(cal).to_numpy(float)
        self.nC = wide["close"][NIFTY].reindex(cal).to_numpy(float)
        self.vix = (wide["close"][VIX].reindex(cal).to_numpy(float) if VIX in wide["close"]
                    else np.full(len(cal), np.nan))
        r1 = self.Cd.pct_change(fill_method=None)
        n1 = pd.Series(self.nC, index=cal).pct_change()
        w, mp = 120, 60
        mx, my = r1.rolling(w, min_periods=mp).mean(), n1.rolling(w, min_periods=mp).mean()
        mxy = r1.mul(n1, axis=0).rolling(w, min_periods=mp).mean()
        vy = (n1 ** 2).rolling(w, min_periods=mp).mean() - my ** 2
        self.beta = (mxy - mx.mul(my, axis=0)).div(vy, axis=0).clip(0.3, 2.5).to_numpy()
        u = pd.read_parquet(snap / "reference" / "universe_daily.parquet", columns=["symbol", "session", "eligible"])
        u["session"], u["e"] = u.session.astype(str), u.eligible.astype(int)
        e = u.pivot_table(index="session", columns="symbol", values="e", aggfunc="max")
        self.elig = e.reindex(index=list(cal), columns=self.syms).fillna(0).to_numpy() > 0

    # features, all known at the close of the row's session
    def sma(self, n: int) -> np.ndarray:
        return self.Cd.rolling(n, min_periods=n).mean().to_numpy()

    def ret(self, n: int) -> np.ndarray:
        return (self.Cd / self.Cd.shift(n) - 1).to_numpy() * 100

    def vol_avg_prior(self, n: int = 20) -> np.ndarray:
        return self.Vd.shift(1).rolling(n, min_periods=int(n * 0.75)).mean().to_numpy()

    def nifty_ret(self, n: int) -> np.ndarray:
        s = pd.Series(self.nC)
        return ((s / s.shift(n) - 1) * 100).to_numpy()

    def cc_mkt(self) -> np.ndarray:
        """Day's close-to-close return minus NIFTY's, percent."""
        return self.ret(1) - self.nifty_ret(1)[:, None]

    def trades(self, ev: pd.DataFrame, cluster: str) -> pd.DataFrame:
        """ev: symbol, entry, exit (session indices). Entry at the OPEN of `entry`, exit at the CLOSE of `exit`;
        the stock must be eligible on the entry session. cluster: date | week | month | a column of ev."""
        ev = ev.copy()
        ev["jj"] = ev.symbol.map(self.j)
        ev = ev[ev.jj.notna() & (ev.entry >= 1) & (ev.exit < len(self.days)) & (ev.exit >= ev.entry)]
        if ev.empty:
            return ev
        jj, a, b = ev.jj.astype(int).to_numpy(), ev.entry.astype(int).to_numpy(), ev.exit.astype(int).to_numpy()
        raw = (self.C[b, jj] / self.O[a, jj] - 1) * 100
        nif = (self.nC[b] / self.nO[a] - 1) * 100
        beta = self.beta[a - 1, jj]
        beta = np.where(np.isfinite(beta), beta, 1.0)
        ev = ev.assign(raw=raw, nifty=nif, beta=beta, mkt=raw - nif, badj=raw - beta * nif,
                       eligible=self.elig[a, jj], entry_day=[self.days[x] for x in a],
                       exit_day=[self.days[x] for x in b])
        ev = ev[ev.eligible & np.isfinite(ev.raw) & np.isfinite(ev.nifty)].copy()
        ev["cluster"] = _cluster(ev, cluster)
        return ev.drop(columns=["jj"])


def _cluster(ev: pd.DataFrame, how: str) -> pd.Series:
    d = pd.to_datetime(ev.entry_day)
    if how == "date":
        return ev.entry_day
    if how == "week":
        iso = d.dt.isocalendar()
        return iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
    if how == "month":
        return d.dt.strftime("%Y-%m")
    return ev[how].astype(str)


# --------------------------------------------------------------------------------------------------- stats
def stats(tr: pd.DataFrame, cost: float, col: str = "badj") -> Dict[str, Any]:
    """Mean returns, alpha net of cost, clustered t (by tr.cluster, and by entry date), years, verdict."""
    from research.decision.stats import clustered_se

    n = len(tr)
    if n < 2:
        return {"n": n, "verdict": "INSUFFICIENT"}
    v = tr[col].to_numpy(float)
    se = clustered_se(list(v), list(tr.cluster))
    se_d = clustered_se(list(v), list(tr.entry_day))
    alpha = float(v.mean()) - cost
    t = alpha / se if math.isfinite(se) and se > 0 else math.nan
    years = {}
    for y, g in tr.groupby(tr.entry_day.str[:4]):
        a = float(g[col].mean()) - cost
        years[y] = {"n": len(g), "alpha_net": round(a, 3), "raw_net": round(float(g.raw.mean()) - cost, 3)}
    pos = sum(1 for y in years.values() if y["n"] >= 5 and y["alpha_net"] > 0)
    counted = sum(1 for y in years.values() if y["n"] >= 5)
    verdict = "NO_EDGE"
    if alpha > 0 and math.isfinite(t) and t >= T_PROMISING:
        verdict = "CANDIDATE" if (t >= T_CANDIDATE and pos >= 2) else "PROMISING"
    return {"n": n, "clusters": int(tr.cluster.nunique()), "cost_pct": cost,
            "mean_raw": round(float(tr.raw.mean()), 4), "mean_mkt": round(float(tr.mkt.mean()), 4),
            "mean_badj": round(float(tr.badj.mean()), 4), "median_raw": round(float(tr.raw.median()), 4),
            "raw_net": round(float(tr.raw.mean()) - cost, 4), "alpha_net": round(alpha, 4),
            "t_alpha_net": round(t, 2) if math.isfinite(t) else None,
            "t_alpha_net_by_date": round(alpha / se_d, 2) if math.isfinite(se_d) and se_d > 0 else None,
            "win_raw_over_cost": round(float((tr.raw > cost).mean()), 3), "years": years,
            "years_positive": f"{pos}/{counted}", "verdict": verdict}


# --------------------------------------------------------------------------------------------------- events
def expiry_sessions(snap: Path, P: Panel) -> List[int]:
    """Sessions that are the nearest stock-futures expiry for most F&O stocks (monthly expiry days)."""
    f = pd.read_parquet(snap / "reference" / "fno_point_in_time_2022_2026.parquet",
                        columns=["session", "nearest_fut_expiry", "has_fut"])
    f = f[f.has_fut]
    f["hit"] = f.session.astype(str) == f.nearest_fut_expiry.astype(str)
    share = f.groupby(f.session.astype(str)).hit.mean()
    return sorted(P.i[d] for d, s in share.items() if s > 0.5 and d in P.i)


def results_events(snap: Path, P: Panel, pre_open_only: bool) -> pd.DataFrame:
    """(symbol, d0): the first session in which a 'Financial Result Updates' filing is public.
    pre_open_only keeps filings made after the previous close (15:30) or before 09:15 of day 0."""
    a = pd.read_parquet(snap / "events" / "announcements.parquet", columns=["symbol", "disseminated_at", "desc"])
    a = a[a.desc == "Financial Result Updates"]
    t = pd.to_datetime(a.disseminated_at, utc=True).dt.tz_convert("Asia/Kolkata")
    out = []
    for sym, ts in zip(a.symbol, t):
        d, hm = ts.date().isoformat(), ts.hour * 60 + ts.minute
        if hm >= 15 * 60 + 30:
            k = bisect.bisect_right(P.days, d)
        elif hm < 9 * 60 + 15:
            k = bisect.bisect_left(P.days, d)
        else:
            if pre_open_only:
                continue
            k = bisect.bisect_left(P.days, d)
        if k < len(P.days):
            out.append((sym, k))
    return pd.DataFrame(out, columns=["symbol", "d0"]).drop_duplicates()


def ban_spells(snap: Path, P: Panel) -> pd.DataFrame:
    """One row per ban spell: symbol, first and last banned session index (consecutive sessions)."""
    b = pd.read_parquet(snap / "events" / "fo_ban.parquet")
    b["i"] = b.trade_date.astype(str).map(P.i)
    b = b.dropna(subset=["i"]).drop_duplicates(["symbol", "i"]).sort_values(["symbol", "i"])
    b["i"] = b.i.astype(int)
    b["new"] = (b.groupby("symbol").i.diff() != 1) | (b.symbol != b.symbol.shift())
    b["spell"] = b.new.cumsum()
    return b.groupby("spell").agg(symbol=("symbol", "first"), first=("i", "min"), last=("i", "max")).reset_index(drop=True)


def month_starts(P: Panel) -> List[int]:
    out, prev = [], None
    for n, d in enumerate(P.days):
        if d[:7] != prev:
            out.append(n)
            prev = d[:7]
    return out[1:]                                    # the first month has no previous close in the panel


# --------------------------------------------------------------------------------------------------- tests
def _ev(sym_idx: np.ndarray, sig_idx: np.ndarray, P: Panel, entry_off: int, hold: int) -> pd.DataFrame:
    return pd.DataFrame({"symbol": [P.syms[j] for j in sym_idx], "signal": sig_idx,
                         "entry": sig_idx + entry_off, "exit": sig_idx + entry_off + hold - 1})


def l1_expiry(snap: Path, P: Panel) -> tuple:
    r20 = P.ret(20)
    exp = expiry_sessions(snap, P)
    exp_set = set(exp)
    si, sj = np.where(r20 <= -5.0)
    at_exp = np.array([i in exp_set for i in si], dtype=bool)
    ev = _ev(sj[at_exp], si[at_exp], P, 1, 5)
    ev["expiry"] = [P.days[i] for i in si[at_exp]]
    tr = P.trades(ev, "expiry")
    pl = P.trades(_ev(sj[~at_exp], si[~at_exp], P, 1, 5), "week")
    return {"expiries": len(exp), "L1": stats(tr, CNC), "L1p_placebo_non_expiry": stats(pl, CNC),
            "L1_raw_win_rate": round(float((tr.raw > 0).mean()), 3) if len(tr) else None}, {"L1": tr}


def l2_ban_exit(snap: Path, P: Panel) -> tuple:
    sp = ban_spells(snap, P)
    s20 = P.sma(20)
    rows = []
    banned = {(s, i) for s, f, l in zip(sp.symbol, sp["first"], sp["last"]) for i in range(f, l + 1)}
    for s, last in zip(sp.symbol, sp["last"]):
        x = last + 1
        j = P.j.get(s)
        if j is None or x + 4 >= len(P.days):
            continue
        if not (P.C[x - 1, j] > s20[x - 1, j]):
            continue
        reenter = any((s, k) in banned for k in range(x + 1, x + 5))
        rows.append((s, x - 1, x, x + 4, reenter))
    ev = pd.DataFrame(rows, columns=["symbol", "signal", "entry", "exit", "reenter"])
    tr = P.trades(ev, "week")
    out = {"spells": len(sp), "L2": stats(tr, CNC)}
    for flag, g in tr.groupby("reenter"):
        out[f"L2_lookahead_split_reenter_{bool(flag)}"] = {"n": len(g), "mean_raw": round(float(g.raw.mean()), 3),
                                                          "note": "known only afterwards; not tradeable"}
    return out, {"L2": tr}


def l3_pead(snap: Path, P: Panel) -> tuple:
    ev0 = results_events(snap, P, pre_open_only=False)
    cc, va = P.cc_mkt(), P.vol_avg_prior(20)
    j = ev0.symbol.map(P.j)
    ev0 = ev0[j.notna()].assign(j=j[j.notna()].astype(int))
    d0, jj = ev0.d0.to_numpy(int), ev0.j.to_numpy(int)
    keep = (cc[d0, jj] >= 3.0) & (P.V[d0, jj] >= 2.0 * va[d0, jj])
    out, trs = {"events_all": len(ev0), "events_kept": int(keep.sum())}, {}
    for hold, cl in ((20, "month"), (5, "week"), (10, "week")):
        tr = P.trades(_ev(jj[keep], d0[keep], P, 1, hold), cl)
        out[f"L3_hold{hold}"] = stats(tr, CNC)
        trs[f"L3_hold{hold}"] = tr
    return out, trs


def l5_totm(P: Panel) -> tuple:
    """NIFTY 50: open of the last session of month m -> close of the 5th session of month m+1 (6 sessions)."""
    ms = month_starts(P)
    starts = [m - 1 for m in ms if m + 4 < len(P.days)]
    ret6 = np.array([(P.nC[i + 5] / P.nO[i] - 1) * 100 if i + 5 < len(P.days) else np.nan
                     for i in range(len(P.days))])
    s = set(starts)
    a = np.array([ret6[i] for i in starts])
    b = np.array([ret6[i] for i in range(1, len(P.days)) if i not in s and np.isfinite(ret6[i])])
    diff = a.mean() - b.mean()
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b) * 6)      # other windows overlap 6-fold
    return {"L5": {"n_months": len(a), "mean_totm_pct": round(float(a.mean()), 4),
                   "mean_other_6d_pct": round(float(b.mean()), 4), "diff_pct": round(float(diff), 4),
                   "diff_minus_cnc": round(float(diff) - CNC, 4), "t_diff": round(diff / se, 2),
                   "verdict": "NO_EDGE" if diff - CNC <= 0 or diff / se < T_PROMISING else "PROMISING"}}, {}


def _monthly_top(P: Panel, score: np.ndarray, ok: np.ndarray, k: int, regime: Optional[np.ndarray],
                 tiebreak: Optional[np.ndarray] = None) -> pd.DataFrame:
    rows = []
    for m in month_starts(P):
        s = m - 1
        if m + 19 >= len(P.days):
            break
        if regime is not None and not regime[s]:
            rows.append({"month": P.days[m][:7], "invested": False})
            continue
        cand = np.where(ok[s] & P.elig[m] & np.isfinite(score[s]))[0]
        if len(cand) < k:
            continue
        key = score[s, cand] if tiebreak is None else score[s, cand] + np.nan_to_num(tiebreak[s, cand])
        top = cand[np.argsort(-key)[:k]]
        ev = pd.DataFrame({"symbol": [P.syms[j] for j in top], "signal": s, "entry": m, "exit": m + 19})
        tr = P.trades(ev, "month")
        if len(tr):
            rows.append({"month": P.days[m][:7], "invested": True, "entry_day": P.days[m], "n": len(tr),
                         "raw": tr.raw.mean(), "mkt": tr.mkt.mean(), "badj": tr.badj.mean(),
                         "names": ",".join(tr.symbol)})
    return pd.DataFrame(rows)


def _portfolio_stats(df: pd.DataFrame) -> Dict[str, Any]:
    inv = df[df.invested] if len(df) else df
    if len(inv) < 2:
        return {"months": len(df), "invested": len(inv), "verdict": "INSUFFICIENT"}
    tr = inv.assign(cluster=inv.month)
    s = stats(tr, CNC)
    s.update(months=len(df), invested=len(inv))
    return s


def l6_xsec(P: Panel) -> tuple:
    s50, s200 = P.sma(50), P.sma(200)
    rel60 = P.ret(60) - P.nifty_ret(60)[:, None]
    ok = (P.C > s50) & (P.C > s200)
    n200 = pd.Series(P.nC).rolling(200, min_periods=200).mean().to_numpy()
    regime = (P.nC > n200) & ~(P.vix > 24)
    top5 = _monthly_top(P, rel60, ok, 5, regime)
    dec = _monthly_top(P, rel60, ok, 20, regime)
    first = top5[top5.invested].month.min() if len(top5) and top5.invested.any() else None
    return {"L6_top5": _portfolio_stats(top5), "L6_diag_top20": _portfolio_stats(dec),
            "L6_first_invested_month": first}, {"L6_top5": top5}


def l8_capitulation(P: Panel) -> tuple:
    cc, va = P.cc_mkt(), P.vol_avg_prior(20)
    rng = P.H - P.L
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = np.where(rng > 0, (P.C - P.L) / rng, np.nan)
    si, sj = np.where((cc <= -5.0) & (P.V >= 3.0 * va) & (clv >= 0.5))
    out, trs = {"signals": len(si)}, {}
    for hold in (5, 10):
        tr = P.trades(_ev(sj, si, P, 1, hold), "week")
        out[f"L8_hold{hold}"] = stats(tr, CNC)
        trs[f"L8_hold{hold}"] = tr
    return out, trs


def c1_ea_premium(snap: Path, P: Panel) -> tuple:
    b = pd.read_parquet(snap / "events" / "board_meetings.parquet")
    b = b[b.purpose.str.contains("financial result", case=False, na=False)].copy()
    b["t"] = pd.to_datetime(b.intimated_at, utc=True).dt.tz_convert("Asia/Kolkata")
    b = b.sort_values("t").drop_duplicates(["symbol", "meeting_date"])
    ra, rb = [], []
    for sym, md, t in zip(b.symbol, b.meeting_date.astype(str), b.t):
        if sym not in P.j or pd.isna(t):
            continue
        m = bisect.bisect_left(P.days, md)
        if m + 1 >= len(P.days) or m < 6:
            continue
        d, hm = t.date().isoformat(), t.hour * 60 + t.minute
        first = bisect.bisect_left(P.days, d) if hm < 9 * 60 + 15 else bisect.bisect_right(P.days, d)
        ea = max(m - 5, first)
        if ea <= m - 1:
            ra.append((sym, ea - 1, ea, m - 1))
        if first <= m - 1:
            rb.append((sym, m - 2, m - 1, m + 1))
    cols = ["symbol", "signal", "entry", "exit"]
    ta = P.trades(pd.DataFrame(ra, columns=cols), "week")
    tb = P.trades(pd.DataFrame(rb, columns=cols), "week")
    return {"meetings": len(b), "C1a_pre_result": stats(ta, CNC), "C1b_through_result": stats(tb, CNC)}, \
        {"C1a": ta, "C1b": tb}


def c2_results_gap(snap: Path, P: Panel) -> tuple:
    ev0 = results_events(snap, P, pre_open_only=True)
    j = ev0.symbol.map(P.j)
    ev0 = ev0[j.notna()].assign(j=j[j.notna()].astype(int))
    d0, jj = ev0.d0.to_numpy(int), ev0.j.to_numpy(int)
    ok = d0 >= 1
    d0, jj = d0[ok], jj[ok]
    gap = (P.O[d0, jj] / P.C[d0 - 1, jj] - 1) * 100 - (P.nO[d0] / P.nC[d0 - 1] - 1) * 100
    big = np.abs(gap) >= 3.0
    ev = pd.DataFrame({"symbol": [P.syms[x] for x in jj[big]], "signal": d0[big] - 1, "entry": d0[big],
                       "exit": d0[big], "sign": np.sign(gap[big])})
    tr = P.trades(ev, "date")
    for c in ("raw", "mkt", "badj"):
        tr[c] = tr[c] * tr["sign"]
    up = gap >= 3.0
    tu = P.trades(pd.DataFrame({"symbol": [P.syms[x] for x in jj[up]], "signal": d0[up] - 1, "entry": d0[up],
                                "exit": d0[up] + 4}), "week")
    return {"C2_intraday_gap_direction_MIS": stats(tr, MIS), "C2_gap_up_hold5_CNC": stats(tu, CNC),
            "C2_note": "MIS leg assumes the day-0 open fill (Codex: pessimise before trusting)"}, \
        {"C2_mis": tr, "C2_up5": tu}


def c3_high52(P: Panel) -> tuple:
    hi = pd.DataFrame(P.H).rolling(252, min_periods=252).max().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        score = P.C / hi
    rel60 = P.ret(60) - P.nifty_ret(60)[:, None]
    top5 = _monthly_top(P, score, np.isfinite(score), 5, None, tiebreak=rel60 / 1e6)
    return {"C3_top5": _portfolio_stats(top5)}, {"C3_top5": top5}


def simulate_daily(P: Panel, tr: pd.DataFrame, stop_pct: float, target_r: Optional[float],
                   slip_pct: float = 0.05) -> pd.DataFrame:
    """Long CNC trades on daily bars with a stop at entry x (1 - stop_pct%) and, optionally, half the position
    out at +target_r R; the rest exits at the planned exit close. A gap through the stop fills at the open; a day
    touching both stop and target counts the stop first (pessimistic). slip_pct per side on stop/target fills.
    Returns tr with net_pct (after CNC cost) and net_r (R = stop distance)."""
    out = []
    for r in tr.itertuples():
        j, a, b = P.j[r.symbol], int(r.entry), int(r.exit)
        entry = P.O[a, j]
        stop = entry * (1 - stop_pct / 100)
        tgt = entry * (1 + target_r * stop_pct / 100) if target_r else None
        half_done, pnl, reason = False, 0.0, "TIME"
        for d in range(a, b + 1):
            o, h, lo, c = P.O[d, j], P.H[d, j], P.L[d, j], P.C[d, j]
            if not np.isfinite(lo):
                continue
            if d > a and o <= stop:
                px = o * (1 - slip_pct / 100)
                pnl += (0.5 if half_done else 1.0) * (px / entry - 1)
                reason = "STOP_GAP"
                break
            if lo <= stop:
                px = stop * (1 - slip_pct / 100)
                pnl += (0.5 if half_done else 1.0) * (px / entry - 1)
                reason = "STOP"
                break
            if tgt is not None and not half_done and h >= tgt:
                pnl += 0.5 * (tgt * (1 - slip_pct / 100) / entry - 1)
                half_done = True
        else:
            c = P.C[b, j]
            pnl += (0.5 if half_done else 1.0) * (c / entry - 1)
        net = pnl * 100 - CNC
        out.append({"net_pct": net, "net_r": net / stop_pct, "exit_reason": reason, "half_target": half_done})
    return tr.reset_index(drop=True).join(pd.DataFrame(out))


def l1_diagnostics(snap: Path, P: Panel) -> tuple:
    """Robustness of L1 (diagnostics of one look, not new strategies): the signal day shifted around the expiry,
    a stock-specific (market-adjusted) filter, per-expiry dispersion, outliers, NIFTY regime, hold length,
    capacity (3 most oversold per expiry) and Antigravity's stop/target exits on daily bars."""
    from research.decision.stats import clustered_se

    r20 = P.ret(20)
    r20m = r20 - P.nifty_ret(20)[:, None]
    exp = expiry_sessions(snap, P)
    out: Dict[str, Any] = {}

    def at(offset: int, filt: np.ndarray, hold: int = 5) -> pd.DataFrame:
        rows = []
        for e in exp:
            s = e + offset
            if not (1 <= s < len(P.days)):
                continue
            for j in np.where(filt[s])[0]:
                rows.append((P.syms[j], s, s + 1, s + hold, P.days[e], r20[s, j]))
        ev = pd.DataFrame(rows, columns=["symbol", "signal", "entry", "exit", "expiry", "r20"])
        return P.trades(ev, "expiry")

    base_filter = r20 <= -5.0
    for off in (-3, -2, -1, 0, 1, 2, 3):
        s = stats(at(off, base_filter), CNC)
        out[f"offset_{off:+d}"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net", "raw_net", "verdict")}
    s = stats(at(0, r20m <= -5.0), CNC)
    out["stock_specific_filter_mkt_adj_r20_le_-5"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net",
                                                                          "raw_net", "years", "verdict")}
    for hold in (3, 10):
        s = stats(at(0, base_filter, hold), CNC)
        out[f"hold_{hold}"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net", "raw_net")}
    tr = at(0, base_filter)
    per = tr.groupby("expiry").agg(n=("badj", "size"), alpha=("badj", "mean"), raw=("raw", "mean"))
    per["alpha_net"] = per.alpha - CNC
    out["per_expiry"] = {"expiries": len(per), "positive": int((per.alpha_net > 0).sum()),
                         "median_alpha_net": round(float(per.alpha_net.median()), 3),
                         "equal_weight_expiry_mean_alpha_net": round(float(per.alpha_net.mean()), 3),
                         "t_equal_weight": round(float(per.alpha_net.mean() / (per.alpha_net.std(ddof=1) /
                                                                             math.sqrt(len(per)))), 2),
                         "top3_trade_share": round(float(per.n.nlargest(3).sum() / per.n.sum()), 3),
                         "table": {e: [int(r.n), round(float(r.alpha_net), 2)] for e, r in per.iterrows()}}
    top3 = per.alpha_net.nlargest(3).index
    rest = tr[~tr.expiry.isin(top3)]
    s = stats(rest, CNC)
    out["without_best_3_expiries"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net")}
    out["largest_raw_trades"] = tr.nlargest(8, "raw")[["symbol", "entry_day", "raw", "r20"]].round(2).values.tolist()
    nr = P.nifty_ret(20)
    tr = tr.assign(nifty_r20=[nr[int(x)] for x in tr.signal])
    for name, g in (("nifty_20d_down", tr[tr.nifty_r20 < 0]), ("nifty_20d_up", tr[tr.nifty_r20 >= 0])):
        s = stats(g, CNC)
        out[f"regime_{name}"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net", "raw_net")}
    cap = tr.sort_values("r20").groupby("expiry").head(3)
    s = stats(cap, CNC)
    out["capacity_3_most_oversold"] = {k: s.get(k) for k in ("n", "alpha_net", "t_alpha_net", "raw_net", "years")}
    for stop, tgt in ((3.0, 1.5), (3.0, None), (5.0, None)):
        sim = simulate_daily(P, tr, stop, tgt)
        v = sim.net_r.to_numpy(float)
        se = clustered_se(list(v), list(sim.expiry))
        out[f"sim_stop{stop:g}_target{tgt}"] = {
            "n": len(sim), "mean_net_pct": round(float(sim.net_pct.mean()), 3), "mean_net_r": round(float(v.mean()), 4),
            "t_by_expiry": round(float(v.mean() / se), 2) if math.isfinite(se) and se > 0 else None,
            "stopped": round(float(sim.exit_reason.str.startswith("STOP").mean()), 3),
            "win": round(float((sim.net_pct > 0).mean()), 3)}
    return {"L1D": out}, {"L1D_base": tr}


# --------------------------------------------------------------------------------------------------- L7 (1-minute)
class Minutes:
    """1-minute Upstox candles per (symbol, day), from the monthly raw files named in the manifest."""

    def __init__(self, raw_root: Path) -> None:
        self.root = raw_root
        self.files: Dict[tuple, str] = {}
        for line in (raw_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("interval") == "1minute" and r.get("status") == 200:
                    self.files[(r["symbol"], r["window"][:7])] = r["file"]
        self._sym: Optional[str] = None
        self._months: Dict[str, Dict[str, list]] = {}

    def month(self, sym: str, ym: str) -> Dict[str, list]:
        if sym != self._sym:
            self._sym, self._months = sym, {}
        if ym not in self._months:
            f = self.files.get((sym, ym))
            days: Dict[str, list] = {}
            if f:
                for c in json.loads(gzip.decompress((self.root / f).read_bytes()))["data"]["candles"]:
                    days.setdefault(c[0][:10], []).append(c)
                for v in days.values():
                    v.sort(key=lambda c: c[0])
            self._months[ym] = days
        return self._months[ym]

    def day(self, sym: str, d: str) -> list:
        return self.month(sym, d[:7]).get(d, [])


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _first5(c: list) -> list:
    return [x for x in c if "09:15" <= x[0][11:16] <= "09:19"]


def simulate_gap_and_go(mins: list) -> Optional[Dict[str, Any]]:
    from research.backtest.bars import tick_size

    f5 = _first5(mins)
    if len(f5) < 3:
        return None
    h5, l5 = max(float(x[2]) for x in f5), min(float(x[3]) for x in f5)
    tick = tick_size(h5)
    trig, entry, stop, exit_px, reason = h5 + tick, None, l5, None, None
    for x in mins:
        hm = x[0][11:16]
        o, h, lo = float(x[1]), float(x[2]), float(x[3])
        if entry is None:
            if "09:20" <= hm <= "15:00" and h >= trig:
                entry = max(trig, o) + tick
                if lo <= stop:
                    exit_px, reason = stop - tick, "STOP_SAME_MINUTE"
                    break
            elif hm > "15:00":
                break
            continue
        if hm >= "15:05":
            exit_px, reason = o - tick, "TIME_1505"
            break
        if lo <= stop:
            exit_px, reason = min(stop, o) - tick, "STOP"
            break
    if entry is None:
        return None
    if exit_px is None:
        exit_px, reason = float(mins[-1][4]) - tick, "LAST_MINUTE"
    risk = (entry - stop) / entry * 100
    if risk <= 0:
        return None
    net = (exit_px / entry - 1) * 100 - MIS_FEES
    return {"entry": entry, "stop": stop, "exit": exit_px, "reason": reason, "risk_pct": risk,
            "net_pct": net, "net_r": net / risk}


def l7_gap_and_go(P: Panel, raw_root: Path) -> tuple:
    from research.decision.stats import clustered_se

    gap = np.full(P.O.shape, np.nan)
    gap[1:] = (P.O[1:] / P.C[:-1] - 1) * 100
    si, sj = np.where((gap >= 1.5) & P.elig)
    order = np.lexsort((si, sj))
    M = Minutes(raw_root)
    rows, counts = [], {"gap_candidates": len(si), "no_minutes": 0, "volume_fail": 0, "no_trigger": 0}
    for k in order:
        i, j = int(si[k]), int(sj[k])
        sym, d = P.syms[j], P.days[i]
        today = M.day(sym, d)
        if not today:
            counts["no_minutes"] += 1
            continue
        prior_days = sorted({**M.month(sym, _prev_month(d[:7])), **M.month(sym, d[:7])})
        prior_days = [x for x in prior_days if x < d][-20:]
        pv = [sum(float(c[5]) for c in _first5(M.day(sym, x))) for x in prior_days]
        v5 = sum(float(c[5]) for c in _first5(today))
        if len(pv) < 15 or not (v5 >= 2.0 * float(np.median(pv))):
            counts["volume_fail"] += 1
            continue
        sim = simulate_gap_and_go(today)
        if sim is None:
            counts["no_trigger"] += 1
            continue
        rows.append({"symbol": sym, "day": d, "gap_pct": gap[i, j], **sim})
    tr = pd.DataFrame(rows)
    out: Dict[str, Any] = {**counts, "trades": len(tr)}
    if len(tr) >= 2:
        for col in ("net_pct", "net_r"):
            v = tr[col].to_numpy(float)
            se = clustered_se(list(v), list(tr.day))
            out[f"mean_{col}"] = round(float(v.mean()), 4)
            out[f"t_{col}"] = round(float(v.mean()) / se, 2) if math.isfinite(se) and se > 0 else None
        out["win_rate"] = round(float((tr.net_pct > 0).mean()), 3)
        out["exit_reasons"] = tr.reason.value_counts().to_dict()
        out["years"] = {y: {"n": len(g), "mean_net_r": round(float(g.net_r.mean()), 4)}
                        for y, g in tr.groupby(tr.day.str[:4])}
        t, m = out.get("t_net_r"), out["mean_net_r"]
        out["verdict"] = ("CANDIDATE" if t is not None and m > 0 and t >= T_CANDIDATE else
                          "PROMISING" if t is not None and m > 0 and t >= T_PROMISING else "NO_EDGE")
    return {"L7": out}, {"L7": tr}


# --------------------------------------------------------------------------------------------------- main
TESTS = ("L1", "L1D", "L2", "L3", "L5", "L6", "L7", "L8", "C1", "C2", "C3")


def run(snap: Path, tests: List[str], raw_root: Optional[Path]) -> tuple:
    P = Panel(snap)
    res: Dict[str, Any] = {"window": [P.days[0], P.days[-1]], "design_end": DESIGN[1], "stocks": len(P.syms),
                           "hurdles": {"CNC": CNC, "MIS": MIS},
                           "L4_BULK_ACCUMULATION": "NOT TESTABLE: no delivery-% or bulk/block-deal data on disk"}
    trades: Dict[str, pd.DataFrame] = {}
    fns = {"L1": lambda: l1_expiry(snap, P), "L1D": lambda: l1_diagnostics(snap, P),
           "L2": lambda: l2_ban_exit(snap, P), "L3": lambda: l3_pead(snap, P),
           "L5": lambda: l5_totm(P), "L6": lambda: l6_xsec(P), "L8": lambda: l8_capitulation(P),
           "C1": lambda: c1_ea_premium(snap, P), "C2": lambda: c2_results_gap(snap, P), "C3": lambda: c3_high52(P),
           "L7": lambda: l7_gap_and_go(P, raw_root)}
    for t in tests:
        out, tr = fns[t]()
        res[t] = out
        trades.update(tr)
    return res, trades


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    ap = argparse.ArgumentParser(description="Design-window strategy lab (fixed specifications, no search)")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--tests", default=",".join(TESTS))
    ap.add_argument("--raw-upstox", default=None, help="raw/upstox root for L7 (default: main history raw/upstox)")
    args = ap.parse_args(argv)
    snap = Path(args.snapshot)
    tests = [t.strip() for t in args.tests.split(",") if t.strip()]
    unknown = sorted(set(tests) - set(TESTS))
    if unknown:
        print(f"unknown tests {unknown}")
        return 2
    raw = Path(args.raw_upstox) if args.raw_upstox else \
        paths.main_checkout() / "shared" / "track2_liquid" / "history" / "raw" / "upstox"
    res, trades = run(snap, tests, raw)
    tag = "_".join(tests) if len(tests) < 4 else "all"
    out_dir = paths.ensure(paths.outputs_dir() / "strategy_lab" / f"{datetime.now(IST):%Y%m%d_%H%M%S}_{tag}")
    for k, df in trades.items():
        if len(df):
            df.to_parquet(out_dir / f"trades_{k}.parquet", index=False)
    res["snapshot"] = str(snap)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
