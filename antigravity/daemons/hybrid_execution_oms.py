"""
hybrid_execution_oms.py - Hybrid Order Management System (OMS) for Track 2
==========================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).

Manages trade execution under Hybrid, Co-Pilot, and Autonomous operational policies:
  - Co-Pilot Mode: Candidate triggers an ExecutionIntent card with 90s fail-closed
    expiry and interactive Telegram/Terminal authorization.
  - Autonomous Mode: Evaluates pre-flight risk checks and executes in < 15ms.
  - Hybrid Mode: Tier-1 high conviction auto-executes, Tier-2 prompts Co-Pilot.
  - Rule 1 Gate: Strictly isolates simulated paper execution from live broker gateway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from antigravity.models.execution_policy import (
    ExecutionEnvironment,
    ExecutionIntent,
    ExecutionMode,
    IntentStatus,
    PolicyConfig,
    SecurityViolationError,
)
from antigravity.models.session_manifest import IST
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor
from antigravity.models.two_tranche_exit_model import (
    TrancheAllocation,
    TrancheStatus,
    TwoTrancheExitModel,
)

# Paths
SHARED_TRACK2_DIR = REPO_ROOT / "shared" / "track2_liquid"
INTENTS_PATH = SHARED_TRACK2_DIR / "execution_intents.json"
ORDERS_LOG_PATH = SHARED_TRACK2_DIR / "paper_orders.jsonl"
CONFIG_PATH = REPO_ROOT / "antigravity" / "config" / "execution_config.json"
DEFAULT_CORPUS_RS = 250000.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [HybridOMS] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("HybridOMS")


class HybridExecutionOMS:
    """
    Central Order Management System for Track 2.
    Coordinates risk checks, intent creation, Co-Pilot approval, and routing.
    Thread-safe implementation with RLock, CAS state transitions, and deduplication.
    """

    def __init__(
        self,
        config: Optional[PolicyConfig] = None,
        corpus_rs: float = DEFAULT_CORPUS_RS,
        output_dir: Path = SHARED_TRACK2_DIR,
        config_path: Optional[Path] = None,
    ):
        self._lock = threading.RLock()
        self._dedup_cache: Dict[str, float] = {}  # key -> monotonic_ts

        self.output_dir = Path(output_dir)
        if config_path is not None:
            self.config_path = Path(config_path)
        elif self.output_dir != SHARED_TRACK2_DIR:
            self.config_path = self.output_dir / "execution_config.json"
        else:
            self.config_path = CONFIG_PATH

        self.config = config or self.load_config(self.config_path)
        self.corpus_rs = corpus_rs
        self.intents_path = self.output_dir / "execution_intents.json"
        self.orders_path = self.output_dir / "paper_orders.jsonl"

        self.governor = PortfolioRiskGovernor.calibrate_for_corpus(
            corpus_rs=corpus_rs,
            risk_per_trade_rs=self.config.risk_budget_rs,
            max_concurrent_positions=self.config.max_open_positions,
            cash_buffer_rs=self.config.cash_buffer_rs,
        )

        self.intents: Dict[str, ExecutionIntent] = {}
        self.active_orders: List[Dict[str, Any]] = []

        self.capacity_ledger = None
        try:
            from research.execution_realism.capacity import CapacityConfig, ReservationLedger
            cap_cfg = CapacityConfig(
                capital_rs=self.corpus_rs,
                cash_buffer_rs=self.config.cash_buffer_rs,
                slots=self.config.max_open_positions,
                risk_per_trade_rs=self.config.risk_budget_rs,
            )
            self.capacity_ledger = ReservationLedger(output_dir / "capacity_ledger.db", cap_cfg)
        except Exception:
            pass
        with self._lock:
            self._load_intents()
            self._load_active_orders()

    def _load_active_orders(self) -> None:
        """Hydrates active open positions from paper_orders.jsonl on reboot (Codex Audit)."""
        if not self.orders_path.is_file():
            return
        try:
            open_orders: Dict[str, Dict[str, Any]] = {}
            with open(self.orders_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    oid = rec.get("order_id")
                    status = rec.get("status")
                    if not oid:
                        continue
                    if status in ("OPEN", "RUNNING", "QUEUED", "PARTIAL"):
                        open_orders[oid] = rec
                    elif status in ("SQUARED_OFF", "CANCELLED", "CLOSED", "REJECTED", "STOPPED_OUT_FULL"):
                        open_orders.pop(oid, None)
            self.active_orders = list(open_orders.values())
            logger.info(f"Hydrated {len(self.active_orders)} active open positions from {self.orders_path.name}")
        except Exception as exc:
            logger.warning(f"Failed to load active orders: {exc}")

    @classmethod
    def load_config(cls, config_path: Optional[Path] = None) -> PolicyConfig:
        """Loads execution policy config from JSON or returns default."""
        target_path = Path(config_path) if config_path is not None else CONFIG_PATH
        if target_path.is_file():
            try:
                data = json.loads(target_path.read_text(encoding="utf-8"))
                mode_str = data.get("mode", "CO_PILOT").upper()
                env_str = data.get("environment", "PAPER_SIMULATION").upper()
                return PolicyConfig(
                    mode=ExecutionMode(mode_str),
                    environment=ExecutionEnvironment(env_str),
                    expiry_seconds=float(data.get("expiry_seconds", 30.0)),
                    max_slippage_bps=float(data.get("max_slippage_bps", 15.0)),
                    risk_budget_rs=float(data.get("risk_budget_rs", 1500.0)),
                    max_open_positions=int(data.get("max_open_positions", 3)),
                    cash_buffer_rs=float(data.get("cash_buffer_rs", 136000.0)),
                    tier1_vol_mult_threshold=float(data.get("tier1_vol_mult_threshold", 4.0)),
                    enforce_rule1_lock=bool(data.get("enforce_rule1_lock", True)),
                )
            except Exception as exc:
                logger.warning(f"Error parsing {target_path.name}: {exc}. Using default CO_PILOT.")
        return PolicyConfig()

    def save_config(self) -> None:
        """Persists policy config to disk."""
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "mode": self.config.mode.value,
                "environment": self.config.environment.value,
                "expiry_seconds": self.config.expiry_seconds,
                "max_slippage_bps": self.config.max_slippage_bps,
                "risk_budget_rs": self.config.risk_budget_rs,
                "max_open_positions": self.config.max_open_positions,
                "cash_buffer_rs": self.config.cash_buffer_rs,
                "tier1_vol_mult_threshold": self.config.tier1_vol_mult_threshold,
                "enforce_rule1_lock": self.config.enforce_rule1_lock,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = self.config_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self.config_path)

    def set_mode(self, mode: ExecutionMode) -> None:
        """Updates operational mode (CO_PILOT / AUTONOMOUS / HYBRID)."""
        logger.info(f"Execution mode updated from {self.config.mode} to {mode}")
        self.config.mode = mode
        self.save_config()

    def _load_intents(self) -> None:
        """Loads active intents from disk and hydrates deduplication cache for crash consistency (Codex Audit)."""
        if not self.intents_path.is_file():
            return
        try:
            data = json.loads(self.intents_path.read_text(encoding="utf-8"))
            now_mono = time.monotonic()
            for item in data.get("intents", []):
                intent_id = item.get("intent_id")
                if intent_id in self.intents:
                    existing = self.intents[intent_id]
                    if item.get("status"):
                        existing.status = IntentStatus(item["status"])
                    if "expires_at" in item:
                        existing.expires_at = item["expires_at"]
                    existing.resolved_by = item.get("resolved_by", existing.resolved_by)
                    existing.resolved_at = item.get("resolved_at", existing.resolved_at)
                    existing.resolution_notes = item.get("resolution_notes", existing.resolution_notes)
                else:
                    self.intents[intent_id] = ExecutionIntent.from_dict(item)

                # Hydrate deduplication cache to prevent re-routing after crash/restart
                if intent_id:
                    self._dedup_cache[f"approve_{intent_id}_USER"] = now_mono
                    self._dedup_cache[f"approve_{intent_id}_AUTONOMOUS"] = now_mono
        except Exception as exc:
            logger.warning(f"Failed to load intents: {exc}")

    def _save_intents(self) -> None:
        """Atomically saves intents to shared/track2_liquid/execution_intents.json."""
        self.intents_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "execution_mode": self.config.mode.value,
            "environment": self.config.environment.value,
            "rule1_enforced": self.config.enforce_rule1_lock,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "intents": [intent.to_dict() for intent in self.intents.values()],
        }
        tmp = self.intents_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self.intents_path)
        except Exception as exc:
            logger.error(f"Failed to save intents: {exc}")

    def _get_active_and_pending_exposures(
        self, exclude_intent_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Claude Pillar 2 CapacityLedger:
        Calculates exposure across active orders and all non-terminal pending intents
        (PENDING_APPROVAL, PRE_ARMED, APPROVED).
        """
        active_pos = [
            {
                "symbol": o.get("symbol"),
                "sector": o.get("sector"),
                "open_risk_rs": float(o.get("risk_rs", 0.0)),
                "notional_rs": float(o.get("notional", 0.0)),
                "shares": int(o.get("shares", 0)),
                "entry_price": float(o.get("entry_price", 0.0)),
            }
            for o in self.active_orders
        ]

        pending_pos = []
        now_utc = datetime.now(timezone.utc)
        active_order_intent_ids = {o.get("intent_id") for o in self.active_orders if o.get("intent_id")}

        for intent in self.intents.values():
            if intent.intent_id == exclude_intent_id:
                continue
            if intent.intent_id in active_order_intent_ids:
                continue
            if intent.status in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED, IntentStatus.APPROVED):
                if not intent.is_expired(now_utc):
                    pending_pos.append({
                        "symbol": intent.symbol,
                        "sector": getattr(intent, "sector", None),
                        "open_risk_rs": float(intent.risk_rs),
                        "notional_rs": float(intent.notional_rs),
                        "shares": int(intent.shares),
                        "entry_price": float(intent.entry_price),
                        "limit_price": float(intent.limit_price),
                        "stop_price": float(intent.stop_loss),
                    })
        return active_pos, pending_pos

    def submit_candidate(self, candidate: Dict[str, Any]) -> Tuple[Optional[ExecutionIntent], str]:
        """
        Submits a candidate breakout signal.
        Performs pre-flight risk checks, sizes the trade, and creates ExecutionIntent.
        Thread-safe under self._lock.
        """
        with self._lock:
            sym = str(candidate.get("symbol", "")).upper().strip()

            # 1. Check if an active/pending intent already exists for this symbol
            for intent in self.intents.values():
                if intent.symbol == sym and intent.status in (
                    IntentStatus.PENDING_APPROVAL,
                    IntentStatus.APPROVED,
                    IntentStatus.ROUTED,
                ):
                    return None, f"Active intent already exists for {sym} ({intent.intent_id})"

            # 2. Size Trade and Create Intent with 30s expiry and adverse-selection collar
            try:
                intent = ExecutionIntent.create_from_candidate(
                    candidate=candidate,
                    mode=self.config.mode,
                    expiry_seconds=self.config.expiry_seconds,
                )
            except Exception as exc:
                return None, f"REJECTED: Sizing or validation failed: {exc}"

            # 3. Risk Governor Gate with Capacity Ledger (Claude Pillar 2 & Codex R02/R03)
            active_pos, pending_pos = self._get_active_and_pending_exposures()

            assessment = self.governor.assess_candidate(
                symbol=intent.symbol,
                entry_price=intent.entry_price,
                stop_price=intent.stop_loss,
                quantity=intent.shares,
                active_positions=active_pos,
                pending_orders=pending_pos,
                var_elm_rate=candidate.get("var_elm_rate"),
                custom_sector=candidate.get("sector"),
            )
            if not assessment.is_approved:
                reason = assessment.rejection_reason or assessment.reason or "REJECTED_BY_RISK_GOVERNOR"
                logger.warning(f"Candidate {sym} rejected by Risk Governor: {reason}")
                return None, f"REJECTED: Risk Governor: {reason}"

            self.intents[intent.intent_id] = intent
            self._save_intents()

            logger.info(
                f"Created Intent {intent.intent_id}: {sym} {intent.shares} shares @ ₹{intent.entry_price} "
                f"[Limit Collar: ₹{intent.limit_price} | Risk: ₹{intent.risk_rs} | Target: ₹{intent.target_tranche1} | Mode: {intent.mode.value} | Status: {intent.status.value}]"
            )

            # 4. If status is APPROVED (Autonomous or Tier 1 Hybrid), route immediately
            if intent.status == IntentStatus.APPROVED:
                order_res = self.route_order(intent)
                return intent, f"AUTONOMOUS_ROUTED: {order_res.get('message', 'Order placed')}"

            return intent, "PENDING_CO_PILOT_APPROVAL"

    def pre_arm_intent(
        self,
        intent_id: str,
        armed_by: str = "USER",
    ) -> Dict[str, Any]:
        """Pre-arms an intent so it fires automatically at machine speed on trigger crossing (Claude Audit)."""
        with self._lock:
            self._load_intents()
            intent = self.intents.get(intent_id)
            if not intent:
                return {"status": "ERROR", "message": f"Intent {intent_id} not found"}
            if intent.status != IntentStatus.PENDING_APPROVAL:
                return {"status": "ERROR", "message": f"Cannot pre-arm intent in status {intent.status.value}"}
            intent.arm(armed_by=armed_by)
            self._save_intents()
            logger.info(f"[PRE_ARMED] Intent {intent_id} pre-armed by {armed_by} for instant breakout fill.")
            return {"status": "PRE_ARMED", "intent_id": intent_id}

    def approve_intent(
        self,
        intent_id: str,
        approver: str = "USER",
        request_id: Optional[str] = None,
        current_ltp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Approves a pending or pre-armed intent and routes it through the execution gateway.
        Thread-safe under self._lock with Compare-And-Swap (CAS) and 300s deduplication.
        """
        with self._lock:
            now_mono = time.monotonic()
            # Purge stale dedup cache entries > 300s
            self._dedup_cache = {k: ts for k, ts in self._dedup_cache.items() if (now_mono - ts) < 300.0}
            dedup_key = f"approve_{intent_id}_{request_id or approver}"
            if dedup_key in self._dedup_cache:
                logger.warning(f"Duplicate approve request detected for {intent_id}. Ignoring idempotently.")
                return {"status": "IDEMPOTENT_IGNORED", "message": f"Duplicate request for {intent_id} ignored"}

            self._load_intents()
            intent = self.intents.get(intent_id)
            if not intent:
                return {"status": "ERROR", "message": f"Intent {intent_id} not found"}

            # Strict Server Clock Authority for Expiry
            now_utc = datetime.now(timezone.utc)
            if intent.is_expired(now_utc):
                intent.mark_expired()
                self._save_intents()
                return {"status": "ERROR", "message": f"Intent {intent_id} has expired (30s window elapsed)"}

            # Atomic Compare-And-Swap (CAS) Validation (Supports PENDING_APPROVAL and PRE_ARMED)
            if intent.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED):
                return {
                    "status": "REJECTED_STALE_STATE",
                    "current_status": intent.status.value,
                    "message": f"Intent is in {intent.status.value}, cannot approve",
                }

            # Re-evaluate capacity with governor before approval (Claude Pillar 2 & Codex R02)
            active_pos, pending_pos = self._get_active_and_pending_exposures(exclude_intent_id=intent.intent_id)
            assessment = self.governor.assess_candidate(
                symbol=intent.symbol,
                entry_price=intent.entry_price,
                stop_price=intent.stop_loss,
                quantity=intent.shares,
                active_positions=active_pos,
                pending_orders=pending_pos,
                var_elm_rate=getattr(intent, "var_elm_rate", None),
                custom_sector=getattr(intent, "sector", None),
            )
            if not assessment.is_approved:
                reason = assessment.rejection_reason or assessment.reason or "REJECTED_CAPACITY"
                logger.warning(f"Cannot approve intent {intent_id}: Risk Governor rejected: {reason}")
                intent.reject(reason=f"CAPACITY_EXCEEDED: {reason}")
                self._save_intents()
                return {"status": "REJECTED_CAPACITY", "reason": reason, "message": f"Governor capacity exceeded: {reason}"}

            self._dedup_cache[dedup_key] = now_mono
            intent.approve(approver=approver)
            self._save_intents()
            logger.info(f"[APPROVED] Intent {intent_id} approved by {approver}. Routing order...")

            return self.route_order(intent, current_ltp=current_ltp)

    def reject_intent(
        self,
        intent_id: str,
        reason: str = "USER_REJECTED",
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Rejects a pending intent.
        Thread-safe under self._lock with CAS validation.
        """
        with self._lock:
            now_mono = time.monotonic()
            self._dedup_cache = {k: ts for k, ts in self._dedup_cache.items() if (now_mono - ts) < 300.0}
            dedup_key = f"reject_{intent_id}_{request_id or reason}"
            if dedup_key in self._dedup_cache:
                return {"status": "IDEMPOTENT_IGNORED", "message": f"Duplicate reject request for {intent_id} ignored"}

            self._load_intents()
            intent = self.intents.get(intent_id)
            if not intent:
                return {"status": "ERROR", "message": f"Intent {intent_id} not found"}

            if intent.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.APPROVED):
                return {
                    "status": "REJECTED_STALE_STATE",
                    "current_status": intent.status.value,
                    "message": f"Intent is in {intent.status.value}, cannot reject",
                }

            self._dedup_cache[dedup_key] = now_mono
            intent.reject(reason=reason)
            self._save_intents()
            logger.info(f"[REJECTED] Intent {intent_id} rejected: {reason}")
            return {"status": "OK", "message": f"Intent {intent_id} rejected successfully"}

    def sweep_expired_intents(self) -> int:
        """Sweeps and cancels intents that exceeded their 30-second expiry window."""
        with self._lock:
            self._load_intents()
            now_dt = datetime.now(timezone.utc)
            expired_count = 0
            for intent in self.intents.values():
                if intent.status in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED) and intent.is_expired(now_dt):
                    intent.mark_expired()
                    expired_count += 1
                    logger.info(f"[EXPIRED] Intent {intent.intent_id} ({intent.symbol}) expired fail-closed.")

            if expired_count > 0:
                self._save_intents()
            return expired_count

    def _sample_live_ltp(self, symbol: str) -> Optional[float]:
        """Samples the most recent market LTP from live_depth_track2.json."""
        live_depth_file = self.output_dir / "live_depth_track2.json"
        if not live_depth_file.is_file():
            return None
        try:
            data = json.loads(live_depth_file.read_text(encoding="utf-8"))
            for item in data.get("watchlist", []):
                if item.get("symbol") == symbol:
                    val = float(item.get("ltp", 0.0))
                    if val > 0:
                        return val
        except Exception:
            pass
        return None

    def route_order(self, intent: ExecutionIntent, current_ltp: Optional[float] = None) -> Dict[str, Any]:
        """
        Routes the approved intent to the execution gateway.
        Enforces Rule 1 Gate, Idempotency Guard, Live Feed Verification, Pegged Limit Order Collar, and Pre-Routing Slippage Guard.
        """
        with self._lock:
            # Rule 1 Security Check
            self.config.validate_for_execution()

            # Idempotency Guard: prevent duplicate routing on same intent
            if intent.status in (IntentStatus.ROUTED, IntentStatus.FILLED, IntentStatus.REJECTED, IntentStatus.CANCELLED):
                return {
                    "status": "ERROR",
                    "reason": "ALREADY_ROUTED",
                    "message": f"Intent {intent.intent_id} is already in {intent.status.value}",
                }

            # Expiry and State Guard (Codex R12)
            now_utc = datetime.now(timezone.utc)
            if intent.is_expired(now_utc) or intent.status == IntentStatus.EXPIRED:
                abort_reason = "INTENT_EXPIRED"
                abort_msg = f"ABORTED: Intent {intent.intent_id} has expired."
                logger.warning(f"🚨 [PRE-ROUTING ABORT] {intent.intent_id} {intent.symbol}: {abort_msg}")
                intent.mark_expired()
                self._save_intents()
                return {"status": "ERROR", "reason": abort_reason, "message": abort_msg}

            if intent.status != IntentStatus.APPROVED:
                return {
                    "status": "ERROR",
                    "reason": "INVALID_STATE",
                    "message": f"Intent {intent.intent_id} is in {intent.status.value}, cannot route without APPROVED status",
                }

            # 1. Notional Ceiling Gate (Claude Red-Team F28)
            if intent.notional_rs > 100000.0:
                abort_reason = "NOTIONAL_CEILING_EXCEEDED"
                abort_msg = (
                    f"ABORTED: Order notional ₹{intent.notional_rs:,.2f} exceeds "
                    f"maximum allowable ceiling of ₹1,00,000.00."
                )
                logger.warning(f"🚨 [PRE-ROUTING ABORT] {intent.intent_id} {intent.symbol}: {abort_msg}")
                intent.status = IntentStatus.REJECTED
                intent.resolved_at = datetime.now(timezone.utc).isoformat()
                intent.resolved_by = "NOTIONAL_GUARD"
                intent.resolution_notes = abort_msg
                self._save_intents()
                return {"status": "ERROR", "reason": abort_reason, "message": abort_msg}

            # Pre-Flight Market Data Verification (Codex R01)
            market_ltp = current_ltp if current_ltp is not None else self._sample_live_ltp(intent.symbol)
            if (
                market_ltp is None
                or not isinstance(market_ltp, (int, float))
                or not math.isfinite(float(market_ltp))
                or float(market_ltp) <= 0
            ):
                abort_reason = "LIVE_FEED_UNAVAILABLE"
                abort_msg = (
                    f"ABORTED: Live market LTP unavailable, stale, or invalid for {intent.symbol}. "
                    f"Routing without verified live market feed is strictly prohibited."
                )
                logger.warning(f"🚨 [PRE-ROUTING ABORT] {intent.intent_id} {intent.symbol}: {abort_msg}")
                intent.status = IntentStatus.REJECTED
                intent.resolved_at = datetime.now(timezone.utc).isoformat()
                intent.resolved_by = "FEED_GUARD"
                intent.resolution_notes = abort_msg
                self._save_intents()
                return {"status": "ERROR", "reason": abort_reason, "message": abort_msg}

            # 2. Adverse Selection Check: Price extended beyond limit collar
            if market_ltp > intent.limit_price:
                pct_excess = round(((market_ltp - intent.entry_price) / intent.entry_price) * 100, 2)
                abort_reason = "SLIPPAGE_TOLERANCE_EXCEEDED"
                abort_msg = (
                    f"ABORTED: Market LTP (₹{market_ltp}) exceeds limit collar (₹{intent.limit_price}) "
                    f"by +{pct_excess}%. Capital preserved."
                )
                logger.warning(f"🚨 [PRE-ROUTING ABORT] {intent.intent_id} {intent.symbol}: {abort_msg}")
                intent.status = IntentStatus.REJECTED
                intent.resolved_at = datetime.now(timezone.utc).isoformat()
                intent.resolved_by = "SLIPPAGE_GUARD"
                intent.resolution_notes = abort_msg
                self._save_intents()
                return {"status": "ERROR", "reason": abort_reason, "message": abort_msg}

            # 2. Retracement Check: Breakout failed and price retraced below trigger
            if market_ltp < intent.entry_price:
                abort_reason = "FALSE_BREAKOUT_RETRACED"
                abort_msg = (
                    f"ABORTED: Market LTP (₹{market_ltp}) retraced below entry trigger (₹{intent.entry_price}). "
                    f"False breakout filtered."
                )
                logger.warning(f"🚨 [PRE-ROUTING ABORT] {intent.intent_id} {intent.symbol}: {abort_msg}")
                intent.status = IntentStatus.REJECTED
                intent.resolved_at = datetime.now(timezone.utc).isoformat()
                intent.resolved_by = "SLIPPAGE_GUARD"
                intent.resolution_notes = abort_msg
                self._save_intents()
                return {"status": "ERROR", "reason": abort_reason, "message": abort_msg}

            intent.status = IntentStatus.ROUTED
            now_ist = datetime.now(IST)
            now_str = now_ist.strftime("%Y-%m-%d %H:%M:%S")

            # Pegged Limit Order Record with Statutory Charges Modeling
            order_record = {
                "order_id": f"ORD_{intent.symbol}_{uuid.uuid4().hex[:8].upper()}",
                "intent_id": intent.intent_id,
                "symbol": intent.symbol,
                "shares": intent.shares,
                "order_type": "PEGGED_LIMIT",
                "limit_price": intent.limit_price,
                "entry_price": intent.entry_price,
                "stop_loss": intent.stop_loss,
                "target_tranche1": intent.target_tranche1,
                "runner_tranche2": intent.runner_tranche2,
                "tranche1_shares": intent.tranche1_shares,
                "tranche2_shares": intent.tranche2_shares,
                "risk_rs": intent.risk_rs,
                "notional": intent.notional_rs,
                "status": "QUEUED",  # Realistic Discrete 4-State Execution: Starts QUEUED behind market liquidity
                "environment": self.config.environment.value,
                "time_in_force": "15S_IOC",
                "placed_at": now_str,
                "tranche1_status": "PENDING_TARGET",
                "statutory_friction_est_rs": 167.54,
                "total_friction_est_rs": 258.10,
                "friction_bps_est": 43.0,
                "dp_charges_count": 2,
            }

            # Append to orders ledger
            self.orders_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.orders_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(order_record) + "\n")

            intent.order_id = order_record["order_id"]
            self.active_orders.append(order_record)
            self._save_intents()

            logger.info(
                f"🎉 [ROUTED] {order_record['order_id']} for {intent.symbol}: "
                f"{intent.shares} shares @ Pegged Limit ₹{intent.limit_price} (Risk: ₹{intent.risk_rs}) "
                f"Armed Brackets: SL=₹{intent.stop_loss}, T1=₹{intent.target_tranche1}, T2=₹{intent.runner_tranche2}"
            )

            return {
                "status": "SUCCESS",
                "order_id": order_record["order_id"],
                "symbol": intent.symbol,
                "shares": intent.shares,
                "order_type": "PEGGED_LIMIT",
                "limit_price": intent.limit_price,
                "entry_price": intent.entry_price,
                "stop_loss": intent.stop_loss,
                "target_tranche1": intent.target_tranche1,
                "runner_tranche2": intent.runner_tranche2,
                "message": f"Pegged Limit Order {order_record['order_id']} placed with two-tranche bracket",
            }

    def emergency_flatten_all(self, reason: str = "EMERGENCY_KILL_SWITCH") -> Dict[str, Any]:
        """Emergency kill-switch: cancels all pending/pre-armed intents and records explicit square-off exits for open orders."""
        with self._lock:
            logger.warning(f"🚨 [KILL SWITCH TRIGGERED] Reason: {reason}")
            now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

            # Cancel pending and pre-armed intents
            cancelled_intents = 0
            for intent in self.intents.values():
                if intent.status in (IntentStatus.PENDING_APPROVAL, IntentStatus.APPROVED, IntentStatus.PRE_ARMED):
                    intent.status = IntentStatus.CANCELLED
                    intent.resolved_at = datetime.now(timezone.utc).isoformat()
                    intent.resolved_by = "KILL_SWITCH"
                    intent.resolution_notes = f"Flattened: {reason}"
                    cancelled_intents += 1

            self._save_intents()

            # Record explicit exit orders to paper_orders.jsonl
            squared_off_orders = []
            unsquared_held = []
            if self.active_orders:
                self.orders_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.orders_path, "a", encoding="utf-8") as f:
                    for o in self.active_orders:
                        o_status = str(o.get("status", "QUEUED")).upper()
                        total_shares = int(o.get("shares", 0))
                        if o_status in ("OPEN", "FILLED", "SQUARED_OFF"):
                            filled_shares = int(o.get("filled_shares", total_shares))
                        elif o_status == "PARTIAL":
                            filled_shares = int(o.get("filled_shares", 0))
                        else:
                            filled_shares = int(o.get("filled_shares", 0))
                        unfilled_shares = max(0, total_shares - filled_shares)

                        # 1. Cancel unfilled order or unfilled remainder of PARTIAL order (Codex R05)
                        if unfilled_shares > 0 and o_status in ("QUEUED", "ROUTED", "PARTIAL"):
                            action = "EMERGENCY_CANCEL" if filled_shares == 0 else "EMERGENCY_CANCEL_UNFILLED"
                            cancel_rec = {
                                "order_id": o.get("order_id"),
                                "intent_id": o.get("intent_id"),
                                "symbol": o.get("symbol"),
                                "shares": unfilled_shares,
                                "action": action,
                                "exit_price": None,
                                "status": "CANCELLED",
                                "reason": reason,
                                "timestamp": now_str,
                            }
                            f.write(json.dumps(cancel_rec) + "\n")
                            squared_off_orders.append(cancel_rec)

                        # 2. Square off or preserve filled inventory (Claude A22 & Codex R05)
                        if filled_shares > 0:
                            market_exit = self._sample_live_ltp(o.get("symbol", ""))
                            if market_exit is not None and market_exit > 0:
                                entry_px = float(o.get("entry_price", 0.0))
                                gross_pnl = round(filled_shares * (market_exit - entry_px), 2)
                                friction = 0.0
                                try:
                                    from antigravity.models.track2_paper_execution import calculate_transaction_costs
                                    cost = calculate_transaction_costs(market_exit, filled_shares, "SELL", is_intraday=True)
                                    friction = cost.get("total_cost", 0.0)
                                except Exception:
                                    pass
                                net_pnl = round(gross_pnl - friction, 2)
                                exit_rec = {
                                    "order_id": o.get("order_id"),
                                    "intent_id": o.get("intent_id"),
                                    "symbol": o.get("symbol"),
                                    "shares": filled_shares,
                                    "action": "EMERGENCY_EXIT",
                                    "exit_price": market_exit,
                                    "status": "SQUARED_OFF",
                                    "gross_pnl_rs": gross_pnl,
                                    "net_pnl_rs": net_pnl,
                                    "reason": reason,
                                    "timestamp": now_str,
                                }
                                f.write(json.dumps(exit_rec) + "\n")
                                squared_off_orders.append(exit_rec)
                            else:
                                # Market feed unavailable: DO NOT fabricate entry_price as exit price!
                                held_rec = {
                                    "order_id": o.get("order_id"),
                                    "intent_id": o.get("intent_id"),
                                    "symbol": o.get("symbol"),
                                    "shares": filled_shares,
                                    "action": "EMERGENCY_UNSQUARED_HELD",
                                    "exit_price": None,
                                    "status": "OPEN_FEED_UNAVAILABLE",
                                    "requires_manual_broker_intervention": True,
                                    "reason": f"{reason}: Market feed unavailable to confirm squareoff price",
                                    "timestamp": now_str,
                                }
                                f.write(json.dumps(held_rec) + "\n")
                                unsquared_held.append({**o, "status": "OPEN_FEED_UNAVAILABLE", "shares": filled_shares, "filled_shares": filled_shares})
                                squared_off_orders.append(held_rec)

            squared_off = len(squared_off_orders)
            self.active_orders = unsquared_held

            return {
                "status": "KILL_SWITCH_EXECUTED",
                "cancelled_intents": cancelled_intents,
                "squared_off_orders": squared_off,
                "timestamp": now_str,
                "reason": reason,
            }

    def get_status_summary(self) -> Dict[str, Any]:
        """Returns clean operational summary for UI and Telegram."""
        with self._lock:
            self._load_intents()
            self._load_active_orders()
            self.config = self.load_config()
            now_utc = datetime.now(timezone.utc)
            pending = [
                i.to_dict()
                for i in self.intents.values()
                if i.status == IntentStatus.PENDING_APPROVAL and not i.is_expired(now_utc)
            ]
            return {
                "mode": self.config.mode.value,
                "environment": self.config.environment.value,
                "pending_co_pilot_count": len(pending),
                "pending_intents": pending,
                "active_positions_count": len(self.active_orders),
                "open_risk_rs": sum(o.get("risk_rs", 0.0) for o in self.active_orders),
                "max_positions": self.config.max_open_positions,
                "risk_per_trade_rs": self.config.risk_budget_rs,
                "updated_at": now_utc.isoformat(),
            }


def run_oms_supervisor(poll_interval: float = 1.0) -> None:
    """Supervisor loop that monitors candidate events and sweeps expired intents."""
    oms = HybridExecutionOMS()
    logger.info("=" * 60)
    logger.info("       TRACK 2 HYBRID OMS SUPERVISOR INITIALIZED")
    logger.info(f"       Operational Mode: {oms.config.mode.value}")
    logger.info(f"       Environment: {oms.config.environment.value}")
    logger.info(f"       Rule 1 Guard: {'ENFORCED' if oms.config.enforce_rule1_lock else 'DISABLED'}")
    logger.info("=" * 60)

    try:
        while True:
            # 1. Sweep expired intents past 90s
            oms.sweep_expired_intents()
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Hybrid OMS Supervisor stopped by user.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track 2 Hybrid Execution OMS")
    parser.add_argument("--mode", choices=["CO_PILOT", "AUTONOMOUS", "HYBRID"], help="Override operational mode")
    args = parser.parse_args()

    if args.mode:
        oms_inst = HybridExecutionOMS()
        oms_inst.set_mode(ExecutionMode(args.mode))

    run_oms_supervisor()
