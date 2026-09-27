"""
research/tests/test_mlofi.py
============================
Unit tests for 5-level Multi-Level Order Flow Imbalance (MLOFI).
Verifies exact Cont-Kukanov-Stoikov signed transitions.
"""

import pytest
from research.derivatives.mlofi import (
    DepthLevel,
    LevelDepthSnapshot,
    MultiLevelOFI,
)


def create_mock_snapshot(
    symbol: str = "TCS",
    timestamp: str = "2026-09-24T09:30:00+05:30",
    bid_px: float = 3500.0,
    ask_px: float = 3500.5,
    bid_qty: int = 1000,
    ask_qty: int = 1000,
) -> LevelDepthSnapshot:
    """Helper to create a clean 5-level depth snapshot."""
    levels = []
    for k in range(5):
        levels.append(
            DepthLevel(
                level=k + 1,
                bid_price=round(bid_px - (k * 0.05), 2),
                bid_shares=bid_qty + (k * 200),
                ask_price=round(ask_px + (k * 0.05), 2),
                ask_shares=ask_qty + (k * 200),
            )
        )
    return LevelDepthSnapshot(symbol=symbol, timestamp=timestamp, levels=levels)


def test_mlofi_bid_addition_produces_positive_ofi():
    """Increasing bid shares at the same price must produce positive OFI."""
    snap1 = create_mock_snapshot(bid_qty=1000, ask_qty=1000)
    # 500 new bids arrive at level 1
    snap2 = create_mock_snapshot(bid_qty=1500, ask_qty=1000)

    res = MultiLevelOFI.evaluate_snapshots(snap1, snap2)
    assert res.is_valid is True
    assert res.raw_mlofi > 0.0
    assert res.normalized_mlofi > 0.0
    # Level 1 e_k should be exactly +500
    assert res.details["L1"]["e_k"] == 500.0


def test_mlofi_ask_addition_produces_negative_ofi():
    """Increasing ask shares at the same price must produce negative OFI."""
    snap1 = create_mock_snapshot(bid_qty=1000, ask_qty=1000)
    # 800 new asks arrive at level 1
    snap2 = create_mock_snapshot(bid_qty=1000, ask_qty=1800)

    res = MultiLevelOFI.evaluate_snapshots(snap1, snap2)
    assert res.is_valid is True
    assert res.raw_mlofi < 0.0
    assert res.normalized_mlofi < 0.0
    # Level 1 e_k should be exactly -800
    assert res.details["L1"]["e_k"] == -800.0


def test_mlofi_bid_price_improvement():
    """Bid price jumping up a tick contributes full new shares as positive flow."""
    snap1 = create_mock_snapshot(bid_px=3500.0, bid_qty=1000)
    # Bid jumps to 3500.05 with 1200 shares
    snap2 = create_mock_snapshot(bid_px=3500.05, bid_qty=1200)

    e_k, _ = MultiLevelOFI.compute_level_delta(snap1.levels[0], snap2.levels[0])
    # b_t > b_{t-1} -> bid_contrib is +q^b_t = +1200
    assert e_k >= 1200.0


def test_mlofi_crossed_quote_fails_closed():
    """Crossed or locked market depth must fail closed with is_valid=False."""
    # Best bid 3501.0 >= Best ask 3500.5
    crossed_snap = create_mock_snapshot(bid_px=3501.0, ask_px=3500.5)
    valid_snap = create_mock_snapshot(bid_px=3500.0, ask_px=3500.5)

    res = MultiLevelOFI.evaluate_snapshots(valid_snap, crossed_snap)
    assert res.is_valid is False
    assert res.normalized_mlofi == 0.0
    assert res.details["reason"] == "CROSSED_OR_ZERO_DEPTH"


def test_mlofi_weighted_midpoint_calculation():
    """Verify microprice skews toward side with lower depth."""
    # High bid quantity (5000), low ask quantity (1000) -> price should skew toward ask
    snap = create_mock_snapshot(bid_px=100.0, ask_px=101.0, bid_qty=5000, ask_qty=1000)
    # microprice = (100 * 1000 + 101 * 5000) / 6000 = (100000 + 505000) / 6000 = 605000 / 6000 = 100.83
    assert snap.weighted_midpoint == 100.83
