"""
pcas_execution.py - Periodic Call Auction (ESM Stage 2) Discrete Execution Engine
Part of Project Swing Trades tri-agent architecture (Antigravity + Claude + ChatGPT).
Models 6 discrete call auctions per day, auction-level participation gates,
lumpy Bernoulli matching, and multi-day compounding price decay.
"""

import math
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional


@dataclass
class PCASExecutionResult:
    is_clearable: bool
    position_shares: int
    daily_volume: int
    auction_volume: float
    max_participation_per_auction: float
    auctions_to_clear: float
    sessions_to_clear: float
    compounding_decay_pct: float
    status: str
    rationale: str


class PCASExecutionEngine:
    """
    Evaluates execution mechanics for securities under Periodic Call Auction (ESM Stage 2).
    
    Per SEBI CIR/MRD/DP/6/2013 and BSE Notice 20230718-46:
    - Stage 2 securities trade strictly under Periodic Call Auction on all trading days.
    - 6 discrete 1-hour call auctions per day (09:30 to 15:30).
    - Price band is +/- 2.0% with 100% margin and Trade-to-Trade settlement.
    - Auction matching is discrete at equilibrium price, not continuous.
    """

    # Statutory periodic call auction session parameters
    BSE_DEFAULT_AUCTIONS_PER_DAY: int = 6   # BSE Notice 20230630-19 / 20230718-46: six 1-hour sessions (09:30-15:30)
    NSE_MIN_AUCTIONS_PER_DAY: int = 2       # NSE Periodic Call Auction framework minimum sessions
    AUCTIONS_PER_DAY: int = 6               # Default venue configuration (BSE)
    MAX_PARTICIPATION_RATE: float = 0.15    # Claude Rule 9 Limit: 15% of volume
    MAX_CLEARABLE_SESSIONS: float = 2.0     # Maximum allowable sessions to clear (2 trading days)

    @classmethod
    def evaluate_pcas_exit(
        cls,
        position_shares: int,
        daily_volume: int,
        circuit_band_pct: float = 2.0,
        auctions_per_day: Optional[int] = None
    ) -> PCASExecutionResult:
        """
        Evaluates whether a position can exit under Periodic Call Auction constraints.
        
        Args:
            position_shares: Order size in shares (must be > 0).
            daily_volume: Expected/observed daily volume in shares (must be > 0).
            circuit_band_pct: Applicable daily circuit band (typically 2.0% for ESM Stage 2).
            auctions_per_day: Configurable session count (default: 6 for BSE, 2 for NSE).
        """
        num_auctions = auctions_per_day if auctions_per_day is not None and auctions_per_day > 0 else cls.AUCTIONS_PER_DAY

        if (
            position_shares is None
            or not isinstance(position_shares, (int, float))
            or not math.isfinite(position_shares)
            or position_shares <= 0
        ):
            return PCASExecutionResult(
                is_clearable=False,
                position_shares=position_shares or 0,
                daily_volume=daily_volume or 0,
                auction_volume=0.0,
                max_participation_per_auction=0.0,
                auctions_to_clear=0.0,
                sessions_to_clear=0.0,
                compounding_decay_pct=0.0,
                status="INVALID_POSITION_SHARES",
                rationale=f"Invalid position shares ({position_shares}). Must be a positive finite integer."
            )

        if (
            daily_volume is None
            or not isinstance(daily_volume, (int, float))
            or not math.isfinite(daily_volume)
            or daily_volume <= 0
        ):
            return PCASExecutionResult(
                is_clearable=False,
                position_shares=position_shares,
                daily_volume=0,
                auction_volume=0.0,
                max_participation_per_auction=0.0,
                auctions_to_clear=float("inf"),
                sessions_to_clear=float("inf"),
                compounding_decay_pct=-100.0,
                status="ZERO_VOLUME_LOCKED",
                rationale="Zero daily volume. No counterparty liquidity exists across any call auction."
            )

        if (
            circuit_band_pct is None
            or not isinstance(circuit_band_pct, (int, float))
            or not math.isfinite(circuit_band_pct)
            or circuit_band_pct <= 0
        ):
            return PCASExecutionResult(
                is_clearable=False,
                position_shares=position_shares,
                daily_volume=daily_volume,
                auction_volume=0.0,
                max_participation_per_auction=0.0,
                auctions_to_clear=0.0,
                sessions_to_clear=0.0,
                compounding_decay_pct=0.0,
                status="INVALID_CIRCUIT_BAND",
                rationale=f"Invalid circuit band ({circuit_band_pct}). Must be a positive finite percentage."
            )

        # 1. Deconstruct daily volume into per-auction volume
        auction_vol = daily_volume / num_auctions

        # 2. Maximum participation per auction (15%)
        max_shares_per_auction = cls.MAX_PARTICIPATION_RATE * auction_vol

        # 3. Auctions and days required to clear
        auctions_needed = round(position_shares / max_shares_per_auction, 2)
        sessions_needed = round(auctions_needed / num_auctions, 2)

        # 4. Compounding downside decay across needed sessions at daily circuit band
        # (1 - band_pct)^sessions - 1
        daily_decay_factor = (1.0 - circuit_band_pct / 100.0)
        total_decay_pct = round(((daily_decay_factor ** sessions_needed) - 1.0) * 100.0, 2)

        is_clearable = sessions_needed <= cls.MAX_CLEARABLE_SESSIONS

        if not is_clearable:
            status = "DISQUALIFIED_PCAS_LIQUIDITY_TRAP"
            rationale = (
                f"REJECTED BY RULE 9 (PCAS GATE): Position ({position_shares:,} sh) requires {auctions_needed:.1f} auctions "
                f"({sessions_needed:.1f} sessions) to exit at 15% participation (limit: {cls.MAX_CLEARABLE_SESSIONS:.1f} sessions). "
                f"Per-auction volume is only {auction_vol:.0f} sh/auction ({max_shares_per_auction:.1f} sh allowed across {num_auctions} sessions/day). "
                f"Estimated compounding exit erosion: {total_decay_pct:.1f}%."
            )
        else:
            status = "QUALIFIED_CLEARABLE_IN_PCAS"
            rationale = (
                f"QUALIFIED: Position clears in {auctions_needed:.1f} auctions ({sessions_needed:.1f} sessions <= {cls.MAX_CLEARABLE_SESSIONS:.1f}). "
                f"Exit decay bounded at {total_decay_pct:.1f}%."
            )

        return PCASExecutionResult(
            is_clearable=is_clearable,
            position_shares=position_shares,
            daily_volume=daily_volume,
            auction_volume=round(auction_vol, 1),
            max_participation_per_auction=round(max_shares_per_auction, 1),
            auctions_to_clear=auctions_needed,
            sessions_to_clear=sessions_needed,
            compounding_decay_pct=total_decay_pct,
            status=status,
            rationale=rationale
        )

    @classmethod
    def simulate_call_auction_equilibrium_fill(
        cls,
        order_qty: int,
        incoming_contra_volume: int,
        queue_rank_ahead: int
    ) -> Tuple[int, int, str]:
        """
        Simulates discrete equilibrium call auction matching under price-time priority.
        
        Returns:
            (filled_shares, remaining_shares, state)
        """
        if order_qty is None or not isinstance(order_qty, (int, float)) or not math.isfinite(order_qty) or order_qty <= 0:
            return 0, 0, "INVALID_ORDER_QTY"

        if queue_rank_ahead is None or not isinstance(queue_rank_ahead, (int, float)) or not math.isfinite(queue_rank_ahead) or queue_rank_ahead < 0:
            return 0, int(order_qty), "INVALID_QUEUE_RANK"

        if incoming_contra_volume is None or not isinstance(incoming_contra_volume, (int, float)) or not math.isfinite(incoming_contra_volume) or incoming_contra_volume <= 0:
            return 0, int(order_qty), "NO_FILL_BEHIND_EQUILIBRIUM_QUEUE"

        if incoming_contra_volume <= queue_rank_ahead:
            return 0, int(order_qty), "NO_FILL_BEHIND_EQUILIBRIUM_QUEUE"

        available_at_equilibrium = int(incoming_contra_volume - queue_rank_ahead)
        filled = min(int(order_qty), available_at_equilibrium)
        remaining = int(order_qty) - filled

        if filled >= order_qty:
            return filled, 0, "FULL_AUCTION_FILL"
        else:
            return filled, remaining, "PARTIAL_AUCTION_FILL"

    # Backward-compatibility alias
    simulate_bernoulli_auction_fill = simulate_call_auction_equilibrium_fill


if __name__ == "__main__":
    print("=== PERIODIC CALL AUCTION (PCAS) EXECUTION ENGINE TEST ===")

    # Test Case 1: CHANDRIMA (4,500 shares vs 3,134 daily volume @ 2% band)
    # Target: 57 auctions, 9.6 sessions, -17.6% decay -> MUST FAIL
    res_chand = PCASExecutionEngine.evaluate_pcas_exit(
        position_shares=4500,
        daily_volume=3134,
        circuit_band_pct=2.0
    )
    print(f"\nTest 1 (CHANDRIMA PCAS Evaluation):")
    print(f"  Auction Volume: {res_chand.auction_volume} sh/auction")
    print(f"  Max Participation / Auction: {res_chand.max_participation_per_auction} sh")
    print(f"  Auctions to Clear: {res_chand.auctions_to_clear} auctions")
    print(f"  Sessions to Clear: {res_chand.sessions_to_clear} sessions")
    print(f"  Compounding Decay: {res_chand.compounding_decay_pct}%")
    print(f"  Status: {res_chand.status}")
    print(f"  Rationale: {res_chand.rationale}")

    assert not res_chand.is_clearable, "CHANDRIMA must fail PCAS liquidity gate!"
    assert 56.0 <= res_chand.auctions_to_clear <= 58.0, "Auctions should be ~57!"
    assert 9.4 <= res_chand.sessions_to_clear <= 9.7, "Sessions should be ~9.6!"
    assert -18.0 <= res_chand.compounding_decay_pct <= -17.0, "Decay should be ~ -17.6%!"

    # Test Case 2: Discrete Bernoulli matching in a single call auction
    # Order: 500 shares, Queue ahead: 200 shares, Total auction contra-volume matched: 600 shares
    filled, remaining, state = PCASExecutionEngine.simulate_bernoulli_auction_fill(
        order_qty=500,
        incoming_contra_volume=600,
        queue_rank_ahead=200
    )
    print(f"\nTest 2 (Discrete Bernoulli Auction Match): Filled = {filled}, Remaining = {remaining}, State = {state}")
    assert filled == 400 and remaining == 100 and state == "PARTIAL_AUCTION_FILL"

    print("\nALL PCAS EXECUTION ENGINE TESTS PASSED 100%!")
