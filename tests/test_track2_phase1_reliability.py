"""Adversarial contracts: no quote-derived fills or malformed sizing passes."""
from dataclasses import replace
import os
import shutil
import tempfile
import pytest
from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine as Engine
from antigravity.models.two_tranche_exit_model import TwoTrancheExitModel as Exit, TrancheStatus
from antigravity.models.track2_surveillance_monitor import Track2SurveillanceMonitor
import antigravity.daemons.track2_live_radar as radar_module
from antigravity.daemons.track2_live_radar import Track2LiveRadar
from antigravity.models.track2_universe_scanner import Track2UniverseScanner


@pytest.mark.parametrize("bad", [None, True, False, float("nan"), float("inf"), -1, "100"])
@pytest.mark.parametrize("field", ["entry_price", "or_low", "atr14", "dtv_med20_cr", "risk_budget_rs", "max_notional_rs"])
def test_sizing_rejects_malformed(field, bad):
    args = dict(entry_price=100.0, or_low=90.0, atr14=10.0, dtv_med20_cr=100.0)
    args[field] = bad
    assert Engine.calculate_position_size(**args).shares == 0


@pytest.mark.parametrize("bad", ["BOGUS", "SLM", "SL_L", "", True, 10])
def test_unknown_execution_type_rejected(bad):
    assert Engine.calculate_position_size(100, 90, 10, 100, order_execution_type=bad).shares == 0


def test_default_conservative_and_structural_stop_preserved():
    size = Engine.calculate_position_size(100, 90, 10, 100)
    assert size.order_type == "SL_LIMIT_NSE"
    assert size.execution_type_defaulted is True
    assert size.tranche_allocation.initial_stop == size.stop_price
    assert size.limit_exit_price < size.stop_price
    assert not size.qualification_eligible


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, "0"])
def test_portfolio_malformed_risk_rejected(bad):
    result = Engine.check_portfolio_risk_capacity(bad, proposed_sector="Power", existing_sector_counts={})
    assert not result["allowed"]


def test_missing_sector_and_negative_counts_fail_closed():
    assert not Engine.check_portfolio_risk_capacity(100)["allowed"]
    assert not Engine.check_portfolio_risk_capacity(100, proposed_sector="Power", existing_sector_counts={"Power": -1})["allowed"]


@pytest.mark.parametrize("bad", [True, 1.5, 0, -1, float("nan"), float("inf"), "10"])
def test_shares_must_be_positive_integer(bad):
    with pytest.raises(ValueError):
        Exit.allocate_tranches(100, 90, bad)


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -1, "100"])
def test_quote_validation(bad):
    with pytest.raises(ValueError):
        Exit.update_state("TEST", Exit.allocate_tranches(100, 90, 10), bad, 100)


def test_ambiguous_bar_does_not_select_profitable_ordering():
    state = Exit.update_state("TEST", Exit.allocate_tranches(100, 90, 10), 110, 120, tick_low=85)
    assert state.combined_risk_state == "AMBIGUOUS_ORDER"
    assert state.t1_status == TrancheStatus.PENDING_STOP_EXIT
    assert state.total_realized_pnl == 0


def test_fabricated_terminal_or_wrong_symbol_rejected():
    alloc = Exit.allocate_tranches(100, 90, 10)
    state = Exit.update_state("TEST", alloc, 100, 100)
    for invalid in [replace(state, symbol="OTHER"), replace(state, t1_status=TrancheStatus.TARGET_FILLED)]:
        with pytest.raises(ValueError):
            Exit.update_state("TEST", alloc, 120, 120, current_state=invalid)


def test_legacy_pending_never_resurrects():
    alloc = Exit.allocate_tranches(100, 90, 10)
    state = Exit.update_state("TEST", alloc, 100, 100)
    state = replace(state, t1_status=TrancheStatus.UNFILLED_TRIGGERED)
    later = Exit.update_state("TEST", alloc, 120, 120, current_state=state)
    assert later.t1_status == TrancheStatus.UNFILLED_TRIGGERED
    assert later.total_realized_pnl == 0


def test_target_pending_then_stop_becomes_stop_priority_conflict():
    allocation = Exit.allocate_tranches(100, 90, 10)
    target = Exit.update_state("TEST", allocation, 120, 120)
    stopped = Exit.update_state("TEST", allocation, 85, 120, current_state=target)
    assert stopped.t1_status == TrancheStatus.PENDING_STOP_EXIT
    assert stopped.total_realized_pnl == 0
    assert stopped.combined_risk_state == "OCO_CONFLICT_PENDING_STOP_PRIORITY"


def test_numpy_real_scalars_are_supported_when_available():
    np = pytest.importorskip("numpy")
    sizing = Engine.calculate_position_size(
        np.float64(100), np.float64(90), np.float64(10), np.float64(100))
    allocation = Exit.allocate_tranches(np.float64(100), np.float64(90), np.int64(10))
    assert sizing.shares > 0
    assert allocation.total_shares == 10


def test_sector_names_normalized_and_unrounded_cap_enforced():
    sector = Engine.check_portfolio_risk_capacity(
        100, proposed_sector=" banks ", existing_sector_counts={"Banks": 2})
    boundary = Engine.check_portfolio_risk_capacity(
        0.004, existing_open_risk_rs=6000, proposed_sector="Power",
        existing_sector_counts={})
    assert sector["allowed"] is False
    assert boundary["allowed"] is False


def test_missing_regime_never_emits_buy_or_qualified_language():
    result = Engine.evaluate_15m_orb_breakout(
        "TEST", 101, 100, 99, 300, 100, 2, regime_snapshot=None)
    assert result["signal"] == "NO_ENTRY_DATA_INVALID"
    assert result.get("qualification_eligible") is False
    assert "QUALIFIED" not in result["reason"]


def test_rounded_stop_and_zero_share_caps_return_rejections():
    rounded = Engine.calculate_position_size(100, 99.999, 0.0001, 100)
    zero = Engine.calculate_position_size(1000000, 999000, 100, 0.000001,
                                          max_notional_rs=1)
    assert rounded.shares == 0
    assert rounded.constrained_by == "ROUNDED_STOP_NOT_BELOW_ENTRY"
    assert zero.shares == 0
    assert zero.constrained_by == "ZERO_SHARE_AFTER_CAPS"


def test_allocation_round_trip_after_input_rounding():
    allocation = Exit.allocate_tranches(100.004, 90.004, 10)
    state = Exit.update_state("TEST", allocation, 100, 100)
    assert allocation.entry_price == 100.0
    assert state.t1_status == TrancheStatus.ACTIVE_INITIAL_STOP


def test_nonzero_slippage_rejected_until_fill_ledger_exists():
    allocation = Exit.allocate_tranches(100, 90, 10)
    with pytest.raises(ValueError, match="INVALID_EXECUTION_INPUT"):
        Exit.update_state("TEST", allocation, 85, 100, slippage_pts=0.5)


def test_radar_surveillance_path_is_unmocked_and_fail_closed():
    directory = tempfile.mkdtemp(prefix="radar_surv_")
    try:
        monitor = Track2SurveillanceMonitor(os.path.join(directory, "history.json"))
        radar = Track2LiveRadar(
            surveillance_monitor=monitor,
            surveillance_snapshot_path=os.path.join(directory, "missing.json"),
            surveillance_log_path=os.path.join(directory, "poller.log"))
        report = radar.audit_daily_surveillance("2026-09-21")
        assert report["qualified_count"] == 0
        assert report["qualification_eligible"] is False
        assert report["provenance"]["verified"] is False
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_log_writer_cannot_record_frozen_payload(monkeypatch):
    directory = tempfile.mkdtemp(prefix="radar_log_")
    try:
        csv_path = os.path.join(directory, "track2.csv")
        monkeypatch.setattr(radar_module, "CSV_LOG_PATH", csv_path)
        payload = {
            "session_date": "2026-09-21", "qualification_eligible": False,
            "candidates": [{"symbol": "TEST", "signal": "RESEARCH_ORB_HYPOTHESIS"}]
        }
        assert radar_module.append_session_summary_to_logs(payload) is False
        assert not os.path.exists(csv_path)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_scanner_to_radar_research_contract_stays_frozen(monkeypatch):
    directory = tempfile.mkdtemp(prefix="radar_contract_")
    try:
        session_date = "2026-09-19"
        history_path = os.path.join(directory, "history.json")
        universe_path = os.path.join(directory, "dynamic_universe.json")
        status_path = os.path.join(directory, "status.json")
        monitor = Track2SurveillanceMonitor(history_path)
        scanner = Track2UniverseScanner(surveillance_monitor=monitor)
        candidate = {
            "symbol": "TEST", "ticker": "TEST.NS", "basket": "R",
            "is_fno": True, "mcap_cr": 10000.0, "dtv_med20_cr": 100.0,
            "beta": 1.5, "atr14_pct": 4.0, "inst_pct": 20.0,
            "checked_at": "2026-09-19 08:30:00", "asm_stage": 0,
            "gsm_stage": 0, "band_pct": 0.0
        }
        saved = scanner.scan_and_save(
            output_path=universe_path, universe=[candidate], session_date=session_date,
            pre_open_data={"TEST": {"pre_open_volume": 200, "median_pre_open_volume": 100,
                                     "gap_pct": 1.0}})
        assert saved["candidates"] == []
        assert saved["total_qualified"] == 0
        assert saved["qualification_eligible"] is False
        assert len(saved["research_candidates"]) == 1

        monkeypatch.setattr(radar_module, "DYNAMIC_UNIVERSE_PATH", universe_path)
        monkeypatch.setattr(radar_module, "SHARED_DIR", directory)
        monkeypatch.setattr(radar_module, "RADAR_STATUS_PATH", status_path)
        monkeypatch.setattr(radar_module, "fetch_15m_candles", lambda *args, **kwargs: [])
        radar = Track2LiveRadar(
            surveillance_monitor=monitor,
            surveillance_snapshot_path=os.path.join(directory, "missing.json"),
            surveillance_log_path=os.path.join(directory, "poller.log"))
        output = radar.scan_session(session_date)
        assert output["qualification_eligible"] is False
        assert output["verified_sessions"] == 0
        assert output["verified_fillable_entries"] == 0
        assert output["active_portfolio"] == []
        assert len(output["candidates"]) == 1
        assert output["candidates"][0]["status"] == "DISQUALIFIED_SURVEILLANCE"
        assert output["candidates"][0]["qualified"] is False
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_current_production_artifacts_are_semantically_quarantined():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    import json
    with open(os.path.join(repo_root, "shared", "track2_liquid", "dynamic_universe.json"),
              "r", encoding="utf-8") as handle:
        universe = json.load(handle)
    with open(os.path.join(repo_root, "shared", "track2_liquid", "live_orb_status.json"),
              "r", encoding="utf-8") as handle:
        status = json.load(handle)
    import csv
    with open(os.path.join(repo_root, "CHATGPT", "track2_orb_paper_log.csv"),
              "r", encoding="utf-8", newline="") as handle:
        legacy_rows = list(csv.DictReader(handle))
    assert universe["qualification_eligible"] is False
    assert universe["total_qualified"] == 0 and universe["candidates"] == []
    assert status["qualification_eligible"] is False
    assert status["active_portfolio"] == []
    # A research-only radar may display candidates, but every row must remain
    # explicitly non-qualifying and must not create a portfolio or paper fill.
    assert all(candidate["research_only"] is True for candidate in status["candidates"])
    assert all(candidate["qualified"] is False for candidate in status["candidates"])
    assert all(candidate["qualification_eligible"] is False for candidate in status["candidates"])
    # Legacy observations may remain for research history, but none may claim a
    # position, fill, or realized execution under the current evidence system.
    assert all(int(row["shares"]) == 0 for row in legacy_rows)
    assert all(float(row["notional_rs"]) == 0.0 for row in legacy_rows)
    assert all(row["signal"] == "ZERO_SIGNAL" for row in legacy_rows)
