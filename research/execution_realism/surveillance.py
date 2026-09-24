"""Pillar 4: fail-closed surveillance snapshot contract (R11) and the pre-market gate.

Unknown never equals cleared. A symbol is eligible only when every list it is
checked against validated today and explicitly does not contain it.

Applicability to F&O underlyings (verified in the research pass):
  GSM     excludes securities with derivatives (NSE GSM page)
  ESM     excludes derivatives; market cap < Rs 1,000 Cr (NSE ESM FAQ, 25-Jul-2025)
  LT-ASM  APPLIES to derivative stocks since 12-Aug-2024 (NSE/SURV/63362): stage I is
          100% margin on the underlying, later stages cut MWPL and stop new contracts
  ST-ASM  exclusion of derivatives per secondary sources only: checked anyway
  T2T/BE  series must be EQ in today's instrument master
  F&O ban daily file; a POLICY exclusion here, not a cash-market prohibition
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
SCHEMA_VERSION = "track2.surveillance.v3"
REQUIRED_SOURCES = ("NSE_ASM", "NSE_GSM", "NSE_FNO", "NSE_FO_BAN", "INSTRUMENT_MASTER")
ALLOWED_HOSTS = frozenset({"www.nseindia.com", "nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com",
                           "api.kite.trade", "images.dhan.co"})
EXPECTED_TYPE = {"NSE_ASM": "json", "NSE_GSM": "json", "NSE_FNO": "json", "NSE_FO_BAN": "csv",
                 "INSTRUMENT_MASTER": "csv", "NSE_ESM": "json"}
MIN_BYTES = {"NSE_ASM": 1024, "NSE_GSM": 512, "NSE_FNO": 4096, "NSE_FO_BAN": 30,
             "INSTRUMENT_MASTER": 100_000, "NSE_ESM": 256}
MIN_ROWS = {"asm_lt": 1, "asm_st": 1, "gsm": 1, "fno_underlyings": 150}
EMPTY_BODIES = {b"", b"{}", b"[]", b"null", b"NULL"}


_SYM = {"type": "string", "pattern": "^[A-Z0-9&\\-]{1,20}$"}
_SOURCE = {
    "type": "object",
    "required": ["url", "sha256", "byte_length", "fetched_at", "http_status", "content_type", "parser_version"],
    "properties": {
        "url": {"type": "string", "pattern": "^https://"},
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "byte_length": {"type": "integer", "minimum": 1},
        "fetched_at": {"type": "string", "format": "date-time"},
        "http_status": {"const": 200},
        "content_type": {"type": "string", "minLength": 1},
        "parser_version": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}
SNAPSHOT_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "track2.surveillance.v3",
    "type": "object",
    "required": ["schema_version", "trading_date", "generated_at", "calendar_sha256", "sources", "lists",
                 "series", "tick", "validation", "sha256"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "trading_date": {"type": "string", "format": "date"},
        "generated_at": {"type": "string", "format": "date-time"},
        "calendar_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "sources": {"type": "object", "required": list(REQUIRED_SOURCES),
                    "additionalProperties": _SOURCE},
        "lists": {
            "type": "object",
            "required": ["asm_lt", "asm_st", "gsm", "fno_underlyings", "fo_ban"],
            "properties": {
                "asm_lt": {"type": "array", "items": _SYM, "uniqueItems": True, "minItems": 1},
                "asm_st": {"type": "array", "items": _SYM, "uniqueItems": True, "minItems": 1},
                "gsm": {"type": "array", "items": _SYM, "uniqueItems": True, "minItems": 1},
                "fno_underlyings": {"type": "array", "items": _SYM, "uniqueItems": True, "minItems": 150},
                "fo_ban": {"type": "array", "items": _SYM, "uniqueItems": True},
            },
            "additionalProperties": False,
        },
        "series": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1}},
        "tick": {"type": "object", "additionalProperties": {"type": "number", "exclusiveMinimum": 0}},
        "validation": {
            "type": "object", "required": ["status", "warnings"],
            "properties": {"status": {"const": "PASS"}, "warnings": {"type": "array", "items": {"type": "string"}}},
        },
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
    "additionalProperties": False,
}


class SurveillanceAbort(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _abort(code: str, detail: str) -> None:
    raise SurveillanceAbort(code, detail)


# ---------------------------------------------------------------- calendar
@dataclass(frozen=True)
class TradingCalendar:
    holidays: FrozenSet[date]
    years: FrozenSet[int]
    source_sha256: str

    def is_trading_day(self, d: date) -> bool:
        if d.year not in self.years:
            _abort("CALENDAR_COVERAGE", f"no holiday list loaded for {d.year}")
        return d.weekday() < 5 and d not in self.holidays

    def previous_trading_day(self, d: date) -> date:
        x = d - timedelta(days=1)
        for _ in range(15):
            if self.is_trading_day(x):
                return x
            x -= timedelta(days=1)
        _abort("CALENDAR", f"no trading day within 15 days before {d}")


# ------------------------------------------------------------ provenance
@dataclass(frozen=True)
class SourceRecord:
    name: str
    url: str
    http_status: int
    content_type: str
    byte_length: int
    sha256: str
    fetched_at: datetime
    parser_version: str


def validate_transport(rec: SourceRecord, raw: bytes, *, trading_date: date, prev_trading_date: date,
                       now: datetime) -> None:
    n = rec.name
    if n not in EXPECTED_TYPE:
        _abort("UNKNOWN_SOURCE", n)
    u = urlparse(rec.url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        _abort("SOURCE_HOST", f"{n}: {rec.url}")
    if rec.http_status != 200:
        _abort("HTTP_STATUS", f"{n}: {rec.http_status}")
    ctype = (rec.content_type or "").lower()
    want = EXPECTED_TYPE[n]
    if want == "json" and "json" not in ctype:
        _abort("CONTENT_TYPE", f"{n}: {ctype!r} (HTML error pages arrive with status 200)")
    if want == "csv" and not any(t in ctype for t in ("csv", "text/plain", "octet-stream")):
        _abort("CONTENT_TYPE", f"{n}: {ctype!r}")
    if len(raw) != rec.byte_length or hashlib.sha256(raw).hexdigest() != rec.sha256:
        _abort("INTEGRITY", f"{n}: bytes do not match the recorded length/hash")
    if raw.strip() in EMPTY_BODIES or len(raw) < MIN_BYTES[n]:
        _abort("EMPTY_PAYLOAD", f"{n}: {len(raw)} bytes")
    if rec.fetched_at.tzinfo is None:
        _abort("NAIVE_TIMESTAMP", f"{n}: fetched_at has no timezone")
    t = rec.fetched_at.astimezone(IST)
    earliest = datetime.combine(prev_trading_date, time(15, 30), IST)
    latest = datetime.combine(trading_date, time(9, 0), IST)
    if not (earliest <= t <= min(latest, now.astimezone(IST) + timedelta(seconds=5))):
        _abort("STALE_OR_FUTURE", f"{n}: fetched {t.isoformat()} outside [{earliest}, {latest}]")


# ---------------------------------------------------------------- parsers
def _symbols(items, where: str) -> List[str]:
    if not isinstance(items, list):
        _abort("SCHEMA", f"{where}: expected a list")
    out = []
    for it in items:
        sym = (it.get("symbol") or it.get("Symbol")) if isinstance(it, dict) else None
        if not isinstance(sym, str) or not SYMBOL_RE.match(sym.strip().upper()):
            _abort("SCHEMA", f"{where}: record without a valid symbol")
        out.append(sym.strip().upper())
    if len(set(out)) != len(out):
        _abort("SCHEMA", f"{where}: duplicate symbols")
    return out


def parse_nse_asm(raw: bytes) -> Tuple[List[str], List[str]]:
    """reportASM: {"shortterm": {"data": [...]}, "longterm": {"data": [...]}}."""
    try:
        d = json.loads(raw)
    except ValueError:
        _abort("SCHEMA", "ASM: malformed JSON")
    if not isinstance(d, dict):
        _abort("SCHEMA", "ASM: top level is not an object")
    for node in ("shortterm", "longterm"):
        if not isinstance(d.get(node), dict) or "data" not in d[node]:
            _abort("SCHEMA", f"ASM: missing {node}.data")
    return _symbols(d["shortterm"]["data"], "ASM_ST"), _symbols(d["longterm"]["data"], "ASM_LT")


def parse_nse_gsm(raw: bytes) -> List[str]:
    try:
        d = json.loads(raw)
    except ValueError:
        _abort("SCHEMA", "GSM: malformed JSON")
    return _symbols(d, "GSM")


def parse_nse_fno(raw: bytes) -> List[str]:
    """underlying-information: {"data": {"UnderlyingList": [...]}}."""
    try:
        d = json.loads(raw)
    except ValueError:
        _abort("SCHEMA", "F&O: malformed JSON")
    if not (isinstance(d, dict) and isinstance(d.get("data"), dict) and "UnderlyingList" in d["data"]):
        _abort("SCHEMA", "F&O: missing data.UnderlyingList")
    return _symbols(d["data"]["UnderlyingList"], "FNO")


_BAN_HEADER = re.compile(r"securities in ban for trade date\s+(\d{2}-[A-Za-z]{3}-\d{4})", re.I)


def parse_fo_ban(raw: bytes, trading_date: date) -> List[str]:
    """fo_secban.csv. The header carries the trade date it applies to, the only
    in-payload as-of field among these sources. Header format assumed from the public
    file: confirm against a saved raw copy before relying on it."""
    text = raw.decode("utf-8", "strict").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    m = _BAN_HEADER.search(lines[0]) if lines else None
    if not m:
        _abort("SCHEMA", "F&O ban: header with trade date not found")
    as_of = datetime.strptime(m.group(1).title(), "%d-%b-%Y").date()
    if as_of != trading_date:
        _abort("STALE_OR_FUTURE", f"F&O ban file is for {as_of}, session is {trading_date}")
    out = []
    for ln in lines[1:]:
        if ln.upper() in ("NIL", "NONE"):
            continue
        parts = [p.strip().upper() for p in ln.split(",")]
        sym = parts[-1]
        if not SYMBOL_RE.match(sym):
            _abort("SCHEMA", f"F&O ban: bad row {ln!r}")
        out.append(sym)
    if len(set(out)) != len(out):
        _abort("SCHEMA", "F&O ban: duplicate symbols")
    return out


def parse_instrument_master(rows: Iterable[Dict[str, str]]) -> Dict[str, Dict[str, object]]:
    """Normalised NSE cash rows {symbol, series, tick}. The broker adapter maps its own
    columns and converts tick to rupees (check the unit of Dhan's SEM_TICK_SIZE)."""
    out: Dict[str, Dict[str, object]] = {}
    for r in rows:
        sym = str(r.get("symbol", "")).strip().upper()
        series = str(r.get("series", "")).strip().upper()
        try:
            tick = float(r.get("tick", "nan"))
        except (TypeError, ValueError):
            tick = math.nan
        if not SYMBOL_RE.match(sym) or not series or not (math.isfinite(tick) and tick > 0):
            continue
        if sym in out and out[sym]["series"] != series:
            out[sym] = {"series": "CONFLICT", "tick": tick}
        else:
            out[sym] = {"series": series, "tick": tick}
    if len(out) < 1000:
        _abort("SCHEMA", f"instrument master has only {len(out)} NSE cash symbols")
    return out


# ------------------------------------------------------------ cardinality
def _robust_z(x: float, hist: Sequence[int]) -> float:
    med = statistics.median(hist)
    mad = statistics.median(abs(h - med) for h in hist) * 1.4826
    return abs(x - med) / (mad if mad > 0 else max(1.0, 0.05 * med))


def _jaccard_change(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return 1.0 - (len(a & b) / len(a | b) if (a | b) else 1.0)


# ------------------------------------------------------------- snapshot
def build_snapshot(sources: Dict[str, Tuple[SourceRecord, bytes]], master_rows: Iterable[Dict[str, str]], *,
                   trading_date: date, calendar: TradingCalendar, now: datetime,
                   history_counts: Optional[Dict[str, Sequence[int]]] = None,
                   previous_lists: Optional[Dict[str, Sequence[str]]] = None,
                   max_missing_from_master: float = 0.05) -> Dict:
    """Validate every source and emit the canonical snapshot. Raises SurveillanceAbort."""
    if not calendar.is_trading_day(trading_date):
        _abort("NOT_A_TRADING_DAY", str(trading_date))
    prev_td = calendar.previous_trading_day(trading_date)
    missing = [n for n in REQUIRED_SOURCES if n not in sources]
    if missing:
        _abort("MISSING_SOURCE", ",".join(missing))
    for name, (rec, raw) in sources.items():
        if rec.name != name:
            _abort("SOURCE_NAME", f"{name} != {rec.name}")
        validate_transport(rec, raw, trading_date=trading_date, prev_trading_date=prev_td, now=now)
    st, lt = parse_nse_asm(sources["NSE_ASM"][1])
    lists = {"asm_st": st, "asm_lt": lt, "gsm": parse_nse_gsm(sources["NSE_GSM"][1]),
             "fno_underlyings": parse_nse_fno(sources["NSE_FNO"][1]),
             "fo_ban": parse_fo_ban(sources["NSE_FO_BAN"][1], trading_date)}
    master = parse_instrument_master(master_rows)
    warnings: List[str] = []
    for k, floor in MIN_ROWS.items():
        if len(lists[k]) < floor:
            _abort("CARDINALITY", f"{k}: {len(lists[k])} < {floor}")
    for k, hist in (history_counts or {}).items():
        if k in lists and len(hist) >= 20:
            z = _robust_z(len(lists[k]), hist)
            if z > 6:
                _abort("CARDINALITY", f"{k}: count {len(lists[k])} is {z:.1f} robust SDs from history")
            if z > 4:
                warnings.append(f"{k}: count {len(lists[k])} unusual (z={z:.1f})")
    for k, prev in (previous_lists or {}).items():
        if k in lists and _jaccard_change(lists[k], prev) > 0.5:
            _abort("CARDINALITY", f"{k}: more than half the list changed overnight; log the circular first")
    fno = lists["fno_underlyings"]
    not_in_master = [s for s in fno if s not in master]
    if len(not_in_master) > max_missing_from_master * len(fno):
        _abort("CROSS_SOURCE", f"{len(not_in_master)} F&O underlyings missing from the instrument master")
    if not_in_master:
        warnings.append(f"F&O symbols absent from master (ineligible today): {sorted(not_in_master)}")
    body = {
        "schema_version": SCHEMA_VERSION,
        "trading_date": trading_date.isoformat(),
        "generated_at": now.astimezone(IST).isoformat(),
        "calendar_sha256": calendar.source_sha256,
        "sources": {n: {"url": r.url, "sha256": r.sha256, "byte_length": r.byte_length,
                        "fetched_at": r.fetched_at.astimezone(IST).isoformat(), "http_status": r.http_status,
                        "content_type": r.content_type, "parser_version": r.parser_version}
                    for n, (r, _) in sorted(sources.items())},
        "lists": {k: sorted(v) for k, v in lists.items()},
        "series": {s: master[s]["series"] for s in sorted(fno) if s in master},
        "tick": {s: master[s]["tick"] for s in sorted(fno) if s in master},
        "validation": {"status": "PASS", "warnings": warnings},
    }
    body["sha256"] = canonical_sha256(body)
    return body


def canonical_sha256(body: Dict) -> str:
    clean = {k: v for k, v in body.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------- verdicts
class Tri(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Verdict:
    symbol: str
    eligible: bool
    flags: Tuple[Tuple[str, Tri], ...]
    reasons: Tuple[str, ...]
    snapshot_sha256: str


def verdict(symbol: str, snap: Dict, *, exclude_fo_ban: bool = True) -> Verdict:
    lists = snap.get("lists", {})

    def member(k: str) -> Tri:
        return Tri.UNKNOWN if k not in lists else (Tri.TRUE if symbol in set(lists[k]) else Tri.FALSE)

    fno = member("fno_underlyings")
    series = snap.get("series", {}).get(symbol)
    flags = {
        "fno_member": fno,
        "asm_lt": member("asm_lt"),
        "asm_st": member("asm_st"),
        "gsm": member("gsm"),
        "esm": Tri.NOT_APPLICABLE if fno is Tri.TRUE else Tri.UNKNOWN,   # ESM excludes derivatives
        "t2t": Tri.UNKNOWN if series is None else (Tri.FALSE if series == "EQ" else Tri.TRUE),
        "fo_ban": member("fo_ban") if exclude_fo_ban else Tri.NOT_APPLICABLE,
    }
    reasons = []
    if snap.get("validation", {}).get("status") != "PASS":
        reasons.append("snapshot not validated")
    if flags["fno_member"] is not Tri.TRUE:
        reasons.append(f"fno_member={flags['fno_member'].value}")
    for k in ("asm_lt", "asm_st", "gsm", "esm", "t2t", "fo_ban"):
        if flags[k] not in (Tri.FALSE, Tri.NOT_APPLICABLE):
            reasons.append(f"{k}={flags[k].value}")
    return Verdict(symbol, not reasons, tuple(flags.items()), tuple(reasons), snap.get("sha256", ""))


# ------------------------------------------------------------------ gate
def premarket_gate(path: Path, trading_date: date, now: datetime, *, max_age_h: float = 18.0) -> Dict:
    """Load today's snapshot or abort the scan. Rejects the R11 probe ('{}' dated 2000-01-01)."""
    path = Path(path)
    if not path.is_file():
        _abort("NO_SNAPSHOT", str(path))
    if trading_date.isoformat() not in path.name:
        _abort("WRONG_SESSION_FILE", f"{path.name} is not for {trading_date}")
    raw = path.read_bytes()
    if raw.strip() in EMPTY_BODIES:
        _abort("EMPTY_PAYLOAD", path.name)
    try:
        snap = json.loads(raw)
    except ValueError:
        _abort("SCHEMA", "snapshot is not JSON")
    if not isinstance(snap, dict) or snap.get("schema_version") != SCHEMA_VERSION:
        _abort("SCHEMA", "wrong or missing schema_version")
    if snap.get("trading_date") != trading_date.isoformat():
        _abort("WRONG_SESSION", f"snapshot is for {snap.get('trading_date')}")
    if snap.get("validation", {}).get("status") != "PASS":
        _abort("NOT_VALIDATED", "snapshot validation status is not PASS")
    if snap.get("sha256") != canonical_sha256(snap):
        _abort("INTEGRITY", "snapshot content does not match its hash")
    gen = datetime.fromisoformat(snap["generated_at"])
    if gen.tzinfo is None or not (timedelta(0) <= now - gen <= timedelta(hours=max_age_h)):
        _abort("STALE_OR_FUTURE", f"generated_at {snap['generated_at']}")
    return snap
