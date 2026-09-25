"""
Upstox V2 history tests (research/data/upstox_history.py): resampling rule, windows, instrument resolution,
polite client, fetch/resume/build, provenance class, and the stricter cross-source pass rule.
No test touches the network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("pyarrow")

from research.data import provenance
from research.data import upstox_history as U
from research.data.store_parquet import ParquetCandleStore

IST = timezone(timedelta(hours=5, minutes=30))


def _minute(day, hh, mm, o, h, l, c, v, ss=0):
    return [datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST).isoformat(), o, h, l, c, v, 0]


def _session_minutes(day, base=100.0):
    rows = []
    for k in range(375):                                          # 09:15 .. 15:29
        t = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=k)
        p = base + 0.01 * k
        rows.append([t.isoformat(), p, p + 0.05, p - 0.05, p + 0.01, 10 + k, 0])
    return rows


# ------------------------------------------------------------------ resampling rule
def test_resample_first_max_min_last_sum_over_t_to_t_plus_14():
    d = date(2026, 8, 10)
    rows = [_minute(d, 9, 15, 48.33, 48.45, 47.90, 47.97, 1000),
            _minute(d, 9, 16, 47.97, 48.60, 47.95, 48.10, 200),
            _minute(d, 9, 29, 48.10, 48.20, 47.80, 48.05, 300),       # last minute of the 09:15 bar
            _minute(d, 9, 30, 48.05, 48.06, 48.00, 48.01, 50),        # first minute of the 09:30 bar
            _minute(d, 9, 14, 1, 1, 1, 1, 1),                         # pre-open minute: dropped
            _minute(d, 15, 30, 1, 1, 1, 1, 1),                        # after 15:29: dropped
            _minute(d, 9, 31, 1, 1, 1, 1, 1, ss=30),                  # not on a minute: dropped
            _minute(d, 9, 30, 48.05, 48.06, 48.00, 48.01, 50),        # identical duplicate: kept once
            ["garbage"]]
    bars, st = U.resample_15m(list(reversed(rows)))                    # API returns newest first
    assert bars[0] == {"timestamp": "2026-08-10T09:15:00+05:30", "open": 48.33, "high": 48.60, "low": 47.80,
                       "close": 48.05, "volume": 1500}
    assert bars[1]["timestamp"] == "2026-08-10T09:30:00+05:30" and bars[1]["volume"] == 50
    assert st["MINUTE_OUTSIDE_0915_1529"] == 3 and st["MINUTE_DUPLICATE"] == 1 and st["MINUTE_UNPARSEABLE"] == 1
    # a conflicting duplicate removes that minute whatever the input order
    conflict = rows[:-2] + [_minute(d, 9, 16, 9, 9, 9, 9, 9)]
    for order in (conflict, list(reversed(conflict))):
        b, s = U.resample_15m(order)
        assert s["MINUTE_CONFLICT"] == 2
        assert b[0] == {"timestamp": "2026-08-10T09:15:00+05:30", "open": 48.33, "high": 48.45, "low": 47.80,
                        "close": 48.05, "volume": 1300}


def test_full_session_gives_25_bars_of_15_minutes():
    bars, st = U.resample_15m(_session_minutes(date(2026, 7, 1)))
    assert len(bars) == 25 and st["bar_minutes_15"] == 25 and st["minutes"] == 375
    assert bars[-1]["timestamp"].endswith("15:15:00+05:30")
    assert bars[0]["volume"] == sum(10 + k for k in range(15))


def test_month_windows_and_url_order():
    assert U.month_windows(date(2022, 1, 15), date(2022, 3, 3)) == [
        (date(2022, 1, 15), date(2022, 1, 31)), (date(2022, 2, 1), date(2022, 2, 28)),
        (date(2022, 3, 1), date(2022, 3, 3))]
    assert U.month_windows(date(2025, 12, 5), date(2026, 1, 2))[-1] == (date(2026, 1, 1), date(2026, 1, 2))
    assert U.candle_url("NSE_EQ|INE040H01021", "1minute", date(2026, 8, 1), date(2026, 8, 31)) == \
        "https://api.upstox.com/v2/historical-candle/NSE_EQ|INE040H01021/1minute/2026-08-31/2026-08-01"


def test_resolve_by_symbol_and_canonical_index_name():
    master = [{"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "SUZLON",
               "instrument_key": "NSE_EQ|INE040H01021"},
              {"segment": "NSE_EQ", "instrument_type": "BE", "trading_symbol": "XYZ", "instrument_key": "k"},
              {"segment": "NSE_INDEX", "name": "Nifty 50", "instrument_key": "NSE_INDEX|Nifty 50"},
              {"segment": "NSE_INDEX", "name": "India VIX", "instrument_key": "NSE_INDEX|India VIX"}]
    found, missing = U.resolve(["suzlon", "NIFTY50", "INDIA VIX", "XYZ", "NOPE", "NIFTYIT"], master)
    assert found == {"SUZLON": "NSE_EQ|INE040H01021", "IDX:NIFTY50": "NSE_INDEX|Nifty 50",
                     "IDX:INDIAVIX": "NSE_INDEX|India VIX"}
    assert missing == ["XYZ", "NOPE", "NIFTYIT"]


# ------------------------------------------------------------------ client
class _Resp:
    def __init__(self, code, body=b""):
        self.status_code, self.content = code, body


class _Session:
    def __init__(self, script):
        self.headers, self.script, self.urls = {}, list(script), []

    def get(self, url, timeout=None):
        self.urls.append(url)
        item = self.script.pop(0) if self.script else _Resp(200, b"{}")
        if isinstance(item, Exception):
            raise item
        return item


class _Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _client(script, **kw):
    clk = _Clock()
    return U.UpstoxClient(session=_Session(script), sleep=clk.sleep, clock=clk.now, **kw), clk


def test_client_paces_and_stops_on_repeated_refusals():
    c, clk = _client([_Resp(200, b"{}"), _Resp(200, b"{}")])
    c.get("u1")
    c.get("u2")
    assert clk.slept == [pytest.approx(1.0)] and "track2-research" in c.s.headers["User-Agent"]
    with pytest.raises(ValueError):
        U.UpstoxClient(session=_Session([]), min_interval=0.5)
    blocked, _ = _client([_Resp(429)] * 5)
    with pytest.raises(U.UpstoxBlocked):
        blocked.get("u")
    resets, _ = _client([ConnectionResetError(10054, "reset")] * 5)
    with pytest.raises(U.UpstoxBlocked, match="transport"):
        resets.get("u")
    recover, clk2 = _client([ConnectionResetError(10054, "reset"), _Resp(200, b"{}")])
    assert recover.get("u") == (200, b"{}") and 30.0 in clk2.slept


# ------------------------------------------------------------------ fetch, resume, build, provenance
def _payload(rows):
    return json.dumps({"status": "success", "data": {"candles": rows}}).encode()


def test_fetch_resume_build_and_upstox_is_qa_only(tmp_path):
    d1, d2 = date(2026, 7, 1), date(2026, 7, 2)
    minutes = list(reversed(_session_minutes(d1) + _session_minutes(d2, 101.0)))
    daily = [[f"{d.isoformat()}T00:00:00+05:30", 100, 104, 99, 103, 9000, 0] for d in (d1, d2)]
    c, _ = _client([_Resp(200, _payload(minutes)), _Resp(200, _payload(daily))])
    stats = U.fetch({"ABC": "NSE_EQ|INE000000001"}, d1, d2, client=c, root=tmp_path, log=lambda s: None)
    assert stats["status_200"] == 2
    assert "1minute/2026-07-02/2026-07-01" in c.s.urls[0] and "/day/2026-07-02/2026-05-02" in c.s.urls[1]
    c2, _ = _client([])
    again = U.fetch({"ABC": "NSE_EQ|INE000000001"}, d1, d2, client=c2, root=tmp_path, log=lambda s: None)
    assert again["skipped"] == 2 and c2.requests_made == 0
    entry, _ = U.build_entry("ABC", root=tmp_path)
    assert entry["source"] == "UPSTOX_API_V2" and len(entry["bars"]) == 50 and len(entry["daily_bars"]) == 2
    assert provenance.classify(entry) == provenance.UPSTOX
    # promoted 25 Sep 2026 after the Kite cross-check passed under Yashu's multi-broker rule
    assert provenance.strategy_eligible(provenance.UPSTOX)
    assert not provenance.strategy_eligible(provenance.HF_UPSTOX)       # the HF mirror stays QA_ONLY
    out = tmp_path / "hist"
    m = U.build(["ABC"], out_root=out, root=tmp_path, label="t")
    assert m["symbols"][0]["source_class"] == "UPSTOX_API_V2" and m["symbols"][0]["bars_out"] == 50
    st = ParquetCandleStore(out)                                         # strategy mode opens it now
    assert st.sources == {"ABC": "UPSTOX_API_V2"}
    # 2026-07-01/02 are inside the locked holdout: invisible in strategy mode, visible in QA mode only
    assert st.sessions("ABC") == []
    qa = ParquetCandleStore(out, mode="QA")
    assert [len(qa.bars("ABC", d)) for d in (d1, d2)] == [25, 25]       # pre-CAS: 25 bars


def test_post_cas_upstox_stock_keeps_24_bars_after_ingest(tmp_path):
    d = date(2026, 8, 10)
    c, _ = _client([_Resp(200, _payload(list(reversed(_session_minutes(d))))), _Resp(200, _payload([]))])
    U.fetch({"ABC": "k"}, d, d, client=c, root=tmp_path, log=lambda s: None)
    m = U.build(["ABC"], out_root=tmp_path / "h", root=tmp_path, label="t")
    assert m["totals"]["CAS_AUCTION_BAR"] == 1 and m["symbols"][0]["bars_out"] == 24


def test_upstox_is_not_mistaken_for_the_hf_mirror():
    assert provenance.classify({"source": "UPSTOX_API_V2"}) == provenance.UPSTOX
    assert provenance.classify({"source_url": "https://api.upstox.com/v2/historical-candle"}) == provenance.UPSTOX
    assert provenance.classify({"source": "upstox_1min",
                                "source_url": "https://huggingface.co/datasets/x/y"}) == provenance.HF_UPSTOX


# ------------------------------------------------------------------ stricter cross-source pass rule
def test_cross_source_fails_on_a_missing_bar_not_only_on_offsets():
    from research.backtest.bars import Bar, CandleStore
    from research.data.cross_source import compare

    d = date(2026, 8, 3)

    def store(n):
        bars = [Bar("ABC", datetime(2026, 8, 3, 9, 15, tzinfo=IST) + timedelta(minutes=15 * k), 15,
                    100, 100.1, 99.9, 100, 1000) for k in range(n)]
        return CandleStore({"ABC": {d: bars}}, {}, mode="QA")

    assert compare(store(24), store(24))["passed"]
    res = compare(store(23), store(24))                                  # one bar missing, no offset
    assert res["start_mismatch_sessions"] == 1 and res["one_bar_offset_sessions"] == 0 and not res["passed"]
    assert not compare(CandleStore({}, {}, mode="QA"), store(24))["passed"]   # nothing compared is not a pass


def _days_store(symbol, n_days, bump=None, vol=1000):
    """n_days x 20 bars (09:15..14:00) at 100; bump = {(day_index, bar_index): (field, value)}."""
    from research.backtest.bars import Bar, CandleStore

    intraday = {}
    for k in range(n_days):
        d = date(2026, 8, 3) + timedelta(days=k)
        bars = []
        for j in range(20):
            o, h, l, c = 100.0, 100.1, 99.9, 100.0
            if bump and (k, j) in bump:
                f, v = bump[(k, j)]
                o, h, l, c = (v if f == "open" else o, v if f == "high" else h, v if f == "low" else l,
                              v if f == "close" else c)
            bars.append(Bar(symbol, datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST) + timedelta(minutes=15 * j),
                            15, o, h, l, c, vol))
        intraday.setdefault(symbol, {})[d] = bars
    return CandleStore(intraday, {}, mode="QA")


def test_agreement_threshold_is_99_5_percent_per_field_and_volume_does_not_gate():
    from research.data.cross_source import compare

    ref = _days_store("ABC", 10)                                          # 200 bars
    one = compare(_days_store("ABC", 10, bump={(0, 0): ("high", 101.0)}), ref)
    assert one["stock_field_agreement"]["high"] == pytest.approx(0.995) and one["passed"]      # 1/200 = 0.5%
    two = compare(_days_store("ABC", 10, bump={(0, 0): ("high", 101.0), (1, 1): ("high", 101.0)}), ref)
    assert not two["passed"]                                                                    # 2/200 = 1%
    vol = compare(_days_store("ABC", 10, vol=1500), ref)
    assert vol["volume_mismatch_bars"] == 200 and vol["passed"]                                 # reported only
    assert not compare(_days_store("ABC", 10, vol=1500), ref, gate_volume=True)["passed"]
    strict = compare(_days_store("ABC", 10, bump={(0, 0): ("close", 100.05)}), ref,
                     price_tol_ticks=1, price_tol_pct=0.0, min_agreement=1.0, gate_volume=True)
    assert not strict["passed"]                                                                 # the plan's rule


def test_index_series_tolerate_no_mismatch():
    from research.data.cross_source import compare

    ref = _days_store("NIFTY50", 10)
    store = _days_store("IDX:NIFTY50", 10, bump={(0, 0): ("close", 100.05)})                   # 5 bps > 0.01%
    res = compare(store, ref)
    assert res["shared_symbols"] == ["IDX:NIFTY50"] and not res["passed"]
