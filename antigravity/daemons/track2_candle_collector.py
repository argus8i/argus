"""Strict, credential-free-output collector for Track 2 Kite 15-minute bars."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any, Callable, Mapping, Sequence

from antigravity.models.session_manifest import IST


def parse_kite_candle_range(
    raw: bytes, *, start_date: str, end_date: str,
) -> list[dict[str, Any]]:
    try:
        first_day = date.fromisoformat(start_date)
        last_day = date.fromisoformat(end_date)
        payload = json.loads(raw)
        rows = payload["data"]["candles"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Kite candle response schema is invalid") from exc
    if not isinstance(rows, list):
        raise ValueError("Kite candles must be a list")
    parsed: list[dict[str, Any]] = []
    previous: datetime | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Kite candle row is malformed")
        try:
            timestamp = datetime.fromisoformat(str(row[0]))
            if timestamp.tzinfo is None:
                raise ValueError("timestamp requires timezone")
            timestamp = timestamp.astimezone(IST)
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in row[1:6]):
                raise ValueError("OHLCV must contain numbers, not booleans")
            if not math.isfinite(row[5]) or row[5] != int(row[5]):
                raise ValueError("volume must be a finite whole number")
            open_, high, low, close = (float(row[index]) for index in range(1, 5))
            volume = int(row[5])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Kite candle values are invalid") from exc
        if (not first_day <= timestamp.date() <= last_day
                or timestamp.minute % 15 != 0 or timestamp.second != 0 or timestamp.microsecond != 0
                or not '09:15' <= timestamp.strftime('%H:%M') <= '15:15'):
            raise ValueError("Kite candle timestamp is outside the requested range or interval")
        if previous is not None and timestamp <= previous:
            raise ValueError("Kite candle timestamps are duplicate or non-monotonic")
        if any(not math.isfinite(value) or value <= 0 for value in (open_, high, low, close)):
            raise ValueError("Kite candle price is invalid")
        if high < max(open_, close, low) or low > min(open_, close, high) or volume < 0:
            raise ValueError("Kite candle OHLCV relationship is invalid")
        parsed.append({
            "timestamp": timestamp.isoformat(), "open": open_, "high": high,
            "low": low, "close": close, "volume": volume,
        })
        previous = timestamp
    return parsed


def parse_kite_candles(raw: bytes, *, session_date: str) -> list[dict[str, Any]]:
    return parse_kite_candle_range(raw, start_date=session_date, end_date=session_date)


def parse_kite_daily_candle_range(
    raw: bytes, *, start_date: str, end_date: str,
) -> list[dict[str, Any]]:
    """Parse one timezone-aware Kite day candle per trading date."""
    try:
        first_day = date.fromisoformat(start_date)
        last_day = date.fromisoformat(end_date)
        rows = json.loads(raw)["data"]["candles"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Kite daily candle response schema is invalid") from exc
    if not isinstance(rows, list):
        raise ValueError("Kite daily candles must be a list")
    parsed: list[dict[str, Any]] = []
    previous_day: date | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError("Kite daily candle row is malformed")
        try:
            timestamp = datetime.fromisoformat(str(row[0]))
            if timestamp.tzinfo is None:
                raise ValueError("daily timestamp requires timezone")
            timestamp = timestamp.astimezone(IST)
            if timestamp.hour or timestamp.minute or timestamp.second or timestamp.microsecond:
                raise ValueError("daily timestamp must identify the local trading date")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in row[1:6]):
                raise ValueError("daily OHLCV must contain numbers, not booleans")
            if not math.isfinite(row[5]) or row[5] != int(row[5]):
                raise ValueError("daily volume must be a finite whole number")
            open_, high, low, close = (float(row[index]) for index in range(1, 5))
            volume = int(row[5])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Kite daily candle values are invalid") from exc
        if not first_day <= timestamp.date() <= last_day:
            raise ValueError("Kite daily candle is outside the requested range")
        if previous_day is not None and timestamp.date() <= previous_day:
            raise ValueError("Kite daily candle dates are duplicate or non-monotonic")
        if any(not math.isfinite(value) or value <= 0 for value in (open_, high, low, close)):
            raise ValueError("Kite daily candle price is invalid")
        if high < max(open_, close, low) or low > min(open_, close, high) or volume < 0:
            raise ValueError("Kite daily candle OHLCV relationship is invalid")
        parsed.append({
            "timestamp": timestamp.isoformat(), "open": open_, "high": high,
            "low": low, "close": close, "volume": volume,
        })
        previous_day = timestamp.date()
    return parsed


def collect_candle_history(
    *,
    start_date: str,
    end_date: str,
    instrument_tokens: Mapping[str, int],
    authorization_token: str,
    fetcher: Callable[[str, Mapping[str, str]], tuple[int, bytes, str]],
) -> dict[str, Any]:
    """Collect a bounded historical baseline without returning credentials."""
    if not authorization_token or len(authorization_token) < 20:
        raise ValueError("Kite authorization token is unavailable")
    if date.fromisoformat(start_date) > date.fromisoformat(end_date):
        raise ValueError("historical candle range is reversed")
    output: dict[str, Any] = {}
    for symbol in sorted(instrument_tokens):
        token = instrument_tokens[symbol]
        if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
            raise ValueError(f"invalid instrument token for {symbol}")
        url = (
            f"https://kite.zerodha.com/oms/instruments/historical/{token}/15minute"
            f"?from={start_date}&to={end_date}"
        )
        requested_at = datetime.now(IST).isoformat()
        status, raw, final_url = fetcher(
            url, {"Authorization": f"enctoken {authorization_token}", "User-Agent": "Mozilla/5.0"},
        )
        if status != 200 or final_url != url or not raw:
            raise ValueError(f"Kite historical candle fetch failed for {symbol}")
        daily_url = (
            f"https://kite.zerodha.com/oms/instruments/historical/{token}/day"
            f"?from={start_date}&to={end_date}"
        )
        daily_requested_at = datetime.now(IST).isoformat()
        daily_status, daily_raw, daily_final_url = fetcher(
            daily_url, {"Authorization": f"enctoken {authorization_token}", "User-Agent": "Mozilla/5.0"},
        )
        if daily_status != 200 or daily_final_url != daily_url or not daily_raw:
            raise ValueError(f"Kite daily candle fetch failed for {symbol}")
        output[symbol] = {
            "requested_at": requested_at,
            "instrument_token": token,
            "source_url": url,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "bars": parse_kite_candle_range(raw, start_date=start_date, end_date=end_date),
            "daily_requested_at": daily_requested_at,
            "daily_source_url": daily_url,
            "daily_raw_sha256": hashlib.sha256(daily_raw).hexdigest(),
            "daily_bars": parse_kite_daily_candle_range(
                daily_raw, start_date=start_date, end_date=end_date,
            ),
        }
    return {
        "start_date": start_date, "end_date": end_date, "interval": "15minute",
        "symbols": output, "credential_serialized": False,
    }


def collect_candles(
    *,
    session_date: str,
    frozen_symbols: Sequence[str],
    instrument_tokens: Mapping[str, int],
    authorization_token: str,
    fetcher: Callable[[str, Mapping[str, str]], tuple[int, bytes, str]],
) -> dict[str, Any]:
    """Collect exact frozen-universe bars without returning the credential."""
    if not authorization_token or len(authorization_token) < 20:
        raise ValueError("Kite authorization token is unavailable")
    normalized = tuple(sorted(str(symbol).strip().upper() for symbol in frozen_symbols))
    if len(normalized) < 4 or len(set(normalized)) != len(normalized):
        raise ValueError("frozen universe is invalid")
    if set(normalized) != set(instrument_tokens):
        raise ValueError("instrument-token map does not match frozen universe")
    output: dict[str, Any] = {}
    for symbol in normalized:
        token = instrument_tokens[symbol]
        if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
            raise ValueError(f"invalid instrument token for {symbol}")
        url = (
            f"https://kite.zerodha.com/oms/instruments/historical/{token}/15minute"
            f"?from={session_date}&to={session_date}"
        )
        requested_at = datetime.now(IST).isoformat()
        status, raw, final_url = fetcher(
            url, {"Authorization": f"enctoken {authorization_token}", "User-Agent": "Mozilla/5.0"},
        )
        if status != 200 or final_url != url or not raw:
            raise ValueError(f"Kite candle fetch failed for {symbol}")
        bars = parse_kite_candles(raw, session_date=session_date)
        output[symbol] = {
            "requested_at": requested_at,
            "instrument_token": token,
            "source_url": url,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "bars": bars,
        }
    return {
        "session_date": session_date,
        "interval": "15minute",
        "symbols": output,
        "credential_serialized": False,
    }
