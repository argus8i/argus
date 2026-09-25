"""P7.2 design helpers: design-window view, data-quality exclusions, z* statistics."""
from datetime import date

import pytest

from research.studies.p7_design import DesignWindowStore, quality_exclusions, zstar_from
from research.tests.synthetic import SyntheticSpec, make_store, sessions


def test_design_window_store_hides_outside_and_excluded_sessions():
    spec = SyntheticSpec(n_sessions=20)
    days = sessions(spec)
    base = make_store(spec)
    view = DesignWindowStore(base, days[2], days[10], excluded_days={days[5]}, excluded_symbol_days={("AAA", days[6])})
    assert view.sessions("BBB") == [d for d in days[2:11] if d != days[5]]
    assert days[6] not in view.sessions("AAA") and view.bars("AAA", days[6]) == []
    assert view.bars("BBB", days[12]) == [] and view.session_arrays("BBB", days[1]) is None
    assert view.bars("BBB", days[3]) == base.bars("BBB", days[3])


def test_quality_exclusions_split_special_sessions_from_symbol_gaps():
    d1, d2, out = date(2024, 3, 2), date(2024, 3, 4), date(2025, 1, 1)
    rows = [{"symbol": s, "session": d1.isoformat(), "valid": s == "A"} for s in "ABC"] + \
           [{"symbol": s, "session": d2.isoformat(), "valid": s != "C"} for s in "ABC"] + \
           [{"symbol": "A", "session": out.isoformat(), "valid": False}]
    special, bad = quality_exclusions(rows, date(2022, 1, 3), date(2024, 9, 30))
    assert special == {d1}                                     # 2 of 3 invalid >= 50%
    assert bad == {("B", d1), ("C", d1), ("C", d2)}            # the 2025 row is outside the window


def test_zstar_is_the_95th_percentile_rounded_to_2dp():
    scan = {(f"S{i}", date(2022, 5, 2 + i % 20)): float(i) / 100 for i in range(1, 1001)}
    out = zstar_from(scan)
    assert out["n_stock_days"] == 1000 and out["z_star"] == round(out["z_star_raw"], 2)
    assert out["z_star_raw"] == pytest.approx(9.5005, abs=1e-3) and out["gaussian_reference"] == 2.59
    assert zstar_from({})["z_star"] is None


def test_design_runs_refuse_live_history_by_default(tmp_path, monkeypatch, capsys):
    from research.studies import p7_design

    monkeypatch.setenv("TRACK2_HISTORY_DIR", str(tmp_path))       # a folder without SNAPSHOT.json
    monkeypatch.setenv("TRACK2_OUTPUTS_DIR", str(tmp_path / "out"))
    assert p7_design.main(["zstar"]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()                           # nothing written


class _Bar:
    def __init__(self, day, slot, o, c):
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        self.start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=ist) + timedelta(minutes=15 * slot)
        self.open, self.close = o, c


class _Store:
    def __init__(self, series):
        self.series = series

    def bars(self, sym, day):
        return [_Bar(day, s, o, c) for s, (o, c) in sorted(self.series[sym].items())]


def test_event_rows_residual_reversion_and_h_eff():
    import math

    from research.studies.p7_design import event_rows, event_summary

    d = date(2023, 5, 10)
    stock = {s: (100.0, 100.0) for s in range(24)}
    stock.update({5: (100.0, 100.0), 6: (99.8, 99.0), 22: (96.0, 96.0)})
    factor = {s: (1000.0, 1000.0) for s in range(24)}
    factor[22] = (1000.0, 1010.0)                               # factor +1% by bar 22
    store = _Store({"AAA": stock, "IDX:F": factor})
    cand = {("AAA", d): {"slot": 5, "beta": 1.5, "E": 0.02, "sg": -1, "factor_used": "IDX:F"}}
    rows = {r["h"]: r for r in event_rows(store, cand)}
    # h = 1: residual = ln(99/100) - 1.5 * 0; the stock was UP (E > 0), so reversion = +1.005%
    assert rows["1"]["h_eff"] == 1
    assert rows["1"]["reversion_bps"] == pytest.approx(-1e4 * math.log(0.99))
    assert rows["1"]["retrace_frac"] == pytest.approx(-math.log(0.99) / 0.02)
    assert rows["1"]["trade_gross_bps"] == pytest.approx(-1e4 * math.log(99.0 / 99.8))   # short from next open
    # EOD: h_eff = 22 - 5; the factor move is taken out with beta
    assert rows["EOD"]["h_eff"] == 17
    resid = math.log(96 / 100) - 1.5 * math.log(1010 / 1000)
    assert rows["EOD"]["reversion_bps"] == pytest.approx(-1e4 * resid)
    # a late signal clips h to the last fully held bar
    late = {("AAA", d): dict(cand[("AAA", d)], slot=16)}
    lrows = {r["h"]: r for r in event_rows(store, late)}
    assert lrows["8"]["h_eff"] == 6 and lrows["EOD"]["h_eff"] == 6
    summ = event_summary(event_rows(store, cand))
    assert summ["1"]["n"] == 1 and summ["1"]["reversion_bps"]["mean"] == pytest.approx(rows["1"]["reversion_bps"])


def test_event_rows_skip_missing_bars():
    from research.studies.p7_design import event_rows

    d = date(2023, 5, 10)
    stock = {s: (100.0, 100.0) for s in range(24) if s != 9}      # bar 9 missing
    store = _Store({"AAA": stock, "IDX:F": {s: (1.0, 1.0) for s in range(24)}})
    cand = {("AAA", d): {"slot": 5, "beta": 1.0, "E": -0.01, "sg": 1, "factor_used": "IDX:F"}}
    hs = {r["h"] for r in event_rows(store, cand)}
    assert "4" not in hs and {"1", "2", "8", "EOD"} <= hs
