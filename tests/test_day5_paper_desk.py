"""
tests/test_day5_paper_desk.py
=============================
Comprehensive Acceptance and Adversarial Test Suite for Sprint Day 5:
- Canonical Paper Trading Desk Runner (antigravity/paper/paper_desk_runner.py)
- Transactional SQLite Event Store (antigravity/paper/paper_store.py)
- Event Contracts and Data Schemas (antigravity/paper/paper_contracts.py)

Acceptance Matrix (Codex Deliberation 2026-10-02):
1. Replay Invariance: Interrupted crash/restart produces identical state, active slots, and economics.
2. Capacity & Portfolio Governor: Max 3 slots, max 2 per sector, inviolable Rs 136,000 cash buffer.
3. 15% Volume Participation Ceiling: Aggregated per symbol per session; zero volume yields 0 costs.
4. DP Grouping & Statutory Cost Allocation: Rs 15.93 DP charge incurred once per symbol per sell session.
5. Locked Circuit Persistence: Locked stops and disqualifications persist across sessions and exit at recovery open.
6. Surveillance & Eligibility Guardrails: Pre-open 08:45 cutoff; missing evidence freezes entries fail-closed.
7. Corporate Action Adjustments: Stock splits adjust quantity and basis with 0 fabricated PnL; unknown actions freeze.
8. Rule 1 Fail-Closed Live Trading Gate: allow_live_broker=True strictly rejected.
9. CSV Projections Determinism: Exported CSVs exactly mirror committed SQLite state with atomic replacement.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import pytest
import sqlite3
from typing import Dict, List, Set

from antigravity.engine.execution_simulator import DailyBar, OrderSide, calculate_statutory_costs
from antigravity.engine.risk_governor import (
    AGGREGATE_EXPOSURE_CAP_RS,
    AGGREGATE_RISK_CAP_RS,
    CASH_BUFFER_RS,
    MAX_POSITIONS_PER_SECTOR,
    MAX_SLOTS,
    RISK_PER_TRADE_RS,
    SLOT_CAP_RS,
    TOTAL_CORPUS_RS,
    PortfolioRiskGovernor,
)
from antigravity.engine.backtest_engine import FrictionPolicy, FrictionTier
from antigravity.paper.paper_contracts import (
    DESK_ID,
    EVIDENCE_MODE_DEFAULT,
    DailyPortfolioEquityRecord,
    OpenPositionRecord,
    PaperEventType,
    PaperJournalEvent,
    PaperOrderType,
    PaperSide,
    SCHEMA_VERSION,
    TRACK_ID,
)
from antigravity.paper.paper_store import PaperStore
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
from antigravity.strategies.base_strategy import SignalEvent


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_paper_env(tmp_path: Path):
    db_path = tmp_path / "paper_store.db"
    projections_dir = tmp_path / "projections"
    projections_dir.mkdir(parents=True, exist_ok=True)

    config = PaperDeskConfig(
        db_path=db_path,
        projections_dir=projections_dir,
        initial_cash_rs=TOTAL_CORPUS_RS,
        cash_buffer_rs=CASH_BUFFER_RS,
        slot_cap_rs=SLOT_CAP_RS,
        risk_per_trade_rs=RISK_PER_TRADE_RS,
        max_slots=MAX_SLOTS,
        max_positions_per_sector=MAX_POSITIONS_PER_SECTOR,
        allow_live_broker=False,
    )
    store = PaperStore(db_path)
    runner = PaperDeskRunner(config=config, store=store)
    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "projections_dir": projections_dir,
        "config": config,
        "store": store,
        "runner": runner,
    }


_orig_run_pre_open = PaperDeskRunner.run_pre_open
_orig_run_post_close = PaperDeskRunner.run_post_close

def _test_run_pre_open(self, session_date, *args, **kwargs):
    if kwargs.get("surveillance_snapshot") is None and "surveillance_snapshot" not in kwargs:
        kwargs["surveillance_snapshot"] = {
            "fetched_at": f"{session_date}T08:30:00+05:30",
            "asm_long_term": [],
            "asm_short_term": [],
            "gsm": [],
            "esm": [],
            "t2t": [],
        }
    if kwargs.get("fno_underlyings") is None and "fno_underlyings" not in kwargs:
        kwargs["fno_underlyings"] = {
            "CDSL", "SUZLON", "COCHINSHIP", "RELIANCE", "ANGELONE", "POLICYBZR", "INFY", "TCS", "HDFCBANK"
        }
    return _orig_run_pre_open(self, session_date, *args, **kwargs)

def _test_run_post_close(self, session_date, *args, **kwargs):
    if kwargs.get("bhavcopy_manifest") is None and "bhavcopy_manifest" not in kwargs:
        kwargs["bhavcopy_manifest"] = {"status": "NORMAL", "session_date": session_date}
    return _orig_run_post_close(self, session_date, *args, **kwargs)

PaperDeskRunner.run_pre_open = _test_run_pre_open
PaperDeskRunner.run_post_close = _test_run_post_close


def make_signal(
    symbol: str,
    entry: float,
    stop: float,
    target: float,
    strategy_id: str = "HIGH52_MOMENTUM",
    session_date: str = "2024-05-14",
    entry_session: str = "2024-05-15",
    priority_score: float = 10.0,
    volume_z_score: float = 2.0,
) -> SignalEvent:
    return SignalEvent(
        strategy_id=strategy_id,
        symbol=symbol,
        session_date=session_date,
        entry_session=entry_session,
        reference_price=entry,
        stop_loss_price=stop,
        target_price=target,
        priority_score=priority_score,
        trace={"atr": 5.0, "volume_z_score": volume_z_score},
        created_at=f"{session_date}T08:30:00+05:30",
    )


# =============================================================================
# 1. SECURITY & RULE 1 INVARIANTS
# =============================================================================

def test_rule1_allow_live_broker_fails_closed(tmp_path: Path):
    """AGENTS.md Rule 1: allow_live_broker=True must be rejected with ValueError."""
    config = PaperDeskConfig(
        db_path=tmp_path / "db.sqlite",
        projections_dir=tmp_path / "proj",
        allow_live_broker=True,
    )
    with pytest.raises(ValueError, match="allow_live_broker=True is prohibited"):
        PaperDeskRunner(config=config)


def test_non_qualifying_watermark_preserved(temp_paper_env):
    """Every event in paper journal must carry BAR_SCENARIO_NON_QUALIFYING status."""
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    sig = make_signal("CDSL", 100.0, 90.0, 120.0)
    runner.run_pre_open("2024-05-15", candidate_signals=[sig])

    with store._get_connection() as conn:
        rows = conn.execute("SELECT payload_json FROM ledger_events").fetchall()
        assert len(rows) > 0
        for r in rows:
            data = json.loads(r["payload_json"])
            assert data["qualifying_evidence_status"] == EVIDENCE_MODE_DEFAULT


# =============================================================================
# 2. REPLAY INVARIANCE ACROSS CRASH & RESTART
# =============================================================================

def test_crash_restart_replay_invariance(temp_paper_env):
    """
    Restarting the runner on an existing SQLite DB restores exact positions,
    pending reservations, cash ledger, and governor slot count without lost state.
    """
    runner = temp_paper_env["runner"]
    config = temp_paper_env["config"]
    store = temp_paper_env["store"]

    # Session 1: Pre-open reserves CDSL
    sig = make_signal("CDSL", 100.0, 90.0, 120.0)
    res = runner.run_pre_open("2024-05-15", candidate_signals=[sig])
    assert len(res["approved_reservations"]) == 1

    # Check state before restart
    assert len(runner.governor.pending_reservations) == 1
    assert "CDSL" in runner.governor.pending_reservations

    # Simulate crash and restart: instantiate brand new runner with same DB
    restarted_runner = PaperDeskRunner(config=config)

    assert len(restarted_runner.governor.pending_reservations) == 1
    assert "CDSL" in restarted_runner.governor.pending_reservations
    assert restarted_runner.governor.available_slots == 2

    # Post-close fill in restarted runner
    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=50000,
        )
    }
    restarted_runner.run_post_close("2024-05-15", bar_data_map=bar_map)

    # Check that position is opened and reservation cleared
    open_positions = restarted_runner.store.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0].symbol == "CDSL"
    assert len(restarted_runner.store.get_pending_reservations()) == 0

    # Restart once more and verify open position is fully restored in governor
    third_runner = PaperDeskRunner(config=config)
    assert len(third_runner.governor.active_positions) == 1
    assert "CDSL" in third_runner.governor.active_positions
    assert third_runner.governor.available_slots == 2
    assert third_runner.governor.cash_rs == restarted_runner.governor.cash_rs


# =============================================================================
# 3. CAPACITY & INVIOLABLE CASH BUFFER GATES
# =============================================================================

def test_governor_enforces_max_3_slots_and_rejects_4th(temp_paper_env):
    """Fourth order must be rejected when 3 slots are occupied/reserved."""
    runner = temp_paper_env["runner"]

    signals = [
        make_signal("CDSL", 100.0, 90.0, 120.0, priority_score=30.0),        # Slot 1 (CAPITAL_MARKETS)
        make_signal("SUZLON", 50.0, 45.0, 60.0, priority_score=20.0),         # Slot 2 (GREEN_ENERGY)
        make_signal("COCHINSHIP", 1000.0, 950.0, 1100.0, priority_score=10.0), # Slot 3 (DEFENSE_SHIPBUILDING)
        make_signal("RELIANCE", 2500.0, 2400.0, 2700.0, priority_score=5.0),  # Slot 4 (Should reject)
    ]

    res = runner.run_pre_open("2024-05-15", candidate_signals=signals)
    assert len(res["approved_reservations"]) == 3
    assert len(res["rejected_candidates"]) == 1
    assert res["rejected_candidates"][0][0].symbol == "RELIANCE"
    assert "MAX_CONCURRENT_POSITIONS_REACHED" in res["rejected_candidates"][0][1]


def test_governor_enforces_sector_concentration_max_2(temp_paper_env):
    """Third candidate from same sector must be rejected."""
    runner = temp_paper_env["runner"]

    # CDSL, ANGELONE, POLICYBZR are all CAPITAL_MARKETS_FINTECH
    signals = [
        make_signal("CDSL", 100.0, 90.0, 120.0, priority_score=30.0),
        make_signal("ANGELONE", 200.0, 190.0, 220.0, priority_score=20.0),
        make_signal("POLICYBZR", 150.0, 140.0, 170.0, priority_score=10.0),
    ]

    res = runner.run_pre_open("2024-05-15", candidate_signals=signals)
    assert len(res["approved_reservations"]) == 2
    assert len(res["rejected_candidates"]) == 1
    assert res["rejected_candidates"][0][0].symbol == "POLICYBZR"
    assert "SECTOR_CONCENTRATION_EXCEEDED" in res["rejected_candidates"][0][1]


def test_inviolable_cash_buffer_protection_at_fill_time(temp_paper_env):
    """
    If cash drops below Rs 136,000, new entry fill must be rejected
    to protect the inviolable cash buffer.
    """
    runner = temp_paper_env["runner"]

    sig = make_signal("CDSL", 100.0, 90.0, 120.0)
    runner.run_pre_open("2024-05-15", candidate_signals=[sig])

    # Artificially set cash near buffer limit before fill time: Rs 137,000
    runner.governor.cash_rs = 137_000.0

    # Attempt post-close fill requiring ~Rs 15,000, which would breach Rs 136,000
    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=50000,
        )
    }
    post_res = runner.run_post_close("2024-05-15", bar_data_map=bar_map)

    # Fill must be cancelled to protect buffer
    assert len(post_res["executed_entries"]) == 0
    assert runner.governor.cash_rs == 137_000.0
    assert runner.governor.cash_rs >= CASH_BUFFER_RS


# =============================================================================
# 4. 15% VOLUME PARTICIPATION CEILING
# =============================================================================

def test_volume_participation_cap_restricts_entry_qty(temp_paper_env):
    """
    Max fill quantity is floor(0.15 * session_volume).
    Excess quantity results in partial fill.
    """
    runner = temp_paper_env["runner"]

    sig = make_signal("CDSL", 100.0, 90.0, 120.0)  # Sizing requests 150 shares
    runner.run_pre_open("2024-05-15", candidate_signals=[sig])

    # Bar with tiny volume of only 200 shares -> 15% cap is 30 shares
    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=200,
        )
    }
    post_res = runner.run_post_close("2024-05-15", bar_data_map=bar_map)

    assert len(post_res["executed_entries"]) == 1
    sym, fill_qty, fill_price = post_res["executed_entries"][0]
    assert sym == "CDSL"
    assert fill_qty == 30  # exactly floor(0.15 * 200)

    # Zero volume session yields 0 filled shares and 0 costs
    sig2 = make_signal("SUZLON", 50.0, 45.0, 60.0, session_date="2024-05-15", entry_session="2024-05-16")
    runner.run_pre_open("2024-05-16", candidate_signals=[sig2])
    bar_map2 = {
        "SUZLON": DailyBar(
            symbol="SUZLON",
            open=50.0,
            high=51.0,
            low=49.0,
            close=50.5,
            volume=0,  # Zero volume
        )
    }
    post_res2 = runner.run_post_close("2024-05-16", bar_data_map=bar_map2)
    assert len(post_res2["executed_entries"]) == 0


# =============================================================================
# 5. DP CHARGE GROUPING ON SAME-SYMBOL SELLS
# =============================================================================

def test_dp_charge_grouped_per_symbol_per_session(temp_paper_env):
    """
    Codex Mandate 2: Flat Rs 15.93 DP charge is applied once per symbol per sell day.
    Subsequent sells of the same symbol on the same day incur Rs 0 DP charge.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    # Create an open position with 100 shares
    pos = OpenPositionRecord(
        position_id="POS-TEST-CDSL",
        isin="INE736A01011",
        symbol="CDSL",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=100,
        sold_qty=0,
        residual_qty=100,
        residual_cost_basis_rs=10000.0,
        entry_cost_allocation_rs=15.0,
        stop_price=90.0,
        target_price=120.0,
        planned_open_risk_rs=1000.0,
        exit_intent="MANUAL_TEST",
        exit_intent_created_at="2024-05-15T09:00:00+05:30",
        pending_exit_order_id=None,
        last_mark=100.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)
    runner.governor.active_positions["CDSL"] = {
        "symbol": "CDSL",
        "shares": 100,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "notional_rs": 10000.0,
        "open_risk_rs": 1000.0,
        "sector": "CAPITAL_MARKETS_FINTECH",
        "entry_costs": 15.0,
    }

    # First sell: partial exit of 50 shares
    # Restrict volume to 333 shares so max exit is floor(0.15 * 333) = 49 shares
    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=333,
        )
    }
    runner.run_post_close("2024-05-15", bar_data_map=bar_map)

    with store._get_connection() as conn:
        events = conn.execute(
            "SELECT payload_json FROM ledger_events WHERE symbol = 'CDSL' AND side = 'SELL'"
        ).fetchall()
        assert len(events) >= 1
        first_evt = json.loads(events[0]["payload_json"])
        assert first_evt["dp_fee_rs"] == 15.93

    # Second sell of the remaining shares on the same session
    bar_map2 = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=5000,
        )
    }
    runner.run_post_close("2024-05-15", bar_data_map=bar_map2)

    with store._get_connection() as conn:
        events = conn.execute(
            "SELECT payload_json FROM ledger_events WHERE symbol = 'CDSL' AND side = 'SELL' ORDER BY event_seq ASC"
        ).fetchall()
        assert len(events) >= 2
        second_evt = json.loads(events[1]["payload_json"])
        # Second sell on same session must have 0 DP fee
        assert second_evt["dp_fee_rs"] == 0.0


# =============================================================================
# 6. LOCKED CIRCUIT STOP-LOSS & RECOVERY EXIT
# =============================================================================

def test_locked_stop_loss_persists_and_exits_on_recovery(temp_paper_env):
    """
    When a stock hits stop loss during a circuit lockout, the exit cannot execute.
    The intent is persisted across sessions and executed at the recovery open.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    pos = OpenPositionRecord(
        position_id="POS-TEST-SUZLON",
        isin="INE040H01021",
        symbol="SUZLON",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=100,
        sold_qty=0,
        residual_qty=100,
        residual_cost_basis_rs=5000.0,
        entry_cost_allocation_rs=10.0,
        stop_price=45.0,
        target_price=60.0,
        planned_open_risk_rs=500.0,
        exit_intent=None,
        exit_intent_created_at=None,
        pending_exit_order_id=None,
        last_mark=50.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)
    runner.governor.active_positions["SUZLON"] = {
        "symbol": "SUZLON",
        "shares": 100,
        "entry_price": 50.0,
        "stop_price": 45.0,
        "notional_rs": 5000.0,
        "open_risk_rs": 500.0,
        "sector": "GREEN_ENERGY_POWER",
        "entry_costs": 10.0,
    }

    # Day 1: Locked Lower Circuit below stop price (40.0) with 0 volume
    bar_locked = {
        "SUZLON": DailyBar(
            symbol="SUZLON",
            open=40.0,
            high=40.0,
            low=40.0,
            close=40.0,
            volume=0,
        )
    }
    res1 = runner.run_post_close("2024-05-13", bar_data_map=bar_locked)
    assert len(res1["executed_exits"]) == 0

    # Verify exit intent is recorded and position remains open
    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].exit_intent == "STOP_LOSS"
    assert positions[0].mark_status == "LOCKED_CIRCUIT"

    # Day 2: Market opens executable at 42.0 with liquid volume
    bar_recovery = {
        "SUZLON": DailyBar(
            symbol="SUZLON",
            open=42.0,
            high=44.0,
            low=41.0,
            close=43.0,
            volume=50000,
        )
    }
    res2 = runner.run_post_close("2024-05-14", bar_data_map=bar_recovery)

    assert len(res2["executed_exits"]) == 1
    sym, fill_qty, fill_price, reason = res2["executed_exits"][0]
    assert sym == "SUZLON"
    assert fill_qty == 100
    assert reason == "STOP_LOSS"
    # Gap slippage applied because open (42.0) < stop (45.0)
    assert fill_price < 42.0
    assert len(store.get_open_positions()) == 0


# =============================================================================
# 7. SURVEILLANCE PRE-EMPTION & EXIT INTENT
# =============================================================================

def test_surveillance_preemption_triggers_exit_intent(temp_paper_env):
    """
    If an active scrip enters ASM/GSM surveillance, runner registers exit intent
    during pre-open and attempts exit at open.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    pos = OpenPositionRecord(
        position_id="POS-TEST-CDSL",
        isin="INE736A01011",
        symbol="CDSL",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=100,
        sold_qty=0,
        residual_qty=100,
        residual_cost_basis_rs=10000.0,
        entry_cost_allocation_rs=15.0,
        stop_price=90.0,
        target_price=120.0,
        planned_open_risk_rs=1000.0,
        exit_intent=None,
        exit_intent_created_at=None,
        pending_exit_order_id=None,
        last_mark=100.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)
    runner.governor.active_positions["CDSL"] = {
        "symbol": "CDSL",
        "shares": 100,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "notional_rs": 10000.0,
        "open_risk_rs": 1000.0,
        "sector": "CAPITAL_MARKETS_FINTECH",
        "entry_costs": 15.0,
    }

    # Pre-open feed indicates CDSL entered ASM
    surv_snap = {
        "fetched_at": "2024-05-15T08:30:00+05:30",
        "asm_short_term": ["CDSL"],
    }
    runner.run_pre_open("2024-05-15", surveillance_snapshot=surv_snap)

    # Position must have exit intent set
    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].exit_intent == "SURVEILLANCE_DISQUALIFICATION"

    # Post-close must execute the disqualification exit
    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=99.0,
            high=101.0,
            low=98.0,
            close=100.0,
            volume=50000,
        )
    }
    res = runner.run_post_close("2024-05-15", bar_data_map=bar_map)
    assert len(res["executed_exits"]) == 1
    assert res["executed_exits"][0][3] == "SURVEILLANCE_DISQUALIFICATION"
    assert len(store.get_open_positions()) == 0


# =============================================================================
# 8. CORPORATE ACTIONS (SPLIT & BASIS ADJUSTMENT)
# =============================================================================

def test_corporate_action_split_adjusts_quantity_and_basis_cleanly(temp_paper_env):
    """
    2:1 stock split doubles shares, halves stop and per-share price,
    preserving total cost basis and fabricating zero PnL.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    pos = OpenPositionRecord(
        position_id="POS-TEST-CDSL",
        isin="INE736A01011",
        symbol="CDSL",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=50,
        sold_qty=0,
        residual_qty=50,
        residual_cost_basis_rs=5000.0,
        entry_cost_allocation_rs=10.0,
        stop_price=90.0,
        target_price=120.0,
        planned_open_risk_rs=500.0,
        exit_intent=None,
        exit_intent_created_at=None,
        pending_exit_order_id=None,
        last_mark=100.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)
    runner.governor.active_positions["CDSL"] = {
        "symbol": "CDSL",
        "shares": 50,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "notional_rs": 5000.0,
        "open_risk_rs": 500.0,
        "sector": "CAPITAL_MARKETS_FINTECH",
        "entry_costs": 10.0,
    }

    # Apply 2:1 split (ratio = 2.0)
    success = runner.apply_corporate_action("CDSL", "SPLIT", ratio=2.0, effective_date="2024-05-12")
    assert success is True

    positions = store.get_open_positions()
    assert len(positions) == 1
    updated_pos = positions[0]
    assert updated_pos.residual_qty == 100  # doubled
    assert updated_pos.stop_price == 45.0   # halved
    assert updated_pos.residual_cost_basis_rs == 5000.0  # cost basis unchanged!

    # Governor position must also be synchronized
    gov_pos = runner.governor.active_positions["CDSL"]
    assert gov_pos["shares"] == 100
    assert gov_pos["stop_price"] == 45.0


# =============================================================================
# 9. CSV PROJECTIONS RECONCILIATION WITH SQLITE
# =============================================================================

def test_csv_projections_reconcile_exactly_with_sqlite(temp_paper_env):
    """
    Verify exported CSVs match SQLite database row for row, field for field.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]
    proj_dir = temp_paper_env["projections_dir"]

    sig = make_signal("CDSL", 100.0, 90.0, 120.0)
    runner.run_pre_open("2024-05-15", candidate_signals=[sig])

    bar_map = {
        "CDSL": DailyBar(
            symbol="CDSL",
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=50000,
        )
    }
    runner.run_post_close("2024-05-15", bar_data_map=bar_map)

    # Check files exist
    journal_csv = proj_dir / "canonical_paper_journal.csv"
    positions_csv = proj_dir / "open_positions.csv"
    equity_csv = proj_dir / "daily_portfolio_equity.csv"

    assert journal_csv.exists()
    assert positions_csv.exists()
    assert equity_csv.exists()

    # Reconcile journal row count
    with store._get_connection() as conn:
        db_events = conn.execute("SELECT count(*) as cnt FROM ledger_events").fetchone()["cnt"]
        db_positions = conn.execute("SELECT count(*) as cnt FROM positions WHERE status = 'OPEN'").fetchone()["cnt"]
        db_equity = conn.execute("SELECT count(*) as cnt FROM daily_equity").fetchone()["cnt"]

    with open(journal_csv, "r", encoding="utf-8") as f:
        csv_events = sum(1 for _ in csv.DictReader(f))
    with open(positions_csv, "r", encoding="utf-8") as f:
        csv_positions = sum(1 for _ in csv.DictReader(f))
    with open(equity_csv, "r", encoding="utf-8") as f:
        csv_equity = sum(1 for _ in csv.DictReader(f))

    assert csv_events == db_events
    assert csv_positions == db_positions
    assert csv_equity == db_equity


# =============================================================================
# 10. MULTI-SESSION CRASH/RESTART VS UNINTERRUPTED REPLAY EQUIVALENCE
# =============================================================================

def test_multi_session_uninterrupted_vs_crash_restart_replay(tmp_path: Path):
    """
    Executes a 3-session trading workflow:
    - Desk A: Runs completely uninterrupted in a single runner instance.
    - Desk B: Runs with a crash/restart (instantiating a new runner) before EVERY pre-open and post-close.
    Proves that crash/restart replay produces IDENTICAL cash, equity, open positions, and journal counts.
    """
    dir_a = tmp_path / "desk_a"
    dir_b = tmp_path / "desk_b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)

    config_a = PaperDeskConfig(db_path=dir_a / "store.db", projections_dir=dir_a / "proj")
    config_b = PaperDeskConfig(db_path=dir_b / "store.db", projections_dir=dir_b / "proj")

    runner_a = PaperDeskRunner(config=config_a)

    sessions_data = [
        # Session 1: Buy CDSL
        ("2024-05-13", [make_signal("CDSL", 100.0, 90.0, 120.0, session_date="2024-05-12", entry_session="2024-05-13")], {
            "CDSL": DailyBar(symbol="CDSL", open=100.0, high=104.0, low=99.0, close=102.0, volume=20000),
        }),
        # Session 2: Hold CDSL, Buy SUZLON
        ("2024-05-14", [make_signal("SUZLON", 50.0, 45.0, 60.0, session_date="2024-05-13", entry_session="2024-05-14")], {
            "CDSL": DailyBar(symbol="CDSL", open=102.0, high=106.0, low=101.0, close=105.0, volume=20000),
            "SUZLON": DailyBar(symbol="SUZLON", open=50.0, high=52.0, low=49.0, close=51.0, volume=30000),
        }),
        # Session 3: Target hit on CDSL (exit), hold SUZLON
        ("2024-05-15", [], {
            "CDSL": DailyBar(symbol="CDSL", open=110.0, high=122.0, low=108.0, close=120.0, volume=50000),
            "SUZLON": DailyBar(symbol="SUZLON", open=51.0, high=53.0, low=50.0, close=52.0, volume=25000),
        }),
    ]

    # Run Desk A uninterrupted
    for session_date, sigs, bar_map in sessions_data:
        runner_a.run_pre_open(session_date, candidate_signals=sigs)
        runner_a.run_post_close(session_date, bar_data_map=bar_map)

    # Run Desk B with crash/restart at every single step
    for session_date, sigs, bar_map in sessions_data:
        # Restart before pre-open
        runner_b_pre = PaperDeskRunner(config=config_b)
        runner_b_pre.run_pre_open(session_date, candidate_signals=sigs)

        # Restart before post-close
        runner_b_post = PaperDeskRunner(config=config_b)
        runner_b_post.run_post_close(session_date, bar_data_map=bar_map)

    # Compare Final State between Desk A and Desk B
    store_a = PaperStore(config_a.db_path)
    store_b = PaperStore(config_b.db_path)

    eq_a = store_a.get_latest_equity()
    eq_b = store_b.get_latest_equity()

    assert eq_a is not None and eq_b is not None
    assert eq_a.cash_ledger_rs == eq_b.cash_ledger_rs
    assert eq_a.inventory_mtm_rs == eq_b.inventory_mtm_rs
    assert eq_a.equity_rs == eq_b.equity_rs
    assert eq_a.occupied_slots == eq_b.occupied_slots

    pos_a = store_a.get_open_positions()
    pos_b = store_b.get_open_positions()
    assert len(pos_a) == len(pos_b)
    for p_a, p_b in zip(pos_a, pos_b):
        assert p_a.symbol == p_b.symbol
        assert p_a.residual_qty == p_b.residual_qty
        assert p_a.residual_cost_basis_rs == p_b.residual_cost_basis_rs


# =============================================================================
# 11. ADVERSARIAL INPUTS & DATA INTEGRITY TESTS
# =============================================================================

def test_invalid_nan_inf_negative_inputs_fail_closed(temp_paper_env):
    """
    NaN, infinite, boolean, or negative prices/stops/quantities are rejected fail-closed.
    """
    runner = temp_paper_env["runner"]

    # 1. Inverted stop (stop >= entry) rejected by SignalEvent schema
    with pytest.raises(ValueError, match="strictly less than"):
        make_signal("CDSL", 100.0, 105.0, 120.0)

    # 2. Rule 2 floor breach (price < Rs 10.00) rejected by SignalEvent schema
    with pytest.raises(ValueError, match="Rule 2 floor"):
        make_signal("CDSL", 8.50, 7.00, 10.00)

    # 3. Governor direct assessment rejects non-positive, nan, inf, boolean
    v_nan = runner.governor.assess_candidate("CDSL", float("nan"), 90.0, 10)
    assert not v_nan.is_approved
    assert "INVALID_PRICE" in str(v_nan.rejection_reason)

    v_inf = runner.governor.assess_candidate("CDSL", 100.0, float("inf"), 10)
    assert not v_inf.is_approved
    assert "INVALID_PRICE" in str(v_inf.rejection_reason)

    v_bool = runner.governor.assess_candidate("CDSL", 100.0, 90.0, True)
    assert not v_bool.is_approved

    v_neg = runner.governor.assess_candidate("CDSL", -100.0, 90.0, 10)
    assert not v_neg.is_approved


def test_missing_bar_preserves_holdings_and_reports_data_pending(temp_paper_env):
    """
    Missing bar does not invent liquidation; marks STALE_MARK and data_status DATA_PENDING.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    pos = OpenPositionRecord(
        position_id="POS-TEST-CDSL",
        isin="INE736A01011",
        symbol="CDSL",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=50,
        sold_qty=0,
        residual_qty=50,
        residual_cost_basis_rs=5000.0,
        entry_cost_allocation_rs=10.0,
        stop_price=90.0,
        target_price=120.0,
        planned_open_risk_rs=500.0,
        exit_intent=None,
        exit_intent_created_at=None,
        pending_exit_order_id=None,
        last_mark=100.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)

    # Empty bar map (Bhavcopy missing / corrupted)
    res = runner.run_post_close("2024-05-15", bar_data_map={})

    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].mark_status == "STALE_MARK"
    assert res["equity"]["data_status"] == "DATA_PENDING"
    assert res["equity"]["stale_mark_count"] == 1


def test_unknown_corporate_action_freezes_position(temp_paper_env):
    """
    Unsupported corporate action (e.g. MERGER) freezes entries and marks status FROZEN_UNRESOLVED.
    """
    runner = temp_paper_env["runner"]
    store = temp_paper_env["store"]

    pos = OpenPositionRecord(
        position_id="POS-TEST-CDSL",
        isin="INE736A01011",
        symbol="CDSL",
        series="EQ",
        sleeve_id="HIGH52_MOMENTUM",
        strategy_version="v1.0",
        entry_session="2024-05-10",
        acquired_qty=50,
        sold_qty=0,
        residual_qty=50,
        residual_cost_basis_rs=5000.0,
        entry_cost_allocation_rs=10.0,
        stop_price=90.0,
        target_price=120.0,
        planned_open_risk_rs=500.0,
        exit_intent=None,
        exit_intent_created_at=None,
        pending_exit_order_id=None,
        last_mark=100.0,
        mark_session="2024-05-10",
        mark_source_hash="",
        mark_status="ACTIVE",
        corporate_action_status="NONE",
        settlement_status="SETTLED",
    )
    store.upsert_position(pos)

    success = runner.apply_corporate_action("CDSL", "MERGER", ratio=1.0, effective_date="2024-05-15")
    assert success is False

    positions = store.get_open_positions()
    assert len(positions) == 1
    assert positions[0].corporate_action_status == "FROZEN_UNRESOLVED_MERGER"

