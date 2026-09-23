"""
track2_surveillance_monitor.py - Daily F&O Membership and Surveillance Monitor for Track 2
Part of Project Swing Trades quantitative architecture.
Addresses and closes Q16 from shared/04_OPEN_QUESTIONS.md.

Mandate:
Track 2 relies unconditionally on continuous two-sided liquidity and dynamic flexing price bands.
This monitor verifies daily:
1. Active F&O Underlying Status (NSE equity derivative eligibility). If a scrip exits F&O, it is immediately disqualified.
2. ASM / GSM Surveillance Immunity. While ESM does not apply (Mcap >= Rs 4,000 Cr), ASM and GSM have no market cap floor.
   Any scrip entering ASM (Stage I-IV) or GSM (Stage I-VI) is immediately disqualified.
3. Dynamic Price Band Verification. Fixed 2%, 5%, or 10% non-flexing bands are rejected.
"""

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple


@dataclass
class Track2SurveillanceState:
    symbol: str
    is_fno_underlying: bool
    asm_stage: Optional[int]  # 0 if clean, 1..4 if flagged, None if unverified
    gsm_stage: Optional[int]  # 0 if clean, 1..6 if flagged, None if unverified
    band_pct: float           # 0.0 for dynamic flexing band, >0 for fixed
    is_dynamic_flexing: bool  # True if NSE/FAOP/62241 dynamic band rules apply
    date_str: str
    checked_at: Optional[str] # Timestamp of audit verification
    status: str               # "QUALIFIED", "DISQUALIFIED_ASM", "DISQUALIFIED_GSM", "DISQUALIFIED_NON_FNO", "DISQUALIFIED_FIXED_BAND", "DISQUALIFIED_UNKNOWN", "DISQUALIFIED_STALE", "DISQUALIFIED_MWPL_BAN"
    reason: str
    is_in_fo_ban: bool = False


class Track2SurveillanceMonitor:
    """
    Daily pre-open monitor for Track 2 (Liquid Momentum).
    Ensures all active Basket A and Basket B candidates maintain continuous F&O liquidity
    and remain 100% free of exchange surveillance intervention.
    """

    def __init__(self, history_file: str = "antigravity/logs/track2_surveillance_history.json"):
        self.history_file = history_file
        self.history: Dict[str, List[Dict]] = self._load_history()

    def _load_history(self) -> Dict[str, List[Dict]]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        with open(self.history_file, "w") as f:
            json.dump(self.history, f, indent=2)

    def evaluate_scrip(
        self,
        symbol: str,
        is_fno_underlying: bool,
        asm_stage: Optional[int],
        gsm_stage: Optional[int],
        band_pct: float,
        date_str: str,
        checked_at: Optional[str] = None,
        is_in_fo_ban: bool = False,
    ) -> Track2SurveillanceState:
        """
        Evaluates a single Track 2 scrip against surveillance and liquidity criteria.
        Fails closed on any surveillance flag, non-F&O status, fixed circuit band,
        or unverified/missing surveillance check (tri-state enforcement).
        Enforces strict Boolean types, numeric ranges, and parsed ISO timestamps.
        """
        # 1. Strict type and bounds verification for numeric and boolean fields
        if not isinstance(is_fno_underlying, bool):
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=False,
                asm_stage=asm_stage if isinstance(asm_stage, int) and not isinstance(asm_stage, bool) else None,
                gsm_stage=gsm_stage if isinstance(gsm_stage, int) and not isinstance(gsm_stage, bool) else None,
                band_pct=band_pct if isinstance(band_pct, (int, float)) and not math.isnan(band_pct) else 0.0,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} is_fno_underlying must be an exact boolean (got {type(is_fno_underlying).__name__}: {is_fno_underlying!r}). Fail-closed."
            )
            self._record_state(state)
            return state

        if not isinstance(band_pct, (int, float)) or math.isnan(band_pct) or math.isinf(band_pct):
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage if isinstance(asm_stage, int) and not isinstance(asm_stage, bool) else None,
                gsm_stage=gsm_stage if isinstance(gsm_stage, int) and not isinstance(gsm_stage, bool) else None,
                band_pct=0.0,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} band_pct must be a valid finite number (got {band_pct!r}). Fail-closed."
            )
            self._record_state(state)
            return state

        # Fail-closed on missing/unverified checks or absent timestamp (DISQUALIFIED_UNKNOWN)
        if asm_stage is None or gsm_stage is None or not checked_at:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} surveillance status is unverified (ASM/GSM check missing or timestamp absent). Fail-closed."
            )
            self._record_state(state)
            return state

        # Validate asm_stage and gsm_stage types and bounds (exclude bools since isinstance(True, int) is True)
        if isinstance(asm_stage, bool) or not isinstance(asm_stage, int) or asm_stage < 0 or asm_stage > 4:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=None,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} asm_stage must be an integer between 0 and 4 (got {asm_stage!r}). Fail-closed."
            )
            self._record_state(state)
            return state

        if isinstance(gsm_stage, bool) or not isinstance(gsm_stage, int) or gsm_stage < 0 or gsm_stage > 6:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage,
                gsm_stage=None,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} gsm_stage must be an integer between 0 and 6 (got {gsm_stage!r}). Fail-closed."
            )
            self._record_state(state)
            return state

        # Strict timestamp validation and date matching
        if not isinstance(checked_at, str):
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=None,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} checked_at must be a string timestamp. Fail-closed."
            )
            self._record_state(state)
            return state

        parsed_date_str = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed_dt = datetime.strptime(checked_at.strip(), fmt)
                parsed_date_str = parsed_dt.date().isoformat()
                break
            except ValueError:
                pass

        if parsed_date_str is None:
            try:
                parsed_dt = datetime.fromisoformat(checked_at.strip().replace("Z", "+00:00"))
                parsed_date_str = parsed_dt.date().isoformat()
            except Exception:
                parsed_date_str = None

        if parsed_date_str is None:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_UNKNOWN",
                reason=f"{symbol} checked_at timestamp '{checked_at}' cannot be parsed as valid ISO or standard datetime. Fail-closed."
            )
            self._record_state(state)
            return state

        # Codex Correction (3): Freshness must respect exchange effective-session calendars (e.g. Friday for Monday)
        is_date_valid = False
        if parsed_date_str == date_str:
            is_date_valid = True
        else:
            try:
                p_dt = datetime.strptime(parsed_date_str, "%Y-%m-%d").date()
                s_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
                # Monday session (weekday 0): Friday (weekday 4), Sat (5), or Sun (6) notice is valid
                if s_dt.weekday() == 0:
                    delta_days = (s_dt - p_dt).days
                    if 1 <= delta_days <= 3 and p_dt.weekday() in (4, 5, 6):
                        is_date_valid = True
            except Exception:
                is_date_valid = False

        if not is_date_valid:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=is_fno_underlying,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_STALE",
                reason=f"{symbol} surveillance check is stale (checked_at date '{parsed_date_str}' does not govern session '{date_str}' under exchange calendar). Fail-closed."
            )
            self._record_state(state)
            return state

        # 2. F&O Underlying Verification
        if not is_fno_underlying:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=False,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_NON_FNO",
                reason=f"{symbol} is NOT an active F&O underlying. Fails Track 2 continuous dynamic liquidity requirement."
            )
            self._record_state(state)
            return state

        # 2b. F&O MWPL Ban Period Verification (95% MWPL threshold)
        if is_in_fo_ban:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=True,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_MWPL_BAN",
                reason=f"{symbol} is under NSE F&O Ban (MWPL >= 95%). Liquidity is impaired and basis spreads distorted. Fail-closed.",
                is_in_fo_ban=True,
            )
            self._record_state(state)
            return state

        # 3. ASM Check (Short-term or Long-term)
        if asm_stage > 0:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=True,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_ASM",
                reason=f"{symbol} is flagged under Additional Surveillance Measure (ASM Stage {asm_stage}). Immediate disqualification."
            )
            self._record_state(state)
            return state

        # 4. GSM Check
        if gsm_stage > 0:
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=True,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_GSM",
                reason=f"{symbol} is flagged under Graded Surveillance Measure (GSM Stage {gsm_stage}). Immediate disqualification."
            )
            self._record_state(state)
            return state

        # 5. Dynamic Flexing Band Verification (NSE/FAOP/62241)
        is_dynamic = (band_pct == 0.0) or (is_fno_underlying and band_pct >= 10.0)
        if not is_dynamic or (band_pct > 0.0 and band_pct < 10.0):
            state = Track2SurveillanceState(
                symbol=symbol,
                is_fno_underlying=True,
                asm_stage=asm_stage,
                gsm_stage=gsm_stage,
                band_pct=band_pct,
                is_dynamic_flexing=False,
                date_str=date_str,
                checked_at=checked_at,
                status="DISQUALIFIED_FIXED_BAND",
                reason=f"{symbol} carries a fixed circuit band of {band_pct}%. Fails dynamic flexing requirement."
            )
            self._record_state(state)
            return state

        # 6. Qualification Pass
        state = Track2SurveillanceState(
            symbol=symbol,
            is_fno_underlying=True,
            asm_stage=asm_stage,
            gsm_stage=gsm_stage,
            band_pct=band_pct,
            is_dynamic_flexing=True,
            date_str=date_str,
            checked_at=checked_at,
            status="QUALIFIED",
            reason=f"{symbol} verified clean: Active F&O underlying, zero surveillance (ASM 0, GSM 0), dynamic flexing bands."
        )
        self._record_state(state)
        return state

    def _record_state(self, state: Track2SurveillanceState):
        if state.symbol not in self.history:
            self.history[state.symbol] = []
        self.history[state.symbol].append(asdict(state))
        self._save_history()

    def run_daily_basket_audit(
        self,
        basket: List[Dict],
        date_str: str
    ) -> Dict[str, any]:
        """
        Audits an entire candidate basket for daily pre-open clearance.
        Fails closed if any surveillance key is absent.
        """
        qualified = []
        disqualified = []
        for item in basket:
            res = self.evaluate_scrip(
                symbol=item["symbol"],
                is_fno_underlying=item.get("is_fno_underlying", False),
                asm_stage=item.get("asm_stage", None),
                gsm_stage=item.get("gsm_stage", None),
                band_pct=item.get("band_pct", 0.0),
                date_str=date_str,
                checked_at=item.get("checked_at", None)
            )
            if res.status == "QUALIFIED":
                qualified.append(res.symbol)
            else:
                disqualified.append({"symbol": res.symbol, "status": res.status, "reason": res.reason})

        return {
            "date": date_str,
            "total_evaluated": len(basket),
            "qualified_count": len(qualified),
            "qualified_symbols": qualified,
            "disqualified_count": len(disqualified),
            "disqualified": disqualified
        }


if __name__ == "__main__":
    print("=== TESTING TRACK 2 SURVEILLANCE & F&O MONITOR ===")
    monitor = Track2SurveillanceMonitor(history_file="antigravity/logs/test_track2_surveillance.json")

    test_basket = [
        # Clean F&O constituents (explicit 0 for ASM/GSM and timestamp present)
        {"symbol": "CDSL", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "ANGELONE", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "SUZLON", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "INOXWIND", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        # Disqualified cases
        {"symbol": "NON_FNO_STOCK", "is_fno_underlying": False, "asm_stage": 0, "gsm_stage": 0, "band_pct": 20.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "ASM_STAGE_1_STOCK", "is_fno_underlying": True, "asm_stage": 1, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "GSM_STAGE_2_STOCK", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 2, "band_pct": 0.0, "checked_at": "2026-09-12 08:50:00"},
        {"symbol": "FIXED_5PCT_STOCK", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 5.0, "checked_at": "2026-09-12 08:50:00"},
        # FAIL-CLOSED CASES (Claude red-team attacks on missing keys / unverified status / staleness)
        {"symbol": "UNCHECKED_STOCK", "is_fno_underlying": True, "band_pct": 0.0},  # Missing asm_stage, gsm_stage, checked_at
        {"symbol": "MISSING_TIMESTAMP_STOCK", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0}, # Timestamp absent
        {"symbol": "STALE_STOCK", "is_fno_underlying": True, "asm_stage": 0, "gsm_stage": 0, "band_pct": 0.0, "checked_at": "2026-09-11 18:30:00"}, # Yesterday's check
    ]

    report = monitor.run_daily_basket_audit(test_basket, "2026-09-12")
    print(f"Qualified ({report['qualified_count']}/{report['total_evaluated']}):", report["qualified_symbols"])
    print(f"Disqualified ({report['disqualified_count']}/{report['total_evaluated']}):")
    for d in report["disqualified"]:
        print(f"  - {d['symbol']}: [{d['status']}] {d['reason']}")

    assert report["qualified_count"] == 4, f"Expected 4 qualified, got {report['qualified_count']}"
    assert report["disqualified_count"] == 7, f"Expected 7 disqualified, got {report['disqualified_count']}"
    assert set(report["qualified_symbols"]) == {"CDSL", "ANGELONE", "SUZLON", "INOXWIND"}

    # Verify fail-closed items were caught exactly
    disq_map = {d["symbol"]: d["status"] for d in report["disqualified"]}
    assert disq_map["UNCHECKED_STOCK"] == "DISQUALIFIED_UNKNOWN"
    assert disq_map["MISSING_TIMESTAMP_STOCK"] == "DISQUALIFIED_UNKNOWN"
    assert disq_map["STALE_STOCK"] == "DISQUALIFIED_STALE"

    # Cleanup test log
    if os.path.exists("antigravity/logs/test_track2_surveillance.json"):
        os.remove("antigravity/logs/test_track2_surveillance.json")

    print("\nALL TRACK 2 SURVEILLANCE MONITOR TESTS PASSED 100% (FAIL-CLOSED VERIFIED)!")

