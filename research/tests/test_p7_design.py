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
