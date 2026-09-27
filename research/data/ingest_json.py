"""
research/data/ingest_json.py
============================
Ingest candle JSON files in the repo's canonical CandleStore format into the parquet history
(plan P3.3 storage layout, P3.7 store).

Accepted inputs (all share the schema of CandleStore.from_historical_json):
- shared/track2_liquid/historical_candles_fno_210.json   (Antigravity, Yahoo chart API, commit a8264e9)
- shared/track2_liquid/historical_indices.json           (Antigravity, Yahoo chart API, commit 94db5ab)
- the Kite 32-session file (the P2 regression fixture)
- later, Dhan downloads written in the same schema

What ingest does, and only this (it normalises; it does not repair):
1. Parses bar starts to IST and assigns the 15-minute slot. A bar whose start is not on the 15-minute
   grid from 09:15 is dropped and counted (MISALIGNED). Yahoo returns such bars for a session that is
   still trading when the file is fetched.
2. Drops every bar of a session that had not closed when the file was fetched (INCOMPLETE_SESSION),
   using the file's fetch time. The matching daily bar is dropped too.
3. Drops the post-CAS 15:15 bar of stocks (CAS_AUCTION_BAR); see session_shape.
4. Canonicalises index names. For Yahoo series the canonical name comes from the source ticker, because
   the fetcher stored ^NSEMDCP50 (NIFTY MIDCAP 50) under the name MIDCPNIFTY (a different index).
5. Stamps every row with its source class (research/data/provenance.py). Only DHAN_API_V2 and SYNTHETIC
   rows can reach strategy code; Yahoo, Kite, the HF/Upstox mirror and unlabelled data are QA_ONLY.
6. Writes one parquet file per symbol under bars_15m/ and daily/, plus a manifest with the input's
   SHA-256, row counts and every drop reason.

Prices are stored exactly as received. Nothing is re-rounded: the Yahoo fetcher already snapped prices
onto the tick grid, so off-grid checks cannot detect Yahoo errors, and that caveat goes in the manifest.

CLI:
    python -m research.data.ingest_json --input <file.json> [--input <file2.json> ...] [--label NAME]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from research.data import paths, provenance
from research.data.indices import YAHOO_TICKER_TO_CANONICAL, canonical_index
from research.data.session_shape import is_cas_auction_bar, slot_of

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_CLOSE = time(15, 30)

YAHOO_CAVEATS = [
    "Source is Yahoo Finance's public chart API (query1.finance.yahoo.com/v8/finance/chart), not an "
    "exchange or broker feed. It serves at most 60 days of 15-minute bars.",
    "The fetcher snapped 15-minute prices onto the NSE tick grid, so an off-grid check cannot reveal "
    "Yahoo price errors.",
    "The fetcher wrote missing volume as 0. A zero-volume bar in a liquid F&O stock is treated as "
    "missing data by validate.py.",
    "Daily bars are split-adjusted by Yahoo (prices divided, volume multiplied). Turnover (close x "
    "volume) is unaffected; raw price levels before a split are not the traded prices.",
]


def safe_name(symbol: str) -> str:
    """File-system safe, reversible enough for humans; the true symbol is stored inside the file."""
    return re.sub(r"[^A-Za-z0-9_.-]", lambda m: f"_{ord(m.group(0)):02X}_", symbol)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value!r}")
    return ts.astimezone(IST)


def _parse_fetch_time(value: Any) -> Optional[datetime]:
    """Fetch times in these files are naive local (IST) strings such as '2026-09-25 13:50:31'."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts.astimezone(IST) if ts.tzinfo else ts.replace(tzinfo=IST)


def session_complete(day: date, fetched_at: Optional[datetime]) -> bool:
    """A session is usable only if the file was fetched after that session closed."""
    if fetched_at is None:
        return True          # unknown fetch time: keep, and let validate.py judge the bar counts
    fetch_day = fetched_at.date()
    if day < fetch_day:
        return True
    if day > fetch_day:
        return False
    return fetched_at.time() >= SESSION_CLOSE


def canonical_symbol(name: str, entry: Mapping[str, Any]) -> Tuple[str, str]:
    """(canonical symbol, kind). Kind is INDEX or TRADABLE."""
    source = str(entry.get("source") or "")
    if source.startswith("YahooFinance_"):
        ticker = source[len("YahooFinance_"):]
        if ticker in YAHOO_TICKER_TO_CANONICAL:
            return YAHOO_TICKER_TO_CANONICAL[ticker], "INDEX"
    canon = canonical_index(name)
    if canon:
        return canon, "INDEX"
    return name.strip().upper(), "TRADABLE"


@dataclass
class SymbolResult:
    symbol: str
    source_name: str
    kind: str
    source: str
    source_class: str = provenance.UNKNOWN
    bars_in: int = 0
    bars_out: int = 0
    daily_in: int = 0
    daily_out: int = 0
    sessions: int = 0
    first_session: Optional[str] = None
    last_session: Optional[str] = None
    dropped: Dict[str, int] = field(default_factory=dict)


def normalise_entry(name: str, entry: Mapping[str, Any], fetched_at: Optional[datetime]
                    ) -> Tuple[SymbolResult, List[Dict[str, Any]], List[Dict[str, Any]]]:
    symbol, kind = canonical_symbol(name, entry)
    source_class = provenance.classify(entry)
    res = SymbolResult(symbol=symbol, source_name=name, kind=kind, source=str(entry.get("source") or ""),
                       source_class=source_class)
    dropped: Counter = Counter()
    rows: Dict[datetime, Dict[str, Any]] = {}
    fetch_row = _parse_fetch_time(entry.get("requested_at")) or fetched_at
    for rec in entry.get("bars") or []:
        res.bars_in += 1
        try:
            start = _parse_ts(rec["timestamp"])
            o, h, l, c = (float(rec[k]) for k in ("open", "high", "low", "close"))
            v = rec.get("volume")
            v = int(v) if v is not None else None
        except (KeyError, TypeError, ValueError):
            dropped["UNPARSEABLE"] += 1
            continue
        day = start.date()
        slot = slot_of(start.time())
        if slot is None:
            dropped["MISALIGNED"] += 1
            continue
        if not session_complete(day, fetch_row):
            dropped["INCOMPLETE_SESSION"] += 1
            continue
        if is_cas_auction_bar(kind, day, start.time()):
            dropped["CAS_AUCTION_BAR"] += 1
            continue
        if start in rows:
            dropped["DUPLICATE"] += 1
            continue
        rows[start] = {"symbol": symbol, "session": day.isoformat(), "slot": slot,
                       "start_epoch": int(start.timestamp()), "open": o, "high": h, "low": l, "close": c,
                       "volume": -1 if v is None else v, "source": source_class}
    bars = [rows[k] for k in sorted(rows)]
    daily_rows: Dict[str, Dict[str, Any]] = {}
    for rec in entry.get("daily_bars") or []:
        res.daily_in += 1
        try:
            day = _parse_ts(rec["timestamp"]).date() if "T" in str(rec["timestamp"]) else \
                date.fromisoformat(str(rec["timestamp"])[:10])
            o, h, l, c = (float(rec[k]) for k in ("open", "high", "low", "close"))
            v = rec.get("volume")
            v = int(v) if v is not None else -1
        except (KeyError, TypeError, ValueError):
            dropped["DAILY_UNPARSEABLE"] += 1
            continue
        if not session_complete(day, fetch_row):
            dropped["DAILY_INCOMPLETE_SESSION"] += 1
            continue
        if day.isoformat() in daily_rows:
            dropped["DAILY_DUPLICATE"] += 1
            continue
        daily_rows[day.isoformat()] = {"symbol": symbol, "day": day.isoformat(), "open": o, "high": h,
                                       "low": l, "close": c, "volume": v, "source": source_class}
    daily = [daily_rows[k] for k in sorted(daily_rows)]
    res.bars_out, res.daily_out = len(bars), len(daily)
    sessions = sorted({b["session"] for b in bars})
    res.sessions = len(sessions)
    res.first_session = sessions[0] if sessions else None
    res.last_session = sessions[-1] if sessions else None
    res.dropped = dict(dropped)
    return res, bars, daily


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_symbol(bars: List[Dict[str, Any]], daily: List[Dict[str, Any]], symbol: str,
                 out_root: Path) -> Dict[str, str]:
    import pandas as pd       # pandas/pyarrow are allowed in research/data (plan section 4)

    written = {}
    fname = safe_name(symbol) + ".parquet"
    if bars:
        p = paths.ensure(out_root / "bars_15m") / fname
        pd.DataFrame(bars).to_parquet(p, index=False)
        written["bars_15m"] = str(p.relative_to(out_root))
    if daily:
        p = paths.ensure(out_root / "daily") / fname
        pd.DataFrame(daily).to_parquet(p, index=False)
        written["daily"] = str(p.relative_to(out_root))
    return written


def ingest_payload(payload: Mapping[str, Any], *, input_name: str, input_sha256: str, label: str,
                   out_root: Optional[Path] = None) -> Dict[str, Any]:
    out_root = out_root or paths.history_dir()
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        raise ValueError(f"{input_name}: no 'symbols' mapping")
    fetched_at = _parse_fetch_time(payload.get("local_write_time"))
    results = []
    totals: Counter = Counter()
    yahoo = False
    for name in sorted(symbols):
        entry = symbols[name]
        res, bars, daily = normalise_entry(name, entry, fetched_at)
        yahoo |= res.source.startswith("Yahoo")
        files = write_symbol(bars, daily, res.symbol, out_root)
        results.append({**res.__dict__, "files": files})
        totals.update(res.dropped)
        totals["bars_in"] += res.bars_in
        totals["bars_out"] += res.bars_out
    manifest = {
        "label": label,
        "input": input_name,
        "input_sha256": input_sha256,
        "ingested_at": datetime.now(IST).isoformat(timespec="seconds"),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "declared_range": [payload.get("start_date"), payload.get("end_date")],
        "interval": payload.get("interval"),
        "totals": dict(totals),
        "caveats": YAHOO_CAVEATS if yahoo else [],
        "source_classes": dict(Counter(r["source_class"] for r in results)),
        "qa_only_symbols": sorted(r["symbol"] for r in results if not provenance.strategy_eligible(r["source_class"])),
        "symbols": results,
    }
    mdir = paths.ensure(out_root / "manifests")
    (mdir / f"{label}.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def ingest_file(path: Path, label: Optional[str] = None, out_root: Optional[Path] = None) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ingest_payload(payload, input_name=Path(path).name, input_sha256=sha256_file(Path(path)),
                          label=label or Path(path).stem, out_root=out_root)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", action="append", required=True, help="candle JSON; repeat for several")
    ap.add_argument("--out", default=None, help="history root (default: TRACK2_HISTORY_DIR)")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else None
    for inp in args.input:
        m = ingest_file(Path(inp), out_root=out)
        t = m["totals"]
        print(f"{m['input']}: {len(m['symbols'])} symbols, bars {t.get('bars_in', 0)} -> {t.get('bars_out', 0)}; "
              f"dropped {({k: v for k, v in t.items() if k not in ('bars_in', 'bars_out')})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
