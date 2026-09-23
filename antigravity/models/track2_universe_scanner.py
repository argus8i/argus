"""
track2_universe_scanner.py - Dynamic Pre-Market Universe Scanner & Ranker
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Quantitative Purpose:
- Dynamically ranks liquid NSE F&O underlyings at 09:15 IST instead of restricting to static 8 scrips.
- Applies strict quantitative filters: F&O dynamic bands, Mcap Rs 4k-75k Cr, DTV >= Rs 30 Cr, Beta >= 1.3.
- Ranks candidates by Relative Pre-Market Momentum Score:
    Score = (Pre-Open Vol / 10D Median Pre-Open Vol) * Beta * (1 + |Gap%| / 10.0)
- Fail-Closed: Never substitutes canonical names when the dynamic scan is missing or invalid.
"""

import json
import math
import numbers
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional, Any

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from antigravity.models.liquid_momentum_screener import LiquidScripSnapshot, LiquidMomentumEngine
from antigravity.models.track2_surveillance_monitor import Track2SurveillanceMonitor

DYNAMIC_UNIVERSE_PATH = os.path.join(REPO_ROOT, "shared", "track2_liquid", "dynamic_universe.json")

# Canonical Static Fallback Universe (Baskets A & B)
CANONICAL_FALLBACK_CANDIDATES = [
    # Basket A: Strict Screen Qualified (Institutional >= 15%)
    {
        "symbol": "CDSL",
        "ticker": "CDSL.NS",
        "basket": "A",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 28000.0,
        "dtv_med20_cr": 120.0,
        "beta": 1.45,
        "atr14_pct": 4.1,
        "inst_pct": 25.5,
    },
    {
        "symbol": "ANGELONE",
        "ticker": "ANGELONE.NS",
        "basket": "A",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 27200.0,
        "dtv_med20_cr": 180.0,
        "beta": 1.62,
        "atr14_pct": 4.8,
        "inst_pct": 34.0,
    },
    {
        "symbol": "SUZLON",
        "ticker": "SUZLON.NS",
        "basket": "A",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 62500.0,
        "dtv_med20_cr": 550.0,
        "beta": 1.39,
        "atr14_pct": 4.8,
        "inst_pct": 26.0,
    },
    {
        "symbol": "INOXWIND",
        "ticker": "INOXWIND.NS",
        "basket": "A",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 24000.0,
        "dtv_med20_cr": 160.0,
        "beta": 1.55,
        "atr14_pct": 4.5,
        "inst_pct": 18.2,
    },
    # Basket B: Sovereign PSU Satellite (Exempt from 15% inst. float due to GOI 70-75% ownership)
    {
        "symbol": "IREDA",
        "ticker": "IREDA.NS",
        "basket": "B",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 31900.0,
        "dtv_med20_cr": 250.0,
        "beta": 2.10,
        "atr14_pct": 5.2,
        "inst_pct": 4.9,
    },
    {
        "symbol": "RVNL",
        "ticker": "RVNL.NS",
        "basket": "B",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 42800.0,
        "dtv_med20_cr": 320.0,
        "beta": 1.70,
        "atr14_pct": 4.2,
        "inst_pct": 9.0,
    },
    {
        "symbol": "COCHINSHIP",
        "ticker": "COCHINSHIP.NS",
        "basket": "B",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 40200.0,
        "dtv_med20_cr": 210.0,
        "beta": 1.85,
        "atr14_pct": 4.9,
        "inst_pct": 9.8,
    },
    {
        "symbol": "BDL",
        "ticker": "BDL.NS",
        "basket": "B",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 45500.0,
        "dtv_med20_cr": 140.0,
        "beta": 1.50,
        "atr14_pct": 3.9,
        "inst_pct": 13.2,
    },
]

# Extended F&O Underlyings Pool for Dynamic Discovery
EXPANDED_FNO_UNIVERSE = CANONICAL_FALLBACK_CANDIDATES + [
    {
        "symbol": "TATACHEM",
        "ticker": "TATACHEM.NS",
        "basket": "DYNAMIC",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 26500.0,
        "dtv_med20_cr": 110.0,
        "beta": 1.35,
        "atr14_pct": 3.8,
        "inst_pct": 38.5,
    },
    {
        "symbol": "POLICYBZR",
        "ticker": "POLICYBZR.NS",
        "basket": "DYNAMIC",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 72000.0,
        "dtv_med20_cr": 190.0,
        "beta": 1.48,
        "atr14_pct": 4.2,
        "inst_pct": 44.0,
    },
    {
        "symbol": "DIXON",
        "ticker": "DIXON.NS",
        "basket": "DYNAMIC",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 68000.0,
        "dtv_med20_cr": 220.0,
        "beta": 1.52,
        "atr14_pct": 3.9,
        "inst_pct": 39.0,
    },
    {
        "symbol": "NATIONALUM",
        "ticker": "NATIONALUM.NS",
        "basket": "DYNAMIC",
        "series": "EQ",
        "is_fno": True,
        "mcap_cr": 35000.0,
        "dtv_med20_cr": 180.0,
        "beta": 1.65,
        "atr14_pct": 4.4,
        "inst_pct": 21.0,
    }
]


@dataclass
class ScannedCandidate:
    symbol: str
    ticker: str
    basket: str
    mcap_cr: float
    dtv_med20_cr: float
    beta: float
    atr14_pct: float
    inst_pct: float
    pre_open_volume: int
    median_pre_open_volume: int
    gap_pct: float
    momentum_score: float
    qualified: bool
    research_eligible: bool = False
    rejection_reason: Optional[str] = None


class Track2UniverseScanner:
    """
    Ranks the most liquid, high-beta F&O underlyings dynamically at 09:15 IST.
    """

    def __init__(self, top_n: int = 8, surveillance_monitor: Optional[Track2SurveillanceMonitor] = None):
        self.top_n = top_n
        self.surveillance_monitor = surveillance_monitor or Track2SurveillanceMonitor()

    def rank_candidates(
        self,
        universe: Optional[List[Dict[str, Any]]] = None,
        pre_open_data: Optional[Dict[str, Dict[str, Any]]] = None,
        session_date: Optional[str] = None
    ) -> List[ScannedCandidate]:
        """
        Ranks candidate scrips by pre-market volume expansion * Beta * (1 + |Gap%|/10).
        Fails closed against ASM/GSM or non-F&O scrips.
        """
        raw_pool = universe if universe is not None else EXPANDED_FNO_UNIVERSE
        scored_list: List[ScannedCandidate] = []

        # Run surveillance check on candidate universe using explicit candidate surveillance metadata
        date_str = session_date or datetime.now().strftime("%Y-%m-%d")

        # If surveillance fields are missing from raw candidates, attempt official circular resolution
        if any(c.get("checked_at") is None for c in raw_pool):
            try:
                from antigravity.daemons.exchange_circular_poller import ExchangeCircularPoller
                poller = ExchangeCircularPoller(history_path=self.surveillance_monitor.history_file)
                p_rep = poller.poll_and_update(date_str)
                if p_rep.get("provenance", {}).get("verified"):
                    q_set = set(p_rep.get("qualified_symbols", []))
                    eff_date = p_rep.get("provenance", {}).get("effective_session_date", date_str)
                    for c in raw_pool:
                        if c.get("checked_at") is None and c["symbol"] in q_set:
                            c["is_fno"] = True
                            c["asm_stage"] = 0
                            c["gsm_stage"] = 0
                            c["band_pct"] = 0.0
                            c["checked_at"] = f"{eff_date} 08:45:00"
            except Exception:
                pass

        basket_items = [
            {
                "symbol": c["symbol"],
                "is_fno_underlying": c.get("is_fno", False),
                "asm_stage": c.get("asm_stage", None),
                "gsm_stage": c.get("gsm_stage", None),
                "band_pct": c.get("band_pct", None),
                "checked_at": c.get("checked_at", None)
            }
            for c in raw_pool
        ]
        surv_report = self.surveillance_monitor.run_daily_basket_audit(basket_items, date_str)
        surv_clean_symbols = set(surv_report.get("qualified_symbols", []))

        for c in raw_pool:
            sym = c["symbol"]
            ticker = c.get("ticker", f"{sym}.NS")
            basket = c.get("basket", "DYNAMIC")
            mcap = c.get("mcap_cr", 0.0)
            dtv = c.get("dtv_med20_cr", 0.0)
            beta = c.get("beta", 1.0)
            atr = c.get("atr14_pct", 3.0)
            inst = c.get("inst_pct", 0.0)
            is_fno = c.get("is_fno") is True

            numeric_values = (mcap, dtv, beta, atr, inst)
            if any(isinstance(v, bool) or not isinstance(v, numbers.Real)
                   or not math.isfinite(float(v)) for v in numeric_values):
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=0.0,
                    dtv_med20_cr=0.0, beta=0.0, atr14_pct=0.0, inst_pct=0.0,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason="DISQUALIFIED_INVALID_NUMERIC_DATA"))
                continue

            # Gate 1: Surveillance filter
            if sym not in surv_clean_symbols:
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                    dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason="DISQUALIFIED_SURVEILLANCE_FLAG"
                ))
                continue

            # Gate 2: F&O dynamic band requirement
            if not is_fno:
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                    dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason="DISQUALIFIED_NON_FNO"
                ))
                continue

            # Gate 3: Quantitative thresholds (Mcap Rs 4k - 75k Cr, DTV >= Rs 30 Cr, Beta >= 1.3)
            if not (4000.0 <= mcap <= 75000.0):
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                    dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason=f"DISQUALIFIED_MCAP ({mcap:.0f} Cr outside 4k-75k Cr)"
                ))
                continue

            if dtv < 30.0:
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                    dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason=f"DISQUALIFIED_DTV ({dtv:.1f} Cr < 30 Cr)"
                ))
                continue

            if beta < 1.30:
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                    dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                    pre_open_volume=0, median_pre_open_volume=0, gap_pct=0.0,
                    momentum_score=0.0, qualified=False,
                    rejection_reason=f"DISQUALIFIED_BETA ({beta:.2f} < 1.30)"
                ))
                continue

            # Pre-open volume and gap metrics
            p_data = (pre_open_data or {}).get(sym, {})
            pre_vol = p_data.get("pre_open_volume", 0)
            med_vol = p_data.get("median_pre_open_volume", 50000)
            gap = p_data.get("gap_pct", 0.0)

            if (any(isinstance(v, bool) or not isinstance(v, numbers.Real)
                    or not math.isfinite(float(v)) for v in (pre_vol, med_vol, gap))
                    or float(pre_vol) < 0 or float(med_vol) <= 0):
                scored_list.append(ScannedCandidate(
                    symbol=sym, ticker=ticker, basket=basket, mcap_cr=float(mcap),
                    dtv_med20_cr=float(dtv), beta=float(beta), atr14_pct=float(atr),
                    inst_pct=float(inst), pre_open_volume=0, median_pre_open_volume=0,
                    gap_pct=0.0, momentum_score=0.0, qualified=False,
                    rejection_reason="DISQUALIFIED_INVALID_PREOPEN_DATA"))
                continue

            # Calculate momentum score
            if pre_vol > 0 and med_vol > 0:
                vol_expansion = pre_vol / float(med_vol)
                score = round(vol_expansion * beta * (1.0 + (abs(gap) / 10.0)), 3)
            else:
                # Baseline static score when pre-market volume unmeasured
                score = round(beta * (atr / 3.0), 3)

            scored_list.append(ScannedCandidate(
                symbol=sym, ticker=ticker, basket=basket, mcap_cr=mcap,
                dtv_med20_cr=dtv, beta=beta, atr14_pct=atr, inst_pct=inst,
                pre_open_volume=pre_vol, median_pre_open_volume=med_vol,
                gap_pct=gap, momentum_score=score, qualified=False,
                research_eligible=True
            ))

        # Sort qualified scrips by momentum score descending
        qualified_scrips = [c for c in scored_list if c.research_eligible]
        qualified_scrips.sort(key=lambda x: x.momentum_score, reverse=True)

        return qualified_scrips

    def scan_and_save(
        self,
        output_path: str = DYNAMIC_UNIVERSE_PATH,
        universe: Optional[List[Dict[str, Any]]] = None,
        pre_open_data: Optional[Dict[str, Dict[str, Any]]] = None,
        session_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Scans universe, applies fallback if pool is thin, and saves to dynamic_universe.json.
        """
        session_date = session_date or datetime.now().strftime("%Y-%m-%d")
        qualified = self.rank_candidates(
            universe=universe, pre_open_data=pre_open_data, session_date=session_date)
        top_candidates = qualified[:self.top_n]
        is_fallback = False

        # Codex Finding 2: Zero or fewer qualifying candidates is a valid market regime outcome.
        # Fail-closed: Do NOT silently synthesize or inject unvetted canonical fallback names.
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session_date": session_date,
            "is_canonical_fallback": is_fallback,
            "total_qualified": 0,
            "research_only": True,
            "qualification_eligible": False,
            "qualification_reason": "PHASE1_HOLD_UNVERIFIED_UNIVERSE_PROVENANCE",
            "research_candidates": [dict(asdict(c), qualified=False) for c in top_candidates],
            "candidates": []
        }

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return payload


if __name__ == "__main__":
    print("Canonical writes are disabled from module self-tests. Run pytest instead.")
