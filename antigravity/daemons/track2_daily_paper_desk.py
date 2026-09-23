"""Track 2 prospective paper-desk adapter for the existing Kite feed.

This component never routes a broker order. It converts the current sealed
15-minute bars into rule-bound paper instructions and writes a small status
artifact that an operator can inspect during the session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from antigravity.daemons.track2_official_source_ingestor import (
    EXPECTED_ENDPOINTS, PARSER_VERSION, Track2OfficialSourceIngestor, replay_and_verify_sources,
)
from antigravity.daemons.track2_candle_collector import (
    parse_kite_candle_range, parse_kite_daily_candle_range,
)
from antigravity.daemons.track2_session_coordinator import SessionWriterLock
from antigravity.models.market_regime_filter import MarketRegimeFilter
from antigravity.models.session_manifest import IST
from antigravity.models.track2_orb_signal_adapter import evaluate_symbol


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK2_ROOT = REPO_ROOT / "shared" / "track2_liquid"
LIVE_CANDLES_PATH = TRACK2_ROOT / "live_candles_track2.json"
HISTORICAL_CANDLES_PATH = TRACK2_ROOT / "historical_candles_track2.json"
STATUS_PATH = TRACK2_ROOT / "paper_desk_status.json"
ORDER_LOG_PATH = TRACK2_ROOT / "paper_orders.jsonl"
ORDER_RULES = {"strategy": "15M_ORB", "risk_rs": 1500, "min_volume_multiple": 2.5}
CANDIDATES = {"CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"}
DYNAMIC_UNIVERSE_PATH = TRACK2_ROOT / "dynamic_universe.json"
FIELD_TEST_ROOT = TRACK2_ROOT / "field_tests"


def get_candidate_universe(dynamic_path: Path | None = None) -> set[str]:
    """
    Dynamically loads the active candidate universe from shared/track2_liquid/dynamic_universe.json.
    Falls back cleanly to the baseline 8-scrip universe if the dynamic artifact is not yet present.
    """
    path = dynamic_path or DYNAMIC_UNIVERSE_PATH
    if path.is_file() and not path.is_symlink():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scrip_list = (
                data.get("symbols")
                or [c.get("symbol") for c in data.get("candidates", []) if isinstance(c, dict)]
                or [c.get("symbol") for c in data.get("research_candidates", []) if isinstance(c, dict)]
            )
            if isinstance(scrip_list, list) and len(scrip_list) >= 4:
                return {str(s).strip().upper() for s in scrip_list if s}
        except Exception:
            pass
    return set(CANDIDATES)


def aware_time(value: Any) -> datetime:
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return timestamp.astimezone(IST)


def read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe input: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def validated_bars(record: Mapping[str, Any], start: str, end: str) -> list[dict[str, Any]]:
    if not isinstance(record, dict) or not isinstance(record.get("bars"), list):
        raise ValueError("malformed candle record")
    rows = []
    for bar in record["bars"]:
        if not isinstance(bar, dict):
            raise ValueError("malformed candle row")
        rows.append([bar.get(key) for key in ("timestamp", "open", "high", "low", "close", "volume")])
    return parse_kite_candle_range(json.dumps({"data": {"candles": rows}}).encode(), start_date=start, end_date=end)


CLOCK_SKEW_TOLERANCE_SECONDS = 5.0


def completed_bars(record: Mapping[str, Any], *, start: str, end: str,
                   now: datetime, max_age: float) -> list[dict[str, Any]]:
    requested = aware_time(record.get("requested_at"))
    diff_sec = (now - requested).total_seconds()
    # Tolerate minor clock drift/poll jitter up to CLOCK_SKEW_TOLERANCE_SECONDS
    if -CLOCK_SKEW_TOLERANCE_SECONDS <= diff_sec < 0:
        diff_sec = 0.0
    if not 0 <= diff_sec <= max_age:
        raise ValueError(f"candle request is stale or future-dated (skew={diff_sec:.2f}s, max_age={max_age}s)")
    # Use request START, not read/write time: an unfinished response cannot
    # mature into a completed candle just because the consumer waited.
    return [bar for bar in validated_bars(record, start, end)
            if aware_time(bar["timestamp"]) + timedelta(minutes=15) <= requested]


def validated_daily_bars(record: Mapping[str, Any], *, start: str, end: str,
                         now: datetime, max_age: float) -> list[dict[str, Any]]:
    rows = record.get("daily_bars")
    if not isinstance(rows, list):
        raise ValueError("historical daily candle record is missing")
    requested = aware_time(record.get("daily_requested_at"))
    diff_sec = (now - requested).total_seconds()
    if -CLOCK_SKEW_TOLERANCE_SECONDS <= diff_sec < 0:
        diff_sec = 0.0
    if not 0 <= diff_sec <= max_age:
        raise ValueError(f"daily candle request is stale or future-dated (skew={diff_sec:.2f}s, max_age={max_age}s)")
    serialized = [
        [bar.get(key) for key in ("timestamp", "open", "high", "low", "close", "volume")]
        if isinstance(bar, dict) else bar
        for bar in rows
    ]
    parsed = parse_kite_daily_candle_range(
        json.dumps({"data": {"candles": serialized}}).encode(),
        start_date=start, end_date=end,
    )
    # Today's day candle is still forming and must never enter a baseline.
    return [bar for bar in parsed if aware_time(bar["timestamp"]).date() < now.date()]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def load_current_candles(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(IST)).astimezone(IST)
    if path.is_symlink() or not path.is_file():
        raise ValueError("current Kite candle artifact is missing or unsafe")
    try:
        payload = read_object(path)
        written_at = datetime.strptime(
            str(payload["local_write_time"]), "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=IST)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("current Kite candle artifact is malformed") from exc
    if payload.get("data_valid") is not True or payload.get("credential_serialized") is not False:
        raise ValueError("current Kite candle artifact failed provenance checks")
    if payload.get("session_date") != current.date().isoformat():
        raise ValueError("current Kite candles belong to another session")
    age = (current - written_at).total_seconds()
    if -CLOCK_SKEW_TOLERANCE_SECONDS <= age < 0:
        age = 0.0
    if age < 0 or age > 90:
        raise ValueError(f"current Kite candle artifact is stale (age={age:.2f}s)")
    symbols = payload.get("symbols")
    allowed = get_candidate_universe() | {'NIFTY50'}
    if not isinstance(symbols, dict) or set(symbols) != allowed:
        if set(symbols) != CANDIDATES | {'NIFTY50'}:
            raise ValueError("current Kite candle universe is incomplete")
    # Kite may expose the currently forming 15-minute candle. It is not usable
    # prospectively until its full interval has elapsed.
    for record in symbols.values():
        if not isinstance(record, dict) or not isinstance(record.get("bars"), list):
            raise ValueError("current Kite candle record is malformed")
        record["bars"] = completed_bars(record, start=payload["session_date"],
                                        end=payload["session_date"], now=current, max_age=90)
    return payload


def load_historical_candles(path: Path, *, session_date: str,
                            now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(IST)
    if path.is_symlink() or not path.is_file():
        raise ValueError("historical Kite candle artifact is missing or unsafe")
    try:
        payload = read_object(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("historical Kite candle artifact is malformed") from exc
    if (payload.get("data_valid") is not True
            or payload.get("credential_serialized") is not False
            or payload.get("interval") != "15minute"
            or payload.get("end_date") != session_date):
        raise ValueError("historical Kite candle provenance is invalid")
    symbols = payload.get("symbols")
    allowed = get_candidate_universe() | {'NIFTY50'}
    if not isinstance(symbols, dict) or set(symbols) != allowed:
        if set(symbols) != CANDIDATES | {"NIFTY50"}:
            raise ValueError("historical Kite candle universe or Nifty data is incomplete")
    for record in symbols.values():
        token = record.get("instrument_token")
        source_url = record.get("source_url")
        daily_source_url = record.get("daily_source_url")
        hashes = (record.get("raw_sha256"), record.get("daily_raw_sha256"))
        if (isinstance(token, bool) or not isinstance(token, int) or token <= 0
                or source_url != (
                    f"https://kite.zerodha.com/oms/instruments/historical/{token}/15minute"
                    f"?from={payload['start_date']}&to={session_date}"
                )
                or daily_source_url != (
                    f"https://kite.zerodha.com/oms/instruments/historical/{token}/day"
                    f"?from={payload['start_date']}&to={session_date}"
                )
                or any(not isinstance(value, str) or len(value) != 64
                       or any(ch not in "0123456789abcdef" for ch in value)
                       for value in hashes)):
            raise ValueError("historical Kite candle source provenance is invalid")
        record["bars"] = completed_bars(record, start=payload["start_date"], end=session_date,
                                        now=current, max_age=420)
        record["daily_bars"] = validated_daily_bars(
            record, start=payload["start_date"], end=session_date,
            now=current, max_age=420,
        )
    return payload


def append_unique_paper_orders(path: Path, signals: Sequence[Mapping[str, Any]]) -> int:
    """Append each preregistered instruction once; never append a fill claim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(str(json.loads(line)["paper_order_id"]))
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("paper order log is malformed") from exc
    appended = 0
    with path.open("a", encoding="utf-8") as target:
        for signal in signals:
            if signal.get("fill_claimed") is not False:
                raise ValueError("paper instruction contains a fill claim")
            order_id = f"{signal.get('symbol')}|{signal.get('signal_timestamp')}|{signal.get('limit_price')}"
            if order_id in seen:
                continue
            row = {
                "paper_order_id": order_id, "recorded_at": datetime.now(IST).isoformat(),
                "execution_state": "QUEUED_UNVERIFIED", "actual_order_sent": False,
                "instruction": dict(signal),
            }
            target.write(json.dumps(row, sort_keys=True) + "\n")
            target.flush()
            os.fsync(target.fileno())
            seen.add(order_id)
            appended += 1
    return appended


def fetch_eligible_symbols(*, session_date: str, surveillance_dir: Path | None = None,
                          now: datetime | None = None, dynamic_path: Path | None = None) -> set[str]:
    """Fail closed against the same-day official F&O, ASM and GSM snapshot."""
    surveillance_dir = surveillance_dir or TRACK2_ROOT / "paper_surveillance"
    if dynamic_path is None and surveillance_dir != TRACK2_ROOT / "paper_surveillance":
        candidate_path = surveillance_dir.parent / "dynamic_universe.json"
    else:
        candidate_path = dynamic_path or DYNAMIC_UNIVERSE_PATH
    current = now or datetime.now(IST)
    snapshot_path = surveillance_dir / f"nse_surveillance_snapshot_{session_date}.json"
    if not snapshot_path.exists():
        result = Track2OfficialSourceIngestor(surveillance_dir=str(surveillance_dir)).ingest_session(session_date)
        if result.get("verified") is not True:
            raise ValueError(f"official NSE preflight failed: {result.get('reason', 'UNKNOWN')}")
        # The source timestamps are written while ingestion is running.  A
        # clock captured before that network work can make our own fresh
        # artifacts appear future-dated for a few seconds.
        if now is None:
            current = datetime.now(IST)
    snapshot = read_object(snapshot_path)
    if snapshot.get("effective_session_date") != session_date or snapshot.get("parse_status") != "SUCCESS":
        raise ValueError("surveillance snapshot date/status mismatch")
    fetched = aware_time(snapshot.get("fetched_at"))
    if fetched.date().isoformat() != session_date or fetched.strftime('%H:%M') >= '09:00' or fetched > current:
        raise ValueError("surveillance snapshot was not fetched prospectively")
    sources = snapshot.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("surveillance sources missing")
    for key, url in EXPECTED_ENDPOINTS.items():
        meta = sources.get(key, {})
        stamp = aware_time(meta.get("fetched_at"))
        if (meta.get("source_url") != url or meta.get("http_status") != 200
                or stamp.date().isoformat() != session_date or stamp.strftime('%H:%M') >= '09:00'
                or stamp > current):
            raise ValueError("surveillance source identity/time mismatch")
    lists = {key: snapshot.get(key, []) for key in ('asm_short_term', 'asm_long_term', 'gsm', 'fno_underlyings')}
    verified, reason = replay_and_verify_sources(sources, surveillance_dir, lists,
                                                 parser_version=PARSER_VERSION, expected_session_date=session_date)
    if not verified:
        raise ValueError(f"surveillance replay failed: {reason}")
    blocked = set(snapshot.get("asm_short_term", ())) | set(snapshot.get("asm_long_term", ())) | set(snapshot.get("gsm", ()))
    fno = set(snapshot.get("fno_underlyings", ()))
    active_candidates = get_candidate_universe(dynamic_path=candidate_path)
    eligible = active_candidates.intersection(fno).difference(blocked)
    if len(eligible) < 4:
        eligible = CANDIDATES.intersection(fno).difference(blocked)
    if len(eligible) < 4:
        raise ValueError("official NSE preflight left fewer than four eligible symbols")
    return eligible


def baseline_from_history(
    bars: Sequence[Mapping[str, Any]], *, daily_bars: Sequence[Mapping[str, Any]],
    decision_timestamp: str,
) -> dict[str, Any]:
    """Build split-source baselines without using the forming session."""
    decision = datetime.fromisoformat(decision_timestamp).astimezone(IST)
    prior_intraday: list[tuple[datetime, Mapping[str, Any]]] = []
    for bar in bars:
        timestamp = datetime.fromisoformat(str(bar.get("timestamp"))).astimezone(IST)
        if timestamp.date() < decision.date():
            prior_intraday.append((timestamp, bar))
    prior_daily: list[tuple[datetime, Mapping[str, Any]]] = []
    for bar in daily_bars:
        timestamp = datetime.fromisoformat(str(bar.get("timestamp"))).astimezone(IST)
        if timestamp.date() < decision.date():
            prior_daily.append((timestamp, bar))
    prior_daily.sort(key=lambda item: item[0])
    dates = [timestamp.date() for timestamp, _ in prior_daily]
    if len(dates) != len(set(dates)):
        raise ValueError("historical daily dates are duplicated")
    if len(prior_daily) < 20:
        raise ValueError("twenty complete prior daily sessions required")
    selected_daily = prior_daily[-20:]
    selected_dates = {timestamp.date() for timestamp, _ in selected_daily}
    bucket_by_date: dict[Any, int] = {}
    for timestamp, bar in prior_intraday:
        if timestamp.date() not in selected_dates or timestamp.time() != decision.time():
            continue
        if timestamp.date() in bucket_by_date:
            raise ValueError("historical same-bucket observation is duplicated")
        volume = bar.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
            raise ValueError("historical same-bucket volume is invalid")
        bucket_by_date[timestamp.date()] = volume
    if set(bucket_by_date) != selected_dates:
        raise ValueError("twenty prior same-bucket observations required")
    ordered = []
    for timestamp, bar in selected_daily:
        try:
            row = {
                "open": float(bar["open"]), "high": float(bar["high"]), "low": float(bar["low"]),
                "close": float(bar["close"]), "volume": int(bar["volume"]),
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("historical daily candle is malformed") from exc
        if (any(not math.isfinite(row[key]) or row[key] <= 0 for key in ("open", "high", "low", "close"))
                or isinstance(bar.get("volume"), bool) or row["volume"] <= 0
                or row["high"] < max(row["open"], row["low"], row["close"])
                or row["low"] > min(row["open"], row["high"], row["close"])):
            raise ValueError("historical daily candle is invalid")
        ordered.append(row)
    true_ranges = []
    previous_close = None
    for row in ordered:
        ranges = [row["high"] - row["low"]]
        if previous_close is not None:
            ranges.extend((abs(row["high"] - previous_close), abs(row["low"] - previous_close)))
        true_ranges.append(max(ranges))
        previous_close = row["close"]
    return {
        "historical_bucket_volume_median": float(statistics.median(bucket_by_date.values())),
        "atr14_points": float(sum(true_ranges[-14:]) / 14),
        "dtv_med20_cr": float(statistics.median(
            row["close"] * row["volume"] for row in ordered
        ) / 10_000_000),
        "dtv_method": "MEDIAN_DAILY_CLOSE_X_VOLUME_APPROX",
        "daily_sessions": 20,
        "same_bucket_sessions": 20,
    }


def evaluate_feed(
    payload: Mapping[str, Any], *, historical: Mapping[str, Sequence[Mapping[str, Any]]],
    historical_daily: Mapping[str, Sequence[Mapping[str, Any]]],
    order_rules: Mapping[str, Any], nifty_bars: Sequence[Mapping[str, Any]],
    eligible_symbols: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(IST)
    if payload.get("session_date") != current.date().isoformat():
        raise ValueError("decision session mismatch")
    if not '09:45' <= current.strftime('%H:%M') < '14:45':
        return {"mode": "FIELD_TEST", "actual_order_sent": False, "decisions": [], "paper_signals": [],
                "state": "OUTSIDE_SIGNAL_WINDOW", "session_date": payload["session_date"]}
    symbols = payload["symbols"]
    nifty_today = [bar for bar in nifty_bars if str(bar["timestamp"])[:10] == payload["session_date"]]
    opening = next((bar for bar in nifty_today if str(bar["timestamp"])[11:16] == "09:15"), None)
    if opening is None or not nifty_today:
        raise ValueError("Nifty opening range is unavailable")
    # A sealed 15-minute market-regime bar remains the latest available bar
    # until the next interval closes.  Allow one full interval plus collection
    # lag; the much stricter 90-second entry window is enforced per symbol.
    diff_nifty = (current - aware_time(nifty_today[-1]["timestamp"]) - timedelta(minutes=15)).total_seconds()
    if -CLOCK_SKEW_TOLERANCE_SECONDS <= diff_nifty < 0:
        diff_nifty = 0.0
    if not 0 <= diff_nifty <= 960:
        raise ValueError(f"Nifty completed candle is stale (skew={diff_nifty:.2f}s)")
    regime = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=float(nifty_today[-1]["close"]),
        nifty_or_high=float(opening["high"]), nifty_or_low=float(opening["low"]),
    )
    decisions: list[dict[str, Any]] = []
    for symbol, candle_record in sorted(symbols.items()):
        if symbol == 'NIFTY50':
            continue
        if eligible_symbols is None or symbol not in eligible_symbols:
            decisions.append({"symbol": symbol, "decision": "SURVEILLANCE_REJECTED", "paper_instruction": None})
            continue
        bars = candle_record.get("bars", [])
        if not bars:
            decisions.append({"symbol": symbol, "decision": "DATA_INVALID", "paper_instruction": None})
            continue
        closed_at = aware_time(bars[-1]["timestamp"]) + timedelta(minutes=15)
        diff_signal = (current - closed_at).total_seconds()
        if -CLOCK_SKEW_TOLERANCE_SECONDS <= diff_signal < 0:
            diff_signal = 0.0
        if not 0 <= diff_signal <= 90:
            decisions.append({"symbol": symbol, "decision": "STALE_SIGNAL_BAR", "paper_instruction": None})
            continue
        try:
            baseline = baseline_from_history(
                historical.get(symbol, ()),
                daily_bars=historical_daily.get(symbol, ()),
                decision_timestamp=str(bars[-1]["timestamp"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            decisions.append({"symbol": symbol, "decision": "DATA_INVALID",
                              "reason": str(exc), "paper_instruction": None})
            continue
        if baseline["dtv_med20_cr"] < 30:
            decisions.append({"symbol": symbol, "decision": "DTV_BELOW_30_CR", "paper_instruction": None})
            continue
        result = evaluate_symbol(
            symbol=symbol, candle_record=candle_record, baseline=baseline,
            regime=regime, order_rules=order_rules,
        )
        instruction = result.get("paper_instruction")
        if instruction:
            instruction["bar_start_timestamp"] = instruction["signal_timestamp"]
            instruction["bar_close_timestamp"] = closed_at.isoformat()
            instruction["signal_timestamp"] = current.isoformat()
            instruction["baseline"] = baseline
            instruction["review_status"] = "PENDING_TRI_AGENT_REVIEW"
        decisions.append(result)
    return {
        "mode": "PAPER_ONLY",
        "actual_order_sent": False,
        "session_date": payload["session_date"],
        "generated_at": datetime.now(IST).isoformat(),
        "market_regime": regime.to_dict(),
        "decisions": decisions,
        "paper_signals": [row["paper_instruction"] for row in decisions if row.get("paper_instruction")],
    }


INPUT_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError)


def archive_input(directory: Path, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    target = directory / 'inputs' / f'{digest}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open('xb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    elif target.read_bytes() != raw:
        raise ValueError('archived input hash mismatch')
    return digest


def append_event(directory: Path, event: Mapping[str, Any]) -> None:
    with (directory / 'events.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps(event, sort_keys=True, allow_nan=False) + '\n')
        output.flush()
        os.fsync(output.fileno())


def record_candidates(directory: Path, signals: Sequence[Mapping[str, Any]]) -> int:
    """Review-pending signal observations, one per symbol/day, never orders."""
    count = 0
    allowed_candidates = get_candidate_universe() | CANDIDATES
    for signal in signals:
        symbol = signal.get('symbol')
        if symbol not in allowed_candidates or signal.get('fill_claimed') is not False:
            raise ValueError('invalid candidate observation')
        row = {'record_type': 'SIGNAL_CANDIDATE', 'instruction': dict(signal),
               'actual_order_sent': False, 'counts_trade_gate': False,
               'review_status': 'PENDING_TRI_AGENT_REVIEW', 'execution_state': 'NOT_SUBMITTED'}
        target = directory / f'candidate_{symbol}.json'
        try:
            with target.open('x', encoding='utf-8') as output:
                json.dump(row, output, sort_keys=True, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            count += 1
        except FileExistsError:
            existing = read_object(target)
            if existing.get('instruction', {}).get('symbol') != symbol:
                raise ValueError('candidate log is corrupt')
    return count


def close_field_test(directory: Path, state: str) -> dict[str, Any]:
    rows = []
    events = directory / 'events.jsonl'
    if events.exists():
        rows = [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines() if line.strip()]
    summary = {'mode': 'FIELD_TEST', 'state': state, 'actual_order_sent': False,
               'generated_at': datetime.now(IST).isoformat(), 'counts_session_gate': False,
               'counts_trade_gate': False, 'confirmed_fills': 0, 'realized_pnl': None,
               'review_status': 'PENDING_TRI_AGENT_REVIEW',
               'captured_cycles': sum('input_sha256' in row for row in rows),
               'candidate_count': len(list(directory.glob('candidate_*.json'))),
               'issues': sorted({str(row['reason']) for row in rows if row.get('reason')})}
    _atomic_json(directory / 'summary.json', summary)
    return summary


def run_status_loop(*, interval_seconds: float = 5.0) -> int:
    """Run a restartable field test. Rule 8 review is required for paper execution."""
    if not math.isfinite(interval_seconds) or not 1 <= interval_seconds <= 30:
        raise ValueError('interval must be between 1 and 30 seconds')
    session_date = datetime.now(IST).date().isoformat()
    directory = FIELD_TEST_ROOT / session_date
    directory.mkdir(parents=True, exist_ok=True)
    lock = SessionWriterLock(directory / 'writer.lock')
    lock.acquire()
    eligible_symbols: set[str] = set()
    preflight_reason = 'PREFLIGHT_PENDING'
    next_preflight = 0.0
    previous_state = None
    try:
        append_event(directory, {'record_type': 'START_OR_RESUME', 'recorded_at': datetime.now(IST).isoformat()})
        while True:
            current = datetime.now(IST)
            if current.date().isoformat() != session_date or current.strftime('%H:%M') >= '15:30':
                summary = close_field_test(directory, 'FIELD_TEST_CLOSED')
                _atomic_json(STATUS_PATH, summary)
                print('Field test closed. Summary: ' + str(directory / 'summary.json'), flush=True)
                return 0
            status: dict[str, Any] = {'mode': 'FIELD_TEST', 'actual_order_sent': False,
                'counts_session_gate': False, 'counts_trade_gate': False,
                'review_status': 'PENDING_TRI_AGENT_REVIEW', 'session_date': session_date,
                'generated_at': current.isoformat()}
            # Prepare independently of Kite bars so a pre-open empty feed does
            # not postpone official preflight beyond its 09:00 cutoff.
            if not eligible_symbols and time.monotonic() >= next_preflight:
                try:
                    eligible_symbols = fetch_eligible_symbols(session_date=session_date)
                    preflight_reason = ''
                except INPUT_ERRORS as exc:
                    preflight_reason = str(exc)
                next_preflight = time.monotonic() + 60
            try:
                payload = load_current_candles(LIVE_CANDLES_PATH)
                status['input_sha256'] = archive_input(directory, payload)
                history = load_historical_candles(HISTORICAL_CANDLES_PATH, session_date=session_date)
                status['history_sha256'] = archive_input(directory, history)
                if preflight_reason:
                    raise ValueError(preflight_reason)
                decision = evaluate_feed(payload,
                    historical={symbol: record['bars'] for symbol, record in history['symbols'].items() if symbol != 'NIFTY50'},
                    historical_daily={symbol: record['daily_bars'] for symbol, record in history['symbols'].items() if symbol != 'NIFTY50'},
                    order_rules=ORDER_RULES, nifty_bars=payload['symbols']['NIFTY50']['bars'],
                    eligible_symbols=eligible_symbols)
                # Some regime states use infinity as an internal gate marker;
                # serialize the marker as null in evidence, never invalid JSON.
                regime = decision.get('market_regime', {})
                for key, value in regime.items():
                    if isinstance(value, float) and not math.isfinite(value):
                        regime[key] = None
                signals = decision.pop('paper_signals', [])
                status.update(decision)
                status['mode'] = 'FIELD_TEST'
                for signal in signals:
                    signal['input_sha256'] = status['input_sha256']
                    signal['history_sha256'] = status['history_sha256']
                    source_record = history['symbols'][signal['symbol']]
                    signal['historical_15m_raw_sha256'] = source_record['raw_sha256']
                    signal['historical_daily_raw_sha256'] = source_record['daily_raw_sha256']
                status['new_candidates'] = record_candidates(directory, signals)
                status['state'] = decision.get('state', 'CANDIDATES_PENDING_REVIEW' if signals else 'NO_SIGNAL')
                status['feed_ready'] = True
            except INPUT_ERRORS as exc:
                status.update(state='WAITING_FOR_VALID_INPUT', feed_ready=False,
                              reason=f'{type(exc).__name__}: {exc}')
            status['official_nse_eligible_symbols'] = sorted(eligible_symbols)
            append_event(directory, status)
            _atomic_json(STATUS_PATH, status)
            display = (status['state'], status.get('reason'))
            if display != previous_state:
                print(f"{current.strftime('%H:%M:%S')} {display[0]} {display[1] or ''}", flush=True)
                previous_state = display
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        summary = close_field_test(directory, 'INTERRUPTED_RESUMABLE')
        _atomic_json(STATUS_PATH, summary)
        return 130
    finally:
        lock.release()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Track 2 paper-only desk adapter")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    return run_status_loop(interval_seconds=args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
