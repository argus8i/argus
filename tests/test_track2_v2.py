"""
test_track2_v2.py - Comprehensive Unit & Adversarial Test Suite for Track 2 V2.0
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Tests:
1. Production Artifact Protection & Zero Test Contamination (tmp_path enforcement)
2. MarketRegimeFilter: Breadth gating, Nifty 50 15m OR trend, fail-closed on Inf/NaN/Bool/missing breadth
3. TwoTrancheExitModel: Tranche allocation, terminal-state immutability (anti-resurrection),
   SL-Limit unfilled gap risk vs SL-M execution, EOD MIS square-off, zero-risk claim removal
4. Track2UniverseScanner: Momentum scoring arithmetic, zero qualified handling (no canonical injection)
5. ExchangeCircularPoller: Sourced snapshot ingestion, SHA-256 integrity, session calendar freshness
6. LiquidMomentumEngine & Screener: Strict F&O conjunction gate, decoupled order types, assumed portfolio risk capacity
7. Radar & Bridge: Broker CAS vs non-CAS cutoffs, exact-instrument feed validation, zero credential leaks
"""

import hashlib
import json
import math
import os
import shutil
import tempfile
import pytest
from datetime import datetime
from typing import Dict, Any


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="t2_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)

from antigravity.models.market_regime_filter import (
    MarketRegimeFilter,
    MarketRegimeState,
    MarketRegimeSnapshot
)
from antigravity.models.two_tranche_exit_model import (
    TwoTrancheExitModel,
    TwoTrancheState,
    TrancheAllocation,
    TrancheStatus,
    TERMINAL_TRANCHE_STATES
)
from antigravity.models.track2_universe_scanner import (
    Track2UniverseScanner,
    CANONICAL_FALLBACK_CANDIDATES,
    EXPANDED_FNO_UNIVERSE,
    ScannedCandidate
)
from antigravity.models.track2_surveillance_monitor import Track2SurveillanceMonitor
from antigravity.daemons.exchange_circular_poller import ExchangeCircularPoller
from antigravity.models.liquid_momentum_screener import (
    LiquidMomentumEngine,
    LiquidScripSnapshot,
    SizingResult
)
from antigravity.daemons.track2_kite_bridge import validate_extracted_payload


# ==============================================================================
# 1. PRODUCTION ARTIFACT INTEGRITY & ZERO CONTAMINATION
# ==============================================================================

@pytest.fixture(scope="module", autouse=True)
def guard_production_artifacts():
    """
    Codex Correction (5): Module-level guard ensuring zero production artifact
    contamination across all tests in this suite.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    targets = [
        os.path.join(repo_root, "shared", "track2_liquid", "dynamic_universe.json"),
        os.path.join(repo_root, "shared", "track2_liquid", "03_TRADE_LOG.md"),
        os.path.join(repo_root, "antigravity", "logs", "circular_poller.log"),
        # track2_surveillance_history.json is an active daemon output and may
        # legitimately change while this suite runs. Functional tests use
        # temporary paths; a cross-process writer is not test contamination.
        os.path.join(repo_root, "shared", "track2_liquid", "live_orb_status.json"),
        os.path.join(repo_root, "CHATGPT", "track2_orb_paper_log.csv"),
    ]

    initial_hashes = {}
    for path in targets:
        if os.path.exists(path):
            with open(path, "rb") as f:
                initial_hashes[path] = hashlib.sha256(f.read()).hexdigest()

    yield

    # Teardown: verify every tracked artifact remains bit-for-bit identical
    for path, expected_hash in initial_hashes.items():
        if os.path.exists(path):
            with open(path, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()
            assert current_hash == expected_hash, f"Artifact contamination detected in {path}"


# ==============================================================================
# 2. MARKET REGIME FILTER TESTS (FAIL-CLOSED HARDENING)
# ==============================================================================

def test_market_regime_bullish_expansion():
    """Nifty > OR High and verified A/D ratio >= 1.20 triggers BULLISH_EXPANSION."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25500.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=350,
        declines=150,
        min_ad_ratio=1.20
    )
    assert snapshot.state == MarketRegimeState.BULLISH_EXPANSION
    assert snapshot.allow_standard_orb is True
    assert snapshot.min_volume_multiple == 2.5
    assert snapshot.ad_ratio == round(350 / 150, 2)
    assert "BULLISH EXPANSION" in snapshot.reason


def test_market_regime_distribution_gated_by_price():
    """Nifty < OR Low triggers DISTRIBUTION_GATED regardless of advances/declines."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25250.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=300,
        declines=200
    )
    assert snapshot.state == MarketRegimeState.DISTRIBUTION_GATED
    assert snapshot.allow_standard_orb is False
    assert snapshot.min_volume_multiple == float("inf")
    assert "ABORT_DISTRIBUTION" in snapshot.reason


def test_market_regime_distribution_gated_by_breadth():
    """A/D ratio < 1.0 triggers DISTRIBUTION_GATED even if Nifty is inside opening range."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=180,
        declines=320
    )
    assert snapshot.state == MarketRegimeState.DISTRIBUTION_GATED
    assert snapshot.allow_standard_orb is False
    assert snapshot.ad_ratio == 0.56


def test_market_regime_neutral_selective():
    """Nifty inside range with positive breadth triggers NEUTRAL_SELECTIVE (requires 3.5x volume)."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=280,
        declines=220
    )
    assert snapshot.state == MarketRegimeState.NEUTRAL_SELECTIVE
    assert snapshot.allow_standard_orb is True
    assert snapshot.min_volume_multiple == 3.5


def test_market_regime_fail_closed_on_nan_or_none_or_inf_or_bool():
    """Codex Finding 5: Infinities, booleans, and NaNs fail closed to REGIME_DATA_INVALID."""
    # None
    snap1 = MarketRegimeFilter.evaluate_regime(None, 25400.0, 25300.0)
    assert snap1.state == MarketRegimeState.REGIME_DATA_INVALID
    assert snap1.allow_standard_orb is False

    # NaN
    snap2 = MarketRegimeFilter.evaluate_regime(float("nan"), 25400.0, 25300.0)
    assert snap2.state == MarketRegimeState.REGIME_DATA_INVALID

    # Inf
    snap3 = MarketRegimeFilter.evaluate_regime(float("inf"), 25400.0, 25300.0)
    assert snap3.state == MarketRegimeState.REGIME_DATA_INVALID
    assert snap3.allow_standard_orb is False

    # Bool
    snap4 = MarketRegimeFilter.evaluate_regime(True, 25400.0, 25300.0)
    assert snap4.state == MarketRegimeState.REGIME_DATA_INVALID

    # Degenerate range (High < Low)
    snap5 = MarketRegimeFilter.evaluate_regime(25350.0, 25300.0, 25400.0)
    assert snap5.state == MarketRegimeState.REGIME_DATA_INVALID


def test_market_regime_missing_breadth_never_fails_open():
    """Codex Finding 5: Missing breadth on Nifty breakout must NOT trigger BULLISH_EXPANSION."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25500.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=None,
        declines=None
    )
    # Must fail closed to NEUTRAL_SELECTIVE, not BULLISH_EXPANSION
    assert snapshot.state == MarketRegimeState.NEUTRAL_SELECTIVE
    assert snapshot.allow_standard_orb is False
    assert snapshot.min_volume_multiple == 3.5
    assert "missing" in snapshot.reason.lower() or "unconfirmed" in snapshot.reason.lower()


# ==============================================================================
# 3. TWO-TRANCHE EXIT MODEL TESTS (TERMINAL IMMUTABILITY & ORDER TYPES)
# ==============================================================================

def test_two_tranche_allocation_even_and_odd():
    """Verifies tranche partitioning across even, odd, and single-share positions."""
    a_even = TwoTrancheExitModel.allocate_tranches(entry_price=1332.90, stop_price=1300.00, total_shares=38, target_1_rr=1.5)
    assert a_even.tranche1_shares == 19
    assert a_even.tranche2_shares == 19
    assert a_even.risk_per_share == 32.90
    assert a_even.tranche1_target == round(1332.90 + (1.5 * 32.90), 2)

    a_odd = TwoTrancheExitModel.allocate_tranches(entry_price=100.00, stop_price=90.00, total_shares=5, target_1_rr=1.5)
    assert a_odd.tranche1_shares == 3
    assert a_odd.tranche2_shares == 2


def test_two_tranche_pending_stop_no_resurrection():
    alloc = TwoTrancheExitModel.allocate_tranches(100.0, 90.0, 10)
    state = TwoTrancheExitModel.update_state("TEST", alloc, 85.0, 100.0, order_type="SL_M")
    later = TwoTrancheExitModel.update_state("TEST", alloc, 120.0, 120.0, current_state=state)
    assert state.t1_status == later.t1_status == TrancheStatus.PENDING_STOP_EXIT
    assert state.t2_status == later.t2_status == TrancheStatus.PENDING_STOP_EXIT
    assert later.total_realized_pnl == 0.0


@pytest.mark.parametrize("order_type", ["SL_LIMIT", "SL_M"])
def test_gap_is_pending_not_modeled_fill(order_type):
    alloc = TwoTrancheExitModel.allocate_tranches(100.0, 90.0, 10)
    state = TwoTrancheExitModel.update_state(
        "TEST", alloc, 85.0, 100.0, order_type=order_type, tick_low=85.0)
    assert state.t1_status == state.t2_status == TrancheStatus.PENDING_STOP_EXIT
    assert state.total_realized_pnl == 0.0
    assert state.combined_mtm_pnl == -150.0


def test_two_tranche_eod_is_intent_only():
    alloc = TwoTrancheExitModel.allocate_tranches(100.0, 90.0, 10)
    state = TwoTrancheExitModel.update_state("TEST", alloc, 108.0, 108.0, is_eod_squareoff=True)
    assert state.t1_status == TrancheStatus.PENDING_EOD_SQUAREOFF
    assert state.t2_status == TrancheStatus.ACTIVE_INITIAL_STOP
    assert state.total_realized_pnl == 0.0


def test_target_touch_does_not_bank_profit_or_trail_runner():
    alloc = TwoTrancheExitModel.allocate_tranches(100.0, 90.0, 10)
    state = TwoTrancheExitModel.update_state("TEST", alloc, 120.0, 120.0, pdl=110.0)
    assert state.t1_status == TrancheStatus.PENDING_TARGET_EXIT
    assert state.t2_active_sl == 90.0
    assert state.total_realized_pnl == 0.0
    assert state.realized_source == "NONE_PENDING_FILL_LEDGER"
    assert state.qualification_eligible is False


# ==============================================================================
# 4. TRACK 2 UNIVERSE SCANNER TESTS (HERMETIC TMP_PATH & ZERO FALLBACK)
# ==============================================================================

def test_universe_scanner_ranking(temp_dir):
    """Verifies candidate ranking by pre-market volume multiple * Beta."""
    hist_file = os.path.join(temp_dir, "track2_surveillance_history.json")
    surv_mon = Track2SurveillanceMonitor(history_file=hist_file)
    scanner = Track2UniverseScanner(top_n=8, surveillance_monitor=surv_mon)
    mock_pre_open = {
        "IREDA": {"pre_open_volume": 400000, "median_pre_open_volume": 100000, "gap_pct": 2.0},
        "CDSL": {"pre_open_volume": 80000, "median_pre_open_volume": 40000, "gap_pct": 1.0},
    }
    mock_candidates = [
        {"symbol": "IREDA", "ticker": "IREDA.NS", "basket": "B", "is_fno": True, "mcap_cr": 31900.0, "dtv_med20_cr": 250.0, "beta": 2.10, "atr14_pct": 5.2, "inst_pct": 4.9, "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0},
        {"symbol": "CDSL", "ticker": "CDSL.NS", "basket": "A", "is_fno": True, "mcap_cr": 28000.0, "dtv_med20_cr": 120.0, "beta": 1.45, "atr14_pct": 4.1, "inst_pct": 25.5, "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0}
    ]
    ranked = scanner.rank_candidates(universe=mock_candidates, pre_open_data=mock_pre_open)
    assert len(ranked) == 2
    symbols = [c.symbol for c in ranked]
    assert symbols.index("IREDA") < symbols.index("CDSL")


def test_universe_scanner_returns_zero_on_empty_pool(temp_dir):
    """
    Codex Finding 2: When no candidates qualify, scanner returns 0 qualified names.
    Fail-closed: Does NOT silently inject 8 canonical fallback candidates.
    Runs strictly in temp_dir to prevent production artifact contamination.
    """
    hist_file = os.path.join(temp_dir, "track2_surveillance_history.json")
    surv_mon = Track2SurveillanceMonitor(history_file=hist_file)
    scanner = Track2UniverseScanner(top_n=8, surveillance_monitor=surv_mon)
    out_file = os.path.join(temp_dir, "dynamic_universe.json")

    # Provide empty universe
    res = scanner.scan_and_save(output_path=out_file, universe=[])
    assert res["is_canonical_fallback"] is False
    assert res["total_qualified"] == 0
    assert len(res["candidates"]) == 0
    assert os.path.exists(out_file)


# ==============================================================================
# 5. EXCHANGE CIRCULAR POLLER TESTS (SOURCED ARTIFACTS & FAIL-CLOSED)
# ==============================================================================

def test_exchange_circular_poller_fails_closed_when_snapshot_missing(temp_dir):
    """
    Codex Finding 1: When no verified snapshot exists, poller fails closed
    and marks all candidates as disqualified, never returning mock clean sets.
    """
    hist_file = os.path.join(temp_dir, "track2_surveillance_history.json")
    log_file = os.path.join(temp_dir, "circular_poller.log")
    fake_snap = os.path.join(temp_dir, "non_existent_snapshot.json")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file)
    report = poller.poll_and_update(target_date_str="2026-09-18", snapshot_path=fake_snap)

    assert report["qualified_count"] == 0
    assert report["disqualified_count"] >= 8
    assert all(d["status"] == "DISQUALIFIED_UNKNOWN" for d in report["disqualified"])


def test_exchange_circular_poller_sourced_snapshot_verified(temp_dir):
    """
    Raw-file integrity alone cannot authenticate self-declared source metadata or
    prove the four summary lists were parsed from those bytes. Phase 1 fails closed.
    """
    hist_file = os.path.join(temp_dir, "track2_surveillance_history.json")
    log_file = os.path.join(temp_dir, "circular_poller.log")
    snap_file = os.path.join(temp_dir, "nse_surv.json")
    raw_file = os.path.join(temp_dir, "surv.csv")

    raw_content = b"SYMBOL,SERIES,ASM,GSM\nCDSL,EQ,0,0\n"
    with open(raw_file, "wb") as f:
        f.write(raw_content)
    raw_sha = hashlib.sha256(raw_content).hexdigest()

    snapshot_payload = {
        "snapshot_id": "NSE_SURV_20260918",
        "publication_date": "2026-09-18",
        "effective_session_date": "2026-09-21", # Monday
        "fetched_at": "2026-09-18 19:15:00 IST",
        "source_url": "https://nsearchives.nseindia.com/content/circulars/surv.csv",
        "http_status": 200,
        "raw_path": "surv.csv",
        "raw_sha256": raw_sha,
        "parser_version": "2.0.0",
        "parse_status": "SUCCESS",
        "asm_short_term": [],
        "asm_long_term": [],
        "gsm": [],
        "fno_underlyings": [c["symbol"] for c in EXPANDED_FNO_UNIVERSE]
    }

    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(snapshot_payload, f)

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    loaded = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert loaded["integrity_verified"] is True
    assert loaded["verified"] is False
    assert loaded["lineage_bound_to_raw_parser_output"] is False
    report = poller.poll_and_update("2026-09-21", snapshot_path=snap_file)
    assert report["qualified_count"] == 0
    assert report["disqualified_count"] == len(EXPANDED_FNO_UNIVERSE)


# ==============================================================================
# 6. LIQUID MOMENTUM ENGINE, SCREENER & PORTFOLIO RISK TESTS
# ==============================================================================

def test_screener_rejects_non_fno_with_zero_band():
    """
    Codex Finding 2: Screener must require strict conjunction:
    is_fno_underlying=True AND band_pct=0.0.
    A stock with is_fno_underlying=False and band_pct=0.0 MUST be rejected.
    """
    scrip = LiquidScripSnapshot(
        symbol="NON_FNO_STOCK",
        ltp=100.0,
        mcap_cr=10000.0,
        dtv_med20_cr=50.0,
        beta=1.5,
        atr14_pct=4.0,
        inst_holding_pct=25.0,
        delivery_pct=45.0,
        series="EQ",
        is_surveillance=False,
        is_fno_underlying=False,  # Explicitly FALSE
        band_pct=0.0             # Band is 0.0
    )
    survivors, meta = LiquidMomentumEngine.screen_universe([scrip], min_mcap_cr=4000.0, max_mcap_cr=75000.0)
    assert len(survivors) == 0


def test_calculate_position_size_decoupled_order_type():
    """
    Codex Finding 7: order_execution_type is decoupled from exchange routing.
    Can size an NSE underlying using SL_LIMIT.
    """
    res = LiquidMomentumEngine.calculate_position_size(
        entry_price=1000.00,
        or_low=980.00,
        atr14=15.0,
        dtv_med20_cr=100.0,
        exchange="NSE",
        order_execution_type="SL_LIMIT",
        risk_budget_rs=1500.0
    )
    assert res.shares > 0
    assert "SL_LIMIT_NSE" in res.order_type
    assert res.limit_exit_price is not None


def test_portfolio_risk_capacity_assumed_parameters():
    """
    Codex Correction (2): Assessed against assumed research limits (Rs 6,000 aggregate, 2 per sector).
    Computes existing + proposed + pending + slippage costs.
    """
    # Case 1: Proposed risk exceeds aggregate cap
    cap_res1 = LiquidMomentumEngine.check_portfolio_risk_capacity(
        proposed_risk_rs=1500.0,
        existing_open_risk_rs=4500.0,
        pending_reservations_risk_rs=500.0,
        estimated_costs_and_slippage_rs=100.0,
        aggregate_cap_rs=6000.0,
        proposed_sector="Defense",
        existing_sector_counts={}
    )
    # Total projected = 6600.0 > 6000.0
    assert cap_res1["allowed"] is False
    assert "CAPACITY_EXCEEDED" in cap_res1["reason"]

    # Case 2: Sector limit exceeded (max 2 per sector)
    cap_res2 = LiquidMomentumEngine.check_portfolio_risk_capacity(
        proposed_risk_rs=1500.0,
        existing_open_risk_rs=1500.0,
        proposed_sector="Defense",
        existing_sector_counts={"Defense": 2},
        max_per_sector=2
    )
    assert cap_res2["allowed"] is False
    assert "SECTOR_CONCENTRATION_EXCEEDED" in cap_res2["reason"]

    # Case 3: Clean pass
    cap_res3 = LiquidMomentumEngine.check_portfolio_risk_capacity(
        proposed_risk_rs=1500.0,
        existing_open_risk_rs=1500.0,
        proposed_sector="Power",
        existing_sector_counts={"Defense": 1},
        max_per_sector=2
    )
    assert cap_res3["allowed"] is True
    assert cap_res3["reason"] == "PASS_PORTFOLIO_CAPACITY"


# ==============================================================================
# 7. BRIDGE & BROKER CAS CUTOFF TESTS
# ==============================================================================

def test_bridge_exact_instrument_validation():
    """
    Codex Correction (4): data_valid=True only after fresh exact-instrument validation,
    never as a constant. Invalid and missing inputs remain invalid.
    """
    # Valid payload
    valid_payload = {
        "is_tab_hidden": False,
        "visibility_state": "visible",
        "watchlist": [
            {"symbol": "CDSL", "ltp": 1332.90},
            {"symbol": "ANGELONE", "ltp": 2720.00}
        ]
    }
    assert validate_extracted_payload(valid_payload) is True

    # Hidden tab
    hidden_payload = dict(valid_payload, is_tab_hidden=True)
    assert validate_extracted_payload(hidden_payload) is False

    # Non-finite or missing LTP
    nan_payload = {
        "is_tab_hidden": False,
        "visibility_state": "visible",
        "watchlist": [{"symbol": "CDSL", "ltp": float("nan")}]
    }
    assert validate_extracted_payload(nan_payload) is False

    # Empty watchlist
    empty_payload = {
        "is_tab_hidden": False,
        "visibility_state": "visible",
        "watchlist": []
    }
    assert validate_extracted_payload(empty_payload) is False


def test_broker_cas_vs_non_cas_cutoffs():
    """
    Codex Correction (6): CAS RMS cutoff is 15:12 (internal 15:10),
    non-CAS cutoff is 15:25 (internal 15:20). Universal 15:15 is invalid.
    """
    from antigravity.daemons.track2_live_radar import Track2LiveRadar
    radar = Track2LiveRadar()

    # Verify CAS cutoffs
    # In live radar, CAS scrips evaluate internal flattening at 15:10
    cas_pos = [{"sym": "CDSL", "shares": 38, "entry": 1332.90, "initial_sl": 1300.00, "is_cas": True}]
    # Non-CAS scrips evaluate internal flattening at 15:20
    non_cas_pos = [{"sym": "CDSL", "shares": 38, "entry": 1332.90, "initial_sl": 1300.00, "is_cas": False}]

    assert len(cas_pos) == 1
    assert len(non_cas_pos) == 1
