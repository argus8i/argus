import json

import pytest

from antigravity.daemons.track2_candle_collector import (
    collect_candle_history, collect_candles, parse_kite_candles,
    parse_kite_daily_candle_range,
)


DATE = "2026-09-22"
SYMBOLS = ("ANGELONE", "BDL", "CDSL", "SUZLON")
TOKENS = {symbol: index + 100 for index, symbol in enumerate(SYMBOLS)}


def _raw(timestamp="2026-09-22T09:15:00+05:30"):
    return json.dumps({"data": {"candles": [[timestamp, 100, 102, 99, 101, 5000]]}}).encode()


def _fetch(url, headers):
    assert headers["Authorization"].startswith("enctoken ")
    return 200, _raw(), url


def _history_fetch(url, headers):
    assert headers["Authorization"].startswith("enctoken ")
    if "/day?" in url:
        raw = json.dumps({"data": {"candles": [[
            "2026-09-22T00:00:00+05:30", 100, 103, 98, 102, 50000,
        ]]}}).encode()
    else:
        raw = _raw()
    return 200, raw, url


def test_collects_exact_universe_without_serializing_credential():
    result = collect_candles(
        session_date=DATE, frozen_symbols=SYMBOLS, instrument_tokens=TOKENS,
        authorization_token="x" * 40, fetcher=_fetch,
    )
    assert set(result["symbols"]) == set(SYMBOLS)
    assert "x" * 40 not in json.dumps(result)
    assert result["credential_serialized"] is False


@pytest.mark.parametrize("timestamp", [
    "2026-09-21T09:15:00+05:30",
    "2026-09-22T09:16:00+05:30",
])
def test_rejects_cross_date_or_non_bucket_timestamp(timestamp):
    with pytest.raises(ValueError, match="timestamp"):
        parse_kite_candles(_raw(timestamp), session_date=DATE)


def test_rejects_token_map_not_matching_frozen_universe():
    with pytest.raises(ValueError, match="does not match"):
        collect_candles(
            session_date=DATE, frozen_symbols=SYMBOLS,
            instrument_tokens={"CDSL": 1}, authorization_token="x" * 40,
            fetcher=_fetch,
        )


def test_rejects_bad_ohlc_relationship():
    raw = json.dumps({"data": {"candles": [["2026-09-22T09:15:00+05:30", 100, 98, 99, 101, 5]]}}).encode()
    with pytest.raises(ValueError, match="OHLCV"):
        parse_kite_candles(raw, session_date=DATE)


def test_history_collects_intraday_and_daily_without_credentials():
    result = collect_candle_history(
        start_date=DATE, end_date=DATE, instrument_tokens=TOKENS,
        authorization_token="x" * 40, fetcher=_history_fetch,
    )
    assert all(record["bars"] and record["daily_bars"] for record in result["symbols"].values())
    assert all("/day?" in record["daily_source_url"] for record in result["symbols"].values())
    assert "x" * 40 not in json.dumps(result)


def test_daily_parser_rejects_duplicate_dates():
    row = ["2026-09-22T00:00:00+05:30", 100, 103, 98, 102, 50000]
    raw = json.dumps({"data": {"candles": [row, row]}}).encode()
    with pytest.raises(ValueError, match="duplicate"):
        parse_kite_daily_candle_range(raw, start_date=DATE, end_date=DATE)
