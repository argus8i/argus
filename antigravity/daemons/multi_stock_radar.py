"""
multi_stock_radar.py - Unified 5-Stock Live Terminal Radar
==========================================================
Displays all 5 Monday focus candidates simultaneously in real-time:
  1. MOBIKWIK (544305)
  2. LOVABLE  (533343)
  3. ANLON    (544497)
  4. VEDAVAAG (533056)
  5. KINETIC  (500240)

Features:
  - Consumes live tick LTPs from Kite Web (shared/live_depth.json)
  - Integrates official BSE Circuit Bands & Surveillance (shared/bse_daily_bands.json)
  - Fetches 20-day historical average volume from SQLite (track1_historical.db)
  - Real-time quantitative calculations:
      * Upper Circuit Headroom %
      * Rule 2 Absolute Rs.10.00 Floor Gate
      * Rule 5 10-Day LC Drawdown Position Sizing (Risk Divisor = 0.401)
      * Rule 9 Market Participation Cap (15% volume over 2 sessions)
      * Surveillance Freeze & Band Tightening Alerts (Rule 6 & Rule 10)
  - Non-flickering, color-coded terminal dashboard updating every 1.5 seconds.
  - Strictly compliant with AGENTS.md (Track 1 Micro-Caps, Observation Only).
"""

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# This module is launched directly by launch_radar.bat, so the repo root must
# be on sys.path before any project import: otherwise Python's stdlib
# 'antigravity' easter-egg module wins resolution and the import fails with
# "'antigravity' is not a package". pytest masks this via conftest.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from antigravity.daemons.feed_validity import check_feed, usable_watchlist

SHARED_DIR = os.path.join(REPO_ROOT, "shared")
LOGS_DIR = os.path.join(REPO_ROOT, "antigravity", "logs")
DB_PATH = os.path.join(LOGS_DIR, "track1_historical.db")
LIVE_DEPTH_PATH = os.path.join(SHARED_DIR, "live_depth.json")
BSE_BANDS_PATH = os.path.join(SHARED_DIR, "bse_daily_bands.json")
RADAR_OUTPUT_PATH = os.path.join(SHARED_DIR, "multi_stock_radar.json")

# 5 Prospective Focus Candidates for Monday (2026-09-14)
CANDIDATES = [
    {
        "symbol": "MOBIKWIK",
        "scripcode": "544305",
        "name": "One Mobikwik Systems",
        "setup": "Rule 7 Flag Breakout",
        "risk_budget": 5000.0,
    },
    {
        "symbol": "LOVABLE",
        "scripcode": "533343",
        "name": "Lovable Lingerie Ltd.",
        "setup": "Base Accumulation",
        "risk_budget": 5000.0,
    },
    {
        "symbol": "ANLON",
        "scripcode": "544497",
        "name": "Anlon Healthcare Ltd.",
        "setup": "Post-IPO Re-accumulation",
        "risk_budget": 5000.0,
    },
    {
        "symbol": "VEDAVAAG",
        "scripcode": "533056",
        "name": "Vedavaag Systems Ltd.",
        "setup": "20% Band Breakout Base",
        "risk_budget": 5000.0,
    },
    {
        "symbol": "KINETIC",
        "scripcode": "500240",
        "name": "Kinetic Engineering",
        "setup": "ESM-1 Expansion Flag",
        "risk_budget": 5000.0,
    },
]

# ANSI Color Codes
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_DIM = "\033[2m"
CLR_GREEN = "\033[92m"
CLR_YELLOW = "\033[93m"
CLR_RED = "\033[91m"
CLR_CYAN = "\033[96m"
CLR_WHITE = "\033[97m"
CLR_BG_DARK = "\033[40m"
CLR_BG_BLUE = "\033[44m"


def load_json(filepath: str) -> dict:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_20d_avg_volumes() -> Dict[str, float]:
    """Retrieves 20-day historical average volume from track1_historical.db."""
    avg_vols = {}
    if not os.path.exists(DB_PATH):
        return avg_vols
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for cand in CANDIDATES:
            scripcode = cand["scripcode"]
            query = """
                SELECT AVG(volume) FROM (
                    SELECT volume FROM daily_quotes 
                    WHERE scripcode = ? 
                    ORDER BY trade_date DESC 
                    LIMIT 20
                )
            """
            c.execute(query, (scripcode,))
            row = c.fetchone()
            if row and row[0] is not None:
                avg_vols[cand["symbol"]] = float(row[0])
        conn.close()
    except Exception as e:
        print(f"Error querying SQLite 20d volume: {e}", file=sys.stderr)
    return avg_vols


def evaluate_radar_state(
    bse_data: dict,
    live_depth: dict,
    avg_20d_vols: Dict[str, float]
) -> Dict[str, Any]:
    """Evaluates real-time quantitative metrics for all 5 candidates simultaneously."""
    now_dt = datetime.now()
    results = {}
    
    # Extract watchlist LTPs from Kite Web
    # Gate the feed before trusting any price from it. This list is
    # stamped KITE_LIVE below, so a stale entry here becomes a live
    # quote downstream.
    feed_ok, feed_reason = check_feed(live_depth)
    kite_wl = usable_watchlist(live_depth)
    if not feed_ok:
        print(f"[FEED] Kite snapshot unusable ({feed_reason}); "
              f"falling back to BSE_OFFICIAL only.")
    kite_ltps = {}
    if isinstance(kite_wl, list):
        for item in kite_wl:
            if isinstance(item, dict) and "symbol" in item:
                sym = item["symbol"].upper()
                ltp_val = item.get("ltp")
                try:
                    kite_ltps[sym] = float(ltp_val)
                except (ValueError, TypeError):
                    pass

    active_kite_stock = (live_depth.get("active_stock") or "").upper() if feed_ok else ""
    active_depth = live_depth.get("depth") or {}
    active_stats = live_depth.get("stats") or {}
    if not isinstance(active_stats, dict):
        active_stats = {}

    for cand in CANDIDATES:
        sym = cand["symbol"]
        scripcode = cand["scripcode"]
        name = cand["name"]
        setup = cand["setup"]
        risk_budget = cand["risk_budget"]

        bse_info = bse_data.get(sym, {})
        raw_bse_ltp = bse_info.get("ltp")
        prev_close = bse_info.get("prev_close")
        uc = bse_info.get("upper_circuit")
        lc = bse_info.get("lower_circuit")
        band_pct = bse_info.get("band_pct")
        bse_vol = bse_info.get("volume_shares") or 0
        surv = bse_info.get("surveillance", "UNKNOWN")

        # Resolve LTP: prefer Kite Web live tick if available, else BSE LTP (exact symbol match)
        ltp = None
        ltp_source = "NONE"
        if sym in kite_ltps:
            ltp = kite_ltps[sym]
            ltp_source = "KITE_LIVE"
        elif raw_bse_ltp is not None:
            try:
                ltp = float(raw_bse_ltp)
                ltp_source = "BSE_OFFICIAL"
            except (ValueError, TypeError):
                ltp = None

        # Baseline volume (20d average) - fail closed if missing, do NOT invent 10,000 baseline
        avg_20d = avg_20d_vols.get(sym)

        # Volume resolution (Kite active stock volume or BSE volume) - exact symbol match
        current_vol = bse_vol
        if active_kite_stock and sym == active_kite_stock:
            kite_vol = active_stats.get("volume")
            if kite_vol is not None and isinstance(kite_vol, (int, float)) and kite_vol >= 0:
                current_vol = int(kite_vol)

        # Headroom to Upper Circuit
        uc_headroom_pct = None
        if ltp and uc and ltp > 0:
            uc_headroom_pct = round(((uc - ltp) / ltp) * 100.0, 2)

        # Day Change %
        day_chg_pct = None
        if ltp and prev_close and prev_close > 0:
            day_chg_pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

        # Volume Multiplier
        vol_multiple = round(current_vol / avg_20d, 2) if (avg_20d and avg_20d > 0) else 0.0

        # Rule 2: Price Floor Gate (>= Rs. 10.00)
        rule2_pass = bool(ltp and ltp >= 10.00)

        # Rule 5: Position Sizing (Risk Divisor = 0.401 for 10-day LC descent)
        rule5_max_shares = 0
        if ltp and ltp > 0:
            rule5_max_shares = int(math.floor(risk_budget / (0.401 * ltp)))

        # Rule 9: Liquidity Participation Gate (15% max volume over 2 sessions)
        rule9_max_shares = int(math.floor(2 * 0.15 * avg_20d)) if (avg_20d and avg_20d > 0) else 0

        # Final Approved Combined Paper Shares
        paper_shares = min(rule5_max_shares, rule9_max_shares) if (rule2_pass and rule5_max_shares > 0 and rule9_max_shares > 0) else 0

        # Surveillance Gate (Rule 6 & Rule 10 Precedence)
        surv_alert = False
        surv_note = "CLEAN"
        if surv in ["ESM_STAGE_1", "ESM_STAGE_2", "GSM_STAGE_1", "GSM_STAGE_2", "GSM_STAGE_3", "GSM_STAGE_4"]:
            surv_alert = True
            surv_note = f"SURVEILLANCE ({surv})"
        elif band_pct and band_pct <= 5.0 and sym != "KINETIC":
            surv_alert = True
            surv_note = f"BAND_CUT ({band_pct}%)"

        # Action / Readiness Status
        if not rule2_pass:
            status = "DISQUALIFIED_SUB_10"
            color = CLR_RED
        elif surv_alert and sym == "KINETIC":
            status = "ESM1_REVIEW_OK"
            color = CLR_YELLOW
        elif surv_alert:
            status = "SURV_FREEZE"
            color = CLR_RED
        elif uc_headroom_pct is not None and uc_headroom_pct <= 0.2:
            status = "UC_LOCKED_NO_CHASE"
            color = CLR_RED
        elif vol_multiple >= 3.0 and uc_headroom_pct is not None and uc_headroom_pct > 2.0:
            status = "BREAKOUT_ARMED"
            color = CLR_GREEN
        elif vol_multiple >= 1.0:
            status = "ACCUMULATING"
            color = CLR_CYAN
        else:
            status = "WATCHING_BASE"
            color = CLR_WHITE

        results[sym] = {
            "symbol": sym,
            "scripcode": scripcode,
            "name": name,
            "setup": setup,
            "ltp": ltp,
            "ltp_source": ltp_source,
            "prev_close": prev_close,
            "day_change_pct": day_chg_pct,
            "upper_circuit": uc,
            "lower_circuit": lc,
            "band_pct": band_pct,
            "uc_headroom_pct": uc_headroom_pct,
            "volume_today": current_vol,
            "volume_20d_avg": int(avg_20d),
            "volume_multiplier": vol_multiple,
            "rule2_pass": rule2_pass,
            "rule5_shares": rule5_max_shares,
            "rule9_shares": rule9_max_shares,
            "paper_shares": paper_shares,
            "surveillance": surv,
            "surv_note": surv_note,
            "status": status,
            "status_color": color,
            "has_live_depth": bool(sym in active_kite_stock and active_depth)
        }

    return results


def format_radar_screen(evaluated_data: Dict[str, Any], last_updated: str) -> str:
    """Renders ANSI colorized 5-stock live monitor grid."""
    lines = []
    lines.append(f"{CLR_BOLD}{CLR_CYAN}===================================================================================================={CLR_RESET}")
    lines.append(f"{CLR_BOLD}{CLR_WHITE}   PROJECT SWING TRADES | TRACK 1: UNIFIED 5-STOCK REAL-TIME RADAR MATRIX{CLR_RESET}")
    lines.append(f"{CLR_DIM}   Observation Gate: Rule 1 Active (0/60 Sessions, 0/20 Real Fills) | Refresh: 1.5s | Time: {last_updated}{CLR_RESET}")
    lines.append(f"{CLR_BOLD}{CLR_CYAN}===================================================================================================={CLR_RESET}")
    
    header = f"{CLR_BOLD}{'SYMBOL':<10} {'LTP (Rs)':<9} {'CHG %':<8} {'BAND':<6} {'UC LIMIT':<9} {'HEADROOM':<9} {'VOL TODAY':<11} {'20D AVG':<10} {'VOLx':<6} {'SIZING':<8} {'STATUS':<16}{CLR_RESET}"
    lines.append(header)
    lines.append(f"{CLR_DIM}{'-'*100}{CLR_RESET}")

    for sym, d in evaluated_data.items():
        ltp_str = f"{d['ltp']:.2f}" if d['ltp'] is not None else "N/A"
        chg_str = f"{d['day_change_pct']:+.2f}%" if d['day_change_pct'] is not None else "0.00%"
        chg_color = CLR_GREEN if (d['day_change_pct'] or 0) > 0 else (CLR_RED if (d['day_change_pct'] or 0) < 0 else CLR_WHITE)
        band_str = f"{int(d['band_pct'])}%" if d['band_pct'] is not None else "N/A"
        uc_str = f"{d['upper_circuit']:.2f}" if d['upper_circuit'] is not None else "N/A"
        
        hr_val = d['uc_headroom_pct']
        hr_str = f"{hr_val:+.1f}%" if hr_val is not None else "N/A"
        hr_color = CLR_GREEN if (hr_val is not None and hr_val > 3.0) else (CLR_YELLOW if (hr_val is not None and hr_val > 0.5) else CLR_RED)

        vol_today_str = f"{d['volume_today']:,}" if d['volume_today'] else "0"
        vol_avg_str = f"{d['volume_20d_avg']:,}"
        vol_x_str = f"{d['volume_multiplier']:.1f}x"
        vol_x_color = CLR_GREEN if d['volume_multiplier'] >= 3.0 else (CLR_YELLOW if d['volume_multiplier'] >= 1.0 else CLR_WHITE)

        sizing_str = f"{d['paper_shares']:,} sh" if d['paper_shares'] > 0 else "0 sh"
        status_str = d['status']
        status_clr = d['status_color']
        depth_flag = " [DEPTH]" if d['has_live_depth'] else ""

        line = (
            f"{CLR_BOLD}{sym:<10}{CLR_RESET} "
            f"{CLR_WHITE}{ltp_str:<9}{CLR_RESET} "
            f"{chg_color}{chg_str:<8}{CLR_RESET} "
            f"{CLR_WHITE}{band_str:<6}{CLR_RESET} "
            f"{CLR_WHITE}{uc_str:<9}{CLR_RESET} "
            f"{hr_color}{hr_str:<9}{CLR_RESET} "
            f"{CLR_WHITE}{vol_today_str:<11}{CLR_RESET} "
            f"{CLR_DIM}{vol_avg_str:<10}{CLR_RESET} "
            f"{vol_x_color}{vol_x_str:<6}{CLR_RESET} "
            f"{CLR_BOLD}{CLR_CYAN}{sizing_str:<8}{CLR_RESET} "
            f"{status_clr}{status_str}{depth_flag:<16}{CLR_RESET}"
        )
        lines.append(line)

    lines.append(f"{CLR_DIM}{'-'*100}{CLR_RESET}")
    lines.append(f"{CLR_BOLD}EXECUTION GATES (Strict AGENTS.md Precedence):{CLR_RESET}")
    lines.append(f"  {CLR_GREEN}* Rule 1 (Paper Only):{CLR_RESET} Live order submission locked. Sizing logged to CHATGPT/observation_log.csv.")
    lines.append(f"  {CLR_YELLOW}* Rule 5 (LC Risk):{CLR_RESET} Rs.5,000 budget / 0.401 drawdown divisor (10-day LC descent buffer).")
    lines.append(f"  {CLR_CYAN}* Rule 9 (Liquidity):{CLR_RESET} Capped at 15% participation of 20-day volume over 2 exit sessions.")
    lines.append(f"  {CLR_RED}* Rule 3 (No Chasing):{CLR_RESET} Orders rejected if Headroom <= 0.2% (Upper Circuit locked).")
    lines.append(f"{CLR_BOLD}{CLR_CYAN}===================================================================================================={CLR_RESET}")
    return "\n".join(lines)


def run_radar_loop():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initializing Unified 5-Stock Live Terminal Radar...")
    print(f"  Loading 20-day volume baselines from {DB_PATH}...")
    avg_vols = get_20d_avg_volumes()
    for sym, v in avg_vols.items():
        print(f"    - {sym:<10}: 20d Avg Vol = {int(v):,} shares")

    print(f"  Monitoring shared feeds: {LIVE_DEPTH_PATH} & {BSE_BANDS_PATH}...")
    time.sleep(1)

    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            bse_data = load_json(BSE_BANDS_PATH)
            live_depth = load_json(LIVE_DEPTH_PATH)

            evaluated = evaluate_radar_state(bse_data, live_depth, avg_vols)

            # Persist evaluation state
            radar_payload = {
                "timestamp": now_str,
                "stocks": evaluated
            }
            try:
                tmp_out = RADAR_OUTPUT_PATH + ".tmp"
                with open(tmp_out, "w", encoding="utf-8") as f:
                    json.dump(radar_payload, f, indent=2)
                os.replace(tmp_out, RADAR_OUTPUT_PATH)
            except Exception:
                pass

            # Render Screen
            os.system('cls' if os.name == 'nt' else 'clear')
            screen = format_radar_screen(evaluated, now_str)
            print(screen)

            time.sleep(1.5)

        except KeyboardInterrupt:
            print("\nShutting down Multi-Stock Radar.")
            break
        except Exception as e:
            print(f"Radar loop error: {e}", file=sys.stderr)
            time.sleep(2)


if __name__ == "__main__":
    run_radar_loop()
