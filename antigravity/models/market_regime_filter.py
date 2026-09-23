"""
market_regime_filter.py - Market Regime & Breadth Filter for Track 2 Momentum
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Quantitative Purpose:
- Prevents taking aggressive long Opening Range Breakouts during broad market distribution.
- Evaluates Nifty 50 15-minute Opening Range (09:15-09:30) and Market Breadth (Advance/Decline).
- Fail-Closed: If market index or breadth data is invalid or missing, defaults to gated or cautious mode.
"""

from dataclasses import dataclass
from enum import Enum
import math
from typing import Optional, Dict, Any


class MarketRegimeState(Enum):
    BULLISH_EXPANSION = "BULLISH_EXPANSION"        # Index above OR High + Breadth positive (A/D >= 1.2)
    NEUTRAL_SELECTIVE = "NEUTRAL_SELECTIVE"        # Index inside OR or Breadth neutral (A/D 1.0 - 1.2)
    DISTRIBUTION_GATED = "DISTRIBUTION_GATED"      # Index below OR Low or Heavy Declines (A/D < 1.0)
    REGIME_DATA_INVALID = "REGIME_DATA_INVALID"    # Corrupted / missing data (fail-closed)


@dataclass
class MarketRegimeSnapshot:
    state: MarketRegimeState
    nifty_ltp: float
    nifty_or_high: float
    nifty_or_low: float
    ad_ratio: Optional[float]
    advances: Optional[int]
    declines: Optional[int]
    reason: str
    allow_standard_orb: bool
    min_volume_multiple: float                      # Dynamic volume threshold (2.5x standard, 3.5x selective, inf if gated)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "nifty_ltp": self.nifty_ltp,
            "nifty_or_high": self.nifty_or_high,
            "nifty_or_low": self.nifty_or_low,
            "ad_ratio": self.ad_ratio,
            "advances": self.advances,
            "declines": self.declines,
            "reason": self.reason,
            "allow_standard_orb": self.allow_standard_orb,
            "min_volume_multiple": self.min_volume_multiple
        }


class MarketRegimeFilter:
    """
    Evaluates broader market regime to protect Track 2 capital against macro pullbacks.
    """

    @staticmethod
    def evaluate_regime(
        nifty_ltp: Optional[float],
        nifty_or_high: Optional[float],
        nifty_or_low: Optional[float],
        advances: Optional[int] = None,
        declines: Optional[int] = None,
        min_ad_ratio: float = 1.20
    ) -> MarketRegimeSnapshot:
        """
        Evaluates Nifty 50 price action relative to its 15-minute opening range
        combined with broad market advance/decline ratio.
        """
        # Validate price inputs fail-closed against None, booleans, NaN, Inf, and non-positive numbers
        for val in [nifty_ltp, nifty_or_high, nifty_or_low]:
            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val) or val <= 0:
                return MarketRegimeSnapshot(
                    state=MarketRegimeState.REGIME_DATA_INVALID,
                    nifty_ltp=0.0,
                    nifty_or_high=0.0,
                    nifty_or_low=0.0,
                    ad_ratio=None,
                    advances=advances,
                    declines=declines,
                    reason="FAIL-CLOSED: Missing, non-finite, boolean, or non-positive Nifty 50 price metrics.",
                    allow_standard_orb=False,
                    min_volume_multiple=float("inf")
                )

        if nifty_or_high < nifty_or_low:
            return MarketRegimeSnapshot(
                state=MarketRegimeState.REGIME_DATA_INVALID,
                nifty_ltp=nifty_ltp,
                nifty_or_high=nifty_or_high,
                nifty_or_low=nifty_or_low,
                ad_ratio=None,
                advances=advances,
                declines=declines,
                reason="FAIL-CLOSED: Degenerate Nifty range (OR High < OR Low).",
                allow_standard_orb=False,
                min_volume_multiple=float("inf")
            )

        # Compute Advance/Decline ratio if counts provided, validating types strictly
        ad_ratio = None
        if advances is not None or declines is not None:
            if advances is None or declines is None:
                return MarketRegimeSnapshot(
                    state=MarketRegimeState.REGIME_DATA_INVALID,
                    nifty_ltp=nifty_ltp,
                    nifty_or_high=nifty_or_high,
                    nifty_or_low=nifty_or_low,
                    ad_ratio=None,
                    advances=advances,
                    declines=declines,
                    reason="FAIL-CLOSED: Partial breadth data (both advances and declines must be provided together).",
                    allow_standard_orb=False,
                    min_volume_multiple=float("inf")
                )
            for cnt in [advances, declines]:
                if cnt is None or isinstance(cnt, bool) or not isinstance(cnt, (int, float)) or math.isnan(cnt) or math.isinf(cnt) or cnt < 0:
                    return MarketRegimeSnapshot(
                        state=MarketRegimeState.REGIME_DATA_INVALID,
                        nifty_ltp=nifty_ltp,
                        nifty_or_high=nifty_or_high,
                        nifty_or_low=nifty_or_low,
                        ad_ratio=None,
                        advances=advances,
                        declines=declines,
                        reason="FAIL-CLOSED: Malformed advance/decline breadth metrics (must be non-negative finite numeric).",
                        allow_standard_orb=False,
                        min_volume_multiple=float("inf")
                    )
            if declines == 0:
                ad_ratio = float("inf") if advances > 0 else 1.0
            else:
                ad_ratio = round(advances / declines, 2)

        # 1. Check Index Distribution Gate (Price below OR Low)
        if nifty_ltp < nifty_or_low:
            return MarketRegimeSnapshot(
                state=MarketRegimeState.DISTRIBUTION_GATED,
                nifty_ltp=nifty_ltp,
                nifty_or_high=nifty_or_high,
                nifty_or_low=nifty_or_low,
                ad_ratio=ad_ratio,
                advances=advances,
                declines=declines,
                reason=f"ABORT_DISTRIBUTION: Nifty 50 ({nifty_ltp:.1f}) broke below 15m OR Low ({nifty_or_low:.1f}). Macro headwind.",
                allow_standard_orb=False,
                min_volume_multiple=float("inf")
            )

        # 2. Check Breadth Distribution Gate (if A/D ratio available and < 1.0)
        if ad_ratio is not None and ad_ratio < 1.0:
            return MarketRegimeSnapshot(
                state=MarketRegimeState.DISTRIBUTION_GATED,
                nifty_ltp=nifty_ltp,
                nifty_or_high=nifty_or_high,
                nifty_or_low=nifty_or_low,
                ad_ratio=ad_ratio,
                advances=advances,
                declines=declines,
                reason=f"ABORT_DISTRIBUTION: Broad market breadth negative (A/D ratio {ad_ratio:.2f} < 1.0). Net declining tape.",
                allow_standard_orb=False,
                min_volume_multiple=float("inf")
            )

        # 3. Check Bullish Expansion (Index above OR High AND Breadth explicitly verified >= min_ad_ratio)
        is_nifty_breakout = nifty_ltp > nifty_or_high

        # Codex Finding 5: Do NOT fail open when breadth is missing.
        # Breadth must be verified >= min_ad_ratio to allow BULLISH_EXPANSION.
        if is_nifty_breakout:
            if ad_ratio is not None and ad_ratio >= min_ad_ratio:
                return MarketRegimeSnapshot(
                    state=MarketRegimeState.BULLISH_EXPANSION,
                    nifty_ltp=nifty_ltp,
                    nifty_or_high=nifty_or_high,
                    nifty_or_low=nifty_or_low,
                    ad_ratio=ad_ratio,
                    advances=advances,
                    declines=declines,
                    reason=f"BULLISH EXPANSION: Nifty 50 ({nifty_ltp:.1f}) > OR High ({nifty_or_high:.1f}) with strong breadth (A/D {ad_ratio:.2f}). Standard ORB active.",
                    allow_standard_orb=True,
                    min_volume_multiple=2.5
                )
            elif ad_ratio is None:
                # Nifty breakout but unmeasured breadth: Selective only, standard ORB NOT allowed
                return MarketRegimeSnapshot(
                    state=MarketRegimeState.NEUTRAL_SELECTIVE,
                    nifty_ltp=nifty_ltp,
                    nifty_or_high=nifty_or_high,
                    nifty_or_low=nifty_or_low,
                    ad_ratio=None,
                    advances=advances,
                    declines=declines,
                    reason=f"SELECTIVE_UNCONFIRMED_BREADTH: Nifty 50 ({nifty_ltp:.1f}) > OR High ({nifty_or_high:.1f}) but broad breadth is missing. Standard ORB disabled; requires 3.5x volume.",
                    allow_standard_orb=False,
                    min_volume_multiple=3.5
                )

        # 4. Neutral / Selective Regime (Inside range, or breakout with lukewarm breadth)
        reason_desc = []
        if not is_nifty_breakout:
            reason_desc.append(f"Nifty ({nifty_ltp:.1f}) inside 15m OR [{nifty_or_low:.1f} - {nifty_or_high:.1f}]")
        if ad_ratio is not None and ad_ratio < min_ad_ratio:
            reason_desc.append(f"A/D {ad_ratio:.2f} < threshold {min_ad_ratio:.2f}")

        return MarketRegimeSnapshot(
            state=MarketRegimeState.NEUTRAL_SELECTIVE,
            nifty_ltp=nifty_ltp,
            nifty_or_high=nifty_or_high,
            nifty_or_low=nifty_or_low,
            ad_ratio=ad_ratio,
            advances=advances,
            declines=declines,
            reason=f"NEUTRAL / SELECTIVE: {'; '.join(reason_desc)}. Requiring higher volume conviction (3.5x).",
            allow_standard_orb=True,
            min_volume_multiple=3.5
        )


if __name__ == "__main__":
    print("=== TESTING MARKET REGIME FILTER ===")
    r1 = MarketRegimeFilter.evaluate_regime(25450.0, 25400.0, 25300.0, advances=320, declines=180)
    print(f"R1 (Bullish): {r1.state.value} | Min Vol: {r1.min_volume_multiple}x | {r1.reason}")
    assert r1.state == MarketRegimeState.BULLISH_EXPANSION
    assert r1.allow_standard_orb is True

    r2 = MarketRegimeFilter.evaluate_regime(25280.0, 25400.0, 25300.0, advances=150, declines=350)
    print(f"R2 (Distribution Index): {r2.state.value} | {r2.reason}")
    assert r2.state == MarketRegimeState.DISTRIBUTION_GATED
    assert r2.allow_standard_orb is False

    r3 = MarketRegimeFilter.evaluate_regime(25350.0, 25400.0, 25300.0, advances=240, declines=260)
    print(f"R3 (Selective): {r3.state.value} | Min Vol: {r3.min_volume_multiple}x | {r3.reason}")
    assert r3.state == MarketRegimeState.DISTRIBUTION_GATED  # A/D < 1.0 gates distribution
    assert r3.allow_standard_orb is False

    r4 = MarketRegimeFilter.evaluate_regime(25350.0, 25400.0, 25300.0, advances=275, declines=225)
    print(f"R4 (Selective Inside Range): {r4.state.value} | Min Vol: {r4.min_volume_multiple}x | {r4.reason}")
    assert r4.state == MarketRegimeState.NEUTRAL_SELECTIVE
    assert r4.min_volume_multiple == 3.5

    r5 = MarketRegimeFilter.evaluate_regime(None, 25400.0, 25300.0)
    print(f"R5 (Fail-closed None): {r5.state.value} | {r5.reason}")
    assert r5.state == MarketRegimeState.REGIME_DATA_INVALID

    print("ALL MARKET REGIME SELF-TESTS PASSED 100%!")
