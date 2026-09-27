"""
test_two_tranche_split_exit.py - Unit & Invariant Tests for Two-Tranche Split-Exit Bracket State Machine
========================================================================================================
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).
"""

from datetime import time as dtime
import pytest

from antigravity.models.track2_paper_execution import (
    BracketOrderManager,
    BracketOrderState,
    calculate_transaction_costs,
)


def test_bracket_creation():
    # Entry: 1000, Stop: 980, Total: 10 shares, Target 1.5R: 1000 + (1.5 * 20) = 1030
    bracket = BracketOrderManager.create_bracket(
        order_id="brk_001",
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=980.0,
        total_shares=10,
        target_1_rr=1.5,
    )
    assert bracket.symbol == "CDSL"
    assert bracket.entry_price == 1000.0
    assert bracket.initial_stop_price == 980.0
    assert bracket.total_shares == 10
    assert bracket.t1_shares == 5
    assert bracket.t2_shares == 5
    assert bracket.t1_target_price == 1030.0
    assert bracket.t2_current_stop_price == 980.0
    assert bracket.is_t1_target_filled is False
    assert bracket.is_t2_breakeven_trailed is False
    assert bracket.terminal_state is None


def test_initial_stop_loss_hit_before_target():
    # Price falls to 975 -> Stop loss at 980 triggers full exit for both tranches
    bracket = BracketOrderManager.create_bracket("brk_002", "CDSL", 1000.0, 980.0, 10)
    updated = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=978.0,
        tick_low=975.0,
        timestamp="2026-09-22T09:45:00+05:30",
    )
    assert updated.is_stopped_out is True
    assert updated.terminal_state == "STOPPED_OUT_FULL"
    assert updated.t1_exit_price == 980.0
    assert updated.t2_exit_price == 980.0
    assert updated.realized_pnl_gross == -200.0  # 10 shares * -20 Rs
    assert updated.total_friction_cost > 0.0
    assert updated.realized_pnl_net < -200.0


def test_tranche1_target_hit_moves_tranche2_to_breakeven():
    # Price rises to 1035 -> Tranche 1 fills at 1030
    bracket = BracketOrderManager.create_bracket("brk_003", "CDSL", 1000.0, 980.0, 10)
    
    # Tick reaches 1035 with execution evidence
    updated = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1032.0,
        tick_high=1035.0,
        timestamp="2026-09-22T10:15:00+05:30",
        execution_evidence=True,
    )
    assert updated.is_t1_target_filled is True
    assert updated.t1_exit_price == 1030.0
    assert updated.realized_pnl_gross == 150.0  # 5 shares * +30 Rs
    assert updated.is_t2_breakeven_trailed is True
    assert updated.t2_current_stop_price == 1000.0  # Trailed to Breakeven
    assert updated.terminal_state is None  # Tranche 2 is still running!

    # Subsequently, price reverses and drops to 995 -> Tranche 2 exits at Breakeven (1000.0)
    final_state = BracketOrderManager.update_bracket_quote(
        bracket=updated,
        ltp=998.0,
        tick_low=995.0,
        timestamp="2026-09-22T11:00:00+05:30",
    )
    assert final_state.is_stopped_out is True
    assert final_state.terminal_state == "STOPPED_OUT_T2_BREAKEVEN"
    assert final_state.t2_exit_price == 1000.0
    assert final_state.realized_pnl_net > 0.0  # Net profit preserved after friction!


def test_quote_only_target_reach_is_unverified():
    # Codex R06: A quote tick touch without execution evidence does not fill target or realize profit
    bracket = BracketOrderManager.create_bracket("brk_probe", "CDSL", 1000.0, 980.0, 10)
    quote_state = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1032.0,
        tick_high=1035.0,
        execution_evidence=False,
    )
    assert quote_state.is_t1_target_filled is False
    assert quote_state.realized_pnl_gross == 0.0
    assert quote_state.total_friction_cost == 0.0
    assert quote_state.is_t2_breakeven_trailed is False



def test_broker_cutoff_cas_squareoff():
    # Open MIS position at 15:13 IST (after 15:12 CAS cutoff) triggers squareoff
    bracket = BracketOrderManager.create_bracket("brk_004", "CDSL", 1000.0, 980.0, 10, product_type="MIS")
    updated = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1015.0,
        current_time_ist=dtime(15, 13),
        is_cas_eligible=True,
    )
    assert updated.is_eod_squared_off is True
    assert updated.terminal_state == "CLOSED_MIS_SQUAREOFF"
    assert updated.t1_exit_price == 1015.0
    assert updated.t2_exit_price == 1015.0
    assert updated.realized_pnl_gross == 150.0  # 10 shares * +15 Rs


def test_broker_cutoff_non_cas_squareoff():
    # MIS Position before 15:08 Dhan MIS cutoff does NOT force squareoff
    bracket = BracketOrderManager.create_bracket("brk_005", "CDSL", 1000.0, 980.0, 10, product_type="MIS")
    mid_state = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1010.0,
        current_time_ist=dtime(15, 5),
        is_cas_eligible=False,
    )
    assert mid_state.terminal_state is None

    # At 15:09 IST forces squareoff for MIS (pre-empting Dhan 15:10 auto-squareoff)
    final_state = BracketOrderManager.update_bracket_quote(
        bracket=mid_state,
        ltp=1012.0,
        current_time_ist=dtime(15, 9),
        is_cas_eligible=False,
    )
    assert final_state.is_eod_squared_off is True
    assert final_state.terminal_state == "CLOSED_MIS_SQUAREOFF"


def test_cnc_position_rolls_overnight_past_cutoff():
    # CNC Delivery Position at 15:26 IST does NOT force squareoff (rolls overnight)
    bracket = BracketOrderManager.create_bracket("brk_006", "CDSL", 1000.0, 980.0, 10, product_type="CNC")
    eod_state = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1012.0,
        current_time_ist=dtime(15, 26),
        is_cas_eligible=False,
    )
    assert eod_state.is_eod_squared_off is False
    assert eod_state.terminal_state is None


def test_gap_down_slippage_execution():
    # Entry at 1000, initial stop at 980. Next morning gaps down to 960 (below stop)
    bracket = BracketOrderManager.create_bracket("brk_007", "CDSL", 1000.0, 980.0, 10, product_type="CNC")
    gapped = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=958.0,
        tick_low=955.0,
        tick_open=960.0,  # Opened at 960 (20 Rs below stop!)
        adverse_slippage_pct=0.01,  # 1% adverse slippage on gap fill -> 960 * 0.99 = 950.40
        timestamp="2026-09-23T09:15:00+05:30",
    )
    assert gapped.is_stopped_out is True
    assert gapped.terminal_state == "STOPPED_OUT_FULL"
    assert gapped.t1_exit_price == 950.40  # Exit modeled at gap price with slippage, NOT stop price!
    assert gapped.t2_exit_price == 950.40
    assert gapped.realized_pnl_gross == round(10 * (950.40 - 1000.0), 2)  # -496.0 Rs (worse than -200 Rs stop)



def test_transaction_cost_calculator():
    # Buy 100 shares of CDSL at 1000 (Turnover: 1,00,000)
    costs_buy = calculate_transaction_costs(1000.0, 100, side="BUY", is_intraday=True)
    assert costs_buy["turnover"] == 100000.0
    assert costs_buy["brokerage"] == 20.0  # min(20, 30) = 20
    assert costs_buy["stt"] == 0.0  # Zero STT on buy for intraday
    assert costs_buy["exchange_charges"] == 3.07  # 0.0030699% per NSE circular FA73061
    assert costs_buy["gst"] == 4.17  # 18% of (20 + 3.07 + 0.10)
    assert costs_buy["stamp_duty"] == 3.0  # 0.003% of 1,00,000 = 3.0
    assert costs_buy["total_cost"] == 30.34

    # Sell 100 shares of CDSL at 1030 (Turnover: 1,03,000)
    costs_sell = calculate_transaction_costs(1030.0, 100, side="SELL", is_intraday=True)
    assert costs_sell["turnover"] == 103000.0
    assert costs_sell["stt"] == 25.75  # 0.025% of 1,03,000 = 25.75
    assert costs_sell["stamp_duty"] == 0.0  # Zero stamp duty on sell
    assert costs_sell["total_cost"] > 50.0


def test_tranche2_target_runner_hit_exits_at_full_target():
    # Entry: 1000, Stop: 980, Risk: 20/sh, 10 shares (5 T1, 5 T2)
    # T1 target: 1030.0 (+1.5R), T2 target: 1060.0 (+3.0R)
    bracket = BracketOrderManager.create_bracket("brk_runner", "CDSL", 1000.0, 980.0, 10, target_1_rr=1.5, target_2_rr=3.0)
    assert bracket.t1_target_price == 1030.0
    assert bracket.t2_target_price == 1060.0

    # Step 1: Hit T1 at 1032 with execution evidence
    t1_state = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1032.0,
        tick_high=1035.0,
        execution_evidence=True,
    )
    assert t1_state.is_t1_target_filled is True
    assert t1_state.t1_exit_price == 1030.0
    assert t1_state.is_t2_breakeven_trailed is True
    assert t1_state.t2_current_stop_price == 1000.0
    assert t1_state.terminal_state is None  # T2 runner still active

    # Step 2: Quote touches 1062 without execution evidence -> must NOT fill T2
    probe_state = BracketOrderManager.update_bracket_quote(
        bracket=t1_state,
        ltp=1062.0,
        tick_high=1065.0,
        execution_evidence=False,
    )
    assert probe_state.is_t2_target_filled is False
    assert probe_state.terminal_state is None

    # Step 3: Hits T2 with execution evidence -> Fills at 1060.0 (+3.0R)
    final_state = BracketOrderManager.update_bracket_quote(
        bracket=t1_state,
        ltp=1062.0,
        tick_high=1065.0,
        execution_evidence=True,
    )
    assert final_state.is_t2_target_filled is True
    assert final_state.t2_exit_price == 1060.0
    assert final_state.terminal_state == "TARGET_FILLED_FULL"
    # Gross PnL: 5 * 30 + 5 * 60 = 150 + 300 = 450 Rs
    assert final_state.realized_pnl_gross == 450.0
    assert final_state.total_friction_cost > 0.0
    assert final_state.realized_pnl_net == round(450.0 - final_state.total_friction_cost, 2)
    assert final_state.realized_pnl_net > 400.0  # Captures full runner upside

