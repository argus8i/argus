"""
track2_portfolio_risk_governor.py - Portfolio Risk Governor & Sector Concentration Engine for Track 2
=====================================================================================================
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Mandate & Mathematical Guardrails:
  1. Strict AGENTS.md Rule 1 & Rule 11 Compliance:
     - 100% Cash; Paper-Trading Observation Mode only. Real capital deployment is strictly prohibited.
     - Operates exclusively on liquid F&O underlyings in Cash EQ.
  2. Single-Trade Rupee Risk Budget:
     - Fixed at Rs 1,500 per trade (calibrated to 1R).
  3. Aggregate Portfolio Open Risk Cap:
     - Fixed at Rs 6,000 maximum total open risk (4 concurrent full-risk trades).
     - Accounts for active positions + pending orders + newly proposed trade.
  4. Sector Concentration Limit:
     - Maximum 2 concurrent positions in any single sector to prevent correlated portfolio drawdowns.
  5. Total Capital & Notional Ceiling:
     - Bounded by Rs 1,00,000 total allocated cash capital; zero leverage/overdraft reliance.
  6. Fail-Closed Validation:
     - Any NaN, inf, negative risk, or missing sector data immediately rejects the trade.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import numbers
from antigravity.models.track2_a1 import SLOT_CAP_RS, AGGREGATE_EXPOSURE_CAP_RS, AGGREGATE_RISK_CAP_RS, RISK_PER_TRADE_RS, MAX_SLOTS
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


DEFAULT_SECTOR_MAP: Dict[str, str] = {
    # Basket A (Strict Core Universe)
    "CDSL": "CAPITAL_MARKETS_FINTECH",
    "ANGELONE": "CAPITAL_MARKETS_FINTECH",
    "SUZLON": "GREEN_ENERGY_POWER",
    "INOXWIND": "GREEN_ENERGY_POWER",

    # Basket B (Sovereign PSU Satellite)
    "IREDA": "PSU_RENEWABLE_FINANCE",
    "RVNL": "PSU_RAILWAYS_INFRA",
    "COCHINSHIP": "DEFENSE_SHIPBUILDING",
    "BDL": "DEFENSE_AEROSPACE",

    # Common Liquid F&O Underlyings
    "TATACHEM": "CHEMICALS_SPECIALTY",
    "POLICYBZR": "CAPITAL_MARKETS_FINTECH",
    "DIXON": "ELECTRONICS_EMS",
    "NATIONALUM": "METALS_MINING",
}

# Standard Sector Clustering / Coarse Groups for strict correlation defense
COARSE_SECTOR_GROUPS: Dict[str, str] = {
    "CAPITAL_MARKETS_FINTECH": "FINANCIAL_SERVICES",
    "GREEN_ENERGY_POWER": "POWER_ENERGY",
    "PSU_RENEWABLE_FINANCE": "FINANCIAL_SERVICES",
    "PSU_RAILWAYS_INFRA": "INFRASTRUCTURE_CAPITAL_GOODS",
    "DEFENSE_SHIPBUILDING": "DEFENSE_MANUFACTURING",
    "DEFENSE_AEROSPACE": "DEFENSE_MANUFACTURING",
    "CHEMICALS_SPECIALTY": "COMMODITIES_MATERIALS",
    "ELECTRONICS_EMS": "ELECTRONICS_MANUFACTURING",
    "METALS_MINING": "COMMODITIES_MATERIALS",
}


@dataclass(frozen=True)
class RiskAssessmentResult:
    is_approved: bool
    rejection_reason: Optional[str]
    symbol: str
    sector: str
    proposed_risk_rs: float
    current_open_risk_rs: float
    new_total_risk_rs: float
    proposed_notional_rs: float
    current_total_notional_rs: float
    new_total_notional_rs: float
    sector_position_count: int
    max_sector_allowed: int
    portfolio_risk_budget_rs: float
    max_portfolio_risk_rs: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def reason(self) -> Optional[str]:
        """Backwards compatibility alias for rejection_reason."""
        return self.rejection_reason


class PortfolioRiskGovernor:
    """
    Evaluates portfolio-level risk invariants, sector concentration limits,
    and rupee risk budgets prior to order generation or paper execution.
    """

    def __init__(
        self,
        max_single_trade_risk_rs: float = RISK_PER_TRADE_RS,
        max_aggregate_risk_rs: float = AGGREGATE_RISK_CAP_RS,
        max_positions_per_sector: int = 2,
        total_capital_allocation_rs: float = AGGREGATE_EXPOSURE_CAP_RS,
        sector_mapping: Optional[Mapping[str, str]] = None,
        max_concurrent_positions: Optional[int] = None,
        enforce_var_elm_gate: bool = True,
        max_single_slot_notional_rs: Optional[float] = SLOT_CAP_RS,
    ):
        for val in [max_single_trade_risk_rs, max_aggregate_risk_rs, total_capital_allocation_rs]:
            if (
                isinstance(val, bool)
                or not isinstance(val, numbers.Real)
                or not math.isfinite(float(val))
                or float(val) <= 0
            ):
                raise ValueError("FAIL-CLOSED: Risk and capital parameters must be finite positive numbers.")

        if (
            isinstance(max_positions_per_sector, bool)
            or not isinstance(max_positions_per_sector, numbers.Integral)
            or int(max_positions_per_sector) <= 0
        ):
            raise ValueError("FAIL-CLOSED: Max positions per sector must be positive integer.")

        self.max_single_trade_risk_rs = round(float(max_single_trade_risk_rs), 2)
        if max_single_slot_notional_rs is not None and (isinstance(max_single_slot_notional_rs, bool) or not isinstance(max_single_slot_notional_rs, numbers.Real) or not math.isfinite(max_single_slot_notional_rs) or max_single_slot_notional_rs <= 0):
            raise ValueError("A1_CONFIG_MISMATCH: slot cap must be finite and positive")
        if max_concurrent_positions is not None and (isinstance(max_concurrent_positions, bool) or not isinstance(max_concurrent_positions, numbers.Integral) or max_concurrent_positions <= 0):
            raise ValueError("A1_CONFIG_MISMATCH: slots must be positive integer")
        self.max_aggregate_risk_rs = round(float(max_aggregate_risk_rs), 2)
        self.max_positions_per_sector = int(max_positions_per_sector)
        self.total_capital_allocation_rs = round(float(total_capital_allocation_rs), 2)
        self.sector_mapping = dict(sector_mapping or DEFAULT_SECTOR_MAP)
        self.max_concurrent_positions = int(max_concurrent_positions) if max_concurrent_positions is not None else 3
        self.enforce_var_elm_gate = True
        self.max_single_slot_notional_rs = (
            round(float(max_single_slot_notional_rs), 2)
            if max_single_slot_notional_rs is not None
            else SLOT_CAP_RS
        )
        # Lower ceilings are allowed for diagnostic sub-books; widening is not.
        self.max_single_trade_risk_rs = min(self.max_single_trade_risk_rs, RISK_PER_TRADE_RS)
        self.max_aggregate_risk_rs = min(self.max_aggregate_risk_rs, AGGREGATE_RISK_CAP_RS)
        self.total_capital_allocation_rs = min(self.total_capital_allocation_rs, AGGREGATE_EXPOSURE_CAP_RS)
        self.max_single_slot_notional_rs = min(self.max_single_slot_notional_rs, SLOT_CAP_RS)
        self.max_concurrent_positions = min(self.max_concurrent_positions, MAX_SLOTS)
        if self.max_single_slot_notional_rs <= 0 or self.max_concurrent_positions <= 0:
            raise ValueError("A1_CONFIG_MISMATCH: invalid cap")

    @classmethod
    def calibrate_for_corpus(
        cls,
        corpus_rs: float = 250000.0,
        risk_per_trade_rs: float = 1500.0,
        max_concurrent_positions: int = 3,
        cash_buffer_rs: float = 136000.0,
        max_positions_per_sector: int = 2,
        sector_mapping: Optional[Mapping[str, str]] = None,
        enforce_slot_cap: bool = True,
    ) -> "PortfolioRiskGovernor":
        """
        Calibrates the PortfolioRiskGovernor specifically for a retail/prop corpus (e.g. Rs 2L - 3L).
        
        Hardened calibration for Rs 2,50,000 Adjusted A1 (Yashu Mandate):
          - Single-trade risk: Rs 1,500 (1R)
          - Max concurrent positions: 3
          - Aggregate open risk cap: 3 * Rs 1,500 = Rs 4,500
          - Unencumbered Cash Buffer: Rs 1,36,000
          - Max active deployable notional: Rs 1,14,000 (Rs 38,000.00 per slot)
          - Max positions per sector: 2
        """
        if corpus_rs <= 0 or cash_buffer_rs < 0 or corpus_rs <= cash_buffer_rs:
            raise ValueError("FAIL-CLOSED: Invalid corpus or cash buffer configuration.")
        
        deployable_capital = round(corpus_rs - cash_buffer_rs, 2)
        aggregate_risk_cap = round(risk_per_trade_rs * max_concurrent_positions, 2)
        slot_cap = round(deployable_capital / max_concurrent_positions, 2) if enforce_slot_cap else None

        return cls(
            max_single_trade_risk_rs=risk_per_trade_rs,
            max_aggregate_risk_rs=aggregate_risk_cap,
            max_positions_per_sector=max_positions_per_sector,
            total_capital_allocation_rs=deployable_capital,
            sector_mapping=sector_mapping,
            max_concurrent_positions=max_concurrent_positions,
            enforce_var_elm_gate=True,
            max_single_slot_notional_rs=slot_cap,
        )

    def resolve_sector(self, symbol: str) -> str:
        """Resolves symbol to its sector cluster, failing closed if completely unmapped."""
        sym = str(symbol).strip().upper()
        if sym in self.sector_mapping:
            return self.sector_mapping[sym]
        return "UNKNOWN_SECTOR"

    def assess_candidate(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        quantity: int,
        active_positions: Sequence[Mapping[str, Any]],
        pending_orders: Optional[Sequence[Mapping[str, Any]]] = None,
        custom_sector: Optional[str] = None,
        var_elm_rate: Any = None,
        worst_entry_price: Optional[float] = None,
    ) -> RiskAssessmentResult:
        """
        Assesses whether proposed candidate trade passes all portfolio risk gates:
        1. Single-trade risk <= Rs 1,500.
        2. Aggregate portfolio open risk (active + pending + proposed) <= Rs 4,500.
        3. Sector position count (active + pending + proposed) <= 2.
        4. Total portfolio notional <= Rs 1,75,000.
        5. Scrip VAR+ELM rate <= 30.0% (SEBI margin shortfall immunity).
        """
        sym = str(symbol).strip().upper()
        if not sym:
            return self._rejected(sym, "INVALID_SYMBOL", "Symbol is empty or invalid.")

        # Input validity checks
        if worst_entry_price is not None:
            entry_price = worst_entry_price
        for val in [entry_price, stop_price]:
            if (
                isinstance(val, bool)
                or not isinstance(val, numbers.Real)
                or not math.isfinite(float(val))
                or float(val) <= 0
            ):
                return self._rejected(sym, "INVALID_PRICE", f"Entry or stop price is invalid: {val}")

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, numbers.Integral)
            or int(quantity) <= 0
        ):
            return self._rejected(sym, "INVALID_QUANTITY", f"Quantity must be positive integer: {quantity}")

        entry_price = round(float(entry_price), 2)
        stop_price = round(float(stop_price), 2)
        quantity = int(quantity)

        if stop_price >= entry_price:
            return self._rejected(
                sym,
                "INVERTED_STOP",
                f"Stop price ({stop_price}) must be strictly below entry price ({entry_price}).",
            )

        # Sector resolution
        sector = custom_sector or self.resolve_sector(sym)
        if sector == "UNKNOWN_SECTOR":
            return self._rejected(
                sym,
                "UNMAPPED_SECTOR",
                f"Symbol '{sym}' does not have a verified sector mapping. Fail-closed.",
            )

        # Proposed Trade Metrics
        risk_per_share = round(entry_price - stop_price, 3)
        proposed_risk = round(quantity * risk_per_share, 2)
        proposed_notional = round(quantity * entry_price, 2)

        # 0. Check existing active & pending exposures first
        active_items = list(active_positions) + list(pending_orders or [])
        current_open_risk = 0.0
        current_total_notional = 0.0
        sector_counts: Dict[str, int] = {}

        for item in active_items:
            item_sym = str(item.get("symbol", "")).strip().upper()
            if not item_sym:
                continue

            item_sec = item.get("sector") or self.resolve_sector(item_sym)
            sector_counts[item_sec] = sector_counts.get(item_sec, 0) + 1

            # Extract or derive risk
            item_risk = item.get("open_risk_rs")
            if item_risk is None:
                i_entry = item.get("limit_price") or item.get("entry_price")
                i_stop = item.get("stop_price") or item.get("initial_stop")
                i_qty = item.get("quantity") or item.get("shares")
                if (
                    isinstance(i_entry, (int, float))
                    and isinstance(i_stop, (int, float))
                    and isinstance(i_qty, int)
                    and i_entry > i_stop > 0
                ):
                    item_risk = round(i_qty * (i_entry - i_stop), 2)
                else:
                    return self._rejected(sym, "CORRUPTED_PORTFOLIO_EXPOSURE", "Existing risk cannot be derived")
            if (
                isinstance(item_risk, bool)
                or not isinstance(item_risk, (int, float))
                or not math.isfinite(float(item_risk))
                or float(item_risk) < 0
            ):
                return self._rejected(
                    sym,
                    "CORRUPTED_PORTFOLIO_EXPOSURE",
                    f"Existing position '{item_sym}' carries invalid/non-finite open risk ({item_risk}). Assessment aborted fail-closed.",
                    sector=sector,
                    proposed_risk=proposed_risk,
                )

            item_notional = item.get("notional_rs")
            if item_notional is None:
                i_price = item.get("limit_price") or item.get("entry_price") or item.get("ltp")
                i_qty = item.get("quantity") or item.get("shares")
                if isinstance(i_price, (int, float)) and isinstance(i_qty, int) and i_price > 0 and i_qty > 0:
                    item_notional = round(i_price * i_qty, 2)
                else:
                    return self._rejected(sym, "CORRUPTED_PORTFOLIO_EXPOSURE", "Existing notional cannot be derived")

            # Prioritize worst-case valuation for pending orders with limit_price
            i_limit = item.get("limit_price")
            i_qty = item.get("quantity") or item.get("shares")
            if isinstance(i_limit, (int, float)) and isinstance(i_qty, int) and i_limit > 0 and i_qty > 0:
                worst_case_notional = round(float(i_limit) * int(i_qty), 2)
                item_notional = max(float(item_notional), worst_case_notional)
                i_stop = item.get("stop_price") or item.get("initial_stop")
                if isinstance(i_stop, (int, float)) and i_limit > i_stop > 0:
                    worst_case_risk = round(int(i_qty) * (float(i_limit) - float(i_stop)), 2)
                    item_risk = max(float(item_risk), worst_case_risk)

            if (
                isinstance(item_notional, bool)
                or not isinstance(item_notional, (int, float))
                or not math.isfinite(float(item_notional))
                or float(item_notional) < 0
            ):
                return self._rejected(
                    sym,
                    "CORRUPTED_PORTFOLIO_EXPOSURE",
                    f"Existing position '{item_sym}' carries invalid/non-finite notional ({item_notional}). Assessment aborted fail-closed.",
                    sector=sector,
                    proposed_notional=proposed_notional,
                )

            current_total_notional += float(item_notional)
            current_open_risk += float(item_risk)

        current_open_risk = round(current_open_risk, 2)
        current_total_notional = round(current_total_notional, 2)

        # 1. Single Trade Risk Ceiling
        if proposed_risk > self.max_single_trade_risk_rs + 1e-4:
            return self._rejected(
                sym,
                "SINGLE_TRADE_RISK_EXCEEDED",
                f"Proposed risk Rs {proposed_risk:.2f} exceeds single trade ceiling Rs {self.max_single_trade_risk_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
            )

        # 1c. Single Slot Notional Cap Check
        if self.max_single_slot_notional_rs and proposed_notional > self.max_single_slot_notional_rs + 1e-4:
            return self._rejected(
                sym,
                "SLOT_CAP_EXCEEDED",
                f"Proposed notional Rs {proposed_notional:.2f} exceeds single slot cap Rs {self.max_single_slot_notional_rs:.2f}.",
                sector=sector,
                proposed_risk=proposed_risk,
                proposed_notional=proposed_notional,
            )

        # 1b. VAR+ELM Margin Ceiling Gate (SEBI T+1 Shortfall Immunity)
        if self.enforce_var_elm_gate or var_elm_rate is not None:
            if var_elm_rate is None:
                return self._rejected(
                    sym,
                    "MISSING_MARGIN_RATE",
                    "Scrip VAR+ELM margin rate is required for SEBI shortfall immunity.",
                    sector=sector,
                    proposed_risk=proposed_risk,
                    proposed_notional=proposed_notional,
                )
            if (
                isinstance(var_elm_rate, bool)
                or not isinstance(var_elm_rate, (int, float))
                or not math.isfinite(float(var_elm_rate))
                or float(var_elm_rate) <= 0
            ):
                return self._rejected(
                    sym,
                    "INVALID_MARGIN_RATE",
                    f"Scrip VAR+ELM margin rate must be a valid positive number: {var_elm_rate}",
                    sector=sector,
                    proposed_risk=proposed_risk,
                    proposed_notional=proposed_notional,
                )
            if float(var_elm_rate) > 0.30:
                return self._rejected(
                    sym,
                    "VAR_ELM_EXCEEDS_MARGIN_CEILING",
                    f"Scrip VAR+ELM margin rate {float(var_elm_rate) * 100:.1f}% exceeds 30.0% ceiling for SEBI shortfall immunity.",
                    sector=sector,
                    proposed_risk=proposed_risk,
                    proposed_notional=proposed_notional,
                )

        # Duplicate Symbol Check: cannot open duplicate position in same symbol
        active_symbols = {str(item.get("symbol", "")).strip().upper() for item in active_items}
        if sym in active_symbols:
            return self._rejected(
                sym,
                "DUPLICATE_SYMBOL_POSITION",
                f"An active position or pending order already exists for '{sym}'. Duplicate position prohibited.",
                sector=sector,
                proposed_risk=proposed_risk,
                current_open_risk=current_open_risk,
                proposed_notional=proposed_notional,
                current_total_notional=current_total_notional,
            )

        # 2. Sector Concentration Limit Check
        existing_sector_count = sector_counts.get(sector, 0)
        if existing_sector_count >= self.max_positions_per_sector:
            return self._rejected(
                sym,
                "SECTOR_CONCENTRATION_EXCEEDED",
                f"Sector '{sector}' already has {existing_sector_count} active/pending positions "
                f"(max allowed: {self.max_positions_per_sector}).",
                sector=sector,
                proposed_risk=proposed_risk,
                current_open_risk=current_open_risk,
                proposed_notional=proposed_notional,
                current_total_notional=current_total_notional,
                sector_count=existing_sector_count,
            )

        # 3. Aggregate Portfolio Risk Cap Check
        new_total_risk = round(current_open_risk + proposed_risk, 2)
        if new_total_risk > self.max_aggregate_risk_rs + 1e-4:
            return self._rejected(
                sym,
                "AGGREGATE_PORTFOLIO_RISK_EXCEEDED",
                f"New total open risk Rs {new_total_risk:.2f} exceeds portfolio risk ceiling "
                f"Rs {self.max_aggregate_risk_rs:.2f} (Current open: Rs {current_open_risk:.2f}).",
                sector=sector,
                proposed_risk=proposed_risk,
                current_open_risk=current_open_risk,
                new_total_risk=new_total_risk,
                proposed_notional=proposed_notional,
                current_total_notional=current_total_notional,
                sector_count=existing_sector_count,
            )

        # 3b. Slot Limit Check (Concurrent Positions)
        if len(active_items) >= self.max_concurrent_positions:
            return self._rejected(
                sym,
                "MAX_CONCURRENT_POSITIONS_REACHED",
                f"Portfolio already has {len(active_items)} active/pending positions (max allowed: {self.max_concurrent_positions}).",
                sector=sector,
                proposed_risk=proposed_risk,
                current_open_risk=current_open_risk,
                new_total_risk=new_total_risk,
                proposed_notional=proposed_notional,
                current_total_notional=current_total_notional,
                sector_count=existing_sector_count,
            )

        # 4. Total Capital Notional Ceiling Check
        new_total_notional = round(current_total_notional + proposed_notional, 2)
        if new_total_notional > self.total_capital_allocation_rs + 1e-4:
            return self._rejected(
                sym,
                "TOTAL_CAPITAL_EXCEEDED",
                f"New total notional Rs {new_total_notional:.2f} exceeds cash capital allocation "
                f"Rs {self.total_capital_allocation_rs:.2f} (Current: Rs {current_total_notional:.2f}).",
                sector=sector,
                proposed_risk=proposed_risk,
                current_open_risk=current_open_risk,
                new_total_risk=new_total_risk,
                proposed_notional=proposed_notional,
                current_total_notional=current_total_notional,
                new_total_notional=new_total_notional,
                sector_count=existing_sector_count,
            )

        # All Gates Approved
        return RiskAssessmentResult(
            is_approved=True,
            rejection_reason=None,
            symbol=sym,
            sector=sector,
            proposed_risk_rs=proposed_risk,
            current_open_risk_rs=current_open_risk,
            new_total_risk_rs=new_total_risk,
            proposed_notional_rs=proposed_notional,
            current_total_notional_rs=current_total_notional,
            new_total_notional_rs=new_total_notional,
            sector_position_count=existing_sector_count + 1,
            max_sector_allowed=self.max_positions_per_sector,
            portfolio_risk_budget_rs=self.max_single_trade_risk_rs,
            max_portfolio_risk_rs=self.max_aggregate_risk_rs,
        )

    def _rejected(
        self,
        symbol: str,
        code: str,
        reason: str,
        sector: str = "UNKNOWN",
        proposed_risk: float = 0.0,
        current_open_risk: float = 0.0,
        new_total_risk: float = 0.0,
        proposed_notional: float = 0.0,
        current_total_notional: float = 0.0,
        new_total_notional: float = 0.0,
        sector_count: int = 0,
    ) -> RiskAssessmentResult:
        return RiskAssessmentResult(
            is_approved=False,
            rejection_reason=f"{code}: {reason}",
            symbol=symbol,
            sector=sector,
            proposed_risk_rs=proposed_risk,
            current_open_risk_rs=current_open_risk,
            new_total_risk_rs=new_total_risk,
            proposed_notional_rs=proposed_notional,
            current_total_notional_rs=current_total_notional,
            new_total_notional_rs=new_total_notional,
            sector_position_count=sector_count,
            max_sector_allowed=self.max_positions_per_sector,
            portfolio_risk_budget_rs=self.max_single_trade_risk_rs,
            max_portfolio_risk_rs=self.max_aggregate_risk_rs,
        )


# Canonical BASTION Alias for ARGUS 8i // BEACON
BastionRiskGovernor = PortfolioRiskGovernor

