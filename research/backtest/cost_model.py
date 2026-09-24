"""
research/backtest/cost_model.py
===============================
Institutional Transaction Cost & Fee Model for Dhan / NSE Cash Equities.
Replaces arbitrary flat percentage hurdles with exact, order-aware statutory
and broker tariff schedules per Dhan published pricing (2026).

References:
- Dhan Pricing Schedule: https://dhan.co/pricing/
- NSE Transaction Charges Circular: NSE/F&A/2023
- SEBI Turnover Charges: Gazetted Tariff Schedule
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ProductType(str, Enum):
    MIS = "MIS"  # Intraday Margin
    CNC = "CNC"  # Delivery / Cash & Carry


@dataclass(frozen=True)
class FeeBreakdown:
    side: OrderSide
    product: ProductType
    price: float
    shares: int
    turnover: float
    brokerage: float
    stt: float
    exchange_charges: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    auto_squareoff_penalty: float
    total_charges: float

    @property
    def cost_bps(self) -> float:
        """Cost in basis points relative to executed turnover."""
        if self.turnover <= 0.0:
            return 0.0
        return (self.total_charges / self.turnover) * 10000.0


@dataclass(frozen=True)
class RoundTripFeeSummary:
    entry: FeeBreakdown
    exit: FeeBreakdown
    total_turnover: float
    total_charges: float
    effective_bps: float
    effective_pct: float


class DhanFeeEngine:
    """
    Exact fee calculation engine for Dhan Trading Account on NSE Cash Equities.
    
    Rates:
    - Brokerage: min(Rs 20.0, 0.03% of turnover) for MIS; Rs 0 for Equity Delivery (CNC).
    - STT (Securities Transaction Tax):
        * MIS: 0.025% on Sell side only.
        * CNC: 0.10% on both Buy and Sell sides.
    - NSE Exchange Transaction Fee: 0.0030699% (NSE Cash segment tariff).
    - SEBI Turnover Fee: 0.0001% (Rs 10 per Crore of turnover).
    - Stamp Duty (Indian Stamp Act): 0.003% on Buy side only (Rs 300 per Crore).
    - GST (Goods & Services Tax): 18.0% on (Brokerage + Exchange Charges + SEBI Charges).
    - Auto Square-Off Penalty: Rs 20.00 + 18% GST = Rs 23.60 per square-off order if triggered by Dhan RMS.
    """

    BROKERAGE_MAX_CAP = 20.00
    BROKERAGE_RATE_MIS = 0.00030         # 0.03%
    STT_RATE_MIS_SELL = 0.00025          # 0.025% on Sell
    STT_RATE_CNC = 0.00100               # 0.10% on Buy and Sell
    NSE_EXCHANGE_FEE_RATE = 0.000030699  # 0.0030699%
    SEBI_FEE_RATE = 0.00000100           # Rs 10 / Crore = 0.0001%
    STAMP_DUTY_BUY = 0.000030            # 0.003% on Buy
    GST_RATE = 0.18                      # 18.0%
    AUTO_SQUAREOFF_BASE = 20.00          # Rs 20.00

    @classmethod
    def calculate_leg(
        cls,
        side: OrderSide,
        price: float,
        shares: int,
        product: ProductType = ProductType.MIS,
        is_auto_squareoff: bool = False,
    ) -> FeeBreakdown:
        """
        Calculate precise charges for a single order leg.
        """
        if price <= 0.0 or shares <= 0:
            raise ValueError(f"Price ({price}) and shares ({shares}) must be strictly positive.")

        turnover = round(price * shares, 2)

        # 1. Brokerage
        if product == ProductType.MIS:
            brokerage = round(min(cls.BROKERAGE_MAX_CAP, turnover * cls.BROKERAGE_RATE_MIS), 2)
        else:
            # CNC Delivery brokerage is Rs 0 on Dhan
            brokerage = 0.0

        # 2. STT
        if product == ProductType.MIS:
            stt = round(turnover * cls.STT_RATE_MIS_SELL, 2) if side == OrderSide.SELL else 0.0
        else:
            stt = round(turnover * cls.STT_RATE_CNC, 2)

        # 3. Exchange Transaction Charges (NSE)
        exchange_charges = round(turnover * cls.NSE_EXCHANGE_FEE_RATE, 2)

        # 4. SEBI Turnover Fee
        sebi_charges = round(turnover * cls.SEBI_FEE_RATE, 2)

        # 5. Stamp Duty (Buy only)
        stamp_duty = round(turnover * cls.STAMP_DUTY_BUY, 2) if side == OrderSide.BUY else 0.0

        # 6. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
        taxable_services = brokerage + exchange_charges + sebi_charges
        gst = round(taxable_services * cls.GST_RATE, 2)

        # 7. RMS Auto Square-off penalty if applicable
        auto_penalty = round(cls.AUTO_SQUAREOFF_BASE * (1.0 + cls.GST_RATE), 2) if is_auto_squareoff else 0.0

        total = round(
            brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst + auto_penalty,
            2,
        )

        return FeeBreakdown(
            side=side,
            product=product,
            price=price,
            shares=shares,
            turnover=turnover,
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            sebi_charges=sebi_charges,
            stamp_duty=stamp_duty,
            gst=gst,
            auto_squareoff_penalty=auto_penalty,
            total_charges=total,
        )

    @classmethod
    def calculate_round_trip(
        cls,
        entry_price: float,
        exit_price: float,
        shares: int,
        entry_side: OrderSide = OrderSide.BUY,
        product: ProductType = ProductType.MIS,
        is_auto_squareoff: bool = False,
    ) -> RoundTripFeeSummary:
        """
        Calculate total round-trip friction for an executed position.
        """
        exit_side = OrderSide.SELL if entry_side == OrderSide.BUY else OrderSide.BUY

        entry_leg = cls.calculate_leg(
            side=entry_side, price=entry_price, shares=shares, product=product, is_auto_squareoff=False
        )
        exit_leg = cls.calculate_leg(
            side=exit_side, price=exit_price, shares=shares, product=product, is_auto_squareoff=is_auto_squareoff
        )

        total_turnover = round(entry_leg.turnover + exit_leg.turnover, 2)
        total_charges = round(entry_leg.total_charges + exit_leg.total_charges, 2)
        effective_bps = (total_charges / total_turnover) * 10000.0 if total_turnover > 0 else 0.0
        effective_pct = (total_charges / total_turnover) * 100.0 if total_turnover > 0 else 0.0

        return RoundTripFeeSummary(
            entry=entry_leg,
            exit=exit_leg,
            total_turnover=total_turnover,
            total_charges=total_charges,
            effective_bps=round(effective_bps, 2),
            effective_pct=round(effective_pct, 4),
        )
