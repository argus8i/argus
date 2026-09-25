"""
research/data/nse_events.py
===========================
NSE corporate events and F&O ban lists (plan P3.4), fetched politely and stored point-in-time.

Sources (Yashu approved NSE website APIs at 1 request per 2 s, section 8 decision 7):
- announcements   GET https://www.nseindia.com/api/corporate-announcements?index=equities&from_date&to_date
- board meetings  GET https://www.nseindia.com/api/corporate-board-meetings?...   (filters by MEETING date)
- corp. actions   GET https://www.nseindia.com/api/corporates-corporateActions?...  (filters by EX date)
- F&O ban lists   GET https://nsearchives.nseindia.com/archives/fo/sec_ban/fo_secban_DDMMYYYY.csv (public archive)

Point-in-time keys, verified on recorded responses of 25 Sep 2026 (fixtures in research/tests/fixtures/nse_*):
- announcements: `exchdisstime`, the exchange dissemination time (IST). `an_dt` (the filing time, a few
  seconds earlier) is used only when `exchdisstime` is missing, and the row says so (ts_field).
- board meetings: `bm_timestamp`, the intimation time; `bm_date` is the meeting date. Every board meeting
  counts (the prereg blocks results and board meetings alike).
- corporate actions: `exDate`. The API returned caBroadcastDate "None", i.e. NO announcement timestamp.
  ASSUMPTION (labelled `announced_basis=ASSUMED_BEFORE_EX_DATE`): an ex-date is public before the ex-date's
  session opens. SEBI LODR requires advance intimation of record dates, and the prereg only asks whether
  the ex-date is TODAY, so the assumption can only matter for a same-morning announcement.
- ban lists: the archive file for trade date d lists the securities banned on d (published the previous
  evening). A missing file (404) on a weekday is recorded; it is a holiday or a gap, never "no ban".

Measured on 25 Sep: no silent row cap (announcements 23 Sep 747 + 24 Sep 944 = 1,691 for the two-day
window); history is served back to Oct 2021.

Politeness and safety: one requests.Session with an honest User-Agent; at most one request every 2 s
(token interval, not bursts); backoff on 429/5xx; the first request warms up the NSE home page, which sets
NSE's own cookies. No cookie or token is ever read from a browser profile. After `block_after` consecutive
401/403 responses the client raises NseBlocked: the run STOPS and is reported (plan P3.4); RESID_REV then
runs only as RESID_REV_NF.

Storage (plan rule 1.2.6: never committed): <history>/raw/nse/<kind>/<window>.{json,csv}.gz, a resume
manifest <history>/raw/nse/manifest.jsonl (request hash over URL + params only), and parsed tables
<history>/events/<kind>.parquet plus coverage.json.

CLI:
    python -m research.data.nse_events fetch --from 2021-10-01 --to 2026-09-24 [--kinds announcements,...]
    python -m research.data.nse_events build
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from research.data import paths

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://www.nseindia.com"
ARCHIVE = "https://nsearchives.nseindia.com"
USER_AGENT = "track2-research/0.1 (personal non-commercial research) python-requests"
MIN_INTERVAL_S = 2.0                         # decision 7: at most 1 request per 2 s

API_KINDS: Dict[str, Tuple[str, int]] = {   # kind -> (path, window days)
    "announcements": ("/api/corporate-announcements", 1),
    "board_meetings": ("/api/corporate-board-meetings", 7),
    "corporate_actions": ("/api/corporates-corporateActions", 7),
}
BAN = "fo_ban"
ALL_KINDS = tuple(API_KINDS) + (BAN,)


class NseBlocked(RuntimeError):
    """Repeated 401/403: stop and report (plan P3.4)."""


# ---------------------------------------------------------------------------------------------- client
class NseClient:
    def __init__(self, session: Any = None, min_interval: float = MIN_INTERVAL_S, max_retries: int = 3,
                 block_after: int = 3, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 60.0) -> None:
        if min_interval < MIN_INTERVAL_S:
            raise ValueError(f"min_interval below the approved {MIN_INTERVAL_S}s")
        if session is None:
            import requests

            session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"})
        self.s, self.min_interval, self.max_retries, self.block_after = session, min_interval, max_retries, block_after
        self.sleep, self.clock, self.timeout = sleep, clock, timeout
        self._last: Optional[float] = None
        self._blocked_run = 0
        self.warmed = False
        self.requests_made = 0

    def _pace(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.min_interval:
            self.sleep(self.min_interval - (now - self._last))
        self._last = self.clock()

    def _raw_get(self, url: str, params: Optional[Mapping[str, str]]) -> Any:
        self._pace()
        self.requests_made += 1
        return self.s.get(url, params=params, timeout=self.timeout)

    def warm_up(self) -> None:
        if not self.warmed:
            self._raw_get(BASE + "/", None)
            self.warmed = True

    def get(self, url: str, params: Optional[Mapping[str, str]] = None) -> Tuple[int, bytes]:
        if url.startswith(BASE):
            self.warm_up()
        backoff = self.min_interval
        code, content = 0, b""
        for attempt in range(self.max_retries + 1):
            r = self._raw_get(url, params)
            code, content = int(r.status_code), bytes(r.content)
            if code in (401, 403):
                self._blocked_run += 1
                if self._blocked_run >= self.block_after:
                    raise NseBlocked(f"{self._blocked_run} consecutive {code} responses (last: {url})")
                self.warmed = False
                self.warm_up()                       # NSE's own cookies may have expired; one refresh
                continue
            self._blocked_run = 0
            if (code == 429 or code >= 500) and attempt < self.max_retries:
                backoff *= 2
                self.sleep(backoff)
                continue
            return code, content
        return code, content


# ---------------------------------------------------------------------------------------------- parsers
def _nse_ts(value: Any) -> Optional[datetime]:
    """'24-Sep-2026 23:58:30' (IST) -> aware datetime; None if missing or unparseable."""
    s = str(value or "").strip()
    if not s or s in ("-", "None", "null"):
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _nse_date(value: Any) -> Optional[date]:
    s = str(value or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _rows(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise ValueError("unexpected NSE payload shape")


def parse_announcements(payload: Any) -> Tuple[List[Dict[str, Any]], Counter]:
    out, issues = [], Counter()
    for r in _rows(payload):
        sym = str(r.get("symbol") or "").strip().upper()
        ts, field = _nse_ts(r.get("exchdisstime")), "exchdisstime"
        if ts is None:
            ts, field = _nse_ts(r.get("an_dt")), "an_dt"
        if not sym or ts is None:
            issues["ANNOUNCEMENT_UNPARSEABLE"] += 1
            continue
        if field == "an_dt":
            issues["DISSEMINATION_TIME_MISSING_USED_AN_DT"] += 1
        out.append({"symbol": sym, "disseminated_at": ts.isoformat(), "ts_field": field,
                    "seq_id": str(r.get("seq_id") or ""), "desc": str(r.get("desc") or "")[:120]})
    return out, issues


def parse_board_meetings(payload: Any) -> Tuple[List[Dict[str, Any]], Counter]:
    out, issues = [], Counter()
    for r in _rows(payload):
        sym = str(r.get("bm_symbol") or r.get("symbol") or "").strip().upper()
        meeting, intimated = _nse_date(r.get("bm_date")), _nse_ts(r.get("bm_timestamp"))
        if not sym or meeting is None or intimated is None:
            issues["BOARD_MEETING_UNPARSEABLE"] += 1
            continue
        out.append({"symbol": sym, "meeting_date": meeting.isoformat(), "intimated_at": intimated.isoformat(),
                    "purpose": str(r.get("bm_purpose") or "")[:120]})
    return out, issues


def parse_corporate_actions(payload: Any) -> Tuple[List[Dict[str, Any]], Counter]:
    out, issues = [], Counter()
    for r in _rows(payload):
        sym = str(r.get("symbol") or "").strip().upper()
        ex = _nse_date(r.get("exDate"))
        if not sym or ex is None:
            issues["CORPORATE_ACTION_UNPARSEABLE"] += 1
            continue
        ann = _nse_ts(r.get("caBroadcastDate"))
        basis = "BROADCAST" if ann is not None else "ASSUMED_BEFORE_EX_DATE"
        if ann is None:
            # ASSUMPTION (module docstring): public before the ex-date session opens
            ann = datetime.combine(ex, dtime(0, 0), IST)
            issues["BROADCAST_DATE_MISSING_ASSUMED"] += 1
        out.append({"symbol": sym, "series": str(r.get("series") or ""), "ex_date": ex.isoformat(),
                    "announced_at": ann.isoformat(), "announced_basis": basis,
                    "subject": str(r.get("subject") or "")[:120]})
    return out, issues


def parse_ban_csv(text: str) -> Tuple[Optional[date], List[str]]:
    """'Securities in Ban For Trade Date 24-SEP-2026:' then 'n,SYMBOL' lines."""
    trade_date, syms = None, []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("securities in ban"):
            tail = line.rstrip(":").split()[-1]
            trade_date = _nse_date(tail.title())
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            syms.append(parts[1].upper())
    return trade_date, syms


PARSERS = {"announcements": parse_announcements, "board_meetings": parse_board_meetings,
           "corporate_actions": parse_corporate_actions}


# ---------------------------------------------------------------------------------------------- windows
def windows(kind: str, start: date, end: date) -> List[Tuple[date, date]]:
    """Inclusive request windows. Ban lists: one per weekday."""
    out: List[Tuple[date, date]] = []
    if kind == BAN:
        d = start
        while d <= end:
            if d.weekday() < 5:
                out.append((d, d))
            d += timedelta(days=1)
        return out
    step = API_KINDS[kind][1]
    d = start
    while d <= end:
        e = min(end, d + timedelta(days=step - 1))
        out.append((d, e))
        d = e + timedelta(days=1)
    return out


def request_for(kind: str, a: date, b: date) -> Tuple[str, Optional[Dict[str, str]]]:
    if kind == BAN:
        return f"{ARCHIVE}/archives/fo/sec_ban/fo_secban_{a.strftime('%d%m%Y')}.csv", None
    path = API_KINDS[kind][0]
    return BASE + path, {"index": "equities", "from_date": a.strftime("%d-%m-%Y"), "to_date": b.strftime("%d-%m-%Y")}


def request_hash(url: str, params: Optional[Mapping[str, str]]) -> str:
    """Plan rule 1.2.5: hash only the URL and the request parameters, never headers or cookies."""
    body = json.dumps({"url": url, "params": dict(sorted((params or {}).items()))}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- fetch
def nse_root(root: Optional[Path] = None) -> Path:
    return (root or paths.history_dir()) / "raw" / "nse"


def _load_manifest(mpath: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    done: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if mpath.exists():
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done[(rec["kind"], rec["window"])] = rec
    return done


def fetch(kinds: Sequence[str], start: date, end: date, client: Optional[NseClient] = None,
          root: Optional[Path] = None, max_requests: Optional[int] = None,
          log: Callable[[str], None] = print) -> Dict[str, Counter]:
    """Fetch every missing window. Resumable: a window already recorded with status 200 (or 404 for ban
    files) is skipped. NseBlocked propagates after everything fetched so far has been recorded."""
    base = paths.ensure(nse_root(root))
    mpath = base / "manifest.jsonl"
    done = _load_manifest(mpath)
    client = client or NseClient()
    stats: Dict[str, Counter] = defaultdict(Counter)
    for kind in kinds:
        if kind not in ALL_KINDS:
            raise ValueError(f"unknown kind {kind}")
        for a, b in windows(kind, start, end):
            wkey = f"{a.isoformat()}_{b.isoformat()}"
            prev = done.get((kind, wkey))
            if prev and (prev["status"] == 200 or (kind == BAN and prev["status"] == 404)):
                stats[kind]["skipped"] += 1
                continue
            if max_requests is not None and client.requests_made >= max_requests:
                log(f"max_requests {max_requests} reached; stopping (resume later)")
                return stats
            url, params = request_for(kind, a, b)
            code, body = client.get(url, params)
            rec: Dict[str, Any] = {"kind": kind, "window": wkey, "url": url, "params": params,
                                   "request_sha256": request_hash(url, params), "status": code, "bytes": len(body),
                                   "body_sha256": hashlib.sha256(body).hexdigest(), "rows": None, "file": None,
                                   "fetched_at": datetime.now(IST).isoformat(timespec="seconds")}
            if code == 200:
                try:
                    if kind == BAN:
                        _, syms = parse_ban_csv(body.decode("utf-8", "replace"))
                        rec["rows"] = len(syms)
                    else:
                        rec["rows"] = len(_rows(json.loads(body)))
                    ext = "csv" if kind == BAN else "json"
                    f = paths.ensure(base / kind) / f"{wkey}.{ext}.gz"
                    f.write_bytes(gzip.compress(body))
                    rec["file"] = str(f.relative_to(base).as_posix())
                except (ValueError, UnicodeDecodeError) as exc:
                    rec["status"] = -1                       # 200 with an unparseable body: retried next run
                    rec["error"] = str(exc)[:120]
            with open(mpath, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            done[(kind, wkey)] = rec
            stats[kind][f"status_{rec['status']}"] += 1
    return stats


# ---------------------------------------------------------------------------------------------- build
def build(root: Optional[Path] = None, out: Optional[Path] = None) -> Dict[str, Any]:
    """Parse every stored raw file into <history>/events/<kind>.parquet and write coverage.json."""
    import pandas as pd

    base = nse_root(root)
    out = paths.ensure(out or (root or paths.history_dir()) / "events")
    done = _load_manifest(base / "manifest.jsonl")
    issues: Counter = Counter()
    tables: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for (kind, wkey), rec in sorted(done.items()):
        if rec["status"] != 200 or not rec.get("file"):
            continue
        body = gzip.decompress((base / rec["file"]).read_bytes())
        if kind == BAN:
            tdate, syms = parse_ban_csv(body.decode("utf-8", "replace"))
            wdate = date.fromisoformat(wkey[:10])
            if tdate is not None and tdate != wdate:
                issues["BAN_FILE_DATE_MISMATCH"] += 1
            tables[BAN] += [{"trade_date": wdate.isoformat(), "symbol": s} for s in syms]
            tables["fo_ban_days"].append({"trade_date": wdate.isoformat()})
        else:
            rows, iss = PARSERS[kind](json.loads(body))
            issues.update(iss)
            tables[kind] += rows
    for kind, rows in tables.items():
        pd.DataFrame(rows).drop_duplicates().to_parquet(out / f"{kind}.parquet", index=False)
    cov = coverage(done)
    cov["parse_issues"] = dict(issues)
    cov["rows"] = {k: len(v) for k, v in tables.items()}
    (out / "coverage.json").write_text(json.dumps(cov, indent=1), encoding="utf-8")
    return cov


def coverage(done: Mapping[Tuple[str, str], Mapping[str, Any]]) -> Dict[str, Any]:
    """Per kind: covered calendar days (status 200; ban: 200 or 404 weekdays), first/last, gaps inside
    [first, last] (ban: weekdays only), failed windows, and a by-month count of covered days."""
    res: Dict[str, Any] = {}
    for kind in ALL_KINDS:
        days: Set[date] = set()
        failed: List[str] = []
        for (k, wkey), rec in done.items():
            if k != kind:
                continue
            a, b = (date.fromisoformat(x) for x in wkey.split("_"))
            ok = rec["status"] == 200 or (kind == BAN and rec["status"] == 404)
            d = a
            while d <= b:
                if ok:
                    days.add(d)
                d += timedelta(days=1)
            if not ok:
                failed.append(wkey)
        if not days:
            res[kind] = {"days": 0, "n_gaps": 0, "failed_windows": sorted(failed)}
            continue
        first, last = min(days), max(days)
        gaps, d = [], first
        while d <= last:
            if d not in days and not (kind == BAN and d.weekday() >= 5):
                gaps.append(d.isoformat())
            d += timedelta(days=1)
        by_month = Counter(x.strftime("%Y-%m") for x in days)
        res[kind] = {"days": len(days), "first": first.isoformat(), "last": last.isoformat(), "gaps": gaps[:200],
                     "n_gaps": len(gaps), "failed_windows": sorted(failed), "by_month": dict(sorted(by_month.items()))}
    return res


# ---------------------------------------------------------------------------------------------- loaders
def load_table_events(root: Optional[Path] = None):
    """TableEvents over the built parquet tables. `covered` is the span where announcements, board
    meetings and corporate actions are all present; any gap in any of them makes the whole table
    unknown (covered=None -> every answer None -> RESID_REV blocks; fail closed)."""
    import pandas as pd

    from research.features.events import TableEvents

    ev_dir = (root or paths.history_dir()) / "events"
    cov = json.loads((ev_dir / "coverage.json").read_text(encoding="utf-8"))
    spans = []
    for kind in API_KINDS:
        c = cov.get(kind) or {}
        if not c.get("days") or c.get("n_gaps"):
            return TableEvents(covered=None)
        spans.append((date.fromisoformat(c["first"]), date.fromisoformat(c["last"])))
    lo, hi = max(s[0] for s in spans), min(s[1] for s in spans)
    if lo > hi:
        return TableEvents(covered=None)
    ann: Dict[str, List[datetime]] = defaultdict(list)
    for r in pd.read_parquet(ev_dir / "announcements.parquet").itertuples():
        ann[r.symbol].append(datetime.fromisoformat(r.disseminated_at))
    sched: Dict[str, List[Tuple[datetime, date]]] = defaultdict(list)
    for r in pd.read_parquet(ev_dir / "board_meetings.parquet").itertuples():
        sched[r.symbol].append((datetime.fromisoformat(r.intimated_at), date.fromisoformat(r.meeting_date)))
    exd: Dict[str, List[Tuple[datetime, date]]] = defaultdict(list)
    for r in pd.read_parquet(ev_dir / "corporate_actions.parquet").itertuples():
        exd[r.symbol].append((datetime.fromisoformat(r.announced_at), date.fromisoformat(r.ex_date)))
    return TableEvents(announcements={k: sorted(v) for k, v in ann.items()}, scheduled=dict(sched),
                       ex_dates=dict(exd),
                       covered=(datetime.combine(lo, dtime(0, 0), IST), datetime.combine(hi, dtime(23, 59, 59), IST)))


def load_fo_ban(root: Optional[Path] = None) -> Tuple[Dict[str, Set[date]], Set[date]]:
    """(symbol -> banned trade dates, trade dates whose ban file was fetched with status 200). A 404 day is
    NOT known: on a holiday there is no session to ask about, and on a trading day it is a gap. Callers must
    treat a session outside the known set as UNKNOWN, never as 'not banned' (universe_build: FO_BAN_UNKNOWN)."""
    import pandas as pd

    ev_dir = (root or paths.history_dir()) / "events"
    banned: Dict[str, Set[date]] = defaultdict(set)
    known: Set[date] = set()
    if (ev_dir / "fo_ban_days.parquet").exists():
        known = {date.fromisoformat(x) for x in pd.read_parquet(ev_dir / "fo_ban_days.parquet")["trade_date"]}
    if (ev_dir / "fo_ban.parquet").exists():
        for r in pd.read_parquet(ev_dir / "fo_ban.parquet").itertuples():
            banned[r.symbol].add(date.fromisoformat(r.trade_date))
    return dict(banned), known


# ---------------------------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NSE events and F&O ban lists (plan P3.4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--from", dest="start", required=True)
    f.add_argument("--to", dest="end", required=True)
    f.add_argument("--kinds", default=",".join(ALL_KINDS))
    f.add_argument("--max-requests", type=int, default=None)
    sub.add_parser("build")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        try:
            stats = fetch(args.kinds.split(","), date.fromisoformat(args.start), date.fromisoformat(args.end),
                          max_requests=args.max_requests)
        except NseBlocked as exc:
            print(f"STOPPED: {exc}. Report it; RESID_REV runs as RESID_REV_NF (plan P3.4).")
            return 2
        print(json.dumps({k: dict(v) for k, v in stats.items()}, indent=1))
    else:
        cov = build()
        print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk not in ("gaps", "by_month")}
                              if isinstance(v, dict) else v) for k, v in cov.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
