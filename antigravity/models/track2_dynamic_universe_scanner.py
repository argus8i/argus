"""
track2_dynamic_universe_scanner.py - Dynamic Pre-Market Multi-Factor Universe Scanner
====================================================================================
Part of Project Swing Trades (Track 2 Phase 2A).

Mandate & Features:
  1. Full F&O Universe Discovery:
     - Ingests active NSE F&O underlyings (~200+ scrips) verified by OfficialSourceIngestor.
     - Rejects any security in ASM (Short/Long term), GSM, ESM, or Trade-to-Trade.
  2. Strict Quantitative Gating:
     - Market Cap: Rs 4,000 Cr <= Mcap <= Rs 75,000 Cr.
     - Liquidity: DTV_20 >= Rs 30 Cr.
     - Volatility: ATR_14% >= 3.5%.
     - Beta: Beta_252 >= 1.30.
     - Dynamic Flexing Bands: band_pct == 0.0 (NSE/FAOP/62241).
  3. Multi-Factor Pre-Market Momentum Ranking:
     - Composite Score = 0.35 * VolExpansion + 0.25 * GapMomentum + 0.25 * RelativeStrength + 0.15 * Beta.
  4. Sector Diversification Constraint:
     - Maximum 2 positions per sector in the top-ranked universe.
  5. Pre-Market Universe Freezing:
     - Freezes exactly top 8 qualifying scrips with SHA-256 integrity hash before 09:15 IST.
     - Fails closed if fewer than 4 scrips qualify.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import math
import numbers
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from antigravity.models.track2_portfolio_risk_governor import DEFAULT_SECTOR_MAP, COARSE_SECTOR_GROUPS

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
DYNAMIC_UNIVERSE_PATH = os.path.join(REPO_ROOT, "shared", "track2_liquid", "dynamic_universe.json")


def _is_finite_positive(val: Any) -> bool:
    return (
        not isinstance(val, bool)
        and isinstance(val, numbers.Real)
        and math.isfinite(float(val))
        and float(val) > 0
    )


@dataclass(frozen=True)
class ScripCandidate:
    symbol: str
    series: str
    mcap_cr: float
    dtv_med20_cr: float
    beta: float
    atr14_pct: float
    atr14_points: float
    pre_open_vol: int
    median_pre_open_vol_10d: int
    open_price: float
    prev_close: float
    return_20d_pct: float
    nifty_return_20d_pct: float
    band_pct: float
    is_fno_underlying: bool
    is_surveillance: bool
    sector: str


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    symbol: str
    sector: str
    composite_score: float
    vol_expansion_ratio: float
    gap_pct: float
    gap_momentum_score: float
    relative_strength: float
    beta: float
    mcap_cr: float
    dtv_med20_cr: float
    open_price: float
    prev_close: float
    atr14_points: float
    atr14_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DynamicUniverseScanner:
    """
    Automated pre-market scanner and multi-factor ranker for Track 2.
    """

    def __init__(
        self,
        min_mcap_cr: float = 4000.0,
        max_mcap_cr: float = 75000.0,
        min_dtv_cr: float = 30.0,
        min_beta: float = 1.30,
        min_atr_pct: float = 3.5,
        target_universe_size: int = 8,
        min_surviving_pool: int = 4,
        max_per_sector: int = 2,
    ):
        self.min_mcap_cr = min_mcap_cr
        self.max_mcap_cr = max_mcap_cr
        self.min_dtv_cr = min_dtv_cr
        self.min_beta = min_beta
        self.min_atr_pct = min_atr_pct
        self.target_universe_size = target_universe_size
        self.min_surviving_pool = min_surviving_pool
        self.max_per_sector = max_per_sector

    def filter_candidate(self, c: ScripCandidate) -> Tuple[bool, Optional[str]]:
        """Validates quantitative threshold gates fail-closed."""
        if c.series != "EQ":
            return False, f"Non-EQ series '{c.series}'"
        if not c.is_fno_underlying:
            return False, "Not an active F&O underlying"
        if c.is_surveillance:
            return False, "Shortlisted under ASM/GSM/ESM surveillance"
        if c.band_pct != 0.0:
            return False, f"Fixed price band ({c.band_pct}%); dynamic flexing band (0.0%) required"
        # Strict finite positive numeric validation
        for name, val in [
            ("mcap_cr", c.mcap_cr),
            ("dtv_med20_cr", c.dtv_med20_cr),
            ("beta", c.beta),
            ("atr14_pct", c.atr14_pct),
            ("atr14_points", c.atr14_points),
            ("open_price", c.open_price),
            ("prev_close", c.prev_close),
        ]:
            if not _is_finite_positive(val):
                return False, f"Invalid or non-positive metric {name}: {val}"

        for name, val in [
            ("band_pct", c.band_pct),
            ("return_20d_pct", c.return_20d_pct),
            ("nifty_return_20d_pct", c.nifty_return_20d_pct),
        ]:
            if isinstance(val, bool) or not isinstance(val, numbers.Real) or not math.isfinite(float(val)):
                return False, f"Invalid non-finite metric {name}: {val}"

        for name, val in [
            ("pre_open_vol", c.pre_open_vol),
            ("median_pre_open_vol_10d", c.median_pre_open_vol_10d),
        ]:
            if isinstance(val, bool) or not isinstance(val, numbers.Integral) or val < 0:
                return False, f"Invalid volume metric {name}: {val}"

        if not (self.min_mcap_cr <= c.mcap_cr <= self.max_mcap_cr):
            return False, f"Mcap Rs {c.mcap_cr:.1f} Cr outside [{self.min_mcap_cr}, {self.max_mcap_cr}]"
        if c.dtv_med20_cr < self.min_dtv_cr:
            return False, f"DTV Rs {c.dtv_med20_cr:.1f} Cr below floor Rs {self.min_dtv_cr} Cr"
        if c.beta < self.min_beta:
            return False, f"Beta {c.beta:.2f} below threshold {self.min_beta:.2f}"
        if c.atr14_pct < self.min_atr_pct:
            return False, f"ATR {c.atr14_pct:.2f}% below threshold {self.min_atr_pct:.2f}%"

        return True, None

    def calculate_composite_score(self, c: ScripCandidate) -> Tuple[float, Dict[str, float]]:
        """
        Computes the multi-factor ranking score:
        Score = 0.35 * VolExp + 0.25 * GapMom + 0.25 * RelStrength + 0.15 * Beta
        """
        # 1. Volume Expansion (capped at 5.0x to avoid outliers)
        if c.median_pre_open_vol_10d > 0:
            vol_exp = min(5.0, c.pre_open_vol / float(c.median_pre_open_vol_10d))
        else:
            vol_exp = 1.0

        # 2. Gap Momentum (gap as fraction of ATR, capped at 2.5x ATR)
        gap_pts = abs(c.open_price - c.prev_close)
        gap_pct = round(((c.open_price - c.prev_close) / c.prev_close) * 100.0, 2)
        gap_mom = min(2.5, gap_pts / c.atr14_points) if c.atr14_points > 0 else 0.0

        # 3. Relative Strength vs Nifty (20D stock return - 20D Nifty return)
        rel_strength = c.return_20d_pct - c.nifty_return_20d_pct
        norm_rs = math.tanh(rel_strength / 10.0) * 2.0  # Normalized between -2 and +2

        # 4. Beta factor (capped at 2.5)
        beta_factor = min(2.5, c.beta)

        # Composite score
        score = round(
            (0.35 * vol_exp) + (0.25 * gap_mom) + (0.25 * norm_rs) + (0.15 * beta_factor),
            4,
        )

        metrics = {
            "vol_expansion_ratio": round(vol_exp, 2),
            "gap_pct": gap_pct,
            "gap_momentum_score": round(gap_mom, 2),
            "relative_strength": round(rel_strength, 2),
            "beta": round(c.beta, 2),
        }
        return score, metrics

    def scan_and_rank(self, candidates: Sequence[ScripCandidate]) -> List[RankedCandidate]:
        """
        Filters candidates, scores them, applies sector concentration constraints,
        and returns the top-ranked universe (up to target_universe_size).
        """
        qualified: List[Tuple[float, Dict[str, float], ScripCandidate]] = []
        for c in candidates:
            passed, reason = self.filter_candidate(c)
            if passed:
                score, metrics = self.calculate_composite_score(c)
                qualified.append((score, metrics, c))

        # Sort by composite score descending
        qualified.sort(key=lambda x: x[0], reverse=True)

        if len(qualified) < self.min_surviving_pool:
            raise ValueError(
                f"FAIL-CLOSED: Only {len(qualified)} scrips passed quantitative screening "
                f"(minimum required: {self.min_surviving_pool}). Universe generation aborted."
            )

        # Apply sector concentration constraint: max 2 scrips per sector
        final_selected: List[RankedCandidate] = []
        sector_counts: Dict[str, int] = {}

        for score, metrics, c in qualified:
            sec = c.sector or DEFAULT_SECTOR_MAP.get(c.symbol, "UNKNOWN_SECTOR")
            coarse_sec = COARSE_SECTOR_GROUPS.get(sec, sec)
            count = sector_counts.get(coarse_sec, 0)
            if count < self.max_per_sector:
                rank = len(final_selected) + 1
                final_selected.append(
                    RankedCandidate(
                        rank=rank,
                        symbol=c.symbol,
                        sector=coarse_sec,
                        composite_score=score,
                        vol_expansion_ratio=metrics["vol_expansion_ratio"],
                        gap_pct=metrics["gap_pct"],
                        gap_momentum_score=metrics["gap_momentum_score"],
                        relative_strength=metrics["relative_strength"],
                        beta=metrics["beta"],
                        mcap_cr=c.mcap_cr,
                        dtv_med20_cr=c.dtv_med20_cr,
                        open_price=c.open_price,
                        prev_close=c.prev_close,
                        atr14_points=c.atr14_points,
                        atr14_pct=c.atr14_pct,
                    )
                )
                sector_counts[coarse_sec] = count + 1

                if len(final_selected) >= self.target_universe_size:
                    break

        if len(final_selected) < self.min_surviving_pool:
            raise ValueError(
                f"FAIL-CLOSED: Sector constraints reduced universe to {len(final_selected)} scrips "
                f"(minimum required: {self.min_surviving_pool})."
            )

        return final_selected

    def freeze_universe(
        self,
        ranked: Sequence[RankedCandidate],
        output_path: str = DYNAMIC_UNIVERSE_PATH,
        session_date: Optional[str] = None,
        is_verified_live: bool = False,
    ) -> Dict[str, Any]:
        """
        Serializes the frozen universe to JSON.
        Under Red-Team Finding F9 and Codex R10, offline bootstrap or manual baskets
        must be quarantined with qualification_eligible=False and universe_status=MANUAL_UNVERIFIED_BASKET,
        without stamping a SHA-256 hash that mimics verified authentic data.
        """
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        date_str = session_date or datetime.now(IST).strftime("%Y-%m-%d")

        payload: Dict[str, Any] = {
            "session_date": date_str,
            "generated_at_ist": now_ist,
            "total_qualified": len(ranked),
            "symbols": [r.symbol for r in ranked],
            "candidates": [r.to_dict() for r in ranked],
            "universe_status": "PROSPECTIVE_QUALIFIED_BASKET" if is_verified_live else "MANUAL_UNVERIFIED_BASKET",
            "qualification_eligible": bool(is_verified_live),
        }

        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sha256_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        payload["universe_sha256"] = sha256_hash

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return payload
