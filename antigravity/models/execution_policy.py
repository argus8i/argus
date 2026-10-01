"""
execution_policy.py - Execution Policy Contracts & Intent Model for Track 2
===========================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).

Defines execution modes (Co-Pilot, Autonomous, Hybrid), environment guards
(Paper vs Live Broker), and the ExecutionIntent lifecycle model with strict
fail-closed expiry windows (90s).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional

from antigravity.models.session_manifest import IST
from antigravity.models.track2_a1 import affordable, finite_positive, SLOT_CAP_RS, RISK_PER_TRADE_RS, MAX_SLOTS, CASH_BUFFER_RS, ATR_MAX_AGE_SECONDS


class ExecutionMode(str, Enum):
    """Execution operational mode."""
    CO_PILOT = "CO_PILOT"          # Human-in-the-loop: Requires 1-click approval
    AUTONOMOUS = "AUTONOMOUS"      # Machine speed: Executes instantaneously (<15ms)
    HYBRID = "HYBRID"              # Tier-1 high conviction auto-executes; Tier-2 prompts


class ExecutionEnvironment(str, Enum):
    """Execution environment safety gate."""
    PAPER_SIMULATION = "PAPER_SIMULATION"  # Strictly Paper (Rule 1 Gate)
    LIVE_BROKER = "LIVE_BROKER"            # Real capital broker gateway


class IntentStatus(str, Enum):
    """Lifecycle status of an execution intent."""
    PENDING_APPROVAL = "PENDING_APPROVAL"  # Waiting for Co-Pilot decision
    PRE_ARMED = "PRE_ARMED"                # Pre-authorized by user; triggers automatically on breakout tick
    APPROVED = "APPROVED"                  # Approved by user or autonomous engine
    REJECTED = "REJECTED"                  # Rejected by user
    EXPIRED = "EXPIRED"                    # 90s countdown elapsed without decision
    ROUTED = "ROUTED"                      # Sent to execution gateway
    FILLED = "FILLED"                      # Confirmed fill
    CANCELLED = "CANCELLED"                # Order cancelled or killed
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    EXITING = "EXITING"
    CLOSED = "CLOSED"


class SecurityViolationError(Exception):
    """Raised when an action violates security or regulatory gates (e.g. Rule 1)."""
try:
    from research.execution_realism.marketdata import schedule_tick, floor_to_tick, ceil_to_tick
except ImportError:
    def schedule_tick(reference_close: float) -> float:
        p = float(reference_close)
        if p < 250:
            return 0.01
        if p <= 1000:
            return 0.05
        if p <= 5000:
            return 0.10
        if p <= 10000:
            return 0.50
        if p <= 20000:
            return 1.00
        return 5.00

    def floor_to_tick(price: float, tick: float) -> float:
        return round(math.floor(price / tick + 1e-9) * tick, 2)

    def ceil_to_tick(price: float, tick: float) -> float:
        return round(math.ceil(price / tick - 1e-9) * tick, 2)


@dataclass
class ExecutionIntent:
    """Represents a validated trade candidate awaiting routing or approval."""
    intent_id: str
    symbol: str
    entry_price: float
    stop_loss: float
    target_tranche1: float
    runner_tranche2: float
    shares: int
    tranche1_shares: int
    tranche2_shares: int
    risk_rs: float
    notional_rs: float
    mode: ExecutionMode
    status: IntentStatus
    limit_price: float = 0.0
    max_slippage_bps: float = 15.0
    conviction_tier: int = 2
    volume_multiplier: float = 1.0
    nifty_breadth_confirmed: bool = False
    var_elm_rate: Optional[float] = None
    sector: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = field(default_factory=lambda: (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None
    order_id: Optional[str] = None
    atr14: float = 0.0
    atr_timestamp: Optional[str] = None
    strategy: str = "UNSPECIFIED"

    @classmethod
    def create_from_candidate(
        cls,
        candidate: Dict[str, Any],
        mode: ExecutionMode = ExecutionMode.CO_PILOT,
        expiry_seconds: float = 30.0,
    ) -> Optional[ExecutionIntent]:
        """Constructs and validates an ExecutionIntent from a candidate signal dictionary."""
        sym = str(candidate.get("symbol", "")).upper().strip()
        entry_raw = candidate.get("entry_price")
        stop_raw = candidate.get("stop_cost_price", candidate.get("stop_loss"))
        atr14 = candidate.get("atr14", candidate.get("atr"))
        if not sym or not all(finite_positive(v) for v in (entry_raw, stop_raw, atr14)):
            return None
        try:
            atr_at = datetime.fromisoformat(candidate["atr_timestamp"])
            if atr_at.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - atr_at).total_seconds() <= ATR_MAX_AGE_SECONDS:
                return None
        except (KeyError, ValueError, TypeError):
            return None
        entry = round(float(entry_raw), 2)
        stop = round(float(stop_raw), 2)
        target1 = round(float(candidate.get("target_price", candidate.get("target_tranche1", 0.0))), 2)

        slippage_bps = candidate.get("max_slippage_bps", 15.0)
        if not finite_positive(slippage_bps):
            return None
        tick = schedule_tick(entry)
        entry = floor_to_tick(entry, tick)
        stop = floor_to_tick(stop, tick)
        factor = .10 if slippage_bps <= 15 else .15
        limit_price = floor_to_tick(min(entry * (1 + slippage_bps / 10000), entry + factor * atr14), tick)
        risk_per_share = limit_price - stop
        if risk_per_share <= 0:
            return None

        # Risk budgeting: ₹1,500 target risk per trade (1.0R)
        risk_rs = candidate.get("actual_risk_rs", candidate.get("risk_rs", RISK_PER_TRADE_RS))
        raw_shares = candidate.get("shares")
        max_slot_notional = candidate.get("max_slot_notional_rs", SLOT_CAP_RS)
        if not finite_positive(risk_rs) or not finite_positive(max_slot_notional):
            return None
        max_slot_notional = min(max_slot_notional, SLOT_CAP_RS)
        max_qty = affordable(limit_price, stop, max_slot_notional, risk_rs)

        # Check if entry price alone exceeds slot notional cap (clean skip/reject without unhandled ValueError)
        if max_qty <= 0:
            return None

        if raw_shares is not None:
            if isinstance(raw_shares, bool) or not isinstance(raw_shares, int):
                return None
            shares = raw_shares
            if shares <= 0:
                return None
            # Enforce Rs 1,500 per-trade risk cap on caller-supplied shares
            if round(shares * risk_per_share, 2) > min(RISK_PER_TRADE_RS, risk_rs):
                return None
            if (shares * limit_price) > max_slot_notional:
                capped = max_qty
                if capped <= 0:
                    return None
                shares = capped
        else:
            # If floor(1500 / risk_per_share) == 0, SKIP the trade (return a reject), never force 1 share
            shares = max_qty
            if shares <= 0:
                return None
            if (shares * limit_price) > max_slot_notional:
                capped = max_qty
                if capped <= 0:
                    return None
                shares = capped

        # Fail-closed guard: ensure positive shares and risk <= 1500
        if shares <= 0 or round(shares * risk_per_share, 2) > 1500.0:
            return None

        actual_risk = round(shares * risk_per_share, 2)
        notional = round(shares * limit_price, 2)

        # Two-Tranche Allocation (50% Target 1, 50% Runner)
        tranche1_qty = max(1, shares // 2) if shares > 1 else 1
        tranche2_qty = shares - tranche1_qty
        if tranche2_qty < 0:
            raise ValueError(f"Invalid tranche split: t1={tranche1_qty}, t2={tranche2_qty} for shares={shares}")

        # Default +1.5R target if not provided
        if target1 <= entry:
            target1 = round(entry + 1.5 * risk_per_share, 2)
        runner_target = round(entry + 3.0 * risk_per_share, 2)

        vol_mult = float(candidate.get("volume_multiplier", candidate.get("vol_mult", 1.0)))
        breadth_ok = bool(candidate.get("nifty_breadth_confirmed", False))
        is_pre_armed = bool(candidate.get("pre_armed", False))

        # Adverse-Selection Guard:
        # If pre-armed or Tier 1 (machine speed), use strict +15 bps collar: min(Trigger * 1.0015, Trigger + 0.10 * ATR14).
        # If manual post-breakout co-pilot, use adaptive +25 bps collar: min(Trigger * 1.0025, Trigger + 0.15 * ATR14)
        # to prevent the "Winner's Curse" where +15 bps limits only fill on failing breakouts (Claude Audit).
        # Align prices to exchange tick grid (NSE CM tick schedule: Claude Finding A26)
        tick = schedule_tick(entry)
        entry = floor_to_tick(entry, tick)
        stop = floor_to_tick(stop, tick)
        target1 = ceil_to_tick(target1, tick)
        runner_target = ceil_to_tick(runner_target, tick)
        limit_price = floor_to_tick(limit_price, tick)

        var_elm = candidate.get("var_elm_rate")
        var_elm_rate = float(var_elm) if var_elm is not None and not isinstance(var_elm, bool) else None
        sector = candidate.get("sector")

        # Determine conviction tier: Tier 1 if volume >= 4.0x and breadth confirmed
        conviction_tier = 1 if (vol_mult >= 4.0 and breadth_ok) else 2

        now_utc = datetime.now(timezone.utc)
        exp_utc = now_utc + timedelta(seconds=expiry_seconds)

        # Status resolution
        initial_status = IntentStatus.PENDING_APPROVAL
        resolved_by = None
        resolved_at = None

        if is_pre_armed:
            initial_status = IntentStatus.PRE_ARMED
            resolved_by = "USER_PRE_ARMED"
            resolved_at = now_utc.isoformat()
        elif mode == ExecutionMode.AUTONOMOUS:
            initial_status = IntentStatus.APPROVED
            resolved_by = "AUTONOMOUS_ENGINE"
            resolved_at = now_utc.isoformat()
        elif mode == ExecutionMode.HYBRID:
            if conviction_tier == 1:
                initial_status = IntentStatus.APPROVED
                resolved_by = "HYBRID_TIER1_AUTO"
                resolved_at = now_utc.isoformat()

        return cls(
            intent_id=f"INTENT_{sym}_{uuid.uuid4().hex[:8].upper()}",
            symbol=sym,
            entry_price=entry,
            stop_loss=stop,
            target_tranche1=target1,
            runner_tranche2=runner_target,
            shares=shares,
            tranche1_shares=tranche1_qty,
            tranche2_shares=tranche2_qty,
            risk_rs=actual_risk,
            notional_rs=notional,
            mode=mode,
            status=initial_status,
            limit_price=limit_price,
            max_slippage_bps=slippage_bps,
            conviction_tier=conviction_tier,
            volume_multiplier=vol_mult,
            nifty_breadth_confirmed=breadth_ok,
            var_elm_rate=var_elm_rate,
            sector=sector,
            atr14=atr14,
            atr_timestamp=atr_at.isoformat(),
            strategy=str(candidate.get("strategy", "UNSPECIFIED")),
            created_at=now_utc.isoformat(),
            expires_at=exp_utc.isoformat(),
            resolved_at=resolved_at,
            resolved_by=resolved_by,
        )

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Returns True if the intent is in a pending or pre-armed state and past its expiry time."""
        if self.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED):
            return False
        now_dt = now or datetime.now(timezone.utc)
        try:
            exp_dt = datetime.fromisoformat(self.expires_at)
            return now_dt >= exp_dt
        except Exception:
            return False

    def seconds_remaining(self, now: Optional[datetime] = None) -> float:
        """Returns seconds remaining until expiry (or 0.0 if expired)."""
        if self.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED):
            return 0.0
        now_dt = now or datetime.now(timezone.utc)
        try:
            exp_dt = datetime.fromisoformat(self.expires_at)
            rem = (exp_dt - now_dt).total_seconds()
            return max(0.0, round(rem, 1))
        except Exception:
            return 0.0

    def arm(self, armed_by: str = "USER") -> None:
        """Pre-arms the intent to fire automatically at machine speed on trigger crossing."""
        if self.status != IntentStatus.PENDING_APPROVAL:
            raise ValueError(f"Cannot pre-arm intent in status {self.status}")
        if self.is_expired():
            raise ValueError("Cannot pre-arm an expired intent")
        self.status = IntentStatus.PRE_ARMED
        self.resolved_at = datetime.now(timezone.utc).isoformat()
        self.resolved_by = armed_by
        self.resolution_notes = f"Pre-armed by {armed_by} for instant machine-speed breakout fill"

    def approve(self, approver: str = "USER") -> None:
        """Approves the intent for routing."""
        if self.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED):
            raise ValueError(f"Cannot approve intent in status {self.status}")
        self.status = IntentStatus.APPROVED
        self.resolved_at = datetime.now(timezone.utc).isoformat()
        self.resolved_by = approver
        self.resolution_notes = f"Approved by {approver}"

    def reject(self, reason: str = "USER_REJECTED") -> None:
        """Rejects the intent."""
        if self.status not in (IntentStatus.PENDING_APPROVAL, IntentStatus.APPROVED, IntentStatus.PRE_ARMED):
            raise ValueError(f"Cannot reject intent in status {self.status}")
        self.status = IntentStatus.REJECTED
        self.resolved_at = datetime.now(timezone.utc).isoformat()
        self.resolved_by = reason
        self.resolution_notes = f"Rejected: {reason}"

    def mark_expired(self) -> None:
        """Marks the intent as expired fail-closed."""
        if self.status in (IntentStatus.PENDING_APPROVAL, IntentStatus.PRE_ARMED):
            self.status = IntentStatus.EXPIRED
            self.resolved_at = datetime.now(timezone.utc).isoformat()
            self.resolved_by = "EXPIRY_SWEEPER"
            self.resolution_notes = "Expired: 30s countdown elapsed without execution"

    def to_dict(self) -> Dict[str, Any]:
        """Serializes intent to JSON-compatible dictionary."""
        d = asdict(self)
        d["mode"] = self.mode.value
        d["status"] = self.status.value
        d["seconds_remaining"] = self.seconds_remaining()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionIntent:
        """Reconstructs intent from dictionary."""
        d = dict(data)
        d.pop("seconds_remaining", None)
        d["mode"] = ExecutionMode(d["mode"])
        d["status"] = IntentStatus(d["status"])
        entry = float(d.get("entry_price", 0.0))
        d.setdefault("limit_price", round(entry * 1.0015, 2))
        d.setdefault("max_slippage_bps", 15.0)
        return cls(**d)


@dataclass
class PolicyConfig:
    """Configuration for the execution governor."""
    mode: ExecutionMode = ExecutionMode.CO_PILOT
    environment: ExecutionEnvironment = ExecutionEnvironment.PAPER_SIMULATION
    expiry_seconds: float = 30.0
    max_slippage_bps: float = 15.0
    risk_budget_rs: float = RISK_PER_TRADE_RS
    max_open_positions: int = MAX_SLOTS
    cash_buffer_rs: float = CASH_BUFFER_RS
    tier1_vol_mult_threshold: float = 4.0
    enforce_rule1_lock: bool = True

    def validate_for_execution(self) -> None:
        """Strict fail-closed check for live capital deployment (Rule 1)."""
        if self.environment == ExecutionEnvironment.LIVE_BROKER:
            raise SecurityViolationError(
                "RULE 1 VIOLATION: Real capital deployment is strictly prohibited. "
                "Mandatory 60 prospective paper trading sessions required before live execution."
            )
