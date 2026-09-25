"""
P3 data layer tests (plan P3.1-P3.9): ingest, parquet store, HoldoutGuard, validation, universe,
sector map, cross-source comparison and the pre-registration loader.
All data here is synthetic; nothing reads the real history.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from research.backtest.bars import Bar, CandleStore, DailyBar
from research.data import holdout, ingest_json, provenance, session_shape
from research.data.provenance import SourceNotAllowedError
from research.data.cross_source import compare
from research.data.holdout import HoldoutGuard, HoldoutLockedError, QAStoreError
from research.data.sector_map import NIFTY, SECTOR_TO_INDEX, build_rows
from research.data.store_parquet import ParquetCandleStore
from research.data.universe_build import TableUniverse, eligibility
from research.data.validate import summarise, validate_store
from research.studies import prereg_io

IST = timezone(timedelta(hours=5, minutes=30))
PRE_CAS = date(2026, 9, 1) - timedelta(days=60)      # 2026-07-03: inside the plan holdout
POST_CAS = date(2026, 9, 1)


def _session_bars(day: date, n: int, base: float = 100.0, vol: int = 1000, extra=()):
    out = []
    for k in range(n):
        t = datetime.combine(day, session_shape.slot_start(k), IST)
        p = base + 0.05 * k
        out.append({"timestamp": t.isoformat(), "open": p, "high": p + 0.1, "low": p - 0.1, "close": p + 0.05,
                    "volume": vol})
    out.extend(extra)
    return out


def _payload(fetched="2026-09-02 14:00:00"):
    day1, day2 = date(2026, 8, 31), date(2026, 9, 1)
    t_mis = datetime.combine(day2, datetime.strptime("13:47", "%H:%M").time(), IST).isoformat()
    stock = {"source": "YahooFinance_ChartAPI_v8", "requested_at": fetched,
             "bars": _session_bars(day1, 25) + _session_bars(day2, 24,
                                                          extra=[{"timestamp": t_mis, "open": 1, "high": 1, "low": 1,
                                                                  "close": 1, "volume": 1}]),
             "daily_bars": [{"timestamp": f"{d.isoformat()}T00:00:00+05:30", "open": 100, "high": 102, "low": 98,
                             "close": 101, "volume": 24000} for d in (day1, day2)]}
    # day 3 is incomplete at fetch time (fetched 2 Sep 14:00)
    day3 = date(2026, 9, 2)
    stock["bars"] += _session_bars(day3, 18)
    stock["daily_bars"].append({"timestamp": f"{day3.isoformat()}T00:00:00+05:30", "open": 1, "high": 1, "low": 1,
                                "close": 1, "volume": 1})
    idx = {"source": "YahooFinance_^NSEMDCP50", "bars": _session_bars(day1, 25, vol=0) + _session_bars(day2, 25, vol=0),
           "daily_bars": []}
    return {"local_write_time": fetched, "symbols": {"ABC": stock, "MIDCPNIFTY": idx}}


# ------------------------------------------------------------------ session shape
def test_expected_bar_counts_by_class_and_cas():
    assert session_shape.expected_bar_count("TRADABLE", date(2026, 7, 31)) == 25
    assert session_shape.expected_bar_count("TRADABLE", date(2026, 8, 3)) == 24
    assert session_shape.expected_bar_count("INDEX", date(2026, 8, 3)) == 25
    assert session_shape.slot_of(datetime.strptime("13:47", "%H:%M").time()) is None
    assert session_shape.slot_of(datetime.strptime("15:15", "%H:%M").time()) == 24


# ------------------------------------------------------------------ ingest + store
def test_ingest_drops_misaligned_incomplete_and_cas_bars_and_renames_by_ticker(tmp_path):
    m = ingest_json.ingest_payload(_payload(), input_name="x.json", input_sha256="0" * 64, label="t",
                                   out_root=tmp_path)
    t = m["totals"]
    assert t["MISALIGNED"] == 1
    assert t["INCOMPLETE_SESSION"] == 18
    assert t["CAS_AUCTION_BAR"] == 1                # the stock's 25th bar on 31 Aug (post-CAS)
    assert t["DAILY_INCOMPLETE_SESSION"] == 1
    syms = {r["symbol"]: r for r in m["symbols"]}
    assert "IDX:NIFTYMIDCAP50" in syms and "IDX:MIDCPNIFTY" not in syms, "canonical name must follow the ticker"
    assert (tmp_path / "manifests" / "t.json").exists()
    assert m["source_classes"] == {"YAHOO_CHART_V8": 2} and m["qa_only_symbols"] == ["ABC", "IDX:NIFTYMIDCAP50"]
    with pytest.raises(SourceNotAllowedError):                 # Yahoo is QA_ONLY: strategy mode refuses it
        ParquetCandleStore(tmp_path)
    store = ParquetCandleStore(tmp_path, mode="QA")
    assert store.sources == {"ABC": "YAHOO_CHART_V8", "IDX:NIFTYMIDCAP50": "YAHOO_CHART_V8"}
    assert store.sessions("ABC") == [date(2026, 8, 31), date(2026, 9, 1)]
    assert all(len(store.bars("ABC", d)) == 24 for d in store.sessions("ABC"))
    assert len(store.bars("IDX:NIFTYMIDCAP50", date(2026, 8, 31))) == 25
    assert store.kind("IDX:NIFTYMIDCAP50") == "INDEX" and store.kind("ABC") == "TRADABLE"
    assert [d.day for d in store.daily("ABC")] == [date(2026, 8, 31), date(2026, 9, 1)]
    assert store.daily_before("ABC", date(2026, 9, 1))[-1].day == date(2026, 8, 31)


def test_missing_volume_is_kept_as_minus_one_and_flagged(tmp_path):
    p = _payload()
    p["symbols"]["ABC"]["bars"][30]["volume"] = None
    ingest_json.ingest_payload(p, input_name="x", input_sha256="0" * 64, label="t", out_root=tmp_path)
    qa = ParquetCandleStore(tmp_path, mode="QA")
    rows = [r for r in validate_store(qa) if r["symbol"] == "ABC"]
    bad = [r for r in rows if not r["valid"]]
    assert len(bad) == 1 and "VOLUME_GAP" in bad[0]["reasons"] and bad[0]["missing_vol_bars"] == 1


# ------------------------------------------------------------------ holdout guard
def _write_bars(tmp_path, days, source="SYNTHETIC"):
    sym = {"source": source, "bars": [], "daily_bars": []}
    for d in days:
        sym["bars"] += _session_bars(d, session_shape.expected_bar_count("TRADABLE", d))
        sym["daily_bars"].append({"timestamp": f"{d.isoformat()}T00:00:00+05:30", "open": 100, "high": 101,
                                  "low": 99, "close": 100, "volume": 1000})
    payload = {"symbols": {"ABC": sym}}
    ingest_json.ingest_payload(payload, input_name="x", input_sha256="0" * 64, label="t", out_root=tmp_path)
    return payload


def test_guard_hides_holdout_in_strategy_mode_and_qa_logs_reads(tmp_path):
    days = [date(2024, 9, 30), date(2025, 1, 2), date(2026, 8, 3)]
    _write_bars(tmp_path, days)
    s = ParquetCandleStore(tmp_path)                                  # default guard, no lock
    assert s.sessions("ABC") == [date(2024, 9, 30), date(2026, 8, 3)]
    assert [d.day for d in s.daily("ABC")] == [date(2024, 9, 30), date(2026, 8, 3)]
    with pytest.raises(HoldoutLockedError):
        s.bars("ABC", date(2025, 1, 2))
    qa = ParquetCandleStore(tmp_path, mode="QA")
    assert qa.sessions("ABC") == days
    qa.bars("ABC", date(2025, 1, 2))
    assert qa.guard.qa_log == ["ABC 2025-01-02"]


@pytest.mark.parametrize("entry,expected", [
    ({"source": "YahooFinance_ChartAPI_v8"}, "YAHOO_CHART_V8"),
    ({"source": "YahooFinance_^NSEI"}, "YAHOO_CHART_V8"),
    ({"source_url": "https://kite.zerodha.com/oms/instruments/historical/1/15minute"}, "KITE_WEB_SESSION"),
    ({"source": "DHAN_API_V2"}, "DHAN_API_V2"),
    ({"source_url": "https://api.dhan.co/v2/charts/intraday"}, "DHAN_API_V2"),
    ({"source": "upstox_1min", "source_url": "https://huggingface.co/datasets/x/y"}, "HF_UPSTOX_MIRROR"),
    ({"source": "SYNTHETIC"}, "SYNTHETIC"),
    ({"source": "test"}, "UNKNOWN"),
    ({}, "UNKNOWN"),
])
def test_provenance_classes(entry, expected):
    assert provenance.classify(entry) == expected
    assert provenance.strategy_eligible(expected) == (expected in ("DHAN_API_V2", "SYNTHETIC"))


@pytest.mark.parametrize("source", ["YahooFinance_ChartAPI_v8", "KITE", "test", "upstox"])
def test_qa_only_sources_never_open_in_strategy_mode(tmp_path, source):
    _write_bars(tmp_path, [date(2026, 8, 3)], source=source)
    with pytest.raises(SourceNotAllowedError):
        ParquetCandleStore(tmp_path)
    assert ParquetCandleStore(tmp_path, mode="QA").sessions("ABC") == [date(2026, 8, 3)]


def test_one_qa_only_symbol_blocks_the_whole_history(tmp_path):
    _write_bars(tmp_path, [date(2026, 8, 3)])                         # SYNTHETIC: eligible
    assert ParquetCandleStore(tmp_path).sessions("ABC") == [date(2026, 8, 3)]
    payload = {"symbols": {"XYZ": {"source": "YahooFinance_ChartAPI_v8",
                                   "bars": _session_bars(date(2026, 8, 3), 24), "daily_bars": []}}}
    ingest_json.ingest_payload(payload, input_name="y", input_sha256="0" * 64, label="y", out_root=tmp_path)
    with pytest.raises(SourceNotAllowedError):
        ParquetCandleStore(tmp_path)


def test_parquet_without_source_column_is_unknown(tmp_path):
    import pandas as pd

    d = tmp_path / "bars_15m"
    d.mkdir()
    pd.DataFrame([{"symbol": "ABC", "session": "2026-08-03", "slot": 0, "start_epoch": 0, "open": 1.0,
                   "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}]).to_parquet(d / "ABC.parquet", index=False)
    with pytest.raises(SourceNotAllowedError):
        ParquetCandleStore(tmp_path)
    assert ParquetCandleStore(tmp_path, mode="QA").sources == {"ABC": "UNKNOWN"}


def test_engine_and_view_refuse_qa_stores(tmp_path):
    from research.backtest.engine import BacktestEngine
    from research.backtest.strategies import PointInTimeView
    from research.backtest.universe import PointInTimeUniverse

    _write_bars(tmp_path, [date(2026, 8, 3)])
    qa = ParquetCandleStore(tmp_path, mode="QA")
    uni = PointInTimeUniverse.assumed_static(["ABC"], date(2026, 1, 1), date(2026, 12, 31))
    with pytest.raises(QAStoreError):
        BacktestEngine(qa, uni, [])
    with pytest.raises(QAStoreError):
        PointInTimeView(qa, date(2026, 8, 3), datetime(2026, 8, 3, 10, tzinfo=IST))
    json_qa = CandleStore({}, {}, mode="QA")
    with pytest.raises(QAStoreError):
        BacktestEngine(json_qa, uni, [])


def test_legacy_json_loader_goes_through_the_guard(tmp_path):
    days = [date(2025, 1, 2), date(2026, 8, 3)]
    sym = {"bars": [], "daily_bars": []}
    for d in days:
        sym["bars"] += _session_bars(d, 24)
        sym["daily_bars"].append({"timestamp": f"{d.isoformat()}T00:00:00+05:30", "open": 1, "high": 1, "low": 1,
                                  "close": 1, "volume": 1})
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"symbols": {"ABC": sym}}), encoding="utf-8")
    s = CandleStore.from_historical_json(f)
    assert s.sessions("ABC") == [date(2026, 8, 3)]
    assert [d.day for d in s.daily("ABC")] == [date(2026, 8, 3)]
    assert s.quality_report()["invalid_bars"] == 0
    qa = CandleStore.from_historical_json(f, mode="QA")
    assert qa.sessions("ABC") == days and qa.mode == "QA"


def test_committed_prereg_window_matches_the_plan_and_is_not_locked():
    g = HoldoutGuard.from_prereg()
    assert g.window == holdout.PLAN_HOLDOUT
    assert not g.unlocked and g.reason in ("LOCK_MISSING", "STATUS_NOT_LOCKED")


def test_lock_verification(tmp_path):
    src = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    y = tmp_path / "resid_rev_v1.yaml"
    # Write LF bytes explicitly: write_text() on Windows already emits CRLF, and converting that again
    # below would produce CR CR LF, which is a genuinely different file.
    lf_text = src.read_bytes().replace(b"\r\n", b"\n").replace(b"status: DRAFT", b"status: LOCKED")
    y.write_bytes(lf_text)
    lock = {"id": "RESID_REV_v1", "yaml_sha256": prereg_io.normalised_sha256(y), "commit": "a" * 40,
            "locked_at": "2026-10-01T10:00:00+05:30"}
    y.with_suffix(".lock").write_text(json.dumps(lock), encoding="utf-8")
    assert prereg_io.verify_lock(y).valid
    g = HoldoutGuard.from_prereg(y)
    assert g.unlocked and not g.hidden(date(2025, 1, 2))
    # CRLF on a Windows checkout hashes the same
    y.write_bytes(lf_text.replace(b"\n", b"\r\n"))
    assert prereg_io.verify_lock(y).valid
    # a stray CR is a real change, not a line ending
    y.write_bytes(lf_text.replace(b"\n", b"\r\r\n", 1))
    assert prereg_io.verify_lock(y).reason == "HASH_MISMATCH"
    y.write_bytes(lf_text)
    # any edit after locking invalidates it
    y.write_text(y.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert prereg_io.verify_lock(y).reason == "HASH_MISMATCH"
    y.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    assert prereg_io.verify_lock(y).reason == "STATUS_NOT_LOCKED"
    lock["commit"] = "not-a-commit"
    y.with_suffix(".lock").write_text(json.dumps(lock), encoding="utf-8")
    assert prereg_io.verify_lock(y).reason == "COMMIT_INVALID"


# ------------------------------------------------------------------ prereg loader
def test_prereg_loader_matches_pyyaml_when_available():
    yaml = pytest.importorskip("yaml")
    for f in sorted(prereg_io.PREREG_DIR.glob("*.yaml")):
        assert prereg_io.load(f) == yaml.safe_load(f.read_text(encoding="utf-8")), f.name


def test_prereg_loader_known_values_and_errors():
    spec = prereg_io.load(prereg_io.PREREG_DIR / "resid_rev_v1.yaml")
    assert spec["id"] == "RESID_REV_v1" and spec["status"] == "DRAFT"
    assert spec["signal"]["z_star"] is None and spec["trade"]["hold_bars"] is None
    assert spec["signal"]["rvol_band"] == [0.7, 1.5]
    assert spec["trade"]["targets"] == [{"retrace_of_E": 0.5, "fraction": 0.5}, {"retrace_of_E": 1.0, "fraction": 0.5}]
    assert spec["calibration"]["beta"]["clip"] == [0.3, 2.5]
    assert spec["data"]["holdout"] == ["2024-10-01", "2026-07-31"]
    with pytest.raises(prereg_io.PreregFormatError):
        prereg_io.loads("a: 1\na: 2\n")
    with pytest.raises(prereg_io.PreregFormatError):
        prereg_io.loads("a: [1, 2\n")
    with pytest.raises(prereg_io.PreregFormatError):
        prereg_io.loads("a:\n  - b: 1\n")


# ------------------------------------------------------------------ validation
def test_validation_counts_and_contiguity(tmp_path):
    d = date(2026, 8, 3)
    bars = _session_bars(d, 24)
    del bars[5]
    payload = {"symbols": {"ABC": {"source": "SYNTHETIC", "bars": bars, "daily_bars": []},
                           "XYZ": {"source": "SYNTHETIC", "bars": _session_bars(d, 24), "daily_bars": []}}}
    ingest_json.ingest_payload(payload, input_name="x", input_sha256="0" * 64, label="t", out_root=tmp_path)
    rows = {r["symbol"]: r for r in validate_store(ParquetCandleStore(tmp_path, mode="QA"))}
    assert not rows["ABC"]["valid"] and "BAR_COUNT_23" in rows["ABC"]["reasons"] and "NON_CONTIGUOUS" in rows["ABC"]["reasons"]
    assert rows["XYZ"]["valid"]
    s = summarise(list(rows.values()))
    assert s["tradables_with_valid_frac_ge"]["count"] == 1
    with pytest.raises(ValueError):
        validate_store(ParquetCandleStore(tmp_path))


# ------------------------------------------------------------------ universe
def test_dtv20_uses_only_prior_bars_and_fails_closed():
    days = [date(2026, 8, 1) + timedelta(days=k) for k in range(25)]
    daily = [(d, 100.0, 4_000_000 if k < 24 else 1) for k, d in enumerate(days)]      # Rs 40 Cr/day
    rows = eligibility(daily, [days[20], days[24], days[19]], member=True)
    by = {r["session"]: r for r in rows}
    assert by[days[20]]["eligible"] and math.isclose(by[days[20]]["dtv20_cr"], 40.0)
    assert by[days[24]]["eligible"], "the last day's own turnover must not enter its DTV20"
    assert by[days[19]]["reason"] == "INSUFFICIENT_HISTORY"
    thin = [(d, 100.0, 1_000_000) for d in days]                                       # Rs 10 Cr/day
    assert eligibility(thin, [days[22]], member=True)[0]["reason"] == "DTV20_BELOW_30CR"
    assert eligibility(daily, [days[22]], member=False)[0]["reason"] == "NOT_FNO_MEMBER"
    cheap = [(d, 9.0, 50_000_000) for d in days]
    assert eligibility(cheap, [days[22]], member=True)[0]["reason"] == "PRICE_BELOW_FLOOR"
    u = TableUniverse({("ABC", days[20]): (True, "ELIGIBLE")})
    assert u.check("abc", days[20]) == (True, "ELIGIBLE")
    assert u.check("ABC", days[21]) == (False, "NOT_IN_UNIVERSE_TABLE")
    assert u.check("ABC", days[20], price=5.0) == (False, "PRICE_BELOW_FLOOR")


# ------------------------------------------------------------------ sector map
def test_sector_map_falls_back_to_nifty_and_labels_sources():
    rows = {r["symbol"]: r for r in build_rows({"HDFCBANK": "BANKING_PRIVATE", "BEL": "DEFENSE_AEROSPACE"}, "2026-09-25")}
    assert rows["HDFCBANK"]["factor_index"] == SECTOR_TO_INDEX["BANKING_PRIVATE"]
    assert rows["HDFCBANK"]["source"].startswith("STATIC_CLASSIFICATION")
    assert rows["BEL"]["factor_index"] == NIFTY and rows["BEL"]["source"].startswith("FALLBACK_NIFTY50")
    assert all(v.startswith("IDX:") for v in SECTOR_TO_INDEX.values())


# ------------------------------------------------------------------ cross-source
def _store_from(days, shift_min=0, px=0.0, vol_mult=1.0, mode="QA"):
    intraday, daily = {}, {}
    for d in days:
        bars = []
        for k in range(24):
            t = datetime.combine(d, session_shape.slot_start(k), IST) + timedelta(minutes=shift_min)
            p = 100 + px
            bars.append(Bar("ABC", t, 15, p, p + 0.1, p - 0.1, p, int(1000 * vol_mult)))
        intraday.setdefault("ABC", {})[d] = bars
    return CandleStore(intraday, daily, mode=mode)


def test_cross_source_passes_on_identical_data_and_flags_offsets_and_prices():
    days = [date(2026, 8, 3)]
    assert compare(_store_from(days), _store_from(days))["passed"]
    off = compare(_store_from(days, shift_min=15), _store_from(days))
    assert off["one_bar_offset_sessions"] == 1 and off["start_mismatch_sessions"] == 1 and not off["passed"]
    gap = compare(_store_from(days, shift_min=5), _store_from(days))
    assert gap["one_bar_offset_sessions"] == 0 and gap["start_mismatch_sessions"] == 1
    res = compare(_store_from(days, px=0.05), _store_from(days))
    assert not res["passed"] and res["price_mismatch_bars"]["close"] == 24
    assert compare(_store_from(days, px=0.01), _store_from(days))["passed"], "one tick is inside tolerance"
    assert compare(_store_from(days, vol_mult=1.02), _store_from(days))["volume_mismatch_bars"] == 24
