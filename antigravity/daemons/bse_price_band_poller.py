"""
BSE Price Band & Surveillance Poller
Fetches official daily circuit limits (UC/LC), band percentages, and ESM status
directly from BSE India public endpoints.
"""

import json
import os
import sys
from typing import Any, Dict
from datetime import datetime
import requests

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import (
    calculate_bse_circuit_bands,
    normalize_surveillance,
    SurveillanceStatus
)

WATCHLIST = {
    # Active Surveillance Micro-Caps
    "CROPSTER": "523105",
    "CHANDRIMA": "540829",
    "CCDL": "539091",
    "GATECH": "531723",
    # Prospective Observation Candidates for Monday (2026-09-14)
    "MOBIKWIK": "544305",
    "LOVABLE": "533343",
    "ANLON": "544497",
    "VEDAVAAG": "533056",
    "KINETIC": "500240"
}

DEFAULT_GROUPS = {
    "544305": "B",
    "533343": "B",
    "544497": "B",
    "533056": "B",
    "500240": "X",
    "523105": "T",
    "540829": "XT",
    "539091": "XT",
    "531723": "T"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*"
}

def fetch_price_band(scripcode: str) -> dict:
    url = f"https://api.bseindia.com/BseIndiaAPI/api/PriceBand/w?scripcode={scripcode}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data
    except Exception as e:
        print(f"Error fetching PriceBand for {scripcode}: {e}", file=sys.stderr)
    return {}

def fetch_scrip_header(scripcode: str) -> dict:
    url = f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode={scripcode}&seriesid="
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("Header", {})
    except Exception as e:
        print(f"Error fetching Header for {scripcode}: {e}", file=sys.stderr)
    return {}

def fetch_stock_trading(scripcode: str) -> dict:
    url = f"https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?scripcode={scripcode}&flag="
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Error fetching StockTrading for {scripcode}: {e}", file=sys.stderr)
    return {}

def clean_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def validate_and_reconcile_bands(
    symbol: str,
    raw_prev_close: Any,
    raw_uc: Any,
    raw_lc: Any,
    raw_band_pct: Any
) -> dict:
    """
    Validates circuit band arithmetic.
    Enforces LC <= prev_close <= UC.
    Reconciles with BSE Item 1.6 inward tick truncation.
    """
    prev_close = clean_float(raw_prev_close)
    band_pct = clean_float(raw_band_pct)
    api_uc = clean_float(raw_uc)
    api_lc = clean_float(raw_lc)

    calc_uc = None
    calc_lc = None
    if prev_close is not None and prev_close > 0 and band_pct is not None:
        calc_uc, calc_lc = calculate_bse_circuit_bands(prev_close, band_pct)

    # Sanity check: LC <= prev_close <= UC
    band_arithmetic_valid = True
    anomaly_reasons = []

    if prev_close is None:
        band_arithmetic_valid = False
        anomaly_reasons.append("MISSING_PREV_CLOSE")
    if band_pct is None:
        band_arithmetic_valid = False
        anomaly_reasons.append("MISSING_BAND_PCT")

    if prev_close is not None:
        if api_uc is not None and api_uc < prev_close:
            band_arithmetic_valid = False
            anomaly_reasons.append(f"UC ({api_uc}) < PrevClose ({prev_close})")
        if api_lc is not None and api_lc > prev_close:
            band_arithmetic_valid = False
            anomaly_reasons.append(f"LC ({api_lc}) > PrevClose ({prev_close})")

        # Check discrepancy against inward tick formula (both UC and LC symmetrically)
        if calc_uc is not None and api_uc is not None:
            if abs(calc_uc - api_uc) > 0.05 * prev_close:  # >5% discrepancy implies stale/corrupted API field
                band_arithmetic_valid = False
                anomaly_reasons.append(f"API UC ({api_uc}) differs sharply from inward tick calc ({calc_uc})")

        if calc_lc is not None and api_lc is not None:
            if abs(calc_lc - api_lc) > 0.05 * prev_close:  # >5% discrepancy implies stale/corrupted API field
                band_arithmetic_valid = False
                anomaly_reasons.append(f"API LC ({api_lc}) differs sharply from inward tick calc ({calc_lc})")

    # If API values are corrupt or fail basic ordering, fall back to calculated values per-field
    uc_valid = band_arithmetic_valid and api_uc is not None
    lc_valid = band_arithmetic_valid and api_lc is not None

    # Per-field override: if a specific field had a sharp discrepancy, invalidate only that field
    if api_uc is not None and calc_uc is not None and prev_close is not None:
        if abs(calc_uc - api_uc) > 0.05 * prev_close:
            uc_valid = False
    if api_lc is not None and calc_lc is not None and prev_close is not None:
        if abs(calc_lc - api_lc) > 0.05 * prev_close:
            lc_valid = False

    final_uc = api_uc if uc_valid else calc_uc
    final_lc = api_lc if lc_valid else calc_lc

    return {
        "prev_close": prev_close,
        "band_pct": band_pct,
        "api_upper_circuit": api_uc,
        "api_lower_circuit": api_lc,
        "calculated_upper_circuit": calc_uc,
        "calculated_lower_circuit": calc_lc,
        "effective_upper_circuit": final_uc,
        "effective_lower_circuit": final_lc,
        "band_arithmetic_valid": band_arithmetic_valid,
        "anomalies": anomaly_reasons,
        "source": "API_VERIFIED" if (uc_valid and lc_valid) else "CALCULATED_INWARD_TICK_FALLBACK"
    }

def get_bhavcopy_group(scripcode: str) -> Optional[str]:
    """Retrieves the latest verified settlement group for scripcode from official Bhavcopy history."""
    bhav_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "bhavcopy_history.csv"))
    if not os.path.exists(bhav_path):
        return None
    import csv
    try:
        with open(bhav_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            last_group = None
            for row in reader:
                if (row.get("scripcode") or "").strip() == str(scripcode).strip():
                    grp = (row.get("group") or "").strip()
                    if grp:
                        last_group = grp
            return last_group
    except Exception:
        return None

def poll_all() -> dict:
    results = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for symbol, scripcode in WATCHLIST.items():
        band_data = fetch_price_band(scripcode)
        header_data = fetch_scrip_header(scripcode)
        trading_data = fetch_stock_trading(scripcode) or {}
        
        raw_ltp = header_data.get("CurrVal") or header_data.get("LTP")
        raw_prev = header_data.get("PrevClose")
        raw_uc = band_data.get("UpperT")
        raw_lc = band_data.get("LowerT")
        raw_band_pct = band_data.get("PBpcUC") or band_data.get("PBpcLC")
        
        # 1. Surveillance resolution from BSE Header
        raw_surv = (
            header_data.get("EMSText")
            or header_data.get("ASMText")
            or header_data.get("GSMText")
            or header_data.get("Surv")
        )
        if not raw_surv and ("EMSText" in header_data or "ASMText" in header_data or "GSMText" in header_data):
            raw_surv = "NONE"

        # 2. Settlement group resolution (Header -> Bhavcopy -> Verified Default fallback)
        raw_group = header_data.get("Group")
        if not raw_group:
            raw_group = get_bhavcopy_group(scripcode)
        if not raw_group:
            raw_group = DEFAULT_GROUPS.get(str(scripcode), "B")

        # 3. Weekend / Post-Close base price rollover resolution
        # When PBdate is updated for the next session, UpperT/LowerT are calculated from today's closing price (LTP).
        base_prev_close = raw_prev
        try:
            flt_ltp = float(raw_ltp) if raw_ltp is not None else None
            flt_prev = float(raw_prev) if raw_prev is not None else None
            flt_uc = float(raw_uc) if raw_uc is not None else None
            flt_band = float(raw_band_pct) if raw_band_pct is not None else None
            if flt_ltp is not None and flt_prev is not None and flt_uc is not None and flt_band is not None:
                calc_from_ltp = round(flt_ltp * (1.0 + flt_band / 100.0), 2)
                calc_from_prev = round(flt_prev * (1.0 + flt_band / 100.0), 2)
                if abs(calc_from_ltp - flt_uc) < abs(calc_from_prev - flt_uc):
                    base_prev_close = raw_ltp
        except (ValueError, TypeError):
            pass

        # Validate bands
        band_check = validate_and_reconcile_bands(
            symbol=symbol,
            raw_prev_close=base_prev_close,
            raw_uc=raw_uc,
            raw_lc=raw_lc,
            raw_band_pct=raw_band_pct
        )

        # Normalize surveillance
        norm_surv = normalize_surveillance(raw_surv) if raw_surv else SurveillanceStatus.UNKNOWN

        # Parse Volume & Turnover
        raw_ttq = trading_data.get("TTQ", "")
        ttq_unit = trading_data.get("TTQin", "")
        traded_shares = None
        try:
            if "Lakh" in ttq_unit:
                traded_shares = int(float(raw_ttq.replace(",", "")) * 100000)
            elif raw_ttq:
                traded_shares = int(float(raw_ttq.replace(",", "")))
        except (ValueError, TypeError):
            traded_shares = None

        raw_turnover = trading_data.get("Turnover", "")
        turnover_lakh = None
        try:
            turnover_lakh = float(raw_turnover.replace(",", "")) if raw_turnover else None
        except (ValueError, TypeError):
            turnover_lakh = None

        raw_2w_avg = trading_data.get("TwoWkAvgQty", "")
        avg_shares_2w = None
        try:
            avg_shares_2w = int(float(raw_2w_avg.replace(",", "")) * 100000) if raw_2w_avg else None
        except (ValueError, TypeError):
            avg_shares_2w = None

        # Compute intraday trading time fraction (09:15 to 15:30 IST = 375 minutes)
        now_dt = datetime.now()
        market_open = now_dt.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)

        elapsed_mins = 375
        if now_dt < market_open:
            elapsed_fraction = 0.0
        elif now_dt > market_close:
            elapsed_fraction = 1.0
        else:
            elapsed_mins = max(1, int((now_dt - market_open).total_seconds() / 60))
            elapsed_fraction = min(1.0, elapsed_mins / 375.0)

        intraday_vs_2w_fraction = None
        projected_volume = None
        if traded_shares is not None:
            if elapsed_fraction > 0:
                projected_volume = int(traded_shares / elapsed_fraction)
            if avg_shares_2w is not None and avg_shares_2w > 0:
                intraday_vs_2w_fraction = round(traded_shares / avg_shares_2w, 2)

        # Record-level validity (ChatGPT Loophole 2)
        record_anomalies = list(band_check["anomalies"])
        record_valid = band_check["band_arithmetic_valid"]

        if norm_surv == SurveillanceStatus.UNKNOWN:
            record_valid = False
            record_anomalies.append("SURVEILLANCE_UNKNOWN")

        if not raw_group or not str(raw_group).strip():
            record_valid = False
            record_anomalies.append("GROUP_MISSING")

        if traded_shares is None and raw_ttq:
            record_valid = False
            record_anomalies.append("TTQ_UNIT_UNPARSEABLE")

        results[symbol] = {
            "scripcode": scripcode,
            "timestamp": now,
            "ltp": clean_float(raw_ltp),
            "prev_close": band_check["prev_close"],
            "upper_circuit": band_check["effective_upper_circuit"],
            "lower_circuit": band_check["effective_lower_circuit"],
            "band_pct": band_check["band_pct"],
            "band_date": band_data.get("PBdate"),
            "volume_shares": traded_shares,
            "turnover_lakh": turnover_lakh,
            "two_week_avg_volume_shares": avg_shares_2w,
            "session_elapsed_minutes": elapsed_mins if elapsed_fraction > 0 else 0,
            "projected_full_day_volume": projected_volume,
            "intraday_vs_2w_fraction": intraday_vs_2w_fraction,
            "wap": clean_float(trading_data.get("WAP")),
            "market_cap_cr": clean_float(trading_data.get("MktCapFull")),
            "surveillance": norm_surv.value,
            "raw_surveillance": raw_surv,
            "group": raw_group,
            "validation": {
                "record_valid": record_valid,
                "band_arithmetic_valid": band_check["band_arithmetic_valid"],
                "anomalies": record_anomalies,
                "band_source": band_check["source"],
                "api_uc": band_check["api_upper_circuit"],
                "api_lc": band_check["api_lower_circuit"]
            }
        }
    return results

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling BSE India official endpoints with inward-tick and volume validation...")
    data = poll_all()
    
    out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "shared")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "bse_daily_bands.json")
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print(f"Updated {out_path}:")
    for sym, d in data.items():
        v = d.get("validation", {})
        status_tag = "OK" if v.get("record_valid") else f"ANOMALY ({','.join(v.get('anomalies', []))})"
        vol_str = f"{d.get('volume_shares'):,}" if d.get('volume_shares') else "0"
        print(f"  {sym:<10} | LTP: {d.get('ltp')} | UC: {d.get('upper_circuit')} | LC: {d.get('lower_circuit')} | Vol: {vol_str:>10} | TO: Rs.{d.get('turnover_lakh')}L | Status: {status_tag}")

if __name__ == "__main__":
    main()
