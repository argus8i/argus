"""
research/data/universe_build.py
===============================
Point-in-time Track 2 universe per session (plan P3.5), plus the TableUniverse the engine consumes.

Eligibility of (symbol, session d), using only information available before d:
- F&O member on d;
- previous daily close >= Rs 10;
- DTV20 >= Rs 30 Cr, where DTV20 is the median of close x volume over the 20 daily bars before d
  (at least 20 valid bars, otherwise INSUFFICIENT_HISTORY);
- not in that day's F&O ban list, if a ban history is supplied;
- not under ASM/GSM, if a surveillance history is supplied.

Caveats this module cannot remove, and which every output carries in `flags`:
- FNO_MEMBERSHIP_CURRENT_LIST: membership comes from today's contract list (fno_lot_sizes.json), applied to
  every past date. That is survivorship bias; names that left F&O are missing.
- FO_BAN_HISTORY_MISSING / SURVEILLANCE_HISTORY_MISSING when those histories are not supplied.
- MCAP_BAND_NOT_APPLIED: the Rs 4,000-75,000 Cr band needs lagged shares outstanding; applying today's
  market cap to past sessions would be look-ahead.
- Yahoo daily prices are split-adjusted, so the Rs 10 floor is tested on adjusted prices before a split.
  Turnover (close x volume) is unaffected by the adjustment.

CLI:
    python -m research.data.universe_build [--fno shared/track2_liquid/fno_lot_sizes.json]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from research.data import paths

MIN_PRICE = 10.0
DTV_MIN_RS = 30e7          # Rs 30 Cr
DTV_LOOKBACK = 20


def load_fno_symbols(path: Path | str) -> List[str]:
    """Equity underlyings from Antigravity's fno_lot_sizes.json (indices are excluded)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    lots = raw.get("symbol_to_lot_size") or {}
    index_names = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYFPI", "MIDCPNIFTY", "NIFTYNXT50"}
    return sorted(s for s in lots if s not in index_names)


def eligibility(daily: Sequence[Tuple[date, float, int]], sessions: Iterable[date], member: bool,
                banned: Optional[Set[date]] = None, surveillance: Optional[Set[date]] = None,
                ban_known: Optional[Set[date]] = None) -> List[Dict[str, object]]:
    """daily: (day, close, volume) sorted by day. Returns one row per session in `sessions`.
    ban_known: sessions whose ban list was fetched. When a ban history is supplied with ban_known, a
    session outside it is FO_BAN_UNKNOWN (ineligible): a missing ban file is never read as "no ban"."""
    days = [d for d, _, _ in daily]
    closes = np.array([c for _, c, _ in daily], dtype=float)
    vols = np.array([v for _, _, v in daily], dtype=float)
    turnover = closes * vols
    valid = np.isfinite(turnover) & (vols > 0) & (closes > 0)
    import bisect

    out = []
    for d in sessions:
        k = bisect.bisect_left(days, d)          # daily bars strictly before d
        prev_close = float(closes[k - 1]) if k > 0 else float("nan")
        window = turnover[max(0, k - DTV_LOOKBACK):k][valid[max(0, k - DTV_LOOKBACK):k]]
        dtv = float(np.median(window)) if len(window) >= DTV_LOOKBACK else float("nan")
        if not member:
            reason = "NOT_FNO_MEMBER"
        elif k == 0 or not np.isfinite(prev_close):
            reason = "NO_PRIOR_DAILY"
        elif prev_close < MIN_PRICE:
            reason = "PRICE_BELOW_FLOOR"
        elif not np.isfinite(dtv):
            reason = "INSUFFICIENT_HISTORY"
        elif dtv < DTV_MIN_RS:
            reason = "DTV20_BELOW_30CR"
        elif banned is not None and ban_known is not None and d not in ban_known:
            reason = "FO_BAN_UNKNOWN"
        elif banned is not None and d in banned:
            reason = "FO_BAN"
        elif surveillance is not None and d in surveillance:
            reason = "SURVEILLANCE"
        else:
            reason = "ELIGIBLE"
        out.append({"session": d, "eligible": reason == "ELIGIBLE", "reason": reason,
                    "prev_close": prev_close, "dtv20_cr": dtv / 1e7 if np.isfinite(dtv) else float("nan")})
    return out


@dataclass
class TableUniverse:
    """Engine-facing universe backed by the universe table. check() mirrors PointInTimeUniverse.check.
    A (symbol, session) missing from the table is ineligible (fail closed)."""
    table: Mapping[Tuple[str, date], Tuple[bool, str]]
    flags: Tuple[str, ...] = ()
    min_price: float = MIN_PRICE
    assumed: bool = False
    note: str = ""

    def check(self, symbol: str, dt: date, price: Optional[float] = None) -> Tuple[bool, str]:
        if price is not None and price < self.min_price:
            return False, "PRICE_BELOW_FLOOR"
        row = self.table.get((symbol.strip().upper(), dt))
        if row is None:
            return False, "NOT_IN_UNIVERSE_TABLE"
        return row

    @classmethod
    def from_parquet(cls, path: Optional[Path | str] = None) -> "TableUniverse":
        import pandas as pd

        path = Path(path) if path else paths.reference_dir() / "universe_daily.parquet"
        df = pd.read_parquet(path)
        table = {(str(s), date.fromisoformat(str(d)[:10])): (bool(e), str(r))
                 for s, d, e, r in zip(df["symbol"], df["session"], df["eligible"], df["reason"])}
        meta_path = path.with_suffix(".json")
        flags: Tuple[str, ...] = ()
        if meta_path.exists():
            flags = tuple(json.loads(meta_path.read_text(encoding="utf-8")).get("flags", []))
        return cls(table=table, flags=flags, note=str(path))


# Symbols whose only available intraday series belongs to a DIFFERENT company (the resolver mapped a merged,
# delisted ISIN to the acquirer's instrument key; Upstox refuses the delisted ISINs themselves, MEASURED
# 25 Sep 2026: UDAPI100011). They are never eligible.
WRONG_COMPANY_SERIES: Dict[str, str] = {
    "HDFC": "HDFC Ltd (INE001A01036) merged into HDFC Bank 2023-07; its file holds HDFCBANK prices",
    "MINDTREE": "Mindtree (INE018I01017) merged into LTI 2022-11; its file holds LTI/LTIM prices",
}


def load_fno_membership(path: Path | str) -> Dict[str, Set[date]]:
    """symbol -> sessions on which it had stock futures listed (point-in-time F&O membership, derived from
    the NSE F&O bhavcopy: `session`, `symbol`, `has_fut`)."""
    import pandas as pd

    df = pd.read_parquet(path, columns=["session", "symbol", "has_fut"])
    out: Dict[str, Set[date]] = {}
    for s, d, f in zip(df["symbol"], df["session"], df["has_fut"]):
        if bool(f):
            out.setdefault(str(s).upper(), set()).add(date.fromisoformat(str(d)[:10]))
    return out


def build(store, fno_symbols: Sequence[str], banned: Optional[Mapping[str, Set[date]]] = None,
          surveillance: Optional[Mapping[str, Set[date]]] = None,
          ban_known: Optional[Set[date]] = None,
          membership: Optional[Mapping[str, Set[date]]] = None,
          excluded: Optional[Mapping[str, str]] = None,
          series_keys: Optional[Mapping[str, str]] = None) -> Tuple[List[Dict[str, object]], List[str]]:
    """membership (point-in-time) replaces the static fno_symbols list when given. excluded: symbol -> reason
    (never eligible, reason WRONG_COMPANY_SERIES). series_keys: symbol -> source instrument key; two symbols
    eligible on the same session with the same key would be one price series counted twice, so all of them
    are marked DUPLICATE_SERIES (fail closed)."""
    flags = ["FNO_MEMBERSHIP_POINT_IN_TIME" if membership is not None else "FNO_MEMBERSHIP_CURRENT_LIST",
             "MCAP_BAND_NOT_APPLIED"]
    if banned is None:
        flags.append("FO_BAN_HISTORY_MISSING")
    if surveillance is None:
        flags.append("SURVEILLANCE_HISTORY_MISSING")
    members = set(fno_symbols)
    excluded = dict(excluded or {})
    rows: List[Dict[str, object]] = []
    for sym in store.symbols:
        if store.kind(sym) != "TRADABLE":
            continue
        daily = [(d.day, d.close, d.volume) for d in store.daily(sym)]
        days = store.sessions(sym)
        if membership is not None:
            mdays = membership.get(sym.upper(), set())
            parts = [eligibility(daily, [d], d in mdays,
                                 (banned or {}).get(sym, set()) if banned is not None else None,
                                 (surveillance or {}).get(sym) if surveillance is not None else None,
                                 ban_known=ban_known)[0] for d in days]
        else:
            parts = eligibility(daily, days, sym in members,
                                (banned or {}).get(sym, set()) if banned is not None else None,
                                (surveillance or {}).get(sym) if surveillance is not None else None,
                                ban_known=ban_known)
        for r in parts:
            if sym in excluded:
                r = {**r, "eligible": False, "reason": "WRONG_COMPANY_SERIES"}
            rows.append({"symbol": sym, **r})
    if series_keys:
        by_day_key: Dict[Tuple[object, str], List[int]] = {}
        for i, r in enumerate(rows):
            key = series_keys.get(str(r["symbol"]))
            if r["eligible"] and key:
                by_day_key.setdefault((r["session"], key), []).append(i)
        for idx in by_day_key.values():
            if len(idx) > 1:
                for i in idx:
                    rows[i] = {**rows[i], "eligible": False, "reason": "DUPLICATE_SERIES"}
    return rows, flags


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the point-in-time universe table (plan P3.5)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--fno", default=str(paths.shared_input("fno_lot_sizes.json")))
    ap.add_argument("--no-ban-history", action="store_true",
                    help="build without the F&O ban history and label the table FO_BAN_HISTORY_MISSING "
                         "(default: use events/fo_ban*.parquet when present; uncovered sessions FO_BAN_UNKNOWN)")
    ap.add_argument("--pit-membership", nargs="?", const="DEFAULT", default=None,
                    help="point-in-time F&O membership parquet (default: <history>/bhavcopy/"
                         "fno_point_in_time_2022_2026.parquet); also applies WRONG_COMPANY_SERIES and "
                         "DUPLICATE_SERIES using the Upstox manifest's instrument keys")
    args = ap.parse_args(argv)
    import pandas as pd
    from collections import Counter

    from research.data.store_parquet import ParquetCandleStore

    store = ParquetCandleStore(args.root)            # STRATEGY mode: holdout sessions stay hidden
    banned = known = None
    if not args.no_ban_history and (paths.history_dir() / "events" / "fo_ban_days.parquet").exists():
        from research.data.nse_events import load_fo_ban

        banned, known = load_fo_ban()                # plan P3.4 archives; unknown days are FO_BAN_UNKNOWN
    membership = excluded = keys = None
    if args.pit_membership:
        mpath = (paths.history_dir() / "bhavcopy" / "fno_point_in_time_2022_2026.parquet"
                 if args.pit_membership == "DEFAULT" else Path(args.pit_membership))
        membership = load_fno_membership(mpath)
        excluded = WRONG_COMPANY_SERIES
        keys = {}
        man = paths.history_dir() / "raw" / "upstox" / "manifest.jsonl"
        for line in man.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                keys.setdefault(str(rec["symbol"]), str(rec["instrument_key"]))
    rows, flags = build(store, load_fno_symbols(args.fno), banned=banned, ban_known=known,
                        membership=membership, excluded=excluded, series_keys=keys)
    out = paths.ensure(paths.reference_dir()) / "universe_daily.parquet"
    df = pd.DataFrame(rows)
    df["session"] = df["session"].astype(str)
    df.to_parquet(out, index=False)
    summary = {"rows": len(rows), "flags": flags, "reasons": dict(Counter(r["reason"] for r in rows)),
               "eligible_per_session_median": float(df[df.eligible].groupby("session").size().median())
               if df.eligible.any() else 0.0,
               "fno_list": args.fno}
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
