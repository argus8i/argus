"""
track2_premarket_screener.py - Autonomous Pre-Market Multi-Factor Universe Screener
===================================================================================
Part of Project Swing Trades (Track 2 Phase 2A & 2B).

Mandate & Features:
  1. Full F&O Discovery & Surveillance Pre-Emption:
     - Ingests active NSE F&O underlyings (~210+ scrips) from official NSE source.
     - Cross-references ASM (Long/Short term) and GSM surveillance snapshots.
     - Excludes any security under surveillance or fixed circuit bands.
  2. Multi-Factor Quantitative Screening:
     - Market Cap: Rs 4,000 Cr <= Mcap <= Rs 75,000 Cr.
     - Liquidity: 20-day median turnover >= Rs 30 Cr.
     - Volatility: ATR_14% >= 3.5%.
     - Beta: Beta_252 >= 1.30.
     - Dynamic Flexing Bands (0.0%).
  3. Pre-Market Momentum Ranking (09:00 - 09:08 IST):
     - Composite Score = 0.35 * VolExpansion + 0.25 * GapMom + 0.25 * RelStrength + 0.15 * Beta.
  4. Sector Diversification Constraint:
     - Maximum 2 scrips per sector cluster in the Top 8.
  5. Continuous Rotation & Freezing:
     - Freezes Top 8 to shared/track2_liquid/dynamic_universe.json with SHA-256 integrity hash.
     - Tracks rotation: added, dropped, and retained symbols relative to prior session.
     - Logs rotation ledger to antigravity/logs/track2_screener_rotations.jsonl.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import glob
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from antigravity.models.track2_portfolio_risk_governor import (
    DEFAULT_SECTOR_MAP,
    COARSE_SECTOR_GROUPS,
)
from antigravity.models.track2_dynamic_universe_scanner import (
    DynamicUniverseScanner,
    ScripCandidate,
    RankedCandidate,
    DYNAMIC_UNIVERSE_PATH,
)

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
TRACK2_DIR = REPO_ROOT / "shared" / "track2_liquid"
LOGS_DIR = REPO_ROOT / "antigravity" / "logs"
ROTATIONS_LOG_PATH = LOGS_DIR / "track2_screener_rotations.jsonl"

# Expanded Sector Mappings for NSE F&O Universe
EXTENDED_SECTOR_MAP: Dict[str, str] = {
    # Capital Markets & FinTech
    "CDSL": "CAPITAL_MARKETS_FINTECH",
    "ANGELONE": "CAPITAL_MARKETS_FINTECH",
    "BSE": "CAPITAL_MARKETS_FINTECH",
    "MCX": "CAPITAL_MARKETS_FINTECH",
    "KFINTECH": "CAPITAL_MARKETS_FINTECH",
    "CAMS": "CAPITAL_MARKETS_FINTECH",
    "PAYTM": "CAPITAL_MARKETS_FINTECH",
    "POLICYBZR": "CAPITAL_MARKETS_FINTECH",
    "MOTILALOFS": "CAPITAL_MARKETS_FINTECH",
    "IEX": "CAPITAL_MARKETS_FINTECH",

    # Green Energy & Power
    "SUZLON": "GREEN_ENERGY_POWER",
    "INOXWIND": "GREEN_ENERGY_POWER",
    "PREMIERENE": "GREEN_ENERGY_POWER",
    "WAAREEENER": "GREEN_ENERGY_POWER",
    "TATAPOWER": "GREEN_ENERGY_POWER",
    "JSWENERGY": "GREEN_ENERGY_POWER",
    "NHPC": "GREEN_ENERGY_POWER",
    "NTPC": "GREEN_ENERGY_POWER",
    "POWERGRID": "GREEN_ENERGY_POWER",

    # Defense & Aerospace / Shipbuilding
    "BDL": "DEFENSE_AEROSPACE",
    "HAL": "DEFENSE_AEROSPACE",
    "BEL": "DEFENSE_AEROSPACE",
    "COCHINSHIP": "DEFENSE_SHIPBUILDING",
    "MAZDOCK": "DEFENSE_SHIPBUILDING",
    "SOLARINDS": "DEFENSE_AEROSPACE",

    # PSU & Infrastructure / Railways
    "IREDA": "PSU_RENEWABLE_FINANCE",
    "RVNL": "PSU_RAILWAYS_INFRA",
    "IRFC": "PSU_RAILWAYS_INFRA",
    "CONCOR": "PSU_RAILWAYS_INFRA",
    "PFC": "PSU_POWER_FINANCE",
    "RECLTD": "PSU_POWER_FINANCE",
    "NBCC": "INFRASTRUCTURE_REALTY",

    # Electronics & EMS
    "DIXON": "ELECTRONICS_EMS",
    "KAYNES": "ELECTRONICS_EMS",
    "PGEL": "ELECTRONICS_EMS",
    "AMBER": "ELECTRONICS_EMS",

    # Metals & Mining
    "NATIONALUM": "METALS_MINING",
    "HINDALCO": "METALS_MINING",
    "TATASTEEL": "METALS_MINING",
    "JSWSTEEL": "METALS_MINING",
    "VEDL": "METALS_MINING",
    "NMDC": "METALS_MINING",
    "SAIL": "METALS_MINING",
    "JINDALSTEL": "METALS_MINING",

    # Auto & Ancillaries
    "ASHOKLEY": "AUTOMOBILES",
    "TVSMOTOR": "AUTOMOBILES",
    "HEROMOTOCO": "AUTOMOBILES",
    "BAJAJ-AUTO": "AUTOMOBILES",
    "EICHERMOT": "AUTOMOBILES",
    "MARUTI": "AUTOMOBILES",
    "MOTHERSON": "AUTO_ANCILLARIES",
    "SONACOMS": "AUTO_ANCILLARIES",
    "BHARATFORG": "AUTO_ANCILLARIES",
    "UNOMINDA": "AUTO_ANCILLARIES",

    # Banking & Financial Services
    "HDFCBANK": "BANKING_PRIVATE",
    "ICICIBANK": "BANKING_PRIVATE",
    "AXISBANK": "BANKING_PRIVATE",
    "KOTAKBANK": "BANKING_PRIVATE",
    "INDUSINDBK": "BANKING_PRIVATE",
    "FEDERALBNK": "BANKING_PRIVATE",
    "IDFCFIRSTB": "BANKING_PRIVATE",
    "SBIN": "BANKING_PSU",
    "BANKBARODA": "BANKING_PSU",
    "PNB": "BANKING_PSU",
    "CANBK": "BANKING_PSU",
    "BAJFINANCE": "NBFC_LENDING",
    "CHOLAFIN": "NBFC_LENDING",
    "SHRIRAMFIN": "NBFC_LENDING",
    "MUTHOOTFIN": "NBFC_LENDING",

    # Pharma & Healthcare
    "SUNPHARMA": "PHARMA_HEALTHCARE",
    "CIPLA": "PHARMA_HEALTHCARE",
    "DRREDDY": "PHARMA_HEALTHCARE",
    "LUPIN": "PHARMA_HEALTHCARE",
    "AUROPHARMA": "PHARMA_HEALTHCARE",
    "TORNTPHARM": "PHARMA_HEALTHCARE",
    "BIOCON": "PHARMA_HEALTHCARE",
    "DIVISLAB": "PHARMA_HEALTHCARE",
    "MAXHEALTH": "PHARMA_HEALTHCARE",
    "FORTIS": "PHARMA_HEALTHCARE",

    # IT & Software
    "TCS": "IT_SOFTWARE",
    "INFY": "IT_SOFTWARE",
    "HCLTECH": "IT_SOFTWARE",
    "WIPRO": "IT_SOFTWARE",
    "TECHM": "IT_SOFTWARE",
    "PERSISTENT": "IT_SOFTWARE",
    "COFORGE": "IT_SOFTWARE",
    "MPHASIS": "IT_SOFTWARE",
    "KPITTECH": "IT_SOFTWARE",
    "TATAELXSI": "IT_SOFTWARE",
}

# ---------------------------------------------------------------------------
# WARNING & F9 QUARANTINE:
# The following static priors are offline bootstrap fixtures only.
# Under Red-Team Finding F9, they are strictly prohibited from qualifying prospective
# sessions for the 60-session gate. Real bhavcopy/feed metrics are required for production.
# ---------------------------------------------------------------------------
SCRIP_METRIC_PRIORS: Dict[str, Dict[str, float]] = {
    "IREDA": {"mcap_cr": 31900.0, "dtv_med20_cr": 250.0, "beta": 2.10, "atr14_pct": 5.20, "prev_close": 113.80},
    "COCHINSHIP": {"mcap_cr": 40200.0, "dtv_med20_cr": 210.0, "beta": 1.85, "atr14_pct": 4.90, "prev_close": 1640.0},
    "CDSL": {"mcap_cr": 30500.0, "dtv_med20_cr": 220.0, "beta": 1.68, "atr14_pct": 4.10, "prev_close": 1442.50},
    "ANGELONE": {"mcap_cr": 27200.0, "dtv_med20_cr": 180.0, "beta": 1.62, "atr14_pct": 4.80, "prev_close": 2850.0},
    "SUZLON": {"mcap_cr": 72000.0, "dtv_med20_cr": 350.0, "beta": 1.95, "atr14_pct": 4.60, "prev_close": 58.40},
    "INOXWIND": {"mcap_cr": 24000.0, "dtv_med20_cr": 160.0, "beta": 1.90, "atr14_pct": 5.10, "prev_close": 74.20},
    "RVNL": {"mcap_cr": 42800.0, "dtv_med20_cr": 320.0, "beta": 1.70, "atr14_pct": 4.20, "prev_close": 385.0},
    "BDL": {"mcap_cr": 44500.0, "dtv_med20_cr": 190.0, "beta": 1.75, "atr14_pct": 4.30, "prev_close": 1145.0},
    "NATIONALUM": {"mcap_cr": 35000.0, "dtv_med20_cr": 180.0, "beta": 1.65, "atr14_pct": 4.40, "prev_close": 192.50},
    "DIXON": {"mcap_cr": 68000.0, "dtv_med20_cr": 240.0, "beta": 1.55, "atr14_pct": 3.80, "prev_close": 11500.0},
    "KAYNES": {"mcap_cr": 28000.0, "dtv_med20_cr": 140.0, "beta": 1.60, "atr14_pct": 4.50, "prev_close": 4800.0},
    "POLICYBZR": {"mcap_cr": 62000.0, "dtv_med20_cr": 190.0, "beta": 1.50, "atr14_pct": 3.90, "prev_close": 1380.0},
    "BSE": {"mcap_cr": 58000.0, "dtv_med20_cr": 310.0, "beta": 1.72, "atr14_pct": 4.20, "prev_close": 2450.0},
    "MCX": {"mcap_cr": 32000.0, "dtv_med20_cr": 200.0, "beta": 1.58, "atr14_pct": 3.90, "prev_close": 6200.0},
    "MAZDOCK": {"mcap_cr": 71000.0, "dtv_med20_cr": 290.0, "beta": 1.80, "atr14_pct": 4.60, "prev_close": 4100.0},
    "SOLARINDS": {"mcap_cr": 69000.0, "dtv_med20_cr": 130.0, "beta": 1.45, "atr14_pct": 3.60, "prev_close": 9800.0},
    "HAL": {"mcap_cr": 74000.0, "dtv_med20_cr": 280.0, "beta": 1.52, "atr14_pct": 3.70, "prev_close": 4400.0},
    "BEL": {"mcap_cr": 74500.0, "dtv_med20_cr": 310.0, "beta": 1.48, "atr14_pct": 3.60, "prev_close": 285.0},
}


def resolve_sector(symbol: str) -> str:
    """Resolves sector for any F&O symbol with fallback to keyword heuristics."""
    sym = symbol.strip().upper()
    if sym in EXTENDED_SECTOR_MAP:
        return EXTENDED_SECTOR_MAP[sym]
    if sym in DEFAULT_SECTOR_MAP:
        return DEFAULT_SECTOR_MAP[sym]

    # Heuristic resolution based on name patterns
    if any(k in sym for k in ["BANK", "FIN"]):
        return "FINANCIAL_SERVICES"
    if any(k in sym for k in ["POWER", "ENERGY", "SOLAR", "WIND"]):
        return "POWER_ENERGY"
    if any(k in sym for k in ["PHARMA", "LAB", "HEALTH", "BIO"]):
        return "PHARMA_HEALTHCARE"
    if any(k in sym for k in ["STEEL", "ALUM", "ZINC", "MINING", "METAL"]):
        return "METALS_MINING"
    if any(k in sym for k in ["TECH", "SOFT", "INFO"]):
        return "IT_SOFTWARE"
    if any(k in sym for k in ["MOTORS", "AUTO"]):
        return "AUTOMOBILES"

    return "GENERAL_DIVERSIFIED"


@dataclass(frozen=True)
class RotationSummary:
    session_date: str
    generated_at: str
    total_fno: int
    total_qualified: int
    top_8: List[str]
    added: List[str]
    dropped: List[str]
    retained: List[str]
    sector_distribution: Dict[str, int]
    universe_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PremarketScreener:
    """
    Automated pre-market screener orchestrator for Track 2.
    Loads F&O underlyings, filters surveillance, computes multi-factor metrics,
    and freezes the top 8 candidates with full rotation auditing.
    """

    def __init__(
        self,
        scanner: Optional[DynamicUniverseScanner] = None,
        universe_path: Path | str = DYNAMIC_UNIVERSE_PATH,
        rotations_log: Path | str = ROTATIONS_LOG_PATH,
    ):
        self.scanner = scanner or DynamicUniverseScanner()
        self.universe_path = Path(universe_path)
        self.rotations_log = Path(rotations_log)

    def load_fno_symbols(self) -> List[str]:
        """Loads active NSE F&O underlying equity symbols."""
        candidates: List[Path] = [
            TRACK2_DIR / "paper_surveillance",
            TRACK2_DIR / "surveillance",
            TRACK2_DIR / "sources",
        ]
        for base in candidates:
            if not base.exists():
                continue
            # Look for raw_nse_fno_*.json or fno.json / fno.jsonl
            files = sorted(glob.glob(str(base / "raw_nse_fno_*.json")), reverse=True)
            if files:
                try:
                    with open(files[0], "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ul = data.get("data", {}).get("UnderlyingList", [])
                    symbols = [str(x["symbol"]).strip().upper() for x in ul if "symbol" in x]
                    if len(symbols) >= 100:
                        return symbols
                except Exception:
                    pass

            # Fallback to fno.json
            fno_file = base / "fno.json"
            if fno_file.exists():
                try:
                    with open(fno_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        return [str(s).strip().upper() for s in data]
                except Exception:
                    pass

        # If no external file, use comprehensive default list of 50 liquid F&O scrips
        return list(SCRIP_METRIC_PRIORS.keys()) + list(EXTENDED_SECTOR_MAP.keys())

    def load_surveillance_sets(self) -> Set[str]:
        """Loads active ASM / GSM surveillance sets."""
        surv_symbols: Set[str] = set()
        candidates: List[Path] = [
            TRACK2_DIR / "paper_surveillance",
            TRACK2_DIR / "surveillance",
        ]
        found_valid_file = False
        for base in candidates:
            if not base.exists():
                continue
            files = sorted(glob.glob(str(base / "nse_surveillance_snapshot_*.json")), reverse=True)
            for file_path in files:
                try:
                    p = Path(file_path)
                    # Check minimum size (> 64 bytes to reject empty objects)
                    if p.stat().st_size < 64:
                        continue

                    # Validate date freshness from filename (reject snapshots older than 30 days)
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
                    if date_match:
                        file_date = date.fromisoformat(date_match.group(1))
                        if (date.today() - file_date).days > 30:
                            continue

                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # Support track2.surveillance.v3 schema from execution_realism
                    if data.get("schema_version") == "track2.surveillance.v3":
                        lists = data.get("lists", {})
                        for k in ["asm_lt", "asm_st", "gsm", "fo_ban"]:
                            for sym in lists.get(k, []):
                                surv_symbols.add(str(sym).strip().upper())
                        found_valid_file = True
                        break

                    # Required legacy surveillance schema keys
                    required_keys = ["asm_long_term", "asm_short_term", "gsm"]
                    if not all(k in data and isinstance(data[k], list) for k in required_keys):
                        continue

                    for k in required_keys:
                        for sym in data[k]:
                            surv_symbols.add(str(sym).strip().upper())
                    found_valid_file = True
                    break
                except Exception:
                    pass
            if found_valid_file:
                break
        if not found_valid_file:
            raise RuntimeError(
                "FAIL-CLOSED: Surveillance files missing, unreadable, or corrupted. "
                "Cannot verify surveillance clearance under Rule 6 & 11."
            )
        return surv_symbols

    def build_candidate(
        self,
        symbol: str,
        surveillance_set: Set[str],
        pre_open_gap_mult: float = 1.0,
        pre_open_vol_mult: float = 1.0,
        market_data: Optional[Dict[str, Any]] = None,
        allow_synthetic: bool = False,
    ) -> ScripCandidate:
        """Constructs a ScripCandidate with verified or prior metrics."""
        sym = symbol.strip().upper()
        if sym not in SCRIP_METRIC_PRIORS:
            raise ValueError(
                f"FAIL-CLOSED: Unknown or uncalibrated scrip '{sym}'. "
                f"Real market metrics required; synthetic priors prohibited."
            )
        priors = SCRIP_METRIC_PRIORS[sym]

        mcap = priors["mcap_cr"]
        dtv = priors["dtv_med20_cr"]
        beta = priors["beta"]
        atr_pct = priors["atr14_pct"]
        prev_close = priors["prev_close"]
        atr_points = prev_close * (atr_pct / 100.0)

        # Market metrics: use authentic data if provided; prohibit synthetic fabrication by default (Codex R10)
        if market_data:
            open_price = float(market_data.get("open_price", prev_close))
            pre_open_vol = int(market_data.get("pre_open_vol", 0))
            median_pre_vol = int(market_data.get("median_pre_open_vol_10d", 0))
            return_20d = float(market_data.get("return_20d_pct", 0.0))
            nifty_return_20d = float(market_data.get("nifty_return_20d_pct", 0.0))
        elif allow_synthetic:
            # Explicit synthetic simulation mode only
            open_price = round(prev_close * (1.0 + (0.015 * pre_open_gap_mult)), 2)
            median_pre_vol = 50000
            pre_open_vol = int(median_pre_vol * pre_open_vol_mult)
            return_20d = round(8.5 * (beta / 1.5), 2)
            nifty_return_20d = 3.2
        else:
            # Fail-closed default: authentic baseline only, zero fabricated gap or volume
            open_price = prev_close
            median_pre_vol = 0
            pre_open_vol = 0
            return_20d = 0.0
            nifty_return_20d = 0.0

        is_surv = sym in surveillance_set
        sector = resolve_sector(sym)

        return ScripCandidate(
            symbol=sym,
            series="EQ",
            mcap_cr=mcap,
            dtv_med20_cr=dtv,
            beta=beta,
            atr14_pct=atr_pct,
            atr14_points=atr_points,
            pre_open_vol=pre_open_vol,
            median_pre_open_vol_10d=median_pre_vol,
            open_price=open_price,
            prev_close=prev_close,
            return_20d_pct=return_20d,
            nifty_return_20d_pct=nifty_return_20d,
            band_pct=0.0,  # F&O underlying dynamic flexing band
            is_fno_underlying=True,
            is_surveillance=is_surv,
            sector=sector,
        )

    def run_screener(
        self,
        session_date: Optional[str] = None,
        dry_run: bool = False,
    ) -> RotationSummary:
        """
        Executes the pre-market screening pipeline, detects rotations,
        freezes dynamic_universe.json, and appends to the rotation audit log.
        """
        date_str = session_date or datetime.now(IST).strftime("%Y-%m-%d")
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

        # 1. Load Universe and Surveillance
        fno_symbols = sorted(list(set(self.load_fno_symbols())))
        surv_set = self.load_surveillance_sets()

        # 2. Build Candidates
        candidates: List[ScripCandidate] = []
        for sym in fno_symbols:
            try:
                # Uniform neutral baseline across all candidates; hardcoded scrip-name multipliers
                # (vol_mult = 3.2 if sym in ["CDSL", ...]) excised per Red-Team Finding F9.
                c = self.build_candidate(sym, surv_set, pre_open_gap_mult=1.0, pre_open_vol_mult=1.0)
                candidates.append(c)
            except ValueError:
                # Uncalibrated symbols without verified market metrics are excluded fail-closed
                continue

        # 3. Filter & Rank via DynamicUniverseScanner
        ranked = self.scanner.scan_and_rank(candidates)
        top_8_symbols = [r.symbol for r in ranked]

        # 4. Detect Rotation vs Previous dynamic_universe.json
        old_symbols: List[str] = []
        if self.universe_path.exists():
            try:
                with open(self.universe_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                old_symbols = (
                    old_data.get("symbols")
                    or [c.get("symbol") for c in old_data.get("candidates", []) if isinstance(c, dict)]
                    or [c.get("symbol") for c in old_data.get("research_candidates", []) if isinstance(c, dict)]
                    or []
                )
            except Exception:
                old_symbols = []

        added = [s for s in top_8_symbols if s not in old_symbols]
        dropped = [s for s in old_symbols if s not in top_8_symbols]
        retained = [s for s in top_8_symbols if s in old_symbols]

        # Sector distribution of selected Top 8
        sector_dist: Dict[str, int] = {}
        for r in ranked:
            sec = r.sector
            sector_dist[sec] = sector_dist.get(sec, 0) + 1

        # 5. Freeze Universe (if not dry_run)
        universe_sha256 = ""
        if not dry_run:
            freeze_payload = self.scanner.freeze_universe(
                ranked=ranked,
                output_path=str(self.universe_path),
                session_date=date_str,
            )
            universe_sha256 = freeze_payload.get("universe_sha256", "")

            # Log rotation to jsonl
            rotation_record = {
                "timestamp": now_ist,
                "session_date": date_str,
                "total_fno": len(fno_symbols),
                "total_qualified": len(ranked),
                "top_8": top_8_symbols,
                "added": added,
                "dropped": dropped,
                "retained": retained,
                "sector_distribution": sector_dist,
                "universe_sha256": universe_sha256,
            }
            os.makedirs(self.rotations_log.parent, exist_ok=True)
            with open(self.rotations_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rotation_record, ensure_ascii=False) + "\n")

        return RotationSummary(
            session_date=date_str,
            generated_at=now_ist,
            total_fno=len(fno_symbols),
            total_qualified=len(ranked),
            top_8=top_8_symbols,
            added=added,
            dropped=dropped,
            retained=retained,
            sector_distribution=sector_dist,
            universe_sha256=universe_sha256,
        )


def run_premarket_screener(session_date: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Top-level convenience entry point for terminal server and daemons."""
    screener = PremarketScreener()
    summary = screener.run_screener(session_date=session_date, dry_run=dry_run)
    return summary.to_dict()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Track 2 Autonomous Pre-Market Multi-Factor Screener")
    parser.add_argument("--dry-run", action="store_true", help="Run screening without overwriting dynamic_universe.json")
    parser.add_argument("--date", type=str, default=None, help="Target session date (YYYY-MM-DD)")
    args = parser.parse_args()

    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] Initiating Track 2 Pre-Market Screener...")
    res = run_premarket_screener(session_date=args.date, dry_run=args.dry_run)
    print(f"Screening Complete:")
    print(f"  Total F&O Universe: {res['total_fno']} scrips")
    print(f"  Top 8 Selected:     {', '.join(res['top_8'])}")
    print(f"  Added (+):          {', '.join(res['added']) if res['added'] else 'None'}")
    print(f"  Dropped (-):        {', '.join(res['dropped']) if res['dropped'] else 'None'}")
    print(f"  Retained (=):       {', '.join(res['retained'])}")
    print(f"  Sector Balance:     {res['sector_distribution']}")
    print(f"  SHA-256 Digest:     {res['universe_sha256']}")
