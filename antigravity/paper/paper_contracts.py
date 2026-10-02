"""
antigravity/paper/paper_contracts.py
===================================
Data contracts, event types, and schemas for ARGUS 8i Track 2 Canonical Paper Desk.

Design Invariants (Codex Deliberation 2026-10-02):
1. Immutable Event Grain: canonical_paper_journal records append-only events, not mutable trade rows.
2. Single-Sequence Synchronization: journal, positions, and equity share a common generation/sequence ID.
3. Separation of Concerns: entry eligibility strictly separated from exit processing.
4. Non-Qualifying Label Invariance: OHLCV fills labeled BAR_SCENARIO_NON_QUALIFYING by default.
5. Finite Arithmetic & Explicit Rounding: all rupee amounts rounded to paise (2 decimal places).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
import math
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1
TRACK_ID = "TRACK_2"
DESK_ID = "ARGUS_TRACK2_LIQUID_PAPER"
EVIDENCE_MODE_DEFAULT = "BAR_SCENARIO_NON_QUALIFYING"
REVIEW_STATUS_DEFAULT = "EXPLORATORY_DIAGNOSTIC"


class PaperEventType(str, Enum):
    RESERVATION_CREATED = "RESERVATION_CREATED"
    RESERVATION_CANCELLED = "RESERVATION_CANCELLED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    FILL_PARTIAL = "FILL_PARTIAL"
    FILL_COMPLETE = "FILL_COMPLETE"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_MTM_VALUED = "POSITION_MTM_VALUED"
    POSITION_EXIT_INTENT = "POSITION_EXIT_INTENT"
    POSITION_DISQUALIFIED = "POSITION_DISQUALIFIED"
    POSITION_CLOSED = "POSITION_CLOSED"
    DIVIDEND_ADJUSTMENT = "DIVIDEND_ADJUSTMENT"
    SETTLEMENT_PROCESSED = "SETTLEMENT_PROCESSED"


class PaperSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PaperOrderType(str, Enum):
    MARKET_OPEN = "MARKET_OPEN"
    LIMIT_OPEN = "LIMIT_OPEN"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_STOP = "TIME_STOP"
    DISQUALIFICATION_OPEN = "DISQUALIFICATION_OPEN"


@dataclass(frozen=True)
class PaperJournalEvent:
    event_id: str
    event_seq: int
    event_type: str
    event_at: str
    recorded_at: str
    session_date: str
    decision_at: str
    signal_session: str
    intended_execution_session: str
    sleeve_id: str
    strategy_version: str
    signal_id: str
    order_id: str
    reservation_id: str
    position_id: str
    fill_id: Optional[str]
    exchange: str
    isin: str
    symbol: str
    series: str
    side: str
    order_type: str
    state_before: str
    state_after: str
    requested_qty: int
    fill_qty_delta: int
    cumulative_fill_qty: int
    remaining_order_qty: int
    benchmark_price: float
    limit_price: Optional[float]
    fill_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    planned_risk_rs: float
    slippage_bps: float
    slippage_rs: float
    turnover_rs: float
    brokerage_rs: float
    stt_rs: float
    exchange_fee_rs: float
    sebi_fee_rs: float
    stamp_duty_rs: float
    gst_rs: float
    dp_fee_rs: float
    dp_group_id: Optional[str]
    total_cost_rs: float
    cash_delta_rs: float
    realized_net_pnl_delta_rs: float
    exit_reason: Optional[str]
    reject_reason: Optional[str]
    eligibility_verdict: str
    evidence_ref: str
    evidence_hash: str
    evidence_available_at: str
    fill_model_version: str
    qualifying_evidence_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpenPositionRecord:
    position_id: str
    isin: str
    symbol: str
    series: str
    sleeve_id: str
    strategy_version: str
    entry_session: str
    acquired_qty: int
    sold_qty: int
    residual_qty: int
    residual_cost_basis_rs: float
    entry_cost_allocation_rs: float
    stop_price: float
    target_price: float
    planned_open_risk_rs: float
    exit_intent: Optional[str]
    exit_intent_created_at: Optional[str]
    pending_exit_order_id: Optional[str]
    last_mark: float
    mark_session: str
    mark_source_hash: str
    mark_status: str
    corporate_action_status: str
    settlement_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DailyPortfolioEquityRecord:
    session_date: str
    valuation_at: str
    cash_ledger_rs: float
    cash_settled_rs: float
    receivable_rs: float
    payable_rs: float
    reserved_cash_rs: float
    free_cash_rs: float
    inventory_mtm_rs: float
    equity_rs: float
    external_flow_rs: float
    realized_net_pnl_cumulative_rs: float
    unrealized_pnl_rs: float
    costs_cumulative_rs: float
    occupied_slots: int
    pending_slots: int
    committed_exposure_rs: float
    marked_exposure_rs: float
    planned_open_risk_rs: float
    reserved_risk_rs: float
    pending_exit_count: int
    unresolved_position_count: int
    stale_mark_count: int
    drawdown_rs: float
    drawdown_pct: float
    cash_buffer_breach: bool
    risk_breach: bool
    data_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
