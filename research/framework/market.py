"""
research/framework/market.py
============================
The official NSE daily files every paper strategy reads, in the layout the daily pipeline stores them:

  history/bhavcopy/raw/cm/<YYYY>/<YYYY-MM-DD>.csv.gz     equities (UDiFF): TckrSymb SctySrs OpnPric HghPric LwPric
                                                          ClsPric PrvsClsgPric TtlTrfVal
  history/bhavcopy/raw/fo/<YYYY>/<YYYY-MM-DD>.csv.gz     derivatives (UDiFF): FinInstrmTp (STF = stock futures), XpryDt
  history/raw/nse/fo_ban/<D>_<D>.csv.gz                  F&O ban list for session D (published the evening before)

Sessions are the dates that have a CM file. Nothing is assumed about holidays:
  - the entry session after a plan day is the first weekday whose ban list NSE has published (skipped weekdays are
    recorded, and a later CM file for a skipped weekday breaks the chain below);
  - chain(prev, day) proves that no session is missing between two files: on adjacent sessions each stock's
    PrvsClsgPric equals the previous file's ClsPric (measured on real files, Sep 2026: 100% of about 2,650 EQ stocks;
    with one session missing, 3-8%). A corporate action changes only its own stock, so a share below MIN_SHARE means
    a missing file, and fewer than MIN_COMMON stocks means the check cannot be made (ok is None, never True).
"""
from __future__ import annotations

import gzip
import hashlib
import io
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

NUMERIC = ["OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTrfVal"]
CHAIN_TOLERANCE = 0.001
MIN_COMMON = 20
MIN_SHARE = 0.90
MAX_ENTRY_SEARCH_DAYS = 7


# NSE's pre-UDiFF bhavcopies (every file before 8 Jul 2024, and the whole 2005-2021 archive) use other column names.
# They are renamed to the UDiFF names so every strategy reads one format. Dates become ISO (the legacy files write
# 04-JAN-2010, 4-JAN-2010 or 28-Jan-2010).
LEGACY_CM = {"SYMBOL": "TckrSymb", "SERIES": "SctySrs", "OPEN": "OpnPric", "HIGH": "HghPric", "LOW": "LwPric",
             "CLOSE": "ClsPric", "LAST": "LastPric", "PREVCLOSE": "PrvsClsgPric", "TOTTRDQTY": "TtlTradgVol",
             "TOTTRDVAL": "TtlTrfVal", "TIMESTAMP": "TradDt", "TOTALTRADES": "TtlNbOfTxsExctd"}
LEGACY_FO = {"INSTRUMENT": "FinInstrmTp", "SYMBOL": "TckrSymb", "EXPIRY_DT": "XpryDt", "STRIKE_PR": "StrkPric",
             "OPTION_TYP": "OptnTp", "OPEN": "OpnPric", "HIGH": "HghPric", "LOW": "LwPric", "CLOSE": "ClsPric",
             "SETTLE_PR": "SttlmPric", "CONTRACTS": "TtlTradgVol", "OPEN_INT": "OpnIntrst", "CHG_IN_OI": "ChngInOpnIntrst",
             "TIMESTAMP": "TradDt"}
LEGACY_INSTRUMENT = {"FUTSTK": "STF", "FUTIDX": "IDF", "OPTSTK": "STO", "OPTIDX": "IDO"}


class UnknownFormat(ValueError):
    """A file that is neither a UDiFF nor a legacy NSE bhavcopy (fail closed: never guess columns)."""


def _bytes(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:4] == b"PK\x03\x04":
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise UnknownFormat(f"{path}: zip holds {len(names)} files, expected 1")
            return z.read(names[0])
    return raw


def _iso(v: str) -> str:
    s = str(v).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.title() if fmt == "%d-%b-%Y" else s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def normalise(df: pd.DataFrame, source: str = "") -> pd.DataFrame:
    cols = [c.strip() for c in df.columns]
    df = df.set_axis(cols, axis=1)
    df = df[[c for c in cols if c and not c.startswith("Unnamed")]]
    if "TckrSymb" in df.columns:
        return df
    if "SYMBOL" not in df.columns:
        raise UnknownFormat(f"{source}: neither a UDiFF (TckrSymb) nor a legacy (SYMBOL) bhavcopy: {cols[:6]}")
    legacy_fo = "INSTRUMENT" in df.columns
    df = df.rename(columns=LEGACY_FO if legacy_fo else LEGACY_CM)
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].str.strip()
    if legacy_fo:
        df["FinInstrmTp"] = df["FinInstrmTp"].map(lambda x: LEGACY_INSTRUMENT.get(x, x))
        df["XpryDt"] = df["XpryDt"].map(_iso)
    if "TradDt" in df.columns:
        df["TradDt"] = df["TradDt"].map(_iso)
    return df


def read_udiff(path: Path) -> pd.DataFrame:
    """Any NSE CM or F&O bhavcopy (UDiFF or legacy; plain, gzip or zip) with UDiFF column names."""
    df = normalise(pd.read_csv(io.BytesIO(_bytes(path)), dtype=str, keep_default_na=False), str(path))
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


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class MarketFiles:
    """Read-only view of the history folder. Files are cached per instance; build a new instance each run."""

    def __init__(self, history: Path) -> None:
        self.h = Path(history)
        self._cm: Dict[date, pd.DataFrame] = {}
        self._fo: Dict[date, pd.DataFrame] = {}
        self._sessions: Optional[List[date]] = None

    # ------------------------------------------------------------------------------------------ files
    def cm_path(self, d: date) -> Path:
        return cm_path(self.h, d)

    def fo_path(self, d: date) -> Path:
        return fo_path(self.h, d)

    def ban_path(self, d: date) -> Path:
        return ban_path(self.h, d)

    def rel(self, p: Path) -> str:
        return Path(p).relative_to(self.h).as_posix()

    def sessions(self) -> List[date]:
        if self._sessions is None:
            root = self.h / "bhavcopy" / "raw" / "cm"
            out = []
            for p in root.glob("*/*.csv.gz") if root.exists() else []:
                try:
                    out.append(date.fromisoformat(p.name[:10]))
                except ValueError:
                    continue
            self._sessions = sorted(out)
        return self._sessions

    def cm(self, d: date) -> pd.DataFrame:
        if d not in self._cm:
            self._cm[d] = read_udiff(self.cm_path(d))
        return self._cm[d]

    def cm_eq(self, d: date) -> pd.DataFrame:
        df = self.cm(d)
        return df[df["SctySrs"] == "EQ"] if "SctySrs" in df else df.iloc[:0]

    def fo(self, d: date) -> pd.DataFrame:
        if d not in self._fo:
            self._fo[d] = read_udiff(self.fo_path(d))
        return self._fo[d]

    def ban(self, d: date) -> Optional[Set[str]]:
        """The ban list for session d, or None when absent or dated differently (fail closed)."""
        from research.data.nse_events import parse_ban_csv

        p = self.ban_path(d)
        if not p.exists():
            return None
        when, syms = parse_ban_csv(_bytes(p).decode("utf-8", errors="replace"))
        return set(syms) if when == d else None

    # ------------------------------------------------------------------------------------------ calendar
    def entry_session(self, day: date) -> Tuple[Optional[date], List[date]]:
        """(first weekday after `day` whose ban list exists, weekdays skipped on the way); (None, []) if none."""
        d, skipped = day, []
        for _ in range(MAX_ENTRY_SEARCH_DAYS):
            d += timedelta(days=1)
            if d.weekday() >= 5:
                continue
            if self.ban_path(d).exists():
                return d, skipped
            skipped.append(d)
        return None, []

    def chain(self, prev: date, day: date) -> Dict[str, Any]:
        """Is `day` the session right after `prev`? ok True / False, or None when it cannot be judged."""
        out: Dict[str, Any] = {"prev": prev.isoformat(), "day": day.isoformat(), "ok": None, "compared": 0,
                               "matched": 0}
        if not (self.cm_path(prev).exists() and self.cm_path(day).exists()):
            out["reason"] = "file missing"
            return out
        a = self.cm_eq(prev).groupby("TckrSymb")["ClsPric"].first()
        b = self.cm_eq(day).groupby("TckrSymb")["PrvsClsgPric"].first()
        j = pd.concat([a.rename("c"), b.rename("p")], axis=1, join="inner").dropna()
        j = j[(j.c > 0) & (j.p > 0)]
        out["compared"] = int(len(j))
        out["matched"] = int((np.abs(j.p / j.c - 1) <= CHAIN_TOLERANCE).sum())
        if len(j) < MIN_COMMON:
            out["reason"] = f"fewer than {MIN_COMMON} stocks in both files"
            return out
        out["ok"] = bool(out["matched"] / out["compared"] >= MIN_SHARE)
        return out

    def chain_window(self, days: Sequence[date]) -> Dict[str, Any]:
        """Every consecutive pair must chain. ok False on the first break, None if any pair cannot be judged."""
        checks = [self.chain(a, b) for a, b in zip(days[:-1], days[1:])]
        bad = [c for c in checks if c["ok"] is False]
        unknown = [c for c in checks if c["ok"] is None]
        ok: Optional[bool] = False if bad else (None if unknown else True)
        return {"ok": ok, "pairs": len(checks), "breaks": bad[:3], "unknown": unknown[:3]}
