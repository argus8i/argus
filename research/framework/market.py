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
from collections import OrderedDict
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
# NSE ASM/GSM surveillance lists (raw NSE API JSON, fetched each trading evening by the data pipeline) are required for
# plan days from this date on; history before it has none, so a plan there states NO_SURVEILLANCE_LIST_ERA.
SURVEILLANCE_FROM = date(2026, 9, 28)


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


def unpack(raw: bytes, source: str = "") -> bytes:
    """The file's content: gunzipped, or the single member of a zip, or the bytes themselves."""
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:4] == b"PK\x03\x04":
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise UnknownFormat(f"{source}: zip holds {len(names)} files, expected 1")
            return z.read(names[0])
    return raw


def _bytes(path: Path) -> bytes:
    return unpack(Path(path).read_bytes(), str(path))


def _iso(v: str) -> str:
    s = str(v).strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.title() if fmt == "%d-%b-%Y" else s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def normalise(df: pd.DataFrame, source: str = "") -> pd.DataFrame:
    cols = [c.strip().lstrip("﻿") for c in df.columns]
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

    def remap(col: str, fn: Any) -> None:                # each distinct value once (47,000 rows, a few dates)
        if col in df.columns:
            df[col] = df[col].map({u: fn(u) for u in df[col].unique()})

    if legacy_fo:
        remap("FinInstrmTp", lambda x: LEGACY_INSTRUMENT.get(x, x))
        remap("XpryDt", _iso)
    if "TradDt" in df.columns:
        remap("TradDt", _iso)
    return df


def _usecols(content: bytes, columns: Sequence[str], source: str) -> Any:
    """The source-file column names to read for the wanted UDiFF `columns` (legacy files use other names). Refuses a
    header that is not a bhavcopy header, so a partial read can never accept a non-bhavcopy file."""
    header = [c.strip() for c in content.split(b"\n", 1)[0].decode("utf-8-sig", "replace").strip("\r").split(",")]
    if "TckrSymb" in header:
        names = set(columns) | {"TckrSymb"}
    elif "SYMBOL" in header:
        legacy = LEGACY_FO if "INSTRUMENT" in header else LEGACY_CM
        rev = {v: k for k, v in legacy.items()}
        names = {rev.get(c, c) for c in columns} | {"SYMBOL"} | ({"INSTRUMENT"} if "INSTRUMENT" in header else set())
    else:
        raise UnknownFormat(f"{source}: neither a UDiFF (TckrSymb) nor a legacy (SYMBOL) bhavcopy: {header[:6]}")
    return lambda c: c.strip().lstrip("﻿") in names


def parse_bhavcopy(raw: bytes, source: str = "", columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """The bytes of any NSE CM or F&O bhavcopy file (UDiFF or legacy; plain, gzip or zip), UDiFF column names.
    columns: read only these UDiFF columns (plus the symbol), for speed."""
    content = unpack(raw, source)
    use = _usecols(content, columns, source) if columns is not None else None
    df = normalise(pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, usecols=use), source)
    for c in NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def read_udiff(path: Path) -> pd.DataFrame:
    return parse_bhavcopy(Path(path).read_bytes(), str(path))


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

    CACHE_FILES = 48          # per kind; a long backtest would otherwise hold every F&O file (8 MB each) in memory
    ban_lists = True          # F&O ban lists exist for this source; a missing one blocks (fail closed)

    def __init__(self, history: Path) -> None:
        self.h = Path(history)
        self._cm: "OrderedDict[date, pd.DataFrame]" = OrderedDict()
        self._fo: "OrderedDict[date, pd.DataFrame]" = OrderedDict()
        self._sessions: Optional[List[date]] = None

    def _cached(self, store: "OrderedDict[date, pd.DataFrame]", d: date, path: Path) -> pd.DataFrame:
        if d in store:
            store.move_to_end(d)
            return store[d]
        df = self._read(path)
        store[d] = df
        if len(store) > self.CACHE_FILES:
            store.popitem(last=False)
        return df

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

    def _read(self, path: Path) -> pd.DataFrame:
        return read_udiff(path)

    def cm(self, d: date) -> pd.DataFrame:
        return self._cached(self._cm, d, self.cm_path(d))

    def cm_eq(self, d: date) -> pd.DataFrame:
        df = self.cm(d)
        return df[df["SctySrs"] == "EQ"] if "SctySrs" in df else df.iloc[:0]

    def fo(self, d: date) -> pd.DataFrame:
        return self._cached(self._fo, d, self.fo_path(d))

    def ban(self, d: date) -> Optional[Set[str]]:
        """The ban list for session d, or None when absent or dated differently (fail closed)."""
        from research.data.nse_events import parse_ban_csv

        p = self.ban_path(d)
        if not p.exists():
            return None
        when, syms = parse_ban_csv(_bytes(p).decode("utf-8", errors="replace"))
        return set(syms) if when == d else None

    def surveillance_path(self, d: date, kind: str) -> Path:
        return self.h / "raw" / "nse" / "surveillance" / f"{d.isoformat()}_{kind}.json"

    def surveillance_required(self, d: date) -> bool:
        return d >= SURVEILLANCE_FROM

    def surveillance(self, d: date) -> Optional[Set[str]]:
        """Symbols on NSE's ASM (long- and short-term) or GSM list as fetched on session d, or None when either file
        is missing or not the expected shape (fail closed: never read as 'nobody is under surveillance')."""
        import json

        try:
            asm = json.loads(self.surveillance_path(d, "asm").read_text(encoding="utf-8"))
            gsm = json.loads(self.surveillance_path(d, "gsm").read_text(encoding="utf-8"))
            rows = list(asm["longterm"]["data"]) + list(asm["shortterm"]["data"]) + list(gsm)
            syms = {str(r["symbol"]).strip() for r in rows}
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return syms if all(syms) else None

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
