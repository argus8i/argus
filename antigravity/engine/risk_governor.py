"""
antigravity/engine/risk_governor.py
===================================
Central Portfolio Risk Governor & Adjusted A1 Capacity Authority for Track 2.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Mathematical Guardrails & Invariants (Yashu Adjusted A1 Hardening):
1. Capacity Limits:
   - Total Portfolio Capital Corpus: Rs 2,50,000.00
   - Unencumbered Inviolable Cash Buffer: Rs 1,36,000.00 (Strictly enforced, 0 breach allowed)
   - Maximum Deployable Capital / Exposure Ceiling: Rs 1,14,000.00
   - Maximum Concurrent Position Slots: 3 slots (MAX_SLOTS = 3, pinned invariant)
   - Maximum Single Position Slot Cap: Rs 38,000.00 (SLOT_CAP_RS = 38,000.00)
   - Planned Rupee Risk Budget per Trade: Rs 1,500.00 (1R)
   - Maximum Aggregate Open Risk Cap: Rs 4,500.00 (3 * Rs 1,500.00)
2. Mathematical Sizing:
   - Exact mathematical floor sizing: min(floor(38000 / price), floor(1500 / (price - stop))).
   - Strict integer sizing without float-boundary rounding up or premature price rounding.
3. Fail-Closed Default Invariants:
   - Missing, non-finite, or <= 0 ATR strictly sizes to 0 shares (fail-closed).
   - Absolute Rs 10.00 price floor (AGENTS.md Rule 2; unrounded evaluation).
   - Inverted stop-loss (stop >= entry) rejected immediately.
   - Unmapped sector rejected immediately (fail-closed; whitelisted authorized sectors only).
   - Maximum 2 positions per sector (pinned invariant, cannot be widened).
4. Reassessment on Actual Fill:
   - Actual fills from reservations re-assess exposure and risk against slot cap and risk ceiling.
   - Re-checks inviolable cash buffer at actual fill time.
   - Fails closed on NaN, inf, boolean, non-positive price/quantity.
   - Preserves residual reservation quantities on partial fills.
5. Ledger Integrity & Partial Fills:
   - Partial exit support preserves residual holdings, open risk, and occupied slot.
   - Duplicate exit events rejected fail-closed via event idempotency tracking.
   - Reconciles sale proceeds deducting only exit costs (no double deduction of entry costs).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from decimal import Decimal, ROUND_FLOOR
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple


TOTAL_CORPUS_RS = 250_000.00
CASH_BUFFER_RS = 136_000.00
AGGREGATE_EXPOSURE_CAP_RS = 114_000.00
MAX_SLOTS = 3
SLOT_CAP_RS = 38_000.00
RISK_PER_TRADE_RS = 1_500.00
AGGREGATE_RISK_CAP_RS = 4_500.00
MIN_PRICE_FLOOR_RS = 10.00
MAX_POSITIONS_PER_SECTOR = 2


DEFAULT_SECTOR_MAP: Dict[str, str] = {
    # Core Track 2 F&O Universe
    "CDSL": "CAPITAL_MARKETS_FINTECH",
    "ANGELONE": "CAPITAL_MARKETS_FINTECH",
    "POLICYBZR": "CAPITAL_MARKETS_FINTECH",
    "SUZLON": "GREEN_ENERGY_POWER",
    "INOXWIND": "GREEN_ENERGY_POWER",
    "IREDA": "PSU_RENEWABLE_FINANCE",
    "RVNL": "PSU_RAILWAYS_INFRA",
    "COCHINSHIP": "DEFENSE_SHIPBUILDING",
    "BDL": "DEFENSE_AEROSPACE",
    "TATACHEM": "CHEMICALS_SPECIALTY",
    "DIXON": "ELECTRONICS_EMS",
    "NATIONALUM": "METALS_MINING",
    "RELIANCE": "OIL_GAS_PETROCHEM",
    "TCS": "IT_SERVICES",
    "INFY": "IT_SERVICES",
}

# Whitelist of authorized NSE sectors for Track 2 (Codex Finding 5)
VALID_SECTORS: Set[str] = {
    "CAPITAL_MARKETS_FINTECH",
    "GREEN_ENERGY_POWER",
    "PSU_RENEWABLE_FINANCE",
    "PSU_RAILWAYS_INFRA",
    "DEFENSE_SHIPBUILDING",
    "DEFENSE_AEROSPACE",
    "CHEMICALS_SPECIALTY",
    "ELECTRONICS_EMS",
    "METALS_MINING",
    "OIL_GAS_PETROCHEM",
    "IT_SERVICES",
    "AUTOMOBILES",
    "PHARMACEUTICALS",
    "BANKING_PRIVATE",
    "BANKING_PSU",
    "CONSUMER_FMCG",
    "INFRASTRUCTURE",
    "POWER_ENERGY",
    "FINANCIAL_SERVICES",
    "COMMODITIES_MATERIALS",
    "ELECTRONICS_MANUFACTURING",
    "DEFENSE_MANUFACTURING",
}


def compute_position_size(
    entry_price: float,
    stop_price: float,
    atr: Optional[float] = None,
    slot_cap_rs: float = SLOT_CAP_RS,
    risk_budget_rs: float = RISK_PER_TRADE_RS,
) -> int:
    """
    Computes integer position sizing with exact mathematical floor (ROUND_FLOOR).
    Fails closed to 0 shares if:
      - ATR is missing, None, NaN, inf, boolean, or <= 0.
      - Entry price < Rs 10.00 (Rule 2 price floor; evaluated without premature rounding).
      - Stop price >= Entry price (inverted stop).
      - Prices or caps are non-positive or non-finite.
    Formula:
      shares = min(floor(slot_cap / entry_price), floor(risk_budget / (entry_price - stop_price)))
    """
    # Fail-closed on missing or invalid ATR
    if (
        atr is None
        or isinstance(atr, bool)
        or not isinstance(atr, numbers.Real)
        or not math.isfinite(float(atr))
        or float(atr) <= 0
    ):
        return 0

    # Validate entry price (Rule 2 Floor - evaluated raw, no premature rounding)
    if (
        isinstance(entry_price, bool)
        or not isinstance(entry_price, numbers.Real)
        or not math.isfinite(float(entry_price))
        or float(entry_price) < MIN_PRICE_FLOOR_RS
    ):
        return 0

    # Validate stop price
    if (
        isinstance(stop_price, bool)
        or not isinstance(stop_price, numbers.Real)
        or not math.isfinite(float(stop_price))
        or float(stop_price) <= 0
        or float(stop_price) >= float(entry_price)
    ):
        return 0

    # Validate caps
    if (
        isinstance(slot_cap_rs, bool)
        or not isinstance(slot_cap_rs, numbers.Real)
        or not math.isfinite(float(slot_cap_rs))
        or float(slot_cap_rs) <= 0
        or isinstance(risk_budget_rs, bool)
        or not isinstance(risk_budget_rs, numbers.Real)
        or not math.isfinite(float(risk_budget_rs))
        or float(risk_budget_rs) <= 0
    ):
        return 0

    # Use raw string representation for exact precision without pre-rounding (Codex Finding 6)
    entry_d = Decimal(str(entry_price))
    stop_d = Decimal(str(stop_price))
    diff_d = entry_d - stop_d
    if diff_d <= 0:
        return 0

    effective_slot_cap = min(Decimal(str(slot_cap_rs)), Decimal(str(SLOT_CAP_RS)))
    effective_risk_cap = min(Decimal(str(risk_budget_rs)), Decimal(str(RISK_PER_TRADE_RS)))

    slot_shares = int((effective_slot_cap / entry_d).to_integral_value(rounding=ROUND_FLOOR))
    risk_shares = int((effective_risk_cap / diff_d).to_integral_value(rounding=ROUND_FLOOR))

    return max(0, min(slot_shares, risk_shares))


@dataclass
class CandidateSignal:
    symbol: str
    entry_price: float
    stop_price: float
    atr: Optional[float] = None
    priority_score: float = 0.0
    sector: Optional[str] = None
    volume_z_score: float = 0.0


@dataclass(frozen=True)
class RiskAssessmentVerdict:
    is_approved: bool
    rejection_reason: Optional[str]
    symbol: str
    sector: str
    proposed_risk_rs: float
    proposed_notional_rs: float
    current_open_risk_rs: float
    new_total_risk_rs: float
    current_total_notional_rs: float
    new_total_notional_rs: float
    sector_position_count: int
    max_sector_allowed: int
    portfolio_risk_budget_rs: float
    max_portfolio_risk_rs: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AllocationResult:
    approved_signals: List[CandidateSignal]
    rejected_signals: List[Tuple[CandidateSignal, str]]


class PortfolioRiskGovernor:
    """
    Central Portfolio State Machine & Risk Authority for Track 2.
    Enforces Adjusted A1 limits (3 slots, Rs 38k slot cap, Rs 1.5k risk, Rs 114k exposure).
    """

    def __init__(
        self,
        corpus_rs: float = TOTAL_CORPUS_RS,
        cash_buffer_rs: float = CASH_BUFFER_RS,
        max_slots: int = MAX_SLOTS,
        slot_cap_rs: float = SLOT_CAP_RS,
        risk_per_trade_rs: float = RISK_PER_TRADE_RS,
        max_positions_per_sector: int = MAX_POSITIONS_PER_SECTOR,
        sector_mapping: Optional[Mapping[str, str]] = None,
    ):
        self.corpus_rs = float(corpus_rs)
        self.cash_rs = float(corpus_rs)
        self.cash_buffer_rs = float(cash_buffer_rs)
        self.max_slots = int(min(max_slots, MAX_SLOTS))
        self.slot_cap_rs = float(min(slot_cap_rs, SLOT_CAP_RS))
        self.risk_per_trade_rs = float(min(risk_per_trade_rs, RISK_PER_TRADE_RS))
        self.aggregate_risk_cap_rs = round(self.risk_per_trade_rs * self.max_slots, 2)
        self.aggregate_exposure_cap_rs = round(self.slot_cap_rs * self.max_slots, 2)
        # Codex Finding 5: max_positions_per_sector is pinned and cannot be widened beyond 2
        self.max_positions_per_sector = int(min(max_positions_per_sector, MAX_POSITIONS_PER_SECTOR))
        self.sector_mapping = dict(sector_mapping or DEFAULT_SECTOR_MAP)

        # Portfolio state ledgers
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.pending_reservations: Dict[str, Dict[str, Any]] = {}
        self.unresolved_exits: Set[str] = set()
        self.processed_exit_events: Set[str] = set()  # Event idempotency tracking (Codex Finding 2)

    @property
    def available_slots(self) -> int:
        occupied = len(self.active_positions) + len(self.pending_reservations)
        return max(0, self.max_slots - occupied)

    @property
    def current_exposure(self) -> float:
        active_notional = sum(p["notional_rs"] for p in self.active_positions.values())
        pending_notional = sum(p["notional_rs"] for p in self.pending_reservations.values())
        return round(active_notional + pending_notional, 2)

    @property
    def current_open_risk(self) -> float:
        active_risk = sum(p["open_risk_rs"] for p in self.active_positions.values())
        pending_risk = sum(p["open_risk_rs"] for p in self.pending_reservations.values())
        return round(active_risk + pending_risk, 2)

    def resolve_sector(self, symbol: str) -> str:
        sym = str(symbol).strip().upper()
        return self.sector_mapping.get(sym, "UNKNOWN_SECTOR")

    def assess_candidate(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        quantity: int,
        custom_sector: Optional[str] = None,
        var_elm_rate: Any = None,
        active_positions: Optional[Sequence[Mapping[str, Any]]] = None,
        pending_orders: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> RiskAssessmentVerdict:
        """
        Assesses whether proposed candidate trade passes all Adjusted A1 portfolio gates.
        """
        sym = str(symbol).strip().upper()
        if not sym:
            return self._rejected(sym, "INVALID_SYMBOL", "Symbol is empty or invalid.")

        # Price & Quantity validations (raw precision, no rounding up)
        for val in [entry_price, stop_price]:
            if (
                isinstance(val, bool)
                or not isinstance(val, numbers.Real)
                or not math.isfinite(float(val))
                or float(val) <= 0
            ):
                return self._rejected(sym, "INVALID_PRICE", f"Entry or stop price invalid: {val}")

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, numbers.Integral)
            or int(quantity) <= 0
        ):
            return self._rejected(sym, "INVALID_QUANTITY", f"Quantity must be positive integer: {quantity}")

        # Codex Finding 6: Evaluate price floor strictly on exact value without rounding
        if float(entry_price) < MIN_PRICE_FLOOR_RS:
            return self._rejected(
                sym,
                "RULE_2_PRICE_FLOOR_VIOLATION",
                f"Entry price Rs {entry_price} is below absolute Rs 10.00 floor.",
            )

        entry_p = float(entry_price)
        stop_p = float(stop_price)
        qty_i = int(quantity)

        if stop_p >= entry_p:
            return self._rejected(
                sym,
                "INVERTED_STOP",
                f"Stop price ({stop_p}) must be strictly below entry price ({entry_p}).",
            )

        # Sector resolution & Whitelist validation (Codex Finding 5)
        if custom_sector:
            cand_sec = str(custom_sector).strip().upper()
            if cand_sec not in VALID_SECTORS:
                return self._rejected(
                    sym,
                    "UNMAPPED_SECTOR",
                    f"Custom sector '{cand_sec}' is not in authorized sector whitelist. Fail-closed.",
                )
            sector = cand_sec
        else:
            sector = self.resolve_sector(sym)

        if sector == "UNKNOWN_SECTOR" or sector not in VALID_SECTORS:
            return self._rejected(
                sym,
                "UNMAPPED_SECTOR",
                f"Symbol '{sym}' does not have a verified authorized sector mapping. Fail-closed.",
            )

        # Proposed Metrics
        risk_per_share = round(entry_p - stop_p, 4)
        proposed_risk = round(qty_i * risk_per_share, 2)
        proposed_notional = round(qty_i * entry_p, 2)

        # 1. Single Trade Risk Ceiling (Rs 1,500)
        if proposed_risk > self.risk_per_trade_rs + 1e-4:
            return self._rejected(
                sym,
                "SINGLE_TRADE_RISK_EXCEEDED",
                f"Proposed risk Rs {proposed_risk:.2f} exceeds single trade ceiling Rs {self.risk_per_trade_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
            )

        # 2. Single Slot Notional Cap Check (Rs 38,000)
        if proposed_notional > self.slot_cap_rs + 1e-4:
            return self._rejected(
                sym,
                "SLOT_CAP_EXCEEDED",
                f"Proposed notional Rs {proposed_notional:.2f} exceeds slot cap Rs {self.slot_cap_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
            )

        # Gather active & pending items (use internal ledger if external not passed)
        if active_positions is not None:
            active_list = list(active_positions)
        else:
            active_list = list(self.active_positions.values())

        if pending_orders is not None:
            pending_list = list(pending_orders)
        else:
            pending_list = list(self.pending_reservations.values())

        all_items = active_list + pending_list

        # Duplicate symbol check
        active_syms = {str(item.get("symbol", "")).strip().upper() for item in all_items}
        if sym in active_syms:
            return self._rejected(
                sym,
                "DUPLICATE_SYMBOL_POSITION",
                f"Active position or reservation already exists for '{sym}'. Duplicate prohibited.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
            )

        # Sector concentration check
        sector_counts: Dict[str, int] = {}
        for item in all_items:
            s_name = item.get("sector") or self.resolve_sector(item.get("symbol", ""))
            sector_counts[s_name] = sector_counts.get(s_name, 0) + 1

        existing_sec_count = sector_counts.get(sector, 0)
        if existing_sec_count >= self.max_positions_per_sector:
            return self._rejected(
                sym,
                "SECTOR_CONCENTRATION_EXCEEDED",
                f"Sector '{sector}' already has {existing_sec_count} positions (max allowed: {self.max_positions_per_sector}).",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
                sector_count=existing_sec_count,
            )

        # Slot limit check (max 3 concurrent)
        if len(all_items) >= self.max_slots:
            return self._rejected(
                sym,
                "MAX_CONCURRENT_POSITIONS_REACHED",
                f"Portfolio already has {len(all_items)} active/pending slots (max allowed: {self.max_slots}).",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
                sector_count=existing_sec_count,
            )

        # Current total open risk & notional calculation
        cur_open_risk = 0.0
        cur_notional = 0.0
        for item in all_items:
            cur_open_risk += float(item.get("open_risk_rs", 0.0))
            cur_notional += float(item.get("notional_rs", 0.0))

        cur_open_risk = round(cur_open_risk, 2)
        cur_notional = round(cur_notional, 2)

        # Aggregate open risk ceiling check (Rs 4,500)
        new_total_risk = round(cur_open_risk + proposed_risk, 2)
        if new_total_risk > self.aggregate_risk_cap_rs + 1e-4:
            return self._rejected(
                sym,
                "AGGREGATE_PORTFOLIO_RISK_EXCEEDED",
                f"New total open risk Rs {new_total_risk:.2f} exceeds ceiling Rs {self.aggregate_risk_cap_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
                current_open_risk=cur_open_risk,
                new_total_risk=new_total_risk,
                sector_count=existing_sec_count,
            )

        # Aggregate exposure ceiling check (Rs 114,000)
        new_total_notional = round(cur_notional + proposed_notional, 2)
        if new_total_notional > self.aggregate_exposure_cap_rs + 1e-4:
            return self._rejected(
                sym,
                "TOTAL_CAPITAL_EXCEEDED",
                f"New total notional Rs {new_total_notional:.2f} exceeds exposure ceiling Rs {self.aggregate_exposure_cap_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
                current_notional=cur_notional,
                new_total_notional=new_total_notional,
                sector_count=existing_sec_count,
            )

        # Strict Inviolable Cash Buffer Gate (Codex Finding 4)
        # Account for cash, outstanding reservations, proposed notional, and estimated friction
        estimated_friction = round(proposed_notional * 0.0015, 2)  # ~0.15% statutory friction
        pending_reservations_notional = sum(p["notional_rs"] for p in self.pending_reservations.values())
        projected_cash_remaining = round(
            self.cash_rs - pending_reservations_notional - proposed_notional - estimated_friction, 2
        )
        if projected_cash_remaining < self.cash_buffer_rs:
            return self._rejected(
                sym,
                "INSUFFICIENT_UNENCUMBERED_CASH",
                f"Projected remaining cash Rs {projected_cash_remaining:.2f} breaches inviolable cash buffer Rs {self.cash_buffer_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
                current_notional=cur_notional,
                new_total_notional=new_total_notional,
                sector_count=existing_sec_count,
            )

        # Approved
        return RiskAssessmentVerdict(
            is_approved=True,
            rejection_reason=None,
            symbol=sym,
            sector=sector,
            proposed_risk_rs=proposed_risk,
            proposed_notional_rs=proposed_notional,
            current_open_risk_rs=cur_open_risk,
            new_total_risk_rs=new_total_risk,
            current_total_notional_rs=cur_notional,
            new_total_notional_rs=new_total_notional,
            sector_position_count=existing_sec_count + 1,
            max_sector_allowed=self.max_positions_per_sector,
            portfolio_risk_budget_rs=self.risk_per_trade_rs,
            max_portfolio_risk_rs=self.aggregate_risk_cap_rs,
        )

    def reserve_slot(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        sector: Optional[str] = None,
    ) -> None:
        sym = str(symbol).strip().upper()
        sec = sector or self.resolve_sector(sym)
        notional = round(quantity * entry_price, 2)
        risk = round(quantity * (entry_price - stop_price), 2)
        self.pending_reservations[sym] = {
            "symbol": sym,
            "quantity": int(quantity),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "notional_rs": notional,
            "open_risk_rs": risk,
            "sector": sec,
        }

    def confirm_fill(
        self,
        symbol: str,
        shares: int,
        entry_price: float,
        stop_price: float,
        sector: Optional[str] = None,
        transaction_costs: float = 0.0,
    ) -> None:
        sym = str(symbol).strip().upper()
        sec = sector or self.resolve_sector(sym)
        notional = round(shares * entry_price, 2)
        risk = round(shares * (entry_price - stop_price), 2)
        if sym in self.active_positions:
            # Add to existing position if partial fill aggregation (Codex Round 3 Finding 1)
            existing = self.active_positions[sym]
            new_shares = existing["shares"] + int(shares)
            new_notional = round(existing["notional_rs"] + notional, 2)
            weighted_entry_price = round(new_notional / new_shares, 4)
            existing["shares"] = new_shares
            existing["entry_price"] = weighted_entry_price
            existing["notional_rs"] = new_notional
            existing["open_risk_rs"] = round(new_shares * (weighted_entry_price - existing["stop_price"]), 2)
            existing["entry_costs"] = round(existing.get("entry_costs", 0.0) + float(transaction_costs), 2)
        else:
            self.active_positions[sym] = {
                "symbol": sym,
                "shares": int(shares),
                "entry_price": float(entry_price),
                "stop_price": float(stop_price),
                "notional_rs": notional,
                "open_risk_rs": risk,
                "sector": sec,
                "entry_costs": round(float(transaction_costs), 2),
            }
        self.cash_rs = round(self.cash_rs - notional - transaction_costs, 2)

    def confirm_fill_from_reservation(
        self,
        symbol: str,
        actual_fill_price: Optional[float] = None,
        filled_quantity: Optional[int] = None,
        transaction_costs: float = 0.0,
    ) -> None:
        """
        Converts a pending reservation to an active position (Codex Findings 3 & 4, Round 3):
        - Fails closed on NaN, inf, boolean, or non-positive values.
        - Re-assesses cumulative fill exposure and risk before converting.
        - Verifies that actual outlay does not breach the inviolable Rs 136,000 cash buffer.
        - Preserves residual reservation quantities on partial fills.
        """
        sym = str(symbol).strip().upper()
        if sym not in self.pending_reservations:
            raise KeyError(f"No pending reservation found for '{sym}'")

        # Strict fail-closed input validation (Codex Round 2 Finding 3)
        if actual_fill_price is not None:
            if (
                isinstance(actual_fill_price, bool)
                or not isinstance(actual_fill_price, numbers.Real)
                or not math.isfinite(float(actual_fill_price))
                or float(actual_fill_price) <= 0
            ):
                raise ValueError("FAIL-CLOSED: Actual fill price must be a finite positive number.")

        if filled_quantity is not None:
            if (
                isinstance(filled_quantity, bool)
                or not isinstance(filled_quantity, numbers.Integral)
                or int(filled_quantity) <= 0
            ):
                raise ValueError("FAIL-CLOSED: Filled quantity must be a positive integer.")

        if (
            isinstance(transaction_costs, bool)
            or not isinstance(transaction_costs, numbers.Real)
            or not math.isfinite(float(transaction_costs))
            or float(transaction_costs) < 0
        ):
            raise ValueError("FAIL-CLOSED: Transaction costs must be a finite non-negative number.")

        res = self.pending_reservations[sym]
        fill_price = float(actual_fill_price) if actual_fill_price is not None else res["entry_price"]
        shares = int(filled_quantity) if filled_quantity is not None else res["quantity"]
        stop_p = res["stop_price"]
        sec = res["sector"]

        if shares > res["quantity"]:
            raise ValueError(f"FAIL-CLOSED: Cannot fill {shares} shares exceeding reserved quantity {res['quantity']}.")

        # Re-assess actual fill notional and risk
        actual_notional = round(shares * fill_price, 2)
        actual_risk = round(shares * (fill_price - stop_p), 2)

        # Cumulative position ceiling checks (Codex Round 3 Finding 1)
        existing_pos = self.active_positions.get(sym)
        if existing_pos:
            comb_shares = existing_pos["shares"] + shares
            comb_notional = round(existing_pos["notional_rs"] + actual_notional, 2)
            comb_risk = round(existing_pos["open_risk_rs"] + actual_risk, 2)
        else:
            comb_shares = shares
            comb_notional = actual_notional
            comb_risk = actual_risk

        if comb_notional > self.slot_cap_rs + 1e-4 or comb_risk > self.risk_per_trade_rs + 1e-4:
            raise ValueError(
                f"EXPOSURE_OR_RISK_BREACH: Cumulative position notional Rs {comb_notional:.2f} (cap: {self.slot_cap_rs:.2f}) "
                f"or risk Rs {comb_risk:.2f} (budget: {self.risk_per_trade_rs:.2f}) breaches limits."
            )

        # Aggregate portfolio ceiling checks
        other_exposure = sum(p["notional_rs"] for s, p in self.active_positions.items() if s != sym)
        other_risk = sum(p["open_risk_rs"] for s, p in self.active_positions.items() if s != sym)
        agg_exposure = round(other_exposure + comb_notional, 2)
        agg_risk = round(other_risk + comb_risk, 2)
        if agg_exposure > self.aggregate_exposure_cap_rs + 1e-4 or agg_risk > self.aggregate_risk_cap_rs + 1e-4:
            raise ValueError(
                f"AGGREGATE_EXPOSURE_OR_RISK_BREACH: Aggregate exposure Rs {agg_exposure:.2f} (cap: {self.aggregate_exposure_cap_rs:.2f}) "
                f"or risk Rs {agg_risk:.2f} (cap: {self.aggregate_risk_cap_rs:.2f}) breaches limits."
            )

        # Enforce Cash Buffer at Actual Fill Time (Codex Round 2 Finding 4)
        remaining_cash_after_fill = round(self.cash_rs - actual_notional - float(transaction_costs), 2)
        if remaining_cash_after_fill < self.cash_buffer_rs:
            raise ValueError(
                f"CASH_BUFFER_BREACH_AT_FILL: Actual fill outlay (Rs {actual_notional:.2f} + {transaction_costs:.2f}) "
                f"leaves cash Rs {remaining_cash_after_fill:.2f}, breaching inviolable buffer Rs {self.cash_buffer_rs:.2f}."
            )

        # Preserve residual reservation on partial fill (Codex Round 2 Finding 3)
        remaining_reserved_qty = res["quantity"] - shares
        if remaining_reserved_qty > 0:
            res["quantity"] = remaining_reserved_qty
            res["notional_rs"] = round(remaining_reserved_qty * res["entry_price"], 2)
            res["open_risk_rs"] = round(remaining_reserved_qty * (res["entry_price"] - res["stop_price"]), 2)
        else:
            self.pending_reservations.pop(sym)

        self.confirm_fill(
            symbol=sym,
            shares=shares,
            entry_price=fill_price,
            stop_price=stop_p,
            sector=sec,
            transaction_costs=transaction_costs,
        )

    def record_circuit_locked_exit(self, symbol: str) -> None:
        sym = str(symbol).strip().upper()
        if sym in self.active_positions:
            self.unresolved_exits.add(sym)

    def reconcile_exit(
        self,
        symbol: str,
        exit_price: float,
        shares: int,
        transaction_costs: float = 0.0,
        exit_transaction_costs: Optional[float] = None,
        exit_event_id: Optional[str] = None,
    ) -> None:
        """
        Reconciles exit order execution with ledger accounting (Codex Round 3 Findings 2 & 3):
        - Enforces MANDATORY event idempotency via exit_event_id to prevent duplicate credits.
        - Validates existence of active position and share quantities BEFORE recording event ID (atomic).
        - Prorates remaining entry-cost basis on partial exits.
        - Deducts sold shares and updates remaining notional, risk, and occupied slot.
        - Removes position and clears unresolved marker only when completely closed.
        - Deducts ONLY exit transaction friction from sale proceeds (no double deduction of entry costs).
        """
        # Event idempotency: exit_event_id is strictly MANDATORY (Codex Round 3 Finding 2)
        if (
            exit_event_id is None
            or not isinstance(exit_event_id, str)
            or not exit_event_id.strip()
        ):
            raise ValueError("FAIL-CLOSED: exit_event_id is required for exit reconciliation.")

        evt_id = exit_event_id.strip()
        if evt_id in self.processed_exit_events:
            raise ValueError(f"DUPLICATE_EXIT_EVENT: Exit event '{evt_id}' has already been processed.")

        sym = str(symbol).strip().upper()
        if sym not in self.active_positions:
            raise KeyError(f"No active position found for '{sym}'. Duplicate or invalid exit prohibited.")

        pos = self.active_positions[sym]
        open_shares = pos["shares"]

        if (
            isinstance(shares, bool)
            or not isinstance(shares, numbers.Integral)
            or int(shares) <= 0
        ):
            raise ValueError(f"Invalid shares quantity for exit: {shares}")

        shares_to_sell = int(shares)
        if shares_to_sell > open_shares:
            raise ValueError(f"Cannot sell {shares_to_sell} shares: only {open_shares} shares currently open.")

        if (
            isinstance(exit_price, bool)
            or not isinstance(exit_price, numbers.Real)
            or not math.isfinite(float(exit_price))
            or float(exit_price) <= 0
        ):
            raise ValueError("FAIL-CLOSED: Exit price must be a finite positive number.")

        actual_exit_costs = exit_transaction_costs if exit_transaction_costs is not None else transaction_costs
        if (
            isinstance(actual_exit_costs, bool)
            or not isinstance(actual_exit_costs, numbers.Real)
            or not math.isfinite(float(actual_exit_costs))
            or float(actual_exit_costs) < 0
        ):
            raise ValueError("FAIL-CLOSED: Exit transaction costs must be a finite non-negative number.")

        # ATOMIC: Record event ID ONLY after all validations pass (Codex Round 3 Finding 2)
        self.processed_exit_events.add(evt_id)

        remaining_shares = open_shares - shares_to_sell
        if remaining_shares > 0:
            # Partial exit: update residual holdings, open risk, notional, and prorate entry costs (Codex Round 3 Finding 3)
            pos["shares"] = remaining_shares
            pos["notional_rs"] = round(remaining_shares * pos["entry_price"], 2)
            pos["open_risk_rs"] = round(remaining_shares * (pos["entry_price"] - pos["stop_price"]), 2)
            if "entry_costs" in pos and open_shares > 0:
                prop_entry = round(pos["entry_costs"] * (shares_to_sell / open_shares), 2)
                pos["entry_costs"] = round(max(0.0, pos["entry_costs"] - prop_entry), 2)
        else:
            # Complete exit: remove position from active ledger
            self.active_positions.pop(sym)
            if sym in self.unresolved_exits:
                self.unresolved_exits.remove(sym)

        # Deduct ONLY exit costs from turnover to avoid double-deducting entry friction (Codex Finding 8)
        proceeds = round((shares_to_sell * float(exit_price)) - float(actual_exit_costs), 2)
        self.cash_rs = round(self.cash_rs + proceeds, 2)

    def rank_and_allocate_signals(
        self,
        candidate_signals: List[CandidateSignal],
    ) -> AllocationResult:
        """
        Simultaneous Signal Priority & Deterministic Allocation:
        Sorts candidates deterministically by priority_score descending,
        tie-breaking by symbol ascending. Allocates available slots in exact order.
        """
        sorted_candidates = sorted(
            candidate_signals,
            key=lambda c: (-c.priority_score, c.symbol),
        )

        approved: List[CandidateSignal] = []
        rejected: List[Tuple[CandidateSignal, str]] = []

        for cand in sorted_candidates:
            if self.available_slots <= 0:
                rejected.append((cand, "MAX_CONCURRENT_POSITIONS_REACHED: No available slots"))
                continue

            shares = compute_position_size(cand.entry_price, cand.stop_price, cand.atr)
            if shares <= 0:
                rejected.append((cand, "INVALID_SIZE_FAIL_CLOSED: Sizing computed 0 shares"))
                continue

            verdict = self.assess_candidate(
                symbol=cand.symbol,
                entry_price=cand.entry_price,
                stop_price=cand.stop_price,
                quantity=shares,
                custom_sector=cand.sector,
            )

            if verdict.is_approved:
                self.reserve_slot(
                    symbol=cand.symbol,
                    quantity=shares,
                    entry_price=cand.entry_price,
                    stop_price=cand.stop_price,
                    sector=cand.sector,
                )
                approved.append(cand)
            else:
                rejected.append((cand, verdict.rejection_reason or "REJECTED"))

        return AllocationResult(approved_signals=approved, rejected_signals=rejected)

    def _rejected(
        self,
        symbol: str,
        code: str,
        reason: str,
        sector: str = "UNKNOWN",
        proposed_risk: float = 0.0,
        proposed_notional: float = 0.0,
        current_open_risk: float = 0.0,
        new_total_risk: float = 0.0,
        current_notional: float = 0.0,
        new_total_notional: float = 0.0,
        sector_count: int = 0,
    ) -> RiskAssessmentVerdict:
        return RiskAssessmentVerdict(
            is_approved=False,
            rejection_reason=f"{code}: {reason}",
            symbol=symbol,
            sector=sector,
            proposed_risk_rs=proposed_risk,
            proposed_notional_rs=proposed_notional,
            current_open_risk_rs=current_open_risk,
            new_total_risk_rs=new_total_risk,
            current_total_notional_rs=current_notional,
            new_total_notional_rs=new_total_notional,
            sector_position_count=sector_count,
            max_sector_allowed=self.max_positions_per_sector,
            portfolio_risk_budget_rs=self.risk_per_trade_rs,
            max_portfolio_risk_rs=self.aggregate_risk_cap_rs,
        )


# Canonical BASTION Alias
BastionRiskGovernor = PortfolioRiskGovernor
