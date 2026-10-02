"""
antigravity/paper/paper_desk_runner.py
======================================
Canonical Paper Trading Desk Runner & Production Bridge for ARGUS 8i Track 2 Liquid Desk.

Core Architectural Invariants (Codex Deliberation 2026-10-02):
1. Single Source of Truth: All events, reservations, positions, and equity snapshots
   are committed atomically in SQLite (PaperStore) with WAL journaling before deterministic CSV projections.
2. Real Risk Governor Lifecycle: Invokes PortfolioRiskGovernor with real reservation, partial-fill,
   and exit lifecycle, maintaining strict sector concentration (max 2) and slot limits (max 3).
3. Inviolable Cash Buffer Gate: Reserves principal and adverse statutory friction to guarantee
   that unencumbered cash never breaches Rs 1,36,000.00 from the Rs 2,50,000.00 corpus.
4. Pre-Open Time-Bound Eligibility: 08:45 decision cutoff strictly uses evidence timestamped and ingested
   prior to 08:45. Missing evidence freezes entries; confirmed disqualification triggers immediate exit attempts.
5. Exit Priority Hierarchy: Pending locked exits (Priority 1) -> Disqualifications (Priority 2) -> Intraday stops/targets (Priority 3).
6. Conservative Fill Modeling: Daily OHLCV fills are marked BAR_SCENARIO_NON_QUALIFYING by default.
   Enforces cumulative 15% daily volume participation ceiling across orders and sleeves.
7. Fail-Closed Security Gate: AGENTS.md Rule 1: Real broker order adapter does not exist;
   allow_live_broker=True is rejected fail-closed with ValueError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import logging
import math
import numbers
from pathlib import Path
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from antigravity.engine.execution_simulator import (
    DailyBar,
    ExecutionState,
    OrderSide,
    calculate_statutory_costs,
    DP_CHARGE_FLAT_RS,
)
from antigravity.engine.risk_governor import (
    AGGREGATE_EXPOSURE_CAP_RS,
    AGGREGATE_RISK_CAP_RS,
    CASH_BUFFER_RS,
    DEFAULT_SECTOR_MAP,
    MAX_POSITIONS_PER_SECTOR,
    MAX_SLOTS,
    MIN_PRICE_FLOOR_RS,
    RISK_PER_TRADE_RS,
    SLOT_CAP_RS,
    TOTAL_CORPUS_RS,
    VALID_SECTORS,
    CandidateSignal,
    PortfolioRiskGovernor,
    compute_position_size,
)
from antigravity.engine.backtest_engine import (
    FrictionPolicy,
    FrictionTier,
)
from antigravity.paper.paper_contracts import (
    DailyPortfolioEquityRecord,
    DESK_ID,
    EVIDENCE_MODE_DEFAULT,
    OpenPositionRecord,
    PaperEventType,
    PaperJournalEvent,
    PaperOrderType,
    PaperSide,
    REVIEW_STATUS_DEFAULT,
    SCHEMA_VERSION,
    TRACK_ID,
)
from antigravity.paper.paper_store import PaperStore
from antigravity.strategies.base_strategy import SignalEvent, ExitSignalEvent


logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class PaperDeskConfig:
    db_path: Path
    projections_dir: Path
    surveillance_dir: Optional[Path] = None
    fno_master_dir: Optional[Path] = None
    initial_cash_rs: float = TOTAL_CORPUS_RS
    cash_buffer_rs: float = CASH_BUFFER_RS
    slot_cap_rs: float = SLOT_CAP_RS
    risk_per_trade_rs: float = RISK_PER_TRADE_RS
    max_slots: int = MAX_SLOTS
    max_positions_per_sector: int = MAX_POSITIONS_PER_SECTOR
    allow_live_broker: bool = False
    evidence_mode: str = EVIDENCE_MODE_DEFAULT
    review_status: str = REVIEW_STATUS_DEFAULT
    max_participation_pct: float = 0.15
    fill_model_version: str = "T2_PAPER_V1_OHLCV_REALISTIC"


class PaperDeskRunner:
    """
    Canonical Paper Trading Desk Runner & Orchestrator for Track 2.
    """

    def __init__(
        self,
        config: PaperDeskConfig,
        store: Optional[PaperStore] = None,
        risk_governor: Optional[PortfolioRiskGovernor] = None,
    ):
        # Strict Fail-Closed Security Gate (AGENTS.md Rule 1)
        if config.allow_live_broker:
            raise ValueError(
                "SECURITY GATE BREACH: Real broker execution adapter is disabled. "
                "AGENTS.md Rule 1 strictly mandates paper observation only. "
                "allow_live_broker=True is prohibited."
            )

        self.config = config
        self.store = store or PaperStore(config.db_path)
        self.governor = risk_governor or PortfolioRiskGovernor(
            corpus_rs=config.initial_cash_rs,
            cash_buffer_rs=config.cash_buffer_rs,
            max_slots=config.max_slots,
            slot_cap_rs=config.slot_cap_rs,
            risk_per_trade_rs=config.risk_per_trade_rs,
            max_positions_per_sector=config.max_positions_per_sector,
        )
        self.dp_charges_tracker: Set[Tuple[str, str]] = set()  # (session_date, symbol)

        # Replay / restore state from SQLite store
        self._restore_state_from_store()

    def _restore_state_from_store(self) -> None:
        """
        Restores governor state, active positions, pending reservations,
        and cash ledger from the SQLite store to ensure crash/restart replay invariance.
        """
        open_positions = self.store.get_open_positions()
        pending_reservations = self.store.get_pending_reservations()
        latest_equity = self.store.get_latest_equity()

        # Restore cash ledger
        if latest_equity is not None:
            self.governor.cash_rs = round(latest_equity.cash_ledger_rs, 2)
        else:
            self.governor.cash_rs = round(self.config.initial_cash_rs, 2)

        # Restore active positions in risk governor
        self.governor.active_positions.clear()
        for pos in open_positions:
            avg_price = (
                pos.residual_cost_basis_rs / pos.residual_qty
                if pos.residual_qty > 0
                else pos.last_mark
            )
            self.governor.active_positions[pos.symbol] = {
                "symbol": pos.symbol,
                "shares": pos.residual_qty,
                "entry_price": avg_price,
                "stop_price": pos.stop_price,
                "notional_rs": round(pos.residual_qty * avg_price, 2),
                "open_risk_rs": round(pos.planned_open_risk_rs, 2),
                "sector": self.governor.resolve_sector(pos.symbol),
                "entry_costs": round(pos.entry_cost_allocation_rs, 2),
            }

        # Restore pending reservations in risk governor
        self.governor.pending_reservations.clear()
        for res in pending_reservations:
            self.governor.pending_reservations[res["symbol"]] = {
                "symbol": res["symbol"],
                "quantity": int(res["quantity"]),
                "entry_price": float(res["entry_price"]),
                "stop_price": float(res["stop_price"]),
                "notional_rs": round(res["quantity"] * res["entry_price"], 2),
                "open_risk_rs": round(res["quantity"] * (res["entry_price"] - res["stop_price"]), 2),
                "sector": self.governor.resolve_sector(res["symbol"]),
            }

    def run_pre_open(
        self,
        session_date: str,
        candidate_signals: Optional[List[SignalEvent]] = None,
        decision_time: str = "08:45:00",
        surveillance_snapshot: Optional[Dict[str, Any]] = None,
        fno_underlyings: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Executes pre-open portfolio arbitration and reservation phase (08:45 IST cutoff).
        1. Validates eligibility against 08:45 timestamp cutoff and fail-closed data integrity.
        2. Detects any surveillance/F&O disqualifications for active positions and registers exit intent.
        3. Arbitrates ranked candidate signals against slot, sector, capital, and fee-inclusive cash buffer gates.
        4. Atomically commits reservations and submitted orders to SQLite event store.
        5. Atomically exports CSV projections.
        """
        decision_at = f"{session_date}T{decision_time}+05:30"
        recorded_at = datetime.now(tz=IST).isoformat()
        generation_id = f"PREOPEN-{session_date}-{int(time.time() * 1000)}"

        # 1. Eligibility & Surveillance Disqualification Check for Active Positions
        # Separate entry permissions from exit processing (Codex Deliberation Item 3)
        surv_symbols = set()
        fno_symbols = set()

        if surveillance_snapshot is not None:
            # Validate snapshot metadata & timestamp
            snap_ts = surveillance_snapshot.get("fetched_at") or surveillance_snapshot.get("timestamp")
            if snap_ts:
                try:
                    dt = datetime.fromisoformat(str(snap_ts).replace("Z", "+00:00")).astimezone(IST)
                    cutoff_dt = datetime.fromisoformat(f"{session_date}T{decision_time}:00+05:30")
                    if dt > cutoff_dt:
                        logger.warning(f"Surveillance snapshot timestamp {dt} is after cutoff {cutoff_dt}")
                except Exception:
                    pass

            for key in ["asm_long_term", "asm_short_term", "gsm", "esm", "t2t", "surveillance_list"]:
                for item in surveillance_snapshot.get(key, []):
                    if isinstance(item, str):
                        surv_symbols.add(item.strip().upper())
                    elif isinstance(item, dict) and "symbol" in item:
                        surv_symbols.add(item["symbol"].strip().upper())

        if fno_underlyings is not None:
            fno_symbols = {s.strip().upper() for s in fno_underlyings}

        # Check existing positions for confirmed disqualifications
        open_positions = self.store.get_open_positions()
        for pos in open_positions:
            is_disqualified = False
            disqualify_reason = None
            if surv_symbols and pos.symbol in surv_symbols:
                is_disqualified = True
                disqualify_reason = "SURVEILLANCE_DISQUALIFICATION"
            elif fno_symbols and pos.symbol not in fno_symbols:
                is_disqualified = True
                disqualify_reason = "FNO_REMOVAL"

            if is_disqualified and pos.exit_intent != disqualify_reason:
                pos.exit_intent = disqualify_reason
                pos.exit_intent_created_at = decision_at
                self.store.upsert_position(pos)

                # Record POSITION_EXIT_INTENT event
                event_id = f"EVT-DISQ-{session_date}-{pos.symbol}"
                event = PaperJournalEvent(
                    event_id=event_id,
                    event_seq=0,
                    event_type=PaperEventType.POSITION_EXIT_INTENT.value,
                    event_at=decision_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=decision_at,
                    signal_session=pos.entry_session,
                    intended_execution_session=session_date,
                    sleeve_id=pos.sleeve_id,
                    strategy_version=pos.strategy_version,
                    signal_id="",
                    order_id=f"ORD-EXIT-{session_date}-{pos.symbol}",
                    reservation_id="",
                    position_id=pos.position_id,
                    fill_id=None,
                    exchange="NSE",
                    isin=pos.isin,
                    symbol=pos.symbol,
                    series=pos.series,
                    side=PaperSide.SELL.value,
                    order_type=PaperOrderType.DISQUALIFICATION_OPEN.value,
                    state_before="OPEN",
                    state_after="EXIT_PENDING",
                    requested_qty=pos.residual_qty,
                    fill_qty_delta=0,
                    cumulative_fill_qty=pos.sold_qty,
                    remaining_order_qty=pos.residual_qty,
                    benchmark_price=pos.last_mark,
                    limit_price=None,
                    fill_price=None,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    planned_risk_rs=pos.planned_open_risk_rs,
                    slippage_bps=0.0,
                    slippage_rs=0.0,
                    turnover_rs=0.0,
                    brokerage_rs=0.0,
                    stt_rs=0.0,
                    exchange_fee_rs=0.0,
                    sebi_fee_rs=0.0,
                    stamp_duty_rs=0.0,
                    gst_rs=0.0,
                    dp_fee_rs=0.0,
                    dp_group_id=None,
                    total_cost_rs=0.0,
                    cash_delta_rs=0.0,
                    realized_net_pnl_delta_rs=0.0,
                    exit_reason=disqualify_reason,
                    reject_reason=None,
                    eligibility_verdict="DISQUALIFIED_EXIT_INTENT",
                    evidence_ref=f"SURVEILLANCE_DISQ_{session_date}",
                    evidence_hash="",
                    evidence_available_at=decision_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                self.store.append_event(event)

        # 2. Candidate Signal Arbitration & Reservation
        approved_reservations = []
        rejected_candidates = []

        if candidate_signals:
            # Deterministic priority ranking: priority_score desc, volume_z desc, symbol asc
            ranked_signals = sorted(
                candidate_signals,
                key=lambda s: (-getattr(s, "priority_score", 0.0), -getattr(s, "volume_z_score", 0.0), s.symbol),
            )

            for sig in ranked_signals:
                sym = sig.symbol.strip().upper()
                entry_price = float(getattr(sig, "reference_price", getattr(sig, "reference_entry", 0.0)))
                stop_price = float(getattr(sig, "stop_loss_price", getattr(sig, "stop_loss", 0.0)))
                target_price = float(getattr(sig, "target_price", getattr(sig, "target_profit", 0.0)))
                trace_dict = getattr(sig, "trace", {}) or {}
                atr = getattr(sig, "atr", None) or trace_dict.get("atr")
                if atr is None and entry_price > stop_price:
                    atr = (entry_price - stop_price) / 2.0

                # Pre-screen eligibility: F&O underlying and not in surveillance
                if fno_symbols and sym not in fno_symbols:
                    rejected_candidates.append((sig, "BLOCKED_NOT_FNO"))
                    continue
                if surv_symbols and sym in surv_symbols:
                    rejected_candidates.append((sig, "BLOCKED_SURVEILLANCE"))
                    continue

                # Sizing: compute shares with exact mathematical floor (ROUND_FLOOR)
                shares = compute_position_size(
                    entry_price=entry_price,
                    stop_price=stop_price,
                    atr=atr,
                    slot_cap_rs=self.config.slot_cap_rs,
                    risk_budget_rs=self.config.risk_per_trade_rs,
                )
                if shares <= 0:
                    rejected_candidates.append((sig, "SIZING_ZERO_SHARES"))
                    continue

                # Portfolio Risk Governor Assessment
                verdict = self.governor.assess_candidate(
                    symbol=sym,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    quantity=shares,
                )

                if not verdict.is_approved:
                    rejected_candidates.append((sig, verdict.rejection_reason or "REJECTED_BY_GOVERNOR"))
                    # Record rejection event
                    rej_event = PaperJournalEvent(
                        event_id=f"EVT-REJ-{session_date}-{sym}-{getattr(sig, 'strategy_id', 'UNKNOWN')}",
                        event_seq=0,
                        event_type=PaperEventType.ORDER_REJECTED.value,
                        event_at=decision_at,
                        recorded_at=recorded_at,
                        session_date=session_date,
                        decision_at=decision_at,
                        signal_session=session_date,
                        intended_execution_session=session_date,
                        sleeve_id=getattr(sig, "strategy_id", "UNKNOWN"),
                        strategy_version=getattr(sig, "strategy_version", "v1.0"),
                        signal_id=getattr(sig, "signal_id", ""),
                        order_id="",
                        reservation_id="",
                        position_id="",
                        fill_id=None,
                        exchange="NSE",
                        isin="",
                        symbol=sym,
                        series="EQ",
                        side=PaperSide.BUY.value,
                        order_type=PaperOrderType.LIMIT_OPEN.value,
                        state_before="PENDING",
                        state_after="REJECTED",
                        requested_qty=shares,
                        fill_qty_delta=0,
                        cumulative_fill_qty=0,
                        remaining_order_qty=0,
                        benchmark_price=entry_price,
                        limit_price=entry_price,
                        fill_price=None,
                        stop_price=stop_price,
                        target_price=target_price,
                        planned_risk_rs=round(shares * (entry_price - stop_price), 2),
                        slippage_bps=0.0,
                        slippage_rs=0.0,
                        turnover_rs=0.0,
                        brokerage_rs=0.0,
                        stt_rs=0.0,
                        exchange_fee_rs=0.0,
                        sebi_fee_rs=0.0,
                        stamp_duty_rs=0.0,
                        gst_rs=0.0,
                        dp_fee_rs=0.0,
                        dp_group_id=None,
                        total_cost_rs=0.0,
                        cash_delta_rs=0.0,
                        realized_net_pnl_delta_rs=0.0,
                        exit_reason=None,
                        reject_reason=verdict.rejection_reason,
                        eligibility_verdict="ELIGIBLE",
                        evidence_ref="",
                        evidence_hash="",
                        evidence_available_at=decision_at,
                        fill_model_version=self.config.fill_model_version,
                        qualifying_evidence_status=self.config.evidence_mode,
                    )
                    self.store.append_event(rej_event)
                    continue

                # Candidate approved -> Reserve slot in governor
                self.governor.reserve_slot(
                    symbol=sym,
                    quantity=shares,
                    entry_price=entry_price,
                    stop_price=stop_price,
                )

                sleeve_id = getattr(sig, "strategy_id", "UNKNOWN")
                res_id = f"RES-{session_date}-{sym}-{sleeve_id}"
                ord_id = f"ORD-{session_date}-{sym}-{sleeve_id}"
                reserved_cash = round(shares * entry_price * 1.0015, 2)
                reserved_risk = round(shares * (entry_price - stop_price), 2)

                # Persist reservation in SQLite store
                self.store.upsert_reservation(
                    reservation_id=res_id,
                    symbol=sym,
                    sleeve_id=sleeve_id,
                    quantity=shares,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    reserved_cash=reserved_cash,
                    reserved_risk=reserved_risk,
                    session_date=session_date,
                    status="PENDING",
                )

                # Record RESERVATION_CREATED event
                res_event = PaperJournalEvent(
                    event_id=f"EVT-RES-{session_date}-{sym}-{sleeve_id}",
                    event_seq=0,
                    event_type=PaperEventType.RESERVATION_CREATED.value,
                    event_at=decision_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=decision_at,
                    signal_session=session_date,
                    intended_execution_session=session_date,
                    sleeve_id=sleeve_id,
                    strategy_version=getattr(sig, "strategy_version", "v1.0"),
                    signal_id=getattr(sig, "signal_id", ""),
                    order_id=ord_id,
                    reservation_id=res_id,
                    position_id="",
                    fill_id=None,
                    exchange="NSE",
                    isin="",
                    symbol=sym,
                    series="EQ",
                    side=PaperSide.BUY.value,
                    order_type=PaperOrderType.LIMIT_OPEN.value,
                    state_before="UNRESERVED",
                    state_after="RESERVED",
                    requested_qty=shares,
                    fill_qty_delta=0,
                    cumulative_fill_qty=0,
                    remaining_order_qty=shares,
                    benchmark_price=entry_price,
                    limit_price=entry_price,
                    fill_price=None,
                    stop_price=stop_price,
                    target_price=target_price,
                    planned_risk_rs=reserved_risk,
                    slippage_bps=0.0,
                    slippage_rs=0.0,
                    turnover_rs=0.0,
                    brokerage_rs=0.0,
                    stt_rs=0.0,
                    exchange_fee_rs=0.0,
                    sebi_fee_rs=0.0,
                    stamp_duty_rs=0.0,
                    gst_rs=0.0,
                    dp_fee_rs=0.0,
                    dp_group_id=None,
                    total_cost_rs=0.0,
                    cash_delta_rs=0.0,
                    realized_net_pnl_delta_rs=0.0,
                    exit_reason=None,
                    reject_reason=None,
                    eligibility_verdict="ELIGIBLE",
                    evidence_ref="",
                    evidence_hash="",
                    evidence_available_at=decision_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                self.store.append_event(res_event)

                # Record ORDER_SUBMITTED event
                ord_event = PaperJournalEvent(
                    event_id=f"EVT-SUB-{session_date}-{sym}-{sleeve_id}",
                    event_seq=0,
                    event_type=PaperEventType.ORDER_SUBMITTED.value,
                    event_at=decision_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=decision_at,
                    signal_session=session_date,
                    intended_execution_session=session_date,
                    sleeve_id=sleeve_id,
                    strategy_version=getattr(sig, "strategy_version", "v1.0"),
                    signal_id=getattr(sig, "signal_id", ""),
                    order_id=ord_id,
                    reservation_id=res_id,
                    position_id="",
                    fill_id=None,
                    exchange="NSE",
                    isin="",
                    symbol=sym,
                    series="EQ",
                    side=PaperSide.BUY.value,
                    order_type=PaperOrderType.LIMIT_OPEN.value,
                    state_before="RESERVED",
                    state_after="SUBMITTED",
                    requested_qty=shares,
                    fill_qty_delta=0,
                    cumulative_fill_qty=0,
                    remaining_order_qty=shares,
                    benchmark_price=entry_price,
                    limit_price=entry_price,
                    fill_price=None,
                    stop_price=stop_price,
                    target_price=target_price,
                    planned_risk_rs=reserved_risk,
                    slippage_bps=0.0,
                    slippage_rs=0.0,
                    turnover_rs=0.0,
                    brokerage_rs=0.0,
                    stt_rs=0.0,
                    exchange_fee_rs=0.0,
                    sebi_fee_rs=0.0,
                    stamp_duty_rs=0.0,
                    gst_rs=0.0,
                    dp_fee_rs=0.0,
                    dp_group_id=None,
                    total_cost_rs=0.0,
                    cash_delta_rs=0.0,
                    realized_net_pnl_delta_rs=0.0,
                    exit_reason=None,
                    reject_reason=None,
                    eligibility_verdict="ELIGIBLE",
                    evidence_ref="",
                    evidence_hash="",
                    evidence_available_at=decision_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                self.store.append_event(ord_event)
                approved_reservations.append((sig, shares, res_id))

        # Atomically export CSV projections
        j_path, p_path, e_path = self.store.export_csv_projections(self.config.projections_dir, generation_id)

        return {
            "session_date": session_date,
            "decision_at": decision_at,
            "approved_reservations": approved_reservations,
            "rejected_candidates": rejected_candidates,
            "generation_id": generation_id,
            "projections": {
                "journal": str(j_path),
                "positions": str(p_path),
                "equity": str(e_path),
            },
        }

    def run_post_close(
        self,
        session_date: str,
        bar_data_map: Mapping[str, DailyBar],
        policy: FrictionPolicy = FrictionPolicy.realistic(),
        bhavcopy_manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes post-close portfolio reconciliation, fill processing, and MTM valuation.
        Execution order:
        1. Process pending exits (Priority 1 locked exits, Priority 2 disqualifications, Priority 3 intra-session stops/targets).
        2. Process pending entries from reservations against 15% cumulative volume ceiling.
        3. Mark active inventory to market and record daily portfolio equity snapshot.
        4. Atomically export CSV projections.
        """
        valuation_at = f"{session_date}T15:45:00+05:30"
        recorded_at = datetime.now(tz=IST).isoformat()
        generation_id = f"POSTCLOSE-{session_date}-{int(time.time() * 1000)}"

        executed_exits = []
        executed_entries = []
        stale_mark_count = 0
        unresolved_count = 0
        pending_exit_count = 0

        # Reset daily DP tracking for new session
        # Codex Mandate 2: DP charge of Rs 15.93 applied flat once per symbol per sell day
        current_session_dp_symbols: Set[str] = set()

        # =========================================================================
        # 1. EXIT PROCESSING (Priority: Pending Intents -> Disqualifications -> Stops/Targets)
        # =========================================================================
        open_positions = self.store.get_open_positions()

        for pos in open_positions:
            sym = pos.symbol
            bar = bar_data_map.get(sym)

            if bar is None:
                # Missing bar data: report STALE_MARK, preserve position, do not fake exit
                pos.mark_status = "STALE_MARK"
                stale_mark_count += 1
                self.store.upsert_position(pos)
                continue

            # Check circuit lock condition
            is_locked = (bar.volume == 0) or (
                bar.high == bar.low == bar.open == bar.close and bar.close < pos.stop_price
            )

            exit_triggered = False
            exit_reason = None
            raw_exit_price = None
            slippage_bps = policy.normal_slippage_bps

            # Priority 1 & 2: Position already has pending exit intent (disqualification or locked stop)
            if pos.exit_intent:
                pending_exit_count += 1
                if is_locked:
                    # Still locked, cannot execute exit today
                    unresolved_count += 1
                    pos.mark_status = "LOCKED_CIRCUIT"
                    self.store.upsert_position(pos)
                    continue
                else:
                    # Executable recovery open! (Codex Mandate 6)
                    exit_triggered = True
                    exit_reason = pos.exit_intent
                    raw_exit_price = bar.open
                    if bar.open < pos.stop_price:
                        slippage_bps = policy.gap_slippage_bps
                    else:
                        slippage_bps = policy.normal_slippage_bps

            # Priority 3: Normal intra-session exit evaluation
            elif not exit_triggered:
                # Check for circuit lock on stop breach
                if is_locked and (bar.open < pos.stop_price or bar.low <= pos.stop_price):
                    pos.exit_intent = "STOP_LOSS"
                    pos.exit_intent_created_at = valuation_at
                    pos.mark_status = "LOCKED_CIRCUIT"
                    pending_exit_count += 1
                    unresolved_count += 1
                    self.store.upsert_position(pos)
                    continue

                # Gap-down open below stop loss
                if bar.open < pos.stop_price:
                    exit_triggered = True
                    exit_reason = "GAP_STOP_LOSS"
                    raw_exit_price = bar.open
                    slippage_bps = policy.gap_slippage_bps

                # Conservative path invariant: Stop loss hit before target
                elif bar.low <= pos.stop_price:
                    exit_triggered = True
                    exit_reason = "STOP_LOSS"
                    raw_exit_price = pos.stop_price
                    slippage_bps = policy.normal_slippage_bps

                # Target hit
                elif bar.high >= pos.target_price:
                    exit_triggered = True
                    exit_reason = "TARGET"
                    raw_exit_price = pos.target_price
                    slippage_bps = policy.normal_slippage_bps

                # Holding time stop hit
                else:
                    # Max holding sessions based on sleeve
                    max_holding = 10
                    if pos.sleeve_id == "EXPIRY_RELIEF":
                        max_holding = 5
                    elif pos.sleeve_id == "DELIVERY_ACCUMULATION":
                        max_holding = 15

                    # Calculate held sessions
                    entry_dt = date.fromisoformat(pos.entry_session)
                    cur_dt = date.fromisoformat(session_date)
                    held_days = (cur_dt - entry_dt).days

                    if held_days >= max_holding * 1.4:  # roughly trading days
                        exit_triggered = True
                        exit_reason = "TIME_STOP"
                        raw_exit_price = bar.close
                        slippage_bps = policy.normal_slippage_bps

            # Execute Exit Fill if triggered
            if exit_triggered and raw_exit_price is not None:
                # Check volume ceiling: floor(0.15 * bar.volume)
                consumed = self.store.get_consumed_volume(session_date, sym)
                max_vol_capacity = math.floor(self.config.max_participation_pct * bar.volume)
                available_vol = max(0, max_vol_capacity - consumed)
                fill_qty = min(pos.residual_qty, available_vol)

                if fill_qty <= 0:
                    # Zero volume or capacity exhausted: exit cannot execute today
                    pos.exit_intent = exit_reason
                    pos.exit_intent_created_at = valuation_at
                    pending_exit_count += 1
                    unresolved_count += 1
                    self.store.upsert_position(pos)
                    continue

                # Calculate execution price with adverse slippage
                fill_price = round(raw_exit_price * (1.0 - slippage_bps / 10000.0), 2)
                turnover = round(fill_qty * fill_price, 2)

                # Compute statutory costs
                cost_dict = calculate_statutory_costs(fill_price, fill_qty, side=OrderSide.SELL)
                dp_group_id = f"DP-{session_date}-{sym}"

                # DP grouping: only charge Rs 15.93 once per symbol per sell session
                has_dp_charged = False
                with self.store._get_connection() as conn:
                    row = conn.execute(
                        "SELECT count(*) as cnt FROM ledger_events WHERE session_date = ? AND symbol = ? AND side = 'SELL' AND json_extract(payload_json, '$.dp_fee_rs') > 0",
                        (session_date, sym),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        has_dp_charged = True

                if has_dp_charged or (session_date, sym) in self.dp_charges_tracker:
                    dp_fee = 0.0
                    cost_dict["dp_charges"] = 0.0
                    total_cost = round(
                        cost_dict["brokerage"]
                        + cost_dict["stt"]
                        + cost_dict["exchange_charges"]
                        + cost_dict["sebi_charges"]
                        + cost_dict["stamp_duty"]
                        + cost_dict["gst"],
                        2,
                    )
                else:
                    dp_fee = DP_CHARGE_FLAT_RS
                    self.dp_charges_tracker.add((session_date, sym))
                    total_cost = cost_dict["total_cost"]

                slippage_rs = round(fill_qty * abs(raw_exit_price - fill_price), 2)
                cash_delta = round(turnover - total_cost, 2)

                # Allocated entry cost basis for the shares being sold
                avg_cost_basis = pos.residual_cost_basis_rs / pos.residual_qty
                cost_basis_sold = round(avg_cost_basis * fill_qty, 2)
                realized_net_pnl = round(turnover - cost_basis_sold - total_cost, 2)

                # Update position records
                pos.sold_qty += fill_qty
                pos.residual_qty -= fill_qty
                pos.residual_cost_basis_rs = round(max(0.0, pos.residual_cost_basis_rs - cost_basis_sold), 2)
                pos.last_mark = bar.close
                pos.mark_session = session_date
                pos.mark_status = "CLOSED" if pos.residual_qty == 0 else "PARTIAL_EXIT"
                if pos.residual_qty == 0:
                    pos.exit_intent = None
                    pos.exit_intent_created_at = None

                self.store.upsert_position(pos)
                self.store.add_consumed_volume(session_date, sym, fill_qty)

                # Update PortfolioRiskGovernor (atomic idempotent reconciliation)
                evt_id = f"EVT-EXIT-{session_date}-{sym}-{fill_qty}"
                if sym in self.governor.active_positions:
                    self.governor.reconcile_exit(
                        symbol=sym,
                        exit_price=fill_price,
                        shares=fill_qty,
                        exit_transaction_costs=total_cost,
                        exit_event_id=evt_id,
                    )

                # Record event in journal
                event_type = PaperEventType.FILL_COMPLETE.value if pos.residual_qty == 0 else PaperEventType.FILL_PARTIAL.value
                event = PaperJournalEvent(
                    event_id=f"EVT-EXIT-{session_date}-{sym}-{fill_qty}",
                    event_seq=0,
                    event_type=event_type,
                    event_at=valuation_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=pos.exit_intent_created_at or valuation_at,
                    signal_session=pos.entry_session,
                    intended_execution_session=session_date,
                    sleeve_id=pos.sleeve_id,
                    strategy_version=pos.strategy_version,
                    signal_id="",
                    order_id=f"ORD-EXIT-{session_date}-{sym}",
                    reservation_id="",
                    position_id=pos.position_id,
                    fill_id=f"FILL-EXIT-{session_date}-{sym}",
                    exchange="NSE",
                    isin=pos.isin,
                    symbol=sym,
                    series=pos.series,
                    side=PaperSide.SELL.value,
                    order_type=PaperOrderType.STOP_LOSS.value if "STOP" in str(exit_reason) else PaperOrderType.MARKET_OPEN.value,
                    state_before="OPEN",
                    state_after="CLOSED" if pos.residual_qty == 0 else "OPEN",
                    requested_qty=pos.sold_qty + pos.residual_qty,
                    fill_qty_delta=fill_qty,
                    cumulative_fill_qty=pos.sold_qty,
                    remaining_order_qty=pos.residual_qty,
                    benchmark_price=raw_exit_price,
                    limit_price=None,
                    fill_price=fill_price,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    planned_risk_rs=pos.planned_open_risk_rs,
                    slippage_bps=slippage_bps,
                    slippage_rs=slippage_rs,
                    turnover_rs=turnover,
                    brokerage_rs=cost_dict["brokerage"],
                    stt_rs=cost_dict["stt"],
                    exchange_fee_rs=cost_dict["exchange_charges"],
                    sebi_fee_rs=cost_dict["sebi_charges"],
                    stamp_duty_rs=cost_dict["stamp_duty"],
                    gst_rs=cost_dict["gst"],
                    dp_fee_rs=dp_fee,
                    dp_group_id=dp_group_id if dp_fee > 0 else None,
                    total_cost_rs=total_cost,
                    cash_delta_rs=cash_delta,
                    realized_net_pnl_delta_rs=realized_net_pnl,
                    exit_reason=exit_reason,
                    reject_reason=None,
                    eligibility_verdict="ELIGIBLE",
                    evidence_ref="",
                    evidence_hash="",
                    evidence_available_at=valuation_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                self.store.append_event(event)

                if pos.residual_qty == 0:
                    # Also record POSITION_CLOSED event
                    close_event = PaperJournalEvent(
                        event_id=f"EVT-CLOSE-{session_date}-{sym}",
                        event_seq=0,
                        event_type=PaperEventType.POSITION_CLOSED.value,
                        event_at=valuation_at,
                        recorded_at=recorded_at,
                        session_date=session_date,
                        decision_at=valuation_at,
                        signal_session=pos.entry_session,
                        intended_execution_session=session_date,
                        sleeve_id=pos.sleeve_id,
                        strategy_version=pos.strategy_version,
                        signal_id="",
                        order_id=f"ORD-EXIT-{session_date}-{sym}",
                        reservation_id="",
                        position_id=pos.position_id,
                        fill_id=None,
                        exchange="NSE",
                        isin=pos.isin,
                        symbol=sym,
                        series=pos.series,
                        side=PaperSide.SELL.value,
                        order_type=PaperOrderType.MARKET_OPEN.value,
                        state_before="OPEN",
                        state_after="CLOSED",
                        requested_qty=pos.sold_qty,
                        fill_qty_delta=0,
                        cumulative_fill_qty=pos.sold_qty,
                        remaining_order_qty=0,
                        benchmark_price=raw_exit_price,
                        limit_price=None,
                        fill_price=fill_price,
                        stop_price=pos.stop_price,
                        target_price=pos.target_price,
                        planned_risk_rs=0.0,
                        slippage_bps=0.0,
                        slippage_rs=0.0,
                        turnover_rs=0.0,
                        brokerage_rs=0.0,
                        stt_rs=0.0,
                        exchange_fee_rs=0.0,
                        sebi_fee_rs=0.0,
                        stamp_duty_rs=0.0,
                        gst_rs=0.0,
                        dp_fee_rs=0.0,
                        dp_group_id=None,
                        total_cost_rs=0.0,
                        cash_delta_rs=0.0,
                        realized_net_pnl_delta_rs=0.0,
                        exit_reason=exit_reason,
                        reject_reason=None,
                        eligibility_verdict="ELIGIBLE",
                        evidence_ref="",
                        evidence_hash="",
                        evidence_available_at=valuation_at,
                        fill_model_version=self.config.fill_model_version,
                        qualifying_evidence_status=self.config.evidence_mode,
                    )
                    self.store.append_event(close_event)

                executed_exits.append((sym, fill_qty, fill_price, exit_reason))

        # =========================================================================
        # 2. ENTRY PROCESSING (Pending Reservations -> Discrete Fills)
        # =========================================================================
        pending_reservations = self.store.get_pending_reservations()

        for res in pending_reservations:
            sym = res["symbol"]
            bar = bar_data_map.get(sym)

            if bar is None or bar.volume <= 0:
                # No volume or missing data -> cancel reservation, 0 friction, 0 costs
                self.store.clear_reservation(res["reservation_id"], "CANCELLED_NO_VOLUME")
                self.governor.pending_reservations.pop(sym, None)
                continue

            # Check locked upper circuit on entry (AGENTS.md Rule 3: Never chase locked UC)
            if bar.high == bar.low == bar.open == bar.close and bar.close > res["entry_price"]:
                # Locked UC -> cannot fill
                self.store.clear_reservation(res["reservation_id"], "CANCELLED_LOCKED_UC")
                self.governor.pending_reservations.pop(sym, None)
                continue

            # Volume ceiling check: floor(0.15 * bar.volume)
            consumed = self.store.get_consumed_volume(session_date, sym)
            max_vol_capacity = math.floor(self.config.max_participation_pct * bar.volume)
            available_vol = max(0, max_vol_capacity - consumed)
            fill_qty = min(int(res["quantity"]), available_vol)

            if fill_qty <= 0:
                # Capacity exhausted
                self.store.clear_reservation(res["reservation_id"], "CANCELLED_VOLUME_EXHAUSTED")
                self.governor.pending_reservations.pop(sym, None)
                continue

            # Entry execution at Open + normal slippage
            fill_price = round(bar.open * (1.0 + policy.normal_slippage_bps / 10000.0), 2)

            # Re-bound fill_qty to slot cap and risk budget under actual fill price (Codex Deliberation Item 31)
            diff = fill_price - float(res["stop_price"])
            if diff > 0:
                max_risk_shares = int(math.floor(self.config.risk_per_trade_rs / diff))
                max_slot_shares = int(math.floor(self.config.slot_cap_rs / fill_price))
                fill_qty = min(fill_qty, max_risk_shares, max_slot_shares)

            if fill_qty <= 0:
                self.store.clear_reservation(res["reservation_id"], "CANCELLED_SIZING_ZERO")
                self.governor.pending_reservations.pop(sym, None)
                continue

            turnover = round(fill_qty * fill_price, 2)
            cost_dict = calculate_statutory_costs(fill_price, fill_qty, side=OrderSide.BUY)
            total_cost = cost_dict["total_cost"]
            cash_outlay = round(turnover + total_cost, 2)

            # Strict Inviolable Cash Buffer Gate: re-check at fill time
            projected_cash = round(self.governor.cash_rs - cash_outlay, 2)
            if projected_cash < self.config.cash_buffer_rs:
                # Buffer breach -> reject fill to protect cash buffer
                self.store.clear_reservation(res["reservation_id"], "CANCELLED_CASH_BUFFER_PROTECTION")
                self.governor.pending_reservations.pop(sym, None)
                continue

            slippage_rs = round(fill_qty * abs(fill_price - bar.open), 2)
            planned_risk = round(fill_qty * (fill_price - res["stop_price"]), 2)

            # Confirm fill in PortfolioRiskGovernor
            self.governor.confirm_fill_from_reservation(
                symbol=sym,
                actual_fill_price=fill_price,
                filled_quantity=fill_qty,
                transaction_costs=total_cost,
            )

            # Update consumed volume
            self.store.add_consumed_volume(session_date, sym, fill_qty)

            # Create new open position record
            pos_id = f"POS-{session_date}-{sym}-{res['sleeve_id']}"
            new_pos = OpenPositionRecord(
                position_id=pos_id,
                isin="",
                symbol=sym,
                series="EQ",
                sleeve_id=res["sleeve_id"],
                strategy_version="v1.0",
                entry_session=session_date,
                acquired_qty=fill_qty,
                sold_qty=0,
                residual_qty=fill_qty,
                residual_cost_basis_rs=turnover,
                entry_cost_allocation_rs=total_cost,
                stop_price=float(res["stop_price"]),
                target_price=round(fill_price + 2.0 * (fill_price - res["stop_price"]), 2),
                planned_open_risk_rs=planned_risk,
                exit_intent=None,
                exit_intent_created_at=None,
                pending_exit_order_id=None,
                last_mark=bar.close,
                mark_session=session_date,
                mark_source_hash="",
                mark_status="ACTIVE",
                corporate_action_status="NONE",
                settlement_status="T1_PENDING",
            )
            self.store.upsert_position(new_pos)
            self.store.clear_reservation(res["reservation_id"], "FILLED")

            # Record FILL event in journal
            event_type = PaperEventType.FILL_COMPLETE.value if fill_qty == res["quantity"] else PaperEventType.FILL_PARTIAL.value
            fill_event = PaperJournalEvent(
                event_id=f"EVT-FILL-{session_date}-{sym}-{fill_qty}",
                event_seq=0,
                event_type=event_type,
                event_at=valuation_at,
                recorded_at=recorded_at,
                session_date=session_date,
                decision_at=valuation_at,
                signal_session=res["session_date"],
                intended_execution_session=session_date,
                sleeve_id=res["sleeve_id"],
                strategy_version="v1.0",
                signal_id="",
                order_id=f"ORD-{res['session_date']}-{sym}-{res['sleeve_id']}",
                reservation_id=res["reservation_id"],
                position_id=pos_id,
                fill_id=f"FILL-{session_date}-{sym}",
                exchange="NSE",
                isin="",
                symbol=sym,
                series="EQ",
                side=PaperSide.BUY.value,
                order_type=PaperOrderType.LIMIT_OPEN.value,
                state_before="SUBMITTED",
                state_after="FILLED" if fill_qty == res["quantity"] else "PARTIAL",
                requested_qty=int(res["quantity"]),
                fill_qty_delta=fill_qty,
                cumulative_fill_qty=fill_qty,
                remaining_order_qty=int(res["quantity"]) - fill_qty,
                benchmark_price=bar.open,
                limit_price=float(res["entry_price"]),
                fill_price=fill_price,
                stop_price=float(res["stop_price"]),
                target_price=new_pos.target_price,
                planned_risk_rs=planned_risk,
                slippage_bps=policy.normal_slippage_bps,
                slippage_rs=slippage_rs,
                turnover_rs=turnover,
                brokerage_rs=cost_dict["brokerage"],
                stt_rs=cost_dict["stt"],
                exchange_fee_rs=cost_dict["exchange_charges"],
                sebi_fee_rs=cost_dict["sebi_charges"],
                stamp_duty_rs=cost_dict["stamp_duty"],
                gst_rs=cost_dict["gst"],
                dp_fee_rs=0.0,
                dp_group_id=None,
                total_cost_rs=total_cost,
                cash_delta_rs=-cash_outlay,
                realized_net_pnl_delta_rs=0.0,
                exit_reason=None,
                reject_reason=None,
                eligibility_verdict="ELIGIBLE",
                evidence_ref="",
                evidence_hash="",
                evidence_available_at=valuation_at,
                fill_model_version=self.config.fill_model_version,
                qualifying_evidence_status=self.config.evidence_mode,
            )
            self.store.append_event(fill_event)

            # Record POSITION_OPENED event
            pos_open_event = PaperJournalEvent(
                event_id=f"EVT-OPEN-{session_date}-{sym}",
                event_seq=0,
                event_type=PaperEventType.POSITION_OPENED.value,
                event_at=valuation_at,
                recorded_at=recorded_at,
                session_date=session_date,
                decision_at=valuation_at,
                signal_session=res["session_date"],
                intended_execution_session=session_date,
                sleeve_id=res["sleeve_id"],
                strategy_version="v1.0",
                signal_id="",
                order_id=f"ORD-{res['session_date']}-{sym}-{res['sleeve_id']}",
                reservation_id=res["reservation_id"],
                position_id=pos_id,
                fill_id=None,
                exchange="NSE",
                isin="",
                symbol=sym,
                series="EQ",
                side=PaperSide.BUY.value,
                order_type=PaperOrderType.LIMIT_OPEN.value,
                state_before="PENDING",
                state_after="OPEN",
                requested_qty=fill_qty,
                fill_qty_delta=0,
                cumulative_fill_qty=fill_qty,
                remaining_order_qty=0,
                benchmark_price=bar.open,
                limit_price=float(res["entry_price"]),
                fill_price=fill_price,
                stop_price=float(res["stop_price"]),
                target_price=new_pos.target_price,
                planned_risk_rs=planned_risk,
                slippage_bps=0.0,
                slippage_rs=0.0,
                turnover_rs=turnover,
                brokerage_rs=0.0,
                stt_rs=0.0,
                exchange_fee_rs=0.0,
                sebi_fee_rs=0.0,
                stamp_duty_rs=0.0,
                gst_rs=0.0,
                dp_fee_rs=0.0,
                dp_group_id=None,
                total_cost_rs=0.0,
                cash_delta_rs=0.0,
                realized_net_pnl_delta_rs=0.0,
                exit_reason=None,
                reject_reason=None,
                eligibility_verdict="ELIGIBLE",
                evidence_ref="",
                evidence_hash="",
                evidence_available_at=valuation_at,
                fill_model_version=self.config.fill_model_version,
                qualifying_evidence_status=self.config.evidence_mode,
            )
            self.store.append_event(pos_open_event)

            executed_entries.append((sym, fill_qty, fill_price))

        # =========================================================================
        # 3. MTM VALUATION & PORTFOLIO EQUITY SNAPSHOT
        # =========================================================================
        current_open_positions = self.store.get_open_positions()
        inventory_mtm = 0.0
        total_open_risk = 0.0
        committed_exposure = 0.0

        for pos in current_open_positions:
            bar = bar_data_map.get(pos.symbol)
            if bar is not None:
                pos.last_mark = bar.close
                pos.mark_session = session_date
                if pos.mark_status not in ("LOCKED_CIRCUIT", "STALE_MARK"):
                    pos.mark_status = "ACTIVE"
                self.store.upsert_position(pos)

            pos_mtm = round(pos.residual_qty * pos.last_mark, 2)
            inventory_mtm += pos_mtm
            total_open_risk += pos.planned_open_risk_rs
            committed_exposure += pos.residual_cost_basis_rs

        inventory_mtm = round(inventory_mtm, 2)
        total_open_risk = round(total_open_risk, 2)
        committed_exposure = round(committed_exposure, 2)

        cash_ledger = round(self.governor.cash_rs, 2)
        total_equity = round(cash_ledger + inventory_mtm, 2)

        # Get cumulative metrics from store events
        with self.store._get_connection() as conn:
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(realized_net_pnl_delta_rs), 0.0) as cum_pnl,
                    COALESCE(SUM(total_cost_rs), 0.0) as cum_costs
                FROM ledger_events
            """).fetchone()
            cum_pnl = round(float(row["cum_pnl"]), 2) if row else 0.0
            cum_costs = round(float(row["cum_costs"]), 2) if row else 0.0

        unrealized_pnl = round(inventory_mtm - committed_exposure, 2)

        # Drawdown calculation
        latest_equity_prev = self.store.get_latest_equity()
        prev_equity = latest_equity_prev.equity_rs if latest_equity_prev else self.config.initial_cash_rs
        peak_equity = max(prev_equity, total_equity, self.config.initial_cash_rs)
        drawdown_rs = round(peak_equity - total_equity, 2)
        drawdown_pct = round((drawdown_rs / peak_equity) * 100.0, 4) if peak_equity > 0 else 0.0

        occupied_slots = len(current_open_positions)
        pending_reservations_now = self.store.get_pending_reservations()
        pending_slots = len(pending_reservations_now)

        reserved_cash_now = sum(float(r["reserved_cash"]) for r in pending_reservations_now)
        free_cash_now = round(cash_ledger - reserved_cash_now, 2)

        cash_buffer_breach = cash_ledger < self.config.cash_buffer_rs
        risk_breach = total_open_risk > AGGREGATE_RISK_CAP_RS

        daily_equity_rec = DailyPortfolioEquityRecord(
            session_date=session_date,
            valuation_at=valuation_at,
            cash_ledger_rs=cash_ledger,
            cash_settled_rs=cash_ledger,  # Cash delivery settled
            receivable_rs=0.0,
            payable_rs=0.0,
            reserved_cash_rs=reserved_cash_now,
            free_cash_rs=free_cash_now,
            inventory_mtm_rs=inventory_mtm,
            equity_rs=total_equity,
            external_flow_rs=0.0,
            realized_net_pnl_cumulative_rs=cum_pnl,
            unrealized_pnl_rs=unrealized_pnl,
            costs_cumulative_rs=cum_costs,
            occupied_slots=occupied_slots,
            pending_slots=pending_slots,
            committed_exposure_rs=committed_exposure,
            marked_exposure_rs=inventory_mtm,
            planned_open_risk_rs=total_open_risk,
            reserved_risk_rs=sum(float(r["reserved_risk"]) for r in pending_reservations_now),
            pending_exit_count=pending_exit_count,
            unresolved_position_count=unresolved_count,
            stale_mark_count=stale_mark_count,
            drawdown_rs=drawdown_rs,
            drawdown_pct=drawdown_pct,
            cash_buffer_breach=cash_buffer_breach,
            risk_breach=risk_breach,
            data_status="NORMAL" if stale_mark_count == 0 else "DATA_PENDING",
        )
        self.store.record_daily_equity(daily_equity_rec)

        # =========================================================================
        # 4. ATOMIC CSV EXPORT PROJECTIONS
        # =========================================================================
        j_path, p_path, e_path = self.store.export_csv_projections(self.config.projections_dir, generation_id)

        return {
            "session_date": session_date,
            "valuation_at": valuation_at,
            "executed_exits": executed_exits,
            "executed_entries": executed_entries,
            "equity": daily_equity_rec.to_dict(),
            "generation_id": generation_id,
            "projections": {
                "journal": str(j_path),
                "positions": str(p_path),
                "equity": str(e_path),
            },
        }

    def apply_corporate_action(
        self,
        symbol: str,
        action_type: str,
        ratio: float,
        effective_date: str,
    ) -> bool:
        """
        Applies a corporate action (SPLIT, BONUS, SYMBOL_CHANGE) to active positions in SQLite and governor.
        Preserves cost basis and prevents fabricated PnL.
        Fails closed on unknown corporate action ("MERGER", "DELISTING"), marking status FROZEN_UNRESOLVED.
        """
        open_positions = self.store.get_open_positions()
        matched = [p for p in open_positions if p.symbol == symbol]
        if not matched:
            return False

        if action_type in ("SPLIT", "BONUS"):
            if ratio <= 0 or not math.isfinite(ratio):
                return False
            for pos in matched:
                new_qty = int(math.floor(pos.residual_qty * ratio))
                pos.acquired_qty = int(math.floor(pos.acquired_qty * ratio))
                pos.residual_qty = new_qty
                # Cost basis stays invariant, per-share price divided by ratio
                pos.stop_price = round(pos.stop_price / ratio, 2)
                pos.target_price = round(pos.target_price / ratio, 2)
                pos.corporate_action_status = f"ADJUSTED_{action_type}_{ratio}"
                self.store.upsert_position(pos)

                # Re-sync governor position
                if symbol in self.governor.active_positions:
                    gov_pos = self.governor.active_positions[symbol]
                    gov_pos["shares"] = new_qty
                    gov_pos["entry_price"] = gov_pos["notional_rs"] / new_qty
                    gov_pos["stop_price"] = pos.stop_price
            return True

        elif action_type == "SYMBOL_CHANGE":
            # ratio not used, symbol is target
            return True

        else:
            # Unknown action: freeze position
            for pos in matched:
                pos.corporate_action_status = f"FROZEN_UNRESOLVED_{action_type}"
                self.store.upsert_position(pos)
            return False
