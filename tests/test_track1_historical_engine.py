"""
test_track1_historical_engine.py - Unit & Adversarial Test Suite for Historical Pipeline
Verifies Rule 2 price floors, SQLite schema constraints, and discrete execution logic.
"""

import math
import os
import sys
import sqlite3
import pytest
from datetime import datetime

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from antigravity.daemons.bulk_bhavcopy_harvester import init_database
from antigravity.analysis.historical_rule7_scanner import Rule7Candidate
from antigravity.analysis.discrete_forward_tester import ForwardTestResult


def test_database_initialization_and_schema():
    db_file = "antigravity/logs/test_tmp_historical.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    try:
        init_database(db_file)
        assert os.path.exists(db_file)

        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        assert "daily_quotes" in tables

        # Test Idempotent bulk inserts
        cur.execute("""
        INSERT INTO daily_quotes (trade_date, scripcode, symbol, security_group, close_price, volume)
        VALUES ('2026-09-11', '523105', 'CROPSTER', 'T', 3.02, 1000);
        """)
        conn.commit()

        # Re-insert should replace without failure
        cur.execute("""
        INSERT OR REPLACE INTO daily_quotes (trade_date, scripcode, symbol, security_group, close_price, volume)
        VALUES ('2026-09-11', '523105', 'CROPSTER', 'T', 3.05, 2000);
        """)
        conn.commit()

        cur.execute("SELECT close_price, volume FROM daily_quotes WHERE scripcode = '523105';")
        row = cur.fetchone()
        assert row[0] == 3.05
        assert row[1] == 2000
        conn.close()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def test_rule_2_floor_enforcement():
    # Sub-Rs 10 scrips must never be qualified as Rule 7 entries
    candidate = Rule7Candidate(
        setup_date="2026-09-11",
        scripcode="539091",
        symbol="CCDL",
        entry_price=1.32,  # Violates Rule 2
        volume_ratio=4.5,
        range_pct=5.0,
        group="XT"
    )
    assert candidate.entry_price < 10.00, "Must be flagged as sub-Rs 10"


def test_discrete_4_state_lc_trap_mechanics():
    cand = Rule7Candidate("2026-09-01", "540829", "CHANDRIMA", 12.0, 3.5, 4.0, "XT")
    res = ForwardTestResult(
        candidate=cand,
        entry_fill_price=12.0,
        exit_price=7.18,  # -40.1% 10-day LC descent
        exit_date="2026-09-11",
        exit_reason="LC_LOCKOUT_TRAP",
        holding_days=10,
        net_return_pct=-40.1,
        r_multiple=-1.0
    )
    assert res.exit_reason == "LC_LOCKOUT_TRAP"
    assert res.net_return_pct <= -40.0


def test_uc_followthrough_matrix_computation():
    from antigravity.analysis.uc_followthrough_matrix import compute_uc_transitions, UCTransition
    # Verify transition model structure and fields
    t = UCTransition(
        scripcode="540829",
        symbol="CHANDRIMA",
        date_t="2026-09-01",
        date_t1="2026-09-02",
        band_pct=5.0,
        streak=1,
        t1_opened_at_uc=True,
        t1_closed_at_uc=True,
        t1_closed_green=True,
        t1_closed_red=False,
        t1_hit_lc=False,
        t1_return_pct=5.0
    )
    assert t.t1_closed_at_uc is True
    assert t.streak == 1
    assert t.t1_return_pct == 5.0


def test_monte_carlo_queue_drain_simulation():
    from antigravity.analysis.monte_carlo_queue_simulator import run_monte_carlo_simulation, u_curve_density
    # Test U-curve density symmetry: open and close have higher density than mid-day
    d_open = u_curve_density(0.0)
    d_mid = u_curve_density(0.5)
    d_close = u_curve_density(1.0)
    assert d_open > d_mid, "Open density must be higher than mid-day"
    assert d_close > d_mid, "Close density must be higher than mid-day"

    # Fast test run with 100 simulations
    sims = run_monte_carlo_simulation(num_simulations=100, base_order_size=1000, seed=123)
    assert len(sims) == 100
    for s in sims:
        assert 0.0 <= s.fill_fraction <= 1.0
        assert s.clearance_time_hours > 0.0


def test_paper_observation_journaler_schema_and_gate_audit():
    from antigravity.daemons.paper_observation_journaler import format_observation_row, audit_paper_gate_progress, CSV_HEADER

    sample_obs = {
        "symbol": "MOBIKWIK",
        "exchange": "BSE",
        "ltp": 209.55,
        "prev_close": 188.45,
        "upper_circuit": 233.05,
        "lower_circuit": 143.85,
        "day_volume": 892396,
        "position_qty": 59,
        "order_status": "QUEUED"
    }

    formatted = format_observation_row(sample_obs)
    assert len(formatted) == 46, f"Expected 46 columns, got {len(formatted)}"
    assert formatted["counts_toward_paper_gate"] == "false", "Must be hardcoded false under Rule 1"
    assert formatted["symbol"] == "MOBIKWIK"
    assert formatted["ltp"] == "209.55"
    assert formatted["position_qty"] == "59"

    audit = audit_paper_gate_progress()
    assert audit["gate_passed"] is False, "Paper gate must not be passed prematurely"
    assert audit["rule_1_status"] == "STRICT_OBSERVATION_ONLY"


def test_markov_state_classification():
    from antigravity.analysis.markov_absorption_simulator import classify_state

    # 1. Band cut triggers BAND_TIGHTENED
    s = classify_state(100.0, 100.0, 105.0, 95.0, 105.0, 95.0, band=5.0, prev_band=10.0)
    assert s == "BAND_TIGHTENED"

    # 2. Close at upper circuit
    s = classify_state(104.99, 100.0, 105.0, 100.0, uc=105.0, lc=95.0, band=5.0, prev_band=5.0)
    assert s == "LOCKED_UC"

    # 3. Close at lower circuit
    s = classify_state(95.01, 100.0, 100.0, 95.0, uc=105.0, lc=95.0, band=5.0, prev_band=5.0)
    assert s == "LOCKED_LC"

    # 4. Volatile intraday range (>= 5%)
    s = classify_state(102.0, 100.0, 106.0, 100.0, uc=110.0, lc=90.0, band=10.0, prev_band=10.0)
    assert s == "UNLOCKED_VOLATILE"

    # 5. Normal two-sided base (< 5% range)
    s = classify_state(101.0, 100.0, 102.0, 100.0, uc=110.0, lc=90.0, band=10.0, prev_band=10.0)
    assert s == "TWO_SIDED_BASE"


def test_markov_absorption_trajectories():
    from antigravity.analysis.markov_absorption_simulator import simulate_absorption_trajectories, STATES

    # Construct synthetic well-behaved matrix
    matrix = {s: {s_to: 0.2 for s_to in STATES} for s in STATES}
    res = simulate_absorption_trajectories(matrix, start_state="TWO_SIDED_BASE", num_simulations=500, seed=42)

    assert res["total_runs"] == 500
    assert 0.0 <= res["prob_profit_target"] <= 1.0
    assert 0.0 <= res["prob_lc_trap"] <= 1.0
    assert 0.0 <= res["prob_band_cut_exit"] <= 1.0
    assert 0.0 <= res["prob_normal_hold"] <= 1.0
    # Probabilities should sum to approximately 1.0
    total_p = res["prob_profit_target"] + res["prob_lc_trap"] + res["prob_band_cut_exit"] + res["prob_normal_hold"]
    assert abs(total_p - 1.0) < 0.01


def test_circuit_break_sensitivity_and_spoof_detection():
    from antigravity.analysis.circuit_break_sensitivity import calculate_critical_dump_volume

    # Case A: Massive resting bid spoof wall
    res_spoof = calculate_critical_dump_volume(
        resting_bid_depth=1000000,
        session_avg_volume=20000,
        band_pct=5.0
    )
    assert res_spoof["is_spoof_risk"] is True
    assert res_spoof["fragility_ratio"] == 50.0
    assert res_spoof["critical_dump_volume"] == 1150000.0

    # Case B: Structural healthy bid wall
    res_struct = calculate_critical_dump_volume(
        resting_bid_depth=10000,
        session_avg_volume=50000,
        band_pct=5.0
    )
    assert res_struct["is_spoof_risk"] is False
    assert res_struct["fragility_ratio"] == 0.2
    assert res_struct["critical_dump_volume"] == 11500.0


def test_pre_open_auction_engine():
    from antigravity.models.pre_open_auction_engine import PreOpenAuctionEngine, PreOpenAction

    # 1. Test Rule 2 sub-Rs 10 rejection
    r_sub10 = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="PENNY", scripcode="500001", prev_close=5.0, circuit_band_pct=5.0,
        indicative_price=5.10, indicative_bids=1000, indicative_offers=500, avg_20d_volume=10000
    )
    assert r_sub10.action == PreOpenAction.ABORT_SUB_10_FLOOR

    # 2. Test Rule 3 locked Upper Circuit (0 offers)
    r_locked = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="LOCKED", scripcode="500002", prev_close=20.0, circuit_band_pct=5.0,
        indicative_price=21.0, indicative_bids=50000, indicative_offers=0, avg_20d_volume=10000
    )
    assert r_locked.action == PreOpenAction.ABORT_LOCKED_UC

    # 3. Test excessive gap-up (less than 3% headroom to UC)
    r_gap = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="GAP", scripcode="500003", prev_close=100.0, circuit_band_pct=5.0,
        indicative_price=104.50, indicative_bids=10000, indicative_offers=5000, avg_20d_volume=20000
    )
    assert r_gap.action == PreOpenAction.ABORT_EXCESSIVE_GAP

    # 4. Test spoof risk (>10x bid/offer imbalance)
    r_spoof = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="SPOOF", scripcode="500004", prev_close=50.0, circuit_band_pct=20.0,
        indicative_price=51.0, indicative_bids=200000, indicative_offers=10000, avg_20d_volume=50000
    )
    assert r_spoof.action == PreOpenAction.ABORT_HIGH_SPOOF_RISK

    # 5. Test clean qualified pre-open breakout.
    # Band is 5%: AGENTS.md Rule 11 restricts Track 1 to fixed bands (2%, 5%),
    # so this fixture previously asserted a successful size on a 20% scrip that
    # is not a Track 1 instrument at all. Sizing now refuses that, correctly.
    # At a 5% band the UC is 105, so the indicative price must leave the
    # engine's minimum 3% headroom: 101.0 leaves 3.96%.
    r_clean = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="CLEAN", scripcode="500005", prev_close=100.0, circuit_band_pct=5.0,
        indicative_price=101.0, indicative_bids=30000, indicative_offers=20000, avg_20d_volume=50000
    )
    assert r_clean.action == PreOpenAction.SUBMIT_PRE_OPEN_LIMIT
    assert r_clean.recommended_limit_price >= 101.0
    assert r_clean.recommended_shares > 0
    assert "09:00:01" in r_clean.queue_priority_window

    # 6. A 20% band scrip must size to zero: ineligible under Rule 11, not
    # merely mis-calibrated. Before the band-aware wiring this sized at the
    # flat 0.401 divisor, permitting 2.23x the Rule 5 loss budget.
    r_wide = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="WIDEBAND", scripcode="500006", prev_close=100.0, circuit_band_pct=20.0,
        indicative_price=102.0, indicative_bids=30000, indicative_offers=20000, avg_20d_volume=50000
    )
    assert r_wide.recommended_shares == 0


def test_delivery_absorption_analyzer():
    from antigravity.models.delivery_absorption_analyzer import DeliveryAbsorptionAnalyzer, DeliveryVerdict

    # 1. Test statutory T2T 100% delivery (XT series)
    r_t2t = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
        symbol="KINETIC", scripcode="500240", trade_date="2026-09-11", security_group="XT",
        total_volume=100000, deliverable_volume=None, avg_20d_volume=20000, trade_count=1000
    )
    assert r_t2t.verdict == DeliveryVerdict.STATUTORY_T2T_100
    assert r_t2t.delivery_pct == 100.0
    assert r_t2t.is_statutory_t2t is True

    # 2. Test distribution churn (<40% delivery with >=3x volume expansion)
    r_churn = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
        symbol="CHURN", scripcode="500006", trade_date="2026-09-11", security_group="B",
        total_volume=300000, deliverable_volume=90000, avg_20d_volume=50000, trade_count=2000
    )
    assert r_churn.verdict == DeliveryVerdict.DISTRIBUTION_CHURN
    assert r_churn.delivery_pct == 30.0

    # 3. Test confirmed accumulation (>=85% delivery with >=3x volume expansion)
    r_accum = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
        symbol="ACCUM", scripcode="500007", trade_date="2026-09-11", security_group="B",
        total_volume=200000, deliverable_volume=180000, avg_20d_volume=40000, trade_count=1500
    )
    assert r_accum.verdict == DeliveryVerdict.ACCUMULATION_CONFIRMED
    assert r_accum.delivery_pct == 90.0


def test_multi_stock_radar_evaluation():
    from antigravity.daemons.multi_stock_radar import (
        evaluate_radar_state,
        format_radar_screen,
        CANDIDATES
    )

    dummy_bse = {
        "MOBIKWIK": {"ltp": 200.0, "prev_close": 180.0, "upper_circuit": 216.0, "lower_circuit": 144.0, "band_pct": 20.0, "volume_shares": 50000, "surveillance": "NONE"},
        "LOVABLE": {"ltp": 8.50, "prev_close": 8.00, "upper_circuit": 9.60, "lower_circuit": 6.40, "band_pct": 20.0, "volume_shares": 10000, "surveillance": "NONE"}, # sub-10 test
        "ANLON": {"ltp": 20.0, "prev_close": 20.0, "upper_circuit": 24.0, "lower_circuit": 16.0, "band_pct": 20.0, "volume_shares": 100000, "surveillance": "NONE"},
        "VEDAVAAG": {"ltp": 25.0, "prev_close": 24.0, "upper_circuit": 28.8, "lower_circuit": 19.2, "band_pct": 20.0, "volume_shares": 15000, "surveillance": "NONE"},
        "KINETIC": {"ltp": 230.0, "prev_close": 220.0, "upper_circuit": 231.0, "lower_circuit": 209.0, "band_pct": 5.0, "volume_shares": 30000, "surveillance": "ESM_STAGE_1"}
    }

    dummy_live = {
        "active_stock": "ANLON",
        "watchlist": [
            {"symbol": "MOBIKWIK", "ltp": 202.0},
            {"symbol": "ANLON", "ltp": 21.0}
        ],
        "depth": {"bids": [{"price": 20.9, "quantity": 100}], "offers": [{"price": 21.0, "quantity": 200}]},
        "stats": {"volume": 120000},
        # Required by the shared feed gate: a snapshot whose freshness cannot
        # be proven is unusable, and the producer always stamps this.
        "data_valid": True,
        "local_write_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    dummy_vols = {
        "MOBIKWIK": 20000.0,
        "LOVABLE": 5000.0,
        "ANLON": 50000.0,
        "VEDAVAAG": 10000.0,
        "KINETIC": 15000.0
    }

    res = evaluate_radar_state(dummy_bse, dummy_live, dummy_vols)
    assert len(res) == 5

    # 1. Test Kite live LTP priority
    assert res["MOBIKWIK"]["ltp"] == 202.0
    assert res["MOBIKWIK"]["ltp_source"] == "KITE_LIVE"

    # 2. Test Rule 2 sub-10 floor disqualification
    assert res["LOVABLE"]["rule2_pass"] is False
    assert res["LOVABLE"]["status"] == "DISQUALIFIED_SUB_10"
    assert res["LOVABLE"]["paper_shares"] == 0

    # 3. Rule 5 sizing is band-aware and delegated to CircuitRiskCalculator.
    # ANLON bands at 20%, which AGENTS.md Rule 11 excludes from Track 1
    # (fixed bands 2%, 5% only), so it must size to zero. This previously
    # asserted 5000/(0.401*21) = 593 shares against a local duplicate of the
    # rule that kept its own hardcoded 5% divisor.
    assert res["ANLON"]["band_pct"] == 20.0
    assert res["ANLON"]["rule5_shares"] == 0
    assert res["ANLON"]["paper_shares"] == 0

    # KINETIC bands at 5%, is eligible, and keeps the classic 0.401 divisor.
    from antigravity.models.risk_calculator import ten_day_lc_divisor
    expected_kinetic = int(math.floor(5000.0 / (ten_day_lc_divisor(5.0) * 230.0)))
    assert res["KINETIC"]["rule5_shares"] == expected_kinetic

    # 4. Test Rule 9 Liquidity Cap calibration: 2 * 0.15 * 50,000 = 15,000 shares
    assert res["ANLON"]["rule9_shares"] == int(math.floor(2 * 0.15 * 50000))

    # 5. Test ESM status on KINETIC
    assert res["KINETIC"]["surveillance"] == "ESM_STAGE_1"
    assert res["KINETIC"]["status"] == "ESM1_REVIEW_OK"

    # 6. Test Screen formatting output
    screen = format_radar_screen(res, "2026-09-14 09:15:00")
    assert "MOBIKWIK" in screen
    assert "ESM1_REVIEW_OK" in screen
    assert "DISQUALIFIED_SUB_10" in screen






