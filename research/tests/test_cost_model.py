"""
research/tests/test_cost_model.py
=================================
Unit tests for the Dhan institutional cost and fee engine.
"""

import pytest
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType


def test_dhan_brokerage_cap_behavior():
    """Verify that MIS brokerage is capped at Rs 20.00 per order."""
    # Large order: 1000 shares @ Rs 200 = Rs 2,00,000 turnover
    # 0.03% of 2,00,000 = Rs 60.00 -> should be capped at Rs 20.00
    fee_large = DhanFeeEngine.calculate_leg(
        side=OrderSide.BUY, price=200.0, shares=1000, product=ProductType.MIS
    )
    assert fee_large.brokerage == 20.00
    assert fee_large.turnover == 200000.0

    # Small order: 50 shares @ Rs 100 = Rs 5,000 turnover
    # 0.03% of 5,000 = Rs 1.50 -> below cap, should be Rs 1.50
    fee_small = DhanFeeEngine.calculate_leg(
        side=OrderSide.BUY, price=100.0, shares=50, product=ProductType.MIS
    )
    assert fee_small.brokerage == 1.50
    assert fee_small.turnover == 5000.0


def test_dhan_statutory_taxes_mis():
    """Verify STT and Stamp Duty side-asymmetry for intraday MIS."""
    # BUY leg: Stamp duty active (0.003%), STT zero
    buy_leg = DhanFeeEngine.calculate_leg(
        side=OrderSide.BUY, price=500.0, shares=100, product=ProductType.MIS
    )
    assert buy_leg.turnover == 50000.0
    assert buy_leg.stamp_duty == 1.50       # 0.003% of 50,000
    assert buy_leg.stt == 0.0               # STT is 0 on Buy for MIS

    # SELL leg: STT active (0.025%), Stamp duty zero
    sell_leg = DhanFeeEngine.calculate_leg(
        side=OrderSide.SELL, price=510.0, shares=100, product=ProductType.MIS
    )
    assert sell_leg.turnover == 51000.0
    assert sell_leg.stt == 12.75            # 0.025% of 51,000
    assert sell_leg.stamp_duty == 0.0       # Stamp duty is 0 on Sell


def test_gst_calculation_integrity():
    """Verify GST is exactly 18% on (Brokerage + Exchange + SEBI)."""
    leg = DhanFeeEngine.calculate_leg(
        side=OrderSide.BUY, price=1000.0, shares=100, product=ProductType.MIS
    )
    # turnover = 1,00,000
    # brokerage = 20.0
    # exchange = 100000 * 0.000030699 = 3.07
    # sebi = 100000 * 0.000001 = 0.10
    # taxable = 20.0 + 3.07 + 0.10 = 23.17
    # gst = 23.17 * 0.18 = 4.1706 -> 4.17
    expected_taxable = leg.brokerage + leg.exchange_charges + leg.sebi_charges
    expected_gst = round(expected_taxable * 0.18, 2)
    assert leg.gst == expected_gst


def test_auto_squareoff_penalty_inclusion():
    """Verify RMS auto square-off penalty is added when flag is set."""
    normal_exit = DhanFeeEngine.calculate_leg(
        side=OrderSide.SELL, price=100.0, shares=100, is_auto_squareoff=False
    )
    rms_exit = DhanFeeEngine.calculate_leg(
        side=OrderSide.SELL, price=100.0, shares=100, is_auto_squareoff=True
    )
    # Rs 20 + 18% GST = Rs 23.60
    assert rms_exit.auto_squareoff_penalty == 23.60
    assert round(rms_exit.total_charges - normal_exit.total_charges, 2) == 23.60


def test_round_trip_fee_summary():
    """Verify round-trip summary aggregates turnover, charges, and bps correctly."""
    rt = DhanFeeEngine.calculate_round_trip(
        entry_price=400.0,
        exit_price=408.0,
        shares=125,  # ~Rs 50,000 slot size
        entry_side=OrderSide.BUY,
        product=ProductType.MIS,
    )
    assert rt.total_turnover == 50000.0 + 51000.0
    assert rt.total_charges == round(rt.entry.total_charges + rt.exit.total_charges, 2)
    assert rt.effective_bps > 0.0
    # For a Rs 50k trade, round-trip charges are typically ~Rs 60-70 (approx 6-7 bps on 100k total turnover)
    assert 5.0 <= rt.effective_bps <= 15.0
