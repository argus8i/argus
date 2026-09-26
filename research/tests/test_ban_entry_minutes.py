"""
Regression tests for the BAN_ENTRY_SHORT one-minute simulator and data contract (Codex review, 26 Sep 2026).
Written before the fix (Rule 8 v2 test-first gate). Each test pins one defect Codex found in the 15-minute path:
pre-entry exposure, the 15:00-open exit labelled 15:05, gap-through-limit fills, unbounded fills at a computed
VWAP, unverified raw files, and D-day eligibility / ban-list coverage.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from research.backtest.bars import tick_size
from research.studies import ban_entry as be

DAY = "2023-03-01"
T = tick_size(100.0)          # the NSE tick at these prices (the grid in research/backtest/bars.py)


def _m(hm: str, o: float, h: float, lo: float, c: float, v: float = 10_000) -> list:
    return [f"{DAY}T{hm}:00+05:30", o, h, lo, c, v, 0]


def _session(pre: list | None = None, body: list | None = None) -> list:
    """09:15-09:19 minutes (pre) followed by 09:20-15:29 minutes (body); defaults to flat at 100."""
    pre = pre or [_m(f"09:{15 + k}", 100, 100.2, 99.8, 100) for k in range(5)]
    mins = []
    for t in pd.date_range(f"{DAY} 09:20", f"{DAY} 15:29", freq="1min"):
        mins.append(_m(t.strftime("%H:%M"), 99.5, 99.7, 99.3, 99.5))
    by = {c[0][11:16]: c for c in (body or [])}
    return pre + [by.get(c[0][11:16], c) for c in mins]


def test_vwap_entry_is_released_at_0920_and_ignores_pre_entry_spike():
    pre = [_m("09:15", 100, 100.2, 99.8, 100), _m("09:16", 100, 110, 100, 100),     # spike before the fill
           _m("09:17", 100, 100.2, 99.8, 100), _m("09:18", 100, 100.2, 99.8, 100), _m("09:19", 100, 100.2, 99.8, 100)]
    r = be.simulate_short_minutes(_session(pre), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["disposition"] == "FILLED"
    assert r["entry_time"] == "09:20"
    assert r["exit_reason"] == "TIME_1505"
    assert r["pre_entry_stop_touch"] is True


def test_stop_after_entry_fills_at_trigger_plus_one_tick():
    body = [_m("10:00", 101, 104, 100.9, 103)]
    r = be.simulate_short_minutes(_session(body=body), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["exit_reason"] == "STOP"
    assert r["exit_time"] == "10:00"
    assert r["exit"] == pytest.approx(r["trigger"] + T)
    assert r["exit"] <= r["limit"]


def test_gap_through_sl_limit_is_not_filled_at_limit_but_covered_at_next_open():
    body = [_m("10:00", 106, 107, 105.5, 106.5), _m("10:01", 106.4, 106.8, 106, 106.2)]
    r = be.simulate_short_minutes(_session(body=body), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["exit_reason"] == "STOP_LIMIT_GAP_MARKET"
    assert r["exit_time"] == "10:01"
    assert r["exit"] == pytest.approx(106.4 + T)


def test_time_exit_is_the_1505_minute_open_not_the_1500_bar():
    body = [_m("15:00", 97, 97.2, 96.8, 97), _m("15:05", 96, 96.2, 95.8, 96)]
    r = be.simulate_short_minutes(_session(body=body), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["exit_reason"] == "TIME_1505"
    assert r["exit_time"] == "15:05"
    assert r["exit"] == pytest.approx(96 + T)


def test_entry_capacity_partial_and_unfilled():
    thin = [_m(f"09:{15 + k}", 100, 100.2, 99.8, 100, v=20) for k in range(5)]     # 100 shares in the window
    r = be.simulate_short_minutes(_session(thin), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["disposition"] == "PARTIAL"
    assert r["qty"] == 10 and r["planned_qty"] > 10
    tiny = [_m(f"09:{15 + k}", 100, 100.2, 99.8, 100, v=1) for k in range(5)]
    assert be.simulate_short_minutes(_session(tiny), "vwap_0915_0919", 0.03, prev_close=100.0)["disposition"] \
        == "UNFILLED_VOLUME"


def test_zero_volume_entry_window_has_no_entry_price():
    zero = [_m(f"09:{15 + k}", 100, 100.2, 99.8, 100, v=0) for k in range(5)]
    assert be.simulate_short_minutes(_session(zero), "vwap_0915_0919", 0.03, prev_close=100.0)["disposition"] \
        == "NO_ENTRY_PRICE"


def test_stop_in_a_minute_near_the_price_band_fills_at_that_minutes_high():
    body = [_m("11:00", 101, 110.2, 100.9, 109)]                   # reaches prev_close x 1.095 or more
    r = be.simulate_short_minutes(_session(body=body), "vwap_0915_0919", 0.03, prev_close=100.0)
    assert r["exit_reason"] == "BAND_STRESS"
    assert r["exit"] == pytest.approx(110.2 + T)
    assert r["band_touch"] is True


def test_auction_open_entry_is_exposed_from_the_0915_minute():
    pre = [_m("09:15", 100, 104, 99.8, 103)] + [_m(f"09:{16 + k}", 100, 100.2, 99.8, 100) for k in range(4)]
    r = be.simulate_short_minutes(_session(pre), "auction_open", 0.03, prev_close=100.0)
    assert r["entry_time"] == "09:15" and r["exit_reason"] == "STOP" and r["exit_time"] == "09:15"


def _manifest(tmp: Path, body: bytes, sha: str) -> Path:
    raw = tmp / "raw"
    (raw / "ABC").mkdir(parents=True)
    (raw / "ABC" / "1minute_2023-03-01_2023-03-31.json.gz").write_bytes(gzip.compress(body))
    rec = {"symbol": "ABC", "interval": "1minute", "window": "2023-03-01_2023-03-31", "status": 200,
           "body_sha256": sha, "file": "ABC/1minute_2023-03-01_2023-03-31.json.gz"}
    (raw / "manifest.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return raw


def test_verified_minutes_refuses_a_file_whose_hash_does_not_match(tmp_path):
    body = json.dumps({"data": {"candles": [_m("09:15", 1, 1, 1, 1)]}}).encode()
    raw = _manifest(tmp_path, body, "0" * 64)
    vm = be.VerifiedMinutes.from_manifest(raw / "manifest.jsonl", raw)
    with pytest.raises(be.MinuteDataRefused):
        vm.day("ABC", DAY)


def test_verified_minutes_reads_and_records_a_matching_file(tmp_path):
    body = json.dumps({"data": {"candles": [_m("09:16", 2, 2, 2, 2), _m("09:15", 1, 1, 1, 1)]}}).encode()
    raw = _manifest(tmp_path, body, hashlib.sha256(body).hexdigest())
    vm = be.VerifiedMinutes.from_manifest(raw / "manifest.jsonl", raw)
    rows = vm.day("ABC", DAY)
    assert [r[0][11:16] for r in rows] == ["09:15", "09:16"]
    assert vm.verified == {"ABC/1minute_2023-03-01_2023-03-31.json.gz": hashlib.sha256(body).hexdigest()}


def test_verified_minutes_refuses_unsafe_paths_and_conflicting_records(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    a = {"symbol": "ABC", "interval": "1minute", "window": "2023-03-01_2023-03-31", "status": 200,
         "body_sha256": "a" * 64, "file": "../evil.json.gz"}
    (raw / "manifest.jsonl").write_text(json.dumps(a) + "\n", encoding="utf-8")
    with pytest.raises(be.MinuteDataRefused):
        be.VerifiedMinutes.from_manifest(raw / "manifest.jsonl", raw)
    b = dict(a, file="ABC/x.json.gz")
    c = dict(b, body_sha256="b" * 64)
    (raw / "manifest.jsonl").write_text(json.dumps(b) + "\n" + json.dumps(c) + "\n", encoding="utf-8")
    with pytest.raises(be.MinuteDataRefused):
        be.VerifiedMinutes.from_manifest(raw / "manifest.jsonl", raw)


def _snap(tmp: Path, ban: dict, cov: list, elig: list) -> Path:
    (tmp / "events").mkdir()
    (tmp / "reference").mkdir()
    pd.DataFrame([(d, s) for d, ss in ban.items() for s in ss], columns=["trade_date", "symbol"]) \
        .to_parquet(tmp / "events" / "fo_ban.parquet")
    pd.DataFrame({"trade_date": cov}).to_parquet(tmp / "events" / "fo_ban_days.parquet")
    pd.DataFrame(elig, columns=["symbol", "session", "eligible"]).to_parquet(tmp / "reference" / "universe_daily.parquet")
    return tmp


def test_the_ban_itself_is_the_only_event_day_reason_allowed(tmp_path):
    days = ["2023-03-01", "2023-03-02"]
    rows = [("AAA", d, True, "ELIGIBLE") for d in days] + [("BBB", d, True, "ELIGIBLE") for d in days]
    rows = [(s, d, e and not (d == "2023-03-02"), "FO_BAN" if (s == "AAA" and d == "2023-03-02") else
             ("NOT_FNO_MEMBER" if d == "2023-03-02" else r)) for s, d, e, r in rows]
    (tmp_path / "events").mkdir()
    (tmp_path / "reference").mkdir()
    pd.DataFrame([("2023-03-02", "AAA"), ("2023-03-02", "BBB")], columns=["trade_date", "symbol"]) \
        .to_parquet(tmp_path / "events" / "fo_ban.parquet")
    pd.DataFrame({"trade_date": days}).to_parquet(tmp_path / "events" / "fo_ban_days.parquet")
    pd.DataFrame(rows, columns=["symbol", "session", "eligible", "reason"]) \
        .to_parquet(tmp_path / "reference" / "universe_daily.parquet")
    assert list(be.ban_entries(tmp_path, "2023-03-01", "2023-03-31").symbol) == ["AAA"]


def test_ban_entries_need_event_day_eligibility_and_known_coverage(tmp_path):
    days = ["2023-03-01", "2023-03-02", "2023-03-03"]
    elig = [(s, d, True) for s in ("AAA", "BBB", "CCC") for d in days]
    elig = [(s, d, not (s == "BBB" and d == "2023-03-02")) for s, d, _ in elig]       # BBB leaves F&O on D
    snap = _snap(tmp_path, {"2023-03-02": ["AAA", "BBB"], "2023-03-03": ["CCC"]},
                 cov=["2023-03-01", "2023-03-02"], elig=elig)                          # 03-03 coverage unknown
    counts: dict = {}
    ev = be.ban_entries(snap, "2023-03-01", "2023-03-31", counts=counts)
    assert list(ev.symbol) == ["AAA"]
    assert counts["dropped_event_day_ineligible"] == 1
    assert counts["dropped_unknown_coverage"] == 1
