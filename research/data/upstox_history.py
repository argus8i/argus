"""
research/data/upstox_history.py
===============================
Intraday and daily history from the Upstox V2 historical-candle API (Yashu, 25 Sep 2026: candidate intraday
source for P7), resampled to the canonical 15-minute bars and written through research/data/ingest_json.

Endpoints (verified 25 Sep 2026; no authentication needed):
- instrument master  https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
  (79,346 rows; equities keyed NSE_EQ|<ISIN>, indices NSE_INDEX|<name>)
- candles            https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}
  1minute: one calendar month per request works (Aug 2026: 7,875 bars = 21 sessions x 375); 41 days is
  refused (UDAPI1148 "Invalid date range"). Served back to 2022-01-03. day: 5+ years per request.
  Rows: [timestamp ISO +05:30 (minute START), open, high, low, close, volume, oi], newest first.

Resampling (Yashu's specification): minutes 09:15..15:29 into 25 bars starting 09:15, 09:30, ..., 15:15;
bar t covers minutes [t, t+14]; open = first minute's open, high = max, low = min, close = last minute's
close, volume = sum. Minutes outside 09:15..15:29 are dropped and counted. A bucket with no minutes has no
bar (validate.py then flags the session). After the closing auction started (3 Aug 2026) the 15:15 bar of a
stock is auction prints; ingest_json drops it (CAS_AUCTION_BAR), exactly as for every other source.

Provenance: rows carry source UPSTOX_API_V2. research/data/provenance.py decides whether strategy code may
see them; it is QA_ONLY until the Kite cross-check (P3.9) passes (see research/notes/p3_upstox_report.md).
Politeness: honest User-Agent, >= 1 s between requests, backoff on 5xx, stop after 3 consecutive
401/403/429 or transport failures (UpstoxBlocked). No credentials are used or needed.

CLI:
    python -m research.data.upstox_history xcheck --reference shared/track2_liquid/historical_candles_track2.json
    python -m research.data.upstox_history fetch --from 2022-01-01 --to 2026-09-24 [--symbols A,B]
    python -m research.data.upstox_history build [--out HISTORY_DIR]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from research.data import paths
from research.data.indices import canonical_index

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.upstox.com/v2/historical-candle"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
SOURCE = "UPSTOX_API_V2"
USER_AGENT = "track2-research/0.1 (personal non-commercial research) python-requests"
MIN_INTERVAL_S = 1.0
FIRST_MIN, LAST_MIN = 9 * 60 + 15, 15 * 60 + 29          # 09:15 .. 15:29 inclusive

# canonical index -> Upstox NSE_INDEX name (resolved against the master at run time; unresolved are reported)
INDEX_NAMES: Dict[str, str] = {
    "IDX:NIFTY50": "Nifty 50", "IDX:INDIAVIX": "India VIX", "IDX:BANKNIFTY": "Nifty Bank",
    "IDX:FINNIFTY": "Nifty Fin Service", "IDX:NIFTYAUTO": "Nifty Auto", "IDX:NIFTYIT": "Nifty IT",
    "IDX:NIFTYFMCG": "Nifty FMCG", "IDX:NIFTYMETAL": "Nifty Metal", "IDX:NIFTYPHARMA": "Nifty Pharma",
    "IDX:NIFTYPSUBANK": "Nifty PSU Bank", "IDX:NIFTYREALTY": "Nifty Realty", "IDX:NIFTYENERGY": "Nifty Energy",
    "IDX:NIFTYINFRA": "Nifty Infra", "IDX:NIFTYMEDIA": "Nifty Media", "IDX:NIFTYPVTBANK": "Nifty Pvt Bank",
    "IDX:NIFTYPSE": "Nifty PSE", "IDX:NIFTYCPSE": "Nifty CPSE", "IDX:NIFTYMIDCAP50": "Nifty Midcap 50",
}


class UpstoxBlocked(RuntimeError):
    """Repeated 401/403/429 or transport failures: stop and report."""


# ---------------------------------------------------------------------------------------------- client
class UpstoxClient:
    def __init__(self, session: Any = None, min_interval: float = MIN_INTERVAL_S, max_retries: int = 3,
                 block_after: int = 3, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 60.0,
                 cooldown_s: float = 30.0) -> None:
        if min_interval < MIN_INTERVAL_S:
            raise ValueError(f"min_interval below {MIN_INTERVAL_S}s")
        if session is None:
            import requests

            session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.s, self.min_interval, self.max_retries, self.block_after = session, min_interval, max_retries, block_after
        self.sleep, self.clock, self.timeout, self.cooldown_s = sleep, clock, timeout, cooldown_s
        self._last: Optional[float] = None
        self._bad_run = 0
        self.requests_made = 0

    def _pace(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.min_interval:
            self.sleep(self.min_interval - (now - self._last))
        self._last = self.clock()

    def get(self, url: str) -> Tuple[int, bytes]:
        code, content = 0, b""
        for attempt in range(self.max_retries + 1):
            self._pace()
            self.requests_made += 1
            try:
                r = self.s.get(url, timeout=self.timeout)
            except (OSError, IOError) as exc:                  # requests' ConnectionError/Timeout
                self._bad_run += 1
                if self._bad_run >= self.block_after:
                    raise UpstoxBlocked(f"{self._bad_run} consecutive transport failures "
                                        f"({type(exc).__name__})") from exc
                self.sleep(self.cooldown_s * self._bad_run)
                continue
            code, content = int(r.status_code), bytes(r.content)
            if code in (401, 403, 429):
                self._bad_run += 1
                if self._bad_run >= self.block_after:
                    raise UpstoxBlocked(f"{self._bad_run} consecutive refusals (last HTTP {code}: {url})")
                self.sleep(self.cooldown_s * self._bad_run)
                continue
            self._bad_run = 0
            if code >= 500 and attempt < self.max_retries:
                self.sleep(self.min_interval * 2 ** (attempt + 1))
                continue
            return code, content
        return code, content


# ---------------------------------------------------------------------------------------------- master
def load_master(path: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], str]:
    """(rows, sha256 of the gzip). Downloads to <history>/raw/upstox/NSE.json.gz unless the file exists."""
    path = path or (paths.history_dir() / "raw" / "upstox" / "NSE.json.gz")
    if not path.exists():
        import requests

        raw = requests.get(MASTER_URL, headers={"User-Agent": USER_AGENT}, timeout=120).content
        paths.ensure(path.parent)
        path.write_bytes(raw)
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw)), hashlib.sha256(raw).hexdigest()


def resolve(symbols: Iterable[str], master: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    """symbol -> instrument_key. Stocks: NSE_EQ, instrument_type EQ, trading_symbol == symbol (the key is the
    ISIN). Indices: canonical name -> INDEX_NAMES -> NSE_INDEX row with that name. Returns (found, unresolved)."""
    eq = {str(r.get("trading_symbol", "")).upper(): r["instrument_key"] for r in master
          if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}
    idx = {str(r.get("name", "")).lower(): r["instrument_key"] for r in master if r.get("segment") == "NSE_INDEX"}
    found: Dict[str, str] = {}
    missing: List[str] = []
    for s in symbols:
        canon = canonical_index(s)
        if canon:
            key = idx.get(INDEX_NAMES.get(canon, "").lower())
            if key:
                found[canon] = key
            else:
                missing.append(s)
        elif s.upper() in eq:
            found[s.upper()] = eq[s.upper()]
        else:
            missing.append(s)
    return found, missing


# ---------------------------------------------------------------------------------------------- resampling
def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:
        raise ValueError(f"timestamp without offset: {ts!r}")
    return t.astimezone(IST)


def resample_15m(candles: Iterable[Sequence[Any]]) -> Tuple[List[Dict[str, Any]], Counter]:
    """1-minute rows -> canonical 15-minute bars (see module docstring). Returns (bars sorted by start, stats)."""
    stats: Counter = Counter()
    buckets: Dict[datetime, List[Tuple[datetime, float, float, float, float, int]]] = defaultdict(list)
    by_ts: Dict[datetime, List[Tuple[float, float, float, float, int]]] = defaultdict(list)
    for row in candles:
        try:
            t = _parse(row[0])
            o, h, l, c = (float(row[k]) for k in (1, 2, 3, 4))
            v = int(row[5]) if row[5] is not None else 0
        except (IndexError, TypeError, ValueError):
            stats["MINUTE_UNPARSEABLE"] += 1
            continue
        by_ts[t].append((o, h, l, c, v))
    minutes: List[Tuple[datetime, Tuple[float, float, float, float, int]]] = []
    for t, vals in by_ts.items():
        if len(set(vals)) > 1:                   # same minute, different values: drop it (order-independent)
            stats["MINUTE_CONFLICT"] += len(vals)
            continue
        stats["MINUTE_DUPLICATE"] += len(vals) - 1
        minutes.append((t, vals[0]))
    for t, (o, h, l, c, v) in minutes:
        m = t.hour * 60 + t.minute
        if t.second or not (FIRST_MIN <= m <= LAST_MIN):
            stats["MINUTE_OUTSIDE_0915_1529"] += 1
            continue
        k = (m - FIRST_MIN) // 15
        start = datetime.combine(t.date(), dtime(9, 15), IST) + timedelta(minutes=15 * k)
        buckets[start].append((t, o, h, l, c, v))
        stats["minutes"] += 1
    bars = []
    for start in sorted(buckets):
        mins = sorted(buckets[start])
        stats[f"bar_minutes_{len(mins)}"] += 1
        bars.append({"timestamp": start.isoformat(), "open": mins[0][1], "high": max(x[2] for x in mins),
                     "low": min(x[3] for x in mins), "close": mins[-1][4], "volume": sum(x[5] for x in mins)})
    return bars, stats


def daily_rows(candles: Iterable[Sequence[Any]]) -> List[Dict[str, Any]]:
    out = {}
    for row in candles:
        t = _parse(row[0])
        out[t.date()] = {"timestamp": f"{t.date().isoformat()}T00:00:00+05:30", "open": float(row[1]),
                         "high": float(row[2]), "low": float(row[3]), "close": float(row[4]),
                         "volume": int(row[5]) if row[5] is not None else 0}
    return [out[d] for d in sorted(out)]


# ---------------------------------------------------------------------------------------------- fetch
def month_windows(start: date, end: date) -> List[Tuple[date, date]]:
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append((max(d, start), min(nxt - timedelta(days=1), end)))
        d = nxt
    return out


def candle_url(key: str, interval: str, a: date, b: date) -> str:
    return f"{API}/{key}/{interval}/{b.isoformat()}/{a.isoformat()}"


def upstox_root(root: Optional[Path] = None) -> Path:
    return (root or paths.history_dir()) / "raw" / "upstox"


def _manifest(base: Path) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    done: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    m = base / "manifest.jsonl"
    if m.exists():
        for line in m.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["symbol"], r["interval"], r["window"])] = r
    return done


def _safe(sym: str) -> str:
    return sym.replace(":", "_")


def fetch(keys: Mapping[str, str], start: date, end: date, client: Optional[UpstoxClient] = None,
          root: Optional[Path] = None, daily: bool = True, log: Callable[[str], None] = print) -> Counter:
    """Fetch 1-minute months (and one daily window) for every symbol; resumable from the manifest. A window
    already recorded with HTTP 200 is skipped. Raw bodies are stored gzipped."""
    base = paths.ensure(upstox_root(root))
    done = _manifest(base)
    client = client or UpstoxClient()
    stats: Counter = Counter()
    jobs = [(s, "1minute", a, b) for s in sorted(keys) for a, b in month_windows(start, end)]
    if daily:
        jobs += [(s, "day", start - timedelta(days=60), end) for s in sorted(keys)]   # 60d lead for DTV20/ATR
    for n, (sym, interval, a, b) in enumerate(jobs, 1):
        wkey = f"{a.isoformat()}_{b.isoformat()}"
        if done.get((sym, interval, wkey), {}).get("status") == 200:
            stats["skipped"] += 1
            continue
        url = candle_url(keys[sym], interval, a, b)
        code, body = client.get(url)
        rec: Dict[str, Any] = {"symbol": sym, "interval": interval, "window": wkey, "instrument_key": keys[sym],
                               "url": url, "request_sha256": hashlib.sha256(url.encode()).hexdigest(),
                               "status": code, "bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                               "rows": None, "file": None, "fetched_at": datetime.now(IST).isoformat(timespec="seconds")}
        if code == 200:
            try:
                rec["rows"] = len((json.loads(body).get("data") or {}).get("candles") or [])
                f = paths.ensure(base / _safe(sym)) / f"{interval}_{wkey}.json.gz"
                f.write_bytes(gzip.compress(body))
                rec["file"] = f.relative_to(base).as_posix()
            except (ValueError, AttributeError) as exc:
                rec["status"], rec["error"] = -1, str(exc)[:120]
        with open(base / "manifest.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        done[(sym, interval, wkey)] = rec
        stats[f"status_{rec['status']}"] += 1
        if n % 200 == 0:
            log(f"{n}/{len(jobs)} requests; {dict(stats)}")
    return stats


# ---------------------------------------------------------------------------------------------- build
def build_entry(sym: str, root: Optional[Path] = None, start: Optional[date] = None,
                end: Optional[date] = None) -> Tuple[Dict[str, Any], Counter]:
    """Canonical candle-JSON entry for one symbol from its stored raw files (the ingest_json input shape)."""
    base = upstox_root(root)
    done = _manifest(base)
    minutes: List[Sequence[Any]] = []
    daily: List[Sequence[Any]] = []
    fetched = []
    for (s, interval, _), rec in done.items():
        if s != sym or rec.get("status") != 200 or not rec.get("file"):
            continue
        body = json.loads(gzip.decompress((base / rec["file"]).read_bytes()))
        rows = (body.get("data") or {}).get("candles") or []
        (minutes if interval == "1minute" else daily).extend(rows)
        fetched.append(rec["fetched_at"])
    if start or end:
        minutes = [r for r in minutes
                   if (start is None or _parse(r[0]).date() >= start) and (end is None or _parse(r[0]).date() <= end)]
    bars, stats = resample_15m(minutes)
    entry = {"source": SOURCE, "source_url": API, "requested_at": max(fetched) if fetched else None,
             "bars": bars, "daily_bars": daily_rows(daily)}
    return entry, stats


def build(symbols: Sequence[str], out_root: Optional[Path] = None, root: Optional[Path] = None,
          label: str = "upstox_v2", start: Optional[date] = None, end: Optional[date] = None) -> Dict[str, Any]:
    """Resample and ingest every symbol into parquet via ingest_json (same drop rules and provenance stamps
    as every other source). Writes <out_root>/manifests/<label>.json."""
    from research.data import ingest_json

    out_root = out_root or paths.history_dir()
    results, totals = [], Counter()
    for sym in symbols:
        entry, rs = build_entry(sym, root, start, end)
        fetched_at = ingest_json._parse_fetch_time(entry["requested_at"])
        res, bars, dly = ingest_json.normalise_entry(sym, entry, fetched_at)
        files = ingest_json.write_symbol(bars, dly, res.symbol, out_root)
        results.append({**res.__dict__, "files": files, "resample": dict(rs)})
        totals.update(res.dropped)
        totals.update({f"resample_{k}": v for k, v in rs.items() if not k.startswith("bar_minutes")})
    manifest = {"label": label, "source": SOURCE, "built_at": datetime.now(IST).isoformat(timespec="seconds"),
                "range": [start.isoformat() if start else None, end.isoformat() if end else None],
                "totals": dict(totals), "symbols": results}
    mdir = paths.ensure(out_root / "manifests")
    (mdir / f"{label}.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------------------------- cross-check
def xcheck(reference: Path, out_root: Path, root: Optional[Path] = None,
           client: Optional[UpstoxClient] = None) -> Dict[str, Any]:
    """P3.9 against the Kite reference: fetch the reference symbols over the reference sessions, build them
    into a separate QA root, and compare with cross_source.compare (both stores in QA mode)."""
    from research.backtest.bars import CandleStore
    from research.data.cross_source import compare
    from research.data.store_parquet import ParquetCandleStore

    ref = CandleStore.from_historical_json(reference, mode="QA")
    master, master_sha = load_master()
    keys, missing = resolve(ref.symbols, master)
    days = sorted({d for s in ref.symbols for d in ref.sessions(s)})
    fetch(keys, days[0], days[-1], client=client, root=root, daily=True)
    build(sorted(keys), out_root=out_root, root=root, label="upstox_xcheck", start=days[0], end=days[-1])
    res = compare(ParquetCandleStore(out_root, mode="QA"), ref)
    res.update({"reference": str(reference), "unresolved": missing, "instrument_keys": keys,
                "master_sha256": master_sha, "sessions": [days[0].isoformat(), days[-1].isoformat()]})
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Upstox V2 historical candles (see module docstring)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("xcheck")
    x.add_argument("--reference", required=True)
    x.add_argument("--out-root", default=None)
    x.add_argument("--report", default=None)
    f = sub.add_parser("fetch")
    f.add_argument("--from", dest="start", required=True)
    f.add_argument("--to", dest="end", required=True)
    f.add_argument("--symbols", default=None, help="comma-separated; default: F&O list + indices")
    f.add_argument("--interval", type=float, default=MIN_INTERVAL_S)
    b = sub.add_parser("build")
    b.add_argument("--out", default=None)
    b.add_argument("--symbols", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "xcheck":
        out_root = Path(args.out_root) if args.out_root else paths.outputs_dir() / "p3" / "upstox_xcheck"
        try:
            res = xcheck(Path(args.reference), out_root)
        except UpstoxBlocked as exc:
            print(f"STOPPED: {exc}")
            return 2
        brief = {k: v for k, v in res.items() if k != "mismatches"}
        brief["mismatch_examples"] = res["mismatches"][:20]
        print(json.dumps(brief, indent=1, default=str))
        if args.report:
            Path(args.report).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        return 0 if res["passed"] else 1

    master, _ = load_master()
    if args.symbols:
        wanted = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        from research.data.universe_build import load_fno_symbols

        wanted = load_fno_symbols(paths.shared_input("fno_lot_sizes.json")) + list(INDEX_NAMES)
    keys, missing = resolve(wanted, master)
    if missing:
        print(f"unresolved ({len(missing)}): {missing}")
    if args.cmd == "fetch":
        try:
            stats = fetch(keys, date.fromisoformat(args.start), date.fromisoformat(args.end),
                          client=UpstoxClient(min_interval=args.interval))
        except UpstoxBlocked as exc:
            print(f"STOPPED: {exc}")
            return 2
        print(json.dumps(dict(stats), indent=1))
    else:
        m = build(sorted(keys), out_root=Path(args.out) if args.out else None)
        print(json.dumps(m["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
