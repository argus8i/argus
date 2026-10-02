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

        # Restore cash ledger from committed economics
        with self.store._get_connection() as conn:
            row = conn.execute("SELECT COALESCE(SUM(cash_delta_rs), 0.0) as cum_cash_delta FROM ledger_events").fetchone()
            cum_cash_delta = float(row["cum_cash_delta"]) if row else 0.0
        self.governor.cash_rs = round(self.config.initial_cash_rs + cum_cash_delta, 2)

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

    def _parse_iso_timestamp(self, ts_str: Any) -> Optional[datetime]:
        if not ts_str:
            return None
        s = str(ts_str).strip()
        if s.endswith(" IST"):
            s_clean = s[:-4].strip()
            try:
                dt = datetime.strptime(s_clean, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=IST)
            except Exception:
                return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except Exception:
            return None

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
        # Enforce fixed 08:45:00 IST hard cutoff (fail-closed against caller overrides past 08:45)
        hard_cutoff_at = f"{session_date}T08:45:00+05:30"
        hard_cutoff_dt = datetime.fromisoformat(hard_cutoff_at)
        caller_cutoff_at = f"{session_date}T{decision_time}+05:30"
        caller_cutoff_dt = datetime.fromisoformat(caller_cutoff_at)
        cutoff_dt = min(caller_cutoff_dt, hard_cutoff_dt)
        decision_at = cutoff_dt.isoformat()
        recorded_at = datetime.now(tz=IST).isoformat()
        generation_id = f"PREOPEN-{session_date}-{int(time.time() * 1000)}"

        # 1. Eligibility & Surveillance Disqualification Check for Active Positions
        # Separate entry permissions from exit processing (Codex Deliberation Item 3)
        surv_symbols = set()
        fno_symbols = set()

        is_evidence_valid = True
        evidence_block_reason = ""

        if surveillance_snapshot is None or fno_underlyings is None:
            is_evidence_valid = False
            evidence_block_reason = "BLOCKED_MISSING_EVIDENCE"
        elif not isinstance(surveillance_snapshot, dict) or not surveillance_snapshot:
            is_evidence_valid = False
            evidence_block_reason = "BLOCKED_MALFORMED_EVIDENCE"
        else:
            snap_ts = surveillance_snapshot.get("fetched_at") or surveillance_snapshot.get("timestamp")
            if not snap_ts:
                is_evidence_valid = False
                evidence_block_reason = "BLOCKED_MISSING_TIMESTAMP"
            else:
                dt = self._parse_iso_timestamp(snap_ts)
                if dt is None:
                    is_evidence_valid = False
                    evidence_block_reason = "BLOCKED_UNPARSEABLE_TIMESTAMP"
                elif dt > cutoff_dt:
                    is_evidence_valid = False
                    evidence_block_reason = "BLOCKED_LOOKAHEAD_EVIDENCE"

        if isinstance(surveillance_snapshot, dict):
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

                # Check fail-closed evidence validity first
                if not is_evidence_valid:
                    rejected_candidates.append((sig, evidence_block_reason))
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
                        requested_qty=0,
                        fill_qty_delta=0,
                        cumulative_fill_qty=0,
                        remaining_order_qty=0,
                        benchmark_price=entry_price,
                        limit_price=entry_price,
                        fill_price=None,
                        stop_price=stop_price,
                        target_price=target_price,
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
                        exit_reason=None,
                        reject_reason=evidence_block_reason,
                        eligibility_verdict="BLOCKED",
                        evidence_ref="",
                        evidence_hash="",
                        evidence_available_at=decision_at,
                        fill_model_version=self.config.fill_model_version,
                        qualifying_evidence_status=self.config.evidence_mode,
                    )
                    self.store.append_event(rej_event)
                    continue

                # Check signal creation timestamp cutoff
                sig_created_at = getattr(sig, "created_at", None)
                if sig_created_at:
                    s_dt = self._parse_iso_timestamp(sig_created_at)
                    if s_dt and s_dt > cutoff_dt:
                        rejected_candidates.append((sig, "BLOCKED_FUTURE_SIGNAL"))
                        continue
                if getattr(sig, "session_date", None) == session_date and sig_created_at:
                    s_dt = self._parse_iso_timestamp(sig_created_at)
                    if s_dt and s_dt >= cutoff_dt:
                        rejected_candidates.append((sig, "BLOCKED_FUTURE_SIGNAL"))
                        continue

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

                # Re-sync governor from DB state before assessing candidate
                self._restore_state_from_store()

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

                sleeve_id = getattr(sig, "strategy_id", "UNKNOWN")
                res_id = f"RES-{session_date}-{sym}-{sleeve_id}"
                ord_id = f"ORD-{session_date}-{sym}-{sleeve_id}"
                reserved_cash = round(shares * entry_price * 1.0015, 2)
                reserved_risk = round(shares * (entry_price - stop_price), 2)

                # Atomically check and reserve in SQLite store inside BEGIN IMMEDIATE transaction
                reserved_in_db = self.store.check_and_reserve_slot(
                    symbol=sym,
                    sleeve_id=sleeve_id,
                    quantity=shares,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    reserved_cash=reserved_cash,
                    reserved_risk=reserved_risk,
                    session_date=session_date,
                    max_slots=self.config.max_slots,
                    max_per_sector=self.config.max_positions_per_sector,
                    sector=self.governor.resolve_sector(sym),
                    reservation_id=res_id,
                )
                if not reserved_in_db:
                    rejected_candidates.append((sig, "REJECTED_SHARED_SLOT_GATE_CONCURRENCY"))
                    continue

                # Candidate approved -> Reserve slot in governor
                self.governor.reserve_slot(
                    symbol=sym,
                    quantity=shares,
                    entry_price=entry_price,
                    stop_price=stop_price,
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
                avg_cost_basis = pos.residual_cost_basis_rs / pos.residual_qty if pos.residual_qty > 0 else 0.0
                cost_basis_sold = round(avg_cost_basis * fill_qty, 2)
                avg_entry_cost = pos.entry_cost_allocation_rs / pos.acquired_qty if pos.acquired_qty > 0 else 0.0
                allocated_entry_cost = round(avg_entry_cost * fill_qty, 2)
                realized_net_pnl = round(turnover - cost_basis_sold - total_cost - allocated_entry_cost, 2)

                # Update position records
                pos.sold_qty += fill_qty
                pos.residual_qty -= fill_qty
                pos.residual_cost_basis_rs = round(max(0.0, pos.residual_cost_basis_rs - cost_basis_sold), 2)
                pos.last_mark = bar.close
                pos.mark_session = session_date
                if pos.residual_qty == 0:
                    pos.mark_status = "CLOSED"
                    pos.exit_intent = None
                    pos.exit_intent_created_at = None
                else:
                    pos.mark_status = "PARTIAL_EXIT"
                    pos.exit_intent = exit_reason
                    pos.exit_intent_created_at = pos.exit_intent_created_at or valuation_at

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

                close_event = None
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

                # Commit atomic execution transition: position, volume, and exit fill events
                transition_events = [event]
                if close_event:
                    transition_events.append(close_event)
                self.store.commit_execution_transition(
                    position=pos,
                    consumed_volume_update=(session_date, sym, fill_qty),
                    events=transition_events,
                )

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
            max_sizing_shares = int(res["quantity"])
            if diff > 0:
                max_risk_shares = int(math.floor(self.config.risk_per_trade_rs / diff))
                max_slot_shares = int(math.floor(self.config.slot_cap_rs / fill_price))
                max_sizing_shares = min(max_sizing_shares, max_risk_shares, max_slot_shares)
                fill_qty = min(fill_qty, max_sizing_shares)

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

            # 1. Check residual reservation
            # If fill hit sizing ceiling, trade is fully sized and no more shares can be added under risk/slot budget
            rem_qty = max(0, max_sizing_shares - fill_qty)
            if rem_qty > 0:
                rem_cash = round(rem_qty * fill_price * 1.0015, 2)
                rem_risk = round(rem_qty * (fill_price - float(res["stop_price"])), 2)
                res_residual = (rem_qty, rem_cash, rem_risk)
                res_status = "PENDING"
            else:
                res_residual = None
                res_status = "FILLED"

            # 2. Commit atomic execution transition for position, reservation, consumed volume, and fill events
            self.store.commit_execution_transition(
                position=new_pos,
                reservation_id=res["reservation_id"],
                reservation_status=res_status,
                reservation_residual=res_residual,
                consumed_volume_update=(session_date, sym, fill_qty),
                events=[fill_event, pos_open_event],
            )

            # 4. Confirm in risk governor
            self.governor.confirm_fill_from_reservation(
                symbol=sym,
                actual_fill_price=fill_price,
                filled_quantity=fill_qty,
                transaction_costs=total_cost,
            )
            if rem_qty <= 0:
                self.governor.pending_reservations.pop(sym, None)
            else:
                if sym in self.governor.pending_reservations:
                    self.governor.pending_reservations[sym]["quantity"] = rem_qty
                    self.governor.pending_reservations[sym]["notional_rs"] = rem_cash
                    self.governor.pending_reservations[sym]["open_risk_rs"] = rem_risk

            # 5. Check same-day stop breach on entry session (Test 12)
            if bar.low <= new_pos.stop_price:
                same_day_exit_reason = "STOP_LOSS"
                raw_exit_p = new_pos.stop_price if bar.open >= new_pos.stop_price else bar.open
                exit_slip = policy.normal_slippage_bps if bar.open >= new_pos.stop_price else policy.gap_slippage_bps
                exit_fill_p = round(raw_exit_p * (1.0 - exit_slip / 10000.0), 2)
                exit_turnover = round(fill_qty * exit_fill_p, 2)
                exit_costs_d = calculate_statutory_costs(exit_fill_p, fill_qty, side=OrderSide.SELL)
                exit_costs_d["dp_charges"] = 0.0  # Same-day square-off: no DP
                exit_tot_cost = round(exit_costs_d["total_cost"], 2)
                exit_cash_d = round(exit_turnover - exit_tot_cost, 2)
                allocated_e_cost = total_cost
                realized_p = round(exit_turnover - turnover - exit_tot_cost - allocated_e_cost, 2)

                new_pos.sold_qty = fill_qty
                new_pos.residual_qty = 0
                new_pos.residual_cost_basis_rs = 0.0
                new_pos.mark_status = "CLOSED"
                new_pos.last_mark = bar.close
                new_pos.exit_intent = None
                new_pos.exit_intent_created_at = None

                exit_evt_id = f"EVT-EXIT-{session_date}-{sym}-{fill_qty}-SAMEDAY"
                exit_event = PaperJournalEvent(
                    event_id=exit_evt_id,
                    event_seq=0,
                    event_type=PaperEventType.FILL_COMPLETE.value,
                    event_at=valuation_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=valuation_at,
                    signal_session=res["session_date"],
                    intended_execution_session=session_date,
                    sleeve_id=res["sleeve_id"],
                    strategy_version="v1.0",
                    signal_id="",
                    order_id=f"ORD-EXIT-{session_date}-{sym}",
                    reservation_id="",
                    position_id=pos_id,
                    fill_id=f"FILL-EXIT-{session_date}-{sym}",
                    exchange="NSE",
                    isin="",
                    symbol=sym,
                    series="EQ",
                    side=PaperSide.SELL.value,
                    order_type=PaperOrderType.STOP_LOSS.value,
                    state_before="OPEN",
                    state_after="CLOSED",
                    requested_qty=fill_qty,
                    fill_qty_delta=fill_qty,
                    cumulative_fill_qty=fill_qty,
                    remaining_order_qty=0,
                    benchmark_price=raw_exit_p,
                    limit_price=None,
                    fill_price=exit_fill_p,
                    stop_price=new_pos.stop_price,
                    target_price=new_pos.target_price,
                    planned_risk_rs=0.0,
                    slippage_bps=exit_slip,
                    slippage_rs=round(fill_qty * abs(raw_exit_p - exit_fill_p), 2),
                    turnover_rs=exit_turnover,
                    brokerage_rs=exit_costs_d["brokerage"],
                    stt_rs=exit_costs_d["stt"],
                    exchange_fee_rs=exit_costs_d["exchange_charges"],
                    sebi_fee_rs=exit_costs_d["sebi_charges"],
                    stamp_duty_rs=exit_costs_d["stamp_duty"],
                    gst_rs=exit_costs_d["gst"],
                    dp_fee_rs=0.0,
                    dp_group_id=None,
                    total_cost_rs=exit_tot_cost,
                    cash_delta_rs=exit_cash_d,
                    realized_net_pnl_delta_rs=realized_p,
                    exit_reason=same_day_exit_reason,
                    reject_reason=None,
                    eligibility_verdict="ELIGIBLE",
                    evidence_ref="",
                    evidence_hash="",
                    evidence_available_at=valuation_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                same_day_close_event = PaperJournalEvent(
                    event_id=f"EVT-CLOSE-{session_date}-{sym}-SAMEDAY",
                    event_seq=0,
                    event_type=PaperEventType.POSITION_CLOSED.value,
                    event_at=valuation_at,
                    recorded_at=recorded_at,
                    session_date=session_date,
                    decision_at=valuation_at,
                    signal_session=res["session_date"],
                    intended_execution_session=session_date,
                    sleeve_id=res["sleeve_id"],
                    strategy_version="v1.0",
                    signal_id="",
                    order_id=f"ORD-EXIT-{session_date}-{sym}",
                    reservation_id="",
                    position_id=pos_id,
                    fill_id=None,
                    exchange="NSE",
                    isin="",
                    symbol=sym,
                    series="EQ",
                    side=PaperSide.SELL.value,
                    order_type=PaperOrderType.MARKET_OPEN.value,
                    state_before="OPEN",
                    state_after="CLOSED",
                    requested_qty=fill_qty,
                    fill_qty_delta=0,
                    cumulative_fill_qty=fill_qty,
                    remaining_order_qty=0,
                    benchmark_price=raw_exit_p,
                    limit_price=None,
                    fill_price=exit_fill_p,
                    stop_price=new_pos.stop_price,
                    target_price=new_pos.target_price,
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
                    exit_reason=same_day_exit_reason,
                    reject_reason=None,
                    eligibility_verdict="ELIGIBLE",
                    evidence_ref="",
                    evidence_hash="",
                    evidence_available_at=valuation_at,
                    fill_model_version=self.config.fill_model_version,
                    qualifying_evidence_status=self.config.evidence_mode,
                )
                self.store.commit_execution_transition(
                    position=new_pos,
                    consumed_volume_update=(session_date, sym, fill_qty),
                    events=[exit_event, same_day_close_event],
                )
                self.governor.reconcile_exit(
                    symbol=sym,
                    exit_price=exit_fill_p,
                    shares=fill_qty,
                    exit_transaction_costs=exit_tot_cost,
                    exit_event_id=exit_evt_id,
                )
                executed_exits.append((sym, fill_qty, exit_fill_p, same_day_exit_reason))

            executed_entries.append((sym, fill_qty, fill_price))

        # =========================================================================
        # 3. MTM VALUATION & PORTFOLIO EQUITY SNAPSHOT
        # =========================================================================
        current_open_positions = self.store.get_open_positions()
        inventory_mtm = 0.0
        total_open_risk = 0.0
        committed_exposure = 0.0

        for pos in current_open_positions:
            if pos.corporate_action_status.startswith("FROZEN_UNRESOLVED"):
                unresolved_count += 1
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

        data_status = "NORMAL"
        if stale_mark_count > 0 or unresolved_count > 0:
            data_status = "DATA_PENDING"

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
            data_status=data_status,
        )

        # Commit EOD equity only if bhavcopy manifest is verified (Codex Round 3 Finding 4)
        is_manifest_valid = False
        if isinstance(bhavcopy_manifest, dict):
            m_status = bhavcopy_manifest.get("status")
            m_date = bhavcopy_manifest.get("session_date") or bhavcopy_manifest.get("date")
            if m_status == "NORMAL" and m_date == session_date:
                has_provenance = any(
                    k in bhavcopy_manifest
                    for k in ("source_sha256", "sha256", "bhavcopy_sha256", "file_sha256", "raw_sha256", "source_file", "source_path", "raw_path", "files")
                )
                has_bars = bool(bar_data_map and len(bar_data_map) > 0)
                is_retry_pending = self.store.is_session_processed(session_date) and not self.store.has_session_equity(session_date)

                if has_provenance or has_bars or is_retry_pending:
                    is_manifest_valid = True

        if is_manifest_valid:
            self.store.record_daily_equity(daily_equity_rec)

        # Mark session as processed for idempotency
        self.store.mark_session_processed(session_date, recorded_at)

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
        ca_key = f"ADJUSTED_{action_type}_{ratio}_{effective_date}"
        ca_id = f"CA-{effective_date}-{symbol}-{action_type}-{ratio}"

        if self.store.is_corporate_action_applied(ca_id):
            return True

        open_positions = self.store.get_open_positions()
        matched = [p for p in open_positions if p.symbol == symbol]
        if not matched:
            return False

        if any(p.corporate_action_status == ca_key for p in matched):
            return True

        if action_type in ("SPLIT", "BONUS"):
            if ratio <= 0 or not math.isfinite(ratio):
                return False
            for pos in matched:
                new_qty = int(math.floor(pos.residual_qty * ratio))
                pos.acquired_qty = int(math.floor(pos.acquired_qty * ratio))
                pos.sold_qty = int(math.floor(pos.sold_qty * ratio))
                pos.residual_qty = new_qty
                # Cost basis stays invariant, per-share price divided by ratio
                pos.last_mark = round(pos.last_mark / ratio, 4)
                pos.stop_price = round(pos.stop_price / ratio, 2)
                pos.target_price = round(pos.target_price / ratio, 2)
                pos.corporate_action_status = ca_key

                # Re-sync governor position
                if symbol in self.governor.active_positions:
                    gov_pos = self.governor.active_positions[symbol]
                    gov_pos["shares"] = new_qty
                    gov_pos["entry_price"] = gov_pos["notional_rs"] / new_qty
                    gov_pos["stop_price"] = pos.stop_price

            recorded_at = datetime.now(tz=IST).isoformat()
            self.store.apply_corporate_action_atomic(
                positions=matched,
                ca_id=ca_id,
                symbol=symbol,
                action_type=action_type,
                ratio=ratio,
                effective_date=effective_date,
                recorded_at=recorded_at,
            )
            return True

        elif action_type == "SYMBOL_CHANGE":
            recorded_at = datetime.now(tz=IST).isoformat()
            self.store.record_corporate_action(ca_id, symbol, action_type, ratio, effective_date, recorded_at)
            return True

        else:
            # Unknown action: freeze position
            for pos in matched:
                pos.corporate_action_status = f"FROZEN_UNRESOLVED_{action_type}"
                self.store.upsert_position(pos)
            return False
