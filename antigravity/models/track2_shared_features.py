"""
track2_shared_features.py - Standardized Shared Feature Computation Engine
==========================================================================
Part of Project Swing Trades (Track 2 Quantitative Strategy Incubator).
Formulated per ChatGPT/Codex & Claude Tri-Agent Consensus Specification (24 Sep 2026).

Standardized Features for 15-Minute Completed Bars:
  1. Typical Price:
     P_t = (H_t + L_t + C_t) / 3.0
  2. 20-Bar Average True Range (ATR20):
     A_t = mean(TR_{t-20}, ..., TR_{t-1}) including overnight gap
  3. Time-of-Day Relative Volume (RVOL):
     RV_t = V_t / median(V_{same 15m time bucket, previous 20 sessions})
     (Never compares opening volume with midday baseline)
  4. Kaufman Trend Efficiency Ratio (ER8 over 8 bars):
     ER_8 = |C_t - C_{t-8}| / sum_{j=t-7}^t |C_j - C_{j-1}|
     (ER_8 >= 0.35 indicates strong trend; ER_8 < 0.25 indicates range/chop)
  5. Market Breadth (B_t):
     B_t = count(Symbols where LTP > Session VWAP) / count(Valid Observed Universe)
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _finite_positive(val: Any) -> bool:
    return (
        not isinstance(val, bool)
        and isinstance(val, numbers.Real)
        and math.isfinite(float(val))
        and float(val) > 0
    )


@dataclass(frozen=True)
class BarFeatures:
    symbol: str
    bar_index: int
    timestamp: str
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: int
    typical_price: float
    atr20: float
    rvol: float
    er8: float
    session_vwap: float
    is_above_vwap: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp,
            "open": self.open_p,
            "high": self.high_p,
            "low": self.low_p,
            "close": self.close_p,
            "volume": self.volume,
            "typical_price": self.typical_price,
            "atr20": self.atr20,
            "rvol": self.rvol,
            "er8": self.er8,
            "session_vwap": self.session_vwap,
            "is_above_vwap": self.is_above_vwap,
        }


class SharedFeatureEngine:
    """
    Computes shared, point-in-time features across all Track 2 strategies.
    Ensures zero lookahead bias and strict numerical sanity.
    """

    @staticmethod
    def calculate_typical_price(high: float, low: float, close: float) -> float:
        return round((high + low + close) / 3.0, 2)

    @staticmethod
    def calculate_atr20(candles_15m: Sequence[Mapping[str, Any]], prev_close: Optional[float] = None) -> float:
        """
        Computes 20-bar ATR including true range with overnight gap.
        Requires at least 2 bars.
        """
        if len(candles_15m) < 2:
            # Fallback to single bar high-low or default
            if candles_15m:
                h = float(candles_15m[0].get("high", 0.0))
                l = float(candles_15m[0].get("low", 0.0))
                return max(0.05, round(h - l, 2))
            return 1.0

        true_ranges: List[float] = []
        prior_c = prev_close

        for i, bar in enumerate(candles_15m):
            h = float(bar.get("high", 0.0))
            l = float(bar.get("low", 0.0))
            c = float(bar.get("close", 0.0))

            if not (_finite_positive(h) and _finite_positive(l) and _finite_positive(c) and h >= l):
                continue

            if prior_c is not None and prior_c > 0:
                tr = max(h - l, abs(h - prior_c), abs(l - prior_c))
            else:
                tr = h - l

            true_ranges.append(tr)
            prior_c = c

        if not true_ranges:
            return 1.0

        # Mean of up to the last 20 true ranges
        window = true_ranges[-20:]
        return round(sum(window) / len(window), 2)

    @staticmethod
    def calculate_rvol(
        current_volume: int,
        bucket_median_volume: float,
        floor_median: float = 1000.0,
    ) -> float:
        """
        Time-of-day relative volume: V_t / median(V_{same bucket, previous 20 sessions}).
        """
        med = max(floor_median, float(bucket_median_volume))
        return round(float(current_volume) / med, 2)

    @staticmethod
    def calculate_er8(candles_15m: Sequence[Mapping[str, Any]]) -> float:
        """
        Kaufman Trend Efficiency Ratio over available bars (up to 8):
        ER = |C_t - C_{t-k}| / sum_{j=t-k+1}^t |C_j - C_{j-1}|
        Returns value between 0.0 (pure chop/random noise) and 1.0 (pure linear trend).
        """
        if len(candles_15m) < 3:
            return 0.50

        window = candles_15m[-9:] if len(candles_15m) >= 9 else candles_15m
        closes = [float(b.get("close", 0.0)) for b in window]
        if not all(_finite_positive(c) for c in closes):
            return 0.50

        net_change = abs(closes[-1] - closes[0])
        gross_volatility = sum(abs(closes[j] - closes[j - 1]) for j in range(1, len(closes)))

        if gross_volatility <= 0:
            return 0.0

        return round(min(1.0, net_change / gross_volatility), 3)

    @staticmethod
    def calculate_session_vwap(candles_15m: Sequence[Mapping[str, Any]]) -> Tuple[float, int]:
        """
        Computes typical price intraday cumulative VWAP.
        """
        cum_turnover = 0.0
        cum_volume = 0

        for bar in candles_15m:
            h = float(bar.get("high", 0.0))
            l = float(bar.get("low", 0.0))
            c = float(bar.get("close", 0.0))
            v = int(bar.get("volume", 0))

            if not (_finite_positive(h) and _finite_positive(l) and _finite_positive(c) and v >= 0):
                continue

            typ = (h + l + c) / 3.0
            cum_turnover += typ * v
            cum_volume += v

        if cum_volume <= 0:
            return 0.0, 0

        return round(cum_turnover / cum_volume, 2), cum_volume

    @classmethod
    def extract_features(
        cls,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        bucket_median_vol: float,
        prev_close: Optional[float] = None,
    ) -> Optional[BarFeatures]:
        """
        Extracts all standard features for the latest completed bar.
        """
        if not candles_15m:
            return None

        latest = candles_15m[-1]
        h = float(latest.get("high", 0.0))
        l = float(latest.get("low", 0.0))
        c = float(latest.get("close", 0.0))
        o = float(latest.get("open", c))
        v = int(latest.get("volume", 0))
        ts = str(latest.get("timestamp", ""))

        if not (_finite_positive(h) and _finite_positive(l) and _finite_positive(c) and h >= l):
            return None

        typ = cls.calculate_typical_price(h, l, c)
        atr = cls.calculate_atr20(candles_15m, prev_close=prev_close)
        rvol = cls.calculate_rvol(v, bucket_median_vol)
        er8 = cls.calculate_er8(candles_15m)
        vwap, _ = cls.calculate_session_vwap(candles_15m)

        return BarFeatures(
            symbol=symbol,
            bar_index=len(candles_15m) - 1,
            timestamp=ts,
            open_p=o,
            high_p=h,
            low_p=l,
            close_p=c,
            volume=v,
            typical_price=typ,
            atr20=atr,
            rvol=rvol,
            er8=er8,
            session_vwap=vwap,
            is_above_vwap=(c >= vwap) if vwap > 0 else True,
        )

    @staticmethod
    def calculate_market_breadth(symbol_features: Mapping[str, BarFeatures]) -> float:
        """
        Calculates market breadth B_t: fraction of observed universe trading above session VWAP.
        """
        valid = [f for f in symbol_features.values() if f.session_vwap > 0]
        if not valid:
            return 0.50
        above_count = sum(1 for f in valid if f.is_above_vwap)
        return round(above_count / float(len(valid)), 2)
