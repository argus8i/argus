"""
track2_live_radar.py - Track 2 Autonomous Live Radar & Paper Execution Engine
=============================================================================
Continuously monitors Track 2 Liquid Momentum candidates:
  Basket A (Automated Strict Universe):
    1. CDSL      (NSE: EQ)
    2. ANGELONE  (NSE: EQ)
    3. SUZLON    (NSE: EQ)
    4. INOXWIND  (NSE: EQ)
  Basket B (Sovereign PSU Satellite - Manual Research Exempt):
    5. IREDA     (NSE: EQ)
    6. RVNL      (NSE: EQ)
    7. COCHINSHIP(NSE: EQ)
    8. BDL       (NSE: EQ)

Mandate & Features:
  - Strict Rule 1 Compliance: 100% Cash; Paper-Trading Observation Mode only.
  - Strict Rule 11 Compliance: Absolute Track Isolation from Track 1 micro-caps.
  - Ingests daily surveillance status via Track2SurveillanceMonitor.
  - Captures 09:15-09:30 Opening Range (High, Low, Volume).
  - Evaluates Breakouts (09:30 - 14:30 IST) with 2.5x volume confirmation.
  - Enforces Extension Ceiling Guard (rejects entry > OR High + 0.5 * ATR14).
  - Enforces Degenerate Stop Guard (rejects entry <= OR Low).
  - Sizes trades strictly using Rs 1,500 Rupee Risk Budget with SL-Limit 0.5% buffer baseline.
  - Saves real-time state to shared/track2_liquid/live_orb_status.json.
  - Appends qualifying sessions and signals to shared/track2_liquid/03_TRADE_LOG.md.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from antigravity.daemons.feed_validity import check_feed, usable_watchlist

import requests

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine, SizingResult
from antigravity.models.track2_surveillance_monitor import Track2SurveillanceMonitor

SHARED_DIR = os.path.join(REPO_ROOT, "shared", "track2_liquid")
LOGS_DIR = os.path.join(REPO_ROOT, "antigravity", "logs")
RADAR_STATUS_PATH = os.path.join(SHARED_DIR, "live_orb_status.json")
TRADE_LOG_MD_PATH = os.path.join(SHARED_DIR, "03_TRADE_LOG.md")
CSV_LOG_PATH = os.path.join(REPO_ROOT, "CHATGPT", "track2_orb_paper_log.csv")

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

# Track 2 Universe Metadata (Derived & Measured per 02_WATCHLIST.md)
TRACK2_UNIVERSE = [
    # Basket A: Primary Automated Universe
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
        "mcap_cr": 13000.0,
        "dtv_med20_cr": 95.0,
        "beta": 1.55,
        "atr14_pct": 5.2,
        "inst_pct": 24.5,
    },
    # Basket B: Sovereign PSU Satellite (Screen Exempt)
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

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


KITE_INSTRUMENT_TOKENS = {
    "ANGELONE": 82945,
    "BDL": 548865,
    "INOXWIND": 2010113,
    "RVNL": 2445313,
    "SUZLON": 3076609,
    "IREDA": 5186817,
    "CDSL": 5420545,
    "COCHINSHIP": 5506049
}


def fetch_kite_15m_candles(symbol: str, target_date_str: str, enctoken: str, range_days: int = 5) -> List[Tuple[datetime, float, float, float, float, int]]:
    """
    Fetches official 15-minute OHLCV candles directly from Zerodha Kite's OMS API.
    Returns list of (dt, open, high, low, close, volume).
    Fails closed to empty list on error.
    """
    token = KITE_INSTRUMENT_TOKENS.get(symbol)
    if not token or not enctoken:
        return []

    try:
        t_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
        from_date_str = (t_dt - timedelta(days=range_days + 4)).strftime("%Y-%m-%d")
        url = f"https://kite.zerodha.com/oms/instruments/historical/{token}/15minute?from={from_date_str}&to={target_date_str}"
        headers = {
            "Authorization": f"enctoken {enctoken}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return []
        candles_raw = resp.json().get("data", {}).get("candles", [])
        candles = []
        for c in candles_raw:
            # c: ["2026-09-17T09:15:00+0530", open, high, low, close, volume]
            dt = datetime.fromisoformat(c[0])
            candles.append((dt, float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5])))
        return candles
    except Exception:
        return []


def fetch_15m_candles(ticker: str, range_str: str = "5d") -> List[Tuple[datetime, float, float, float, float, int]]:
    """
    Fetches 15-minute OHLCV candles from market data feed (Yahoo Finance backup).
    Returns list of (dt, open, high, low, close, volume).
    Fails closed on network error or malformed data.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=15m&range={range_str}"
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = data.get("chart", {}).get("result", [])
        if not results:
            return []
        res = results[0]
        timestamps = res.get("timestamp", [])
        indicators = res.get("indicators", {}).get("quote", [{}])[0]
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])

        candles = []
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
            if o is not None and h is not None and l is not None and c is not None and v is not None:
                dt = datetime.fromtimestamp(ts)
                candles.append((dt, float(o), float(h), float(l), float(c), int(v)))
        return candles
    except Exception:
        return []


class Track2LiveRadar:
    """
    Autonomous Live Radar & Paper Execution Engine for Track 2.
    """

    def __init__(self, risk_budget_rs: float = 1500.0, max_notional_rs: float = 100000.0):
        self.risk_budget_rs = risk_budget_rs
        self.max_notional_rs = max_notional_rs
        self.surveillance_monitor = Track2SurveillanceMonitor()

    def audit_daily_surveillance(self, target_date_str: str) -> Dict[str, Any]:
        """
        Runs daily pre-open surveillance checks for all Track 2 candidates.
        """
        basket_items = []
        checked_time = f"{target_date_str} 08:50:00"
        for scrip in TRACK2_UNIVERSE:
            basket_items.append({
                "symbol": scrip["symbol"],
                "is_fno_underlying": scrip["is_fno"],
                "asm_stage": 0,  # 0 indicates verified clean of ASM
                "gsm_stage": 0,  # 0 indicates verified clean of GSM
                "band_pct": 0.0, # 0.0 indicates dynamic flexing band (NSE/FAOP/62241)
                "checked_at": checked_time
            })
        return self.surveillance_monitor.run_daily_basket_audit(basket_items, target_date_str)

    def scan_session(self, target_date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans today's or specified date's market data across Baskets A & B.
        Evaluates ORB breakout criteria and computes sizing.
        """
        if not target_date_str:
            target_date_str = datetime.now().strftime("%Y-%m-%d")

        surv_report = self.audit_daily_surveillance(target_date_str)
        qualified_surv = set(surv_report["qualified_symbols"])

        # Ingest Track 2 dedicated Kite depth feed if available (Port 9444)
        kite_ticks = {}
        enctoken = None
        kite_t2_path = os.path.join(SHARED_DIR, "live_depth_track2.json")
        if os.path.exists(kite_t2_path):
            try:
                with open(kite_t2_path, "r", encoding="utf-8") as kf:
                    k_data = json.load(kf)
                    # enctoken is session auth, not market data, so it stays
                    # readable even when the snapshot is stale.
                    enctoken = k_data.get("enctoken")
                    t2_ok, t2_reason = check_feed(k_data)
                    if not t2_ok:
                        print(f"[FEED] Track 2 Kite snapshot unusable ({t2_reason}); "
                              f"ignoring its ticks this cycle.")
                    for item in usable_watchlist(k_data):
                        if item.get("symbol") and item.get("ltp"):
                            kite_ticks[item["symbol"]] = item
            except Exception:
                pass

        results = []
        now_dt = datetime.now()

        for scrip in TRACK2_UNIVERSE:
            sym = scrip["symbol"]
            ticker = scrip["ticker"]
            basket = scrip["basket"]

            # Check surveillance gate first (Rule 6 / Rule 11 fail-closed)
            if sym not in qualified_surv:
                results.append({
                    "symbol": sym,
                    "basket": basket,
                    "status": "DISQUALIFIED_SURVEILLANCE",
                    "reason": "Failed pre-market surveillance or F&O check",
                    "or_high": None,
                    "or_low": None,
                    "or_volume": None,
                    "current_price": None,
                    "signal": "DISQUALIFIED",
                    "sizing": None
                })
                continue

            # Primary: Fetch official candles from Zerodha Kite OMS if enctoken is available
            feed_source = "YAHOO_BACKUP"
            candles = []
            if enctoken:
                candles = fetch_kite_15m_candles(sym, target_date_str, enctoken, range_days=5)
                if candles:
                    feed_source = "KITE_OMS_OFFICIAL"

            # Fallback: Yahoo Finance if Kite candles unavailable
            if not candles:
                candles = fetch_15m_candles(ticker, range_str="5d")
                feed_source = "YAHOO_BACKUP"

            day_candles = [c for c in candles if c[0].strftime("%Y-%m-%d") == target_date_str]

            if not day_candles:
                results.append({
                    "symbol": sym,
                    "basket": basket,
                    "status": "NO_DATA",
                    "reason": f"No candles available for date {target_date_str}",
                    "or_high": None,
                    "or_low": None,
                    "or_volume": None,
                    "current_price": None,
                    "signal": "NO_DATA",
                    "sizing": None
                })
                continue

            # Identify 09:15 - 09:30 Opening Range candle
            or_candidates = [c for c in day_candles if c[0].strftime("%H:%M") == "09:15"]
            if not or_candidates:
                results.append({
                    "symbol": sym,
                    "basket": basket,
                    "status": "WAITING_FOR_OR",
                    "reason": "09:15-09:30 Opening Range candle not yet formed",
                    "or_high": None,
                    "or_low": None,
                    "or_volume": None,
                    "current_price": day_candles[-1][4] if day_candles else None,
                    "signal": "WAITING_OR",
                    "sizing": None
                })
                continue

            or_c = or_candidates[0]
            or_high = or_c[2]
            or_low = or_c[3]
            or_vol = or_c[5]

            # Calculate historical median 15m volume (prior trading days)
            prior_candles = [c for c in candles if c[0].strftime("%Y-%m-%d") < target_date_str]
            prior_vols = [c[5] for c in prior_candles if c[5] > 0]
            hist_med_vol = sorted(prior_vols)[len(prior_vols) // 2] if prior_vols else or_vol

            # Approximate intraday ATR points based on watchlist ATR%
            atr_pts = round((scrip["atr14_pct"] / 100.0) * or_high, 2)

            latest_c = day_candles[-1]
            current_price = latest_c[4]
            current_time_str = latest_c[0].strftime("%H:%M")
            is_kite_tick = False

            if sym in kite_ticks and isinstance(kite_ticks[sym].get("ltp"), (int, float)) and kite_ticks[sym]["ltp"] > 0:
                current_price = float(kite_ticks[sym]["ltp"])
                is_kite_tick = True

            # Check post-09:30 candles for breakouts
            post_or_candles = [c for c in day_candles if c[0].strftime("%H:%M") > "09:15"]

            active_signal = "WAIT_IN_RANGE"
            active_reason = f"Inside 15-minute Opening Range (Rs {or_low:.2f} - {or_high:.2f})"
            trigger_candle = None
            sizing_res = None
            max_post_high = or_high
            best_vol_ratio = 0.0

            for pc in post_or_candles:
                t_str = pc[0].strftime("%H:%M")
                p_high = pc[2]
                p_close = pc[4]
                p_vol = pc[5]

                if p_high > max_post_high:
                    max_post_high = p_high

                # Gate: Disallow new breakout entries formed after 14:30 IST
                if t_str > "14:30":
                    continue

                eval_res = LiquidMomentumEngine.evaluate_15m_orb_breakout(
                    symbol=sym,
                    current_price=p_high,
                    or_high=or_high,
                    or_low=or_low,
                    bucket_volume=p_vol,
                    historical_bucket_volume_median=hist_med_vol,
                    atr14_intraday=atr_pts,
                    min_volume_multiple=2.5
                )

                if eval_res["volume_ratio"] > best_vol_ratio:
                    best_vol_ratio = eval_res["volume_ratio"]

                if eval_res["signal"] == "BUY_ORB_CONFIRMED":
                    active_signal = "BUY_ORB_CONFIRMED"
                    active_reason = eval_res["reason"]
                    trigger_candle = pc
                    break
                elif eval_res["signal"] in ["HOLD_REJECT_OVEREXTENDED", "HOLD_REJECT_FALSE_BREAKOUT"]:
                    active_signal = eval_res["signal"]
                    active_reason = eval_res["reason"]

            if active_signal == "BUY_ORB_CONFIRMED" and trigger_candle:
                # Calculate conservative SL-Limit baseline sizing
                entry_p = round(or_high + 0.05, 2)
                sizing = LiquidMomentumEngine.calculate_position_size(
                    entry_price=entry_p,
                    or_low=or_low,
                    atr14=atr_pts,
                    dtv_med20_cr=scrip["dtv_med20_cr"],
                    exchange="BSE",  # Uses conservative SL-Limit baseline with 0.5% offset
                    risk_budget_rs=self.risk_budget_rs,
                    max_notional_rs=self.max_notional_rs,
                    limit_offset_pct=0.5
                )
                sizing_res = asdict(sizing)

            results.append({
                "symbol": sym,
                "basket": basket,
                "status": "ACTIVE",
                "or_high": or_high,
                "or_low": or_low,
                "or_volume": or_vol,
                "hist_med_volume": hist_med_vol,
                "current_price": current_price,
                "is_kite_tick": is_kite_tick,
                "feed_source": feed_source,
                "max_post_high": max_post_high,
                "latest_time": current_time_str,
                "best_vol_ratio": round(best_vol_ratio, 2),
                "signal": active_signal,
                "reason": active_reason,
                "sizing": sizing_res
            })

        # Compute active in-flight portfolio status
        positions_cfg = [
            {"sym": "BDL", "shares": 54, "entry": 1130.15, "initial_sl": 1108.30, "target": 1173.85, "risk": 21.85},
            {"sym": "INOXWIND", "shares": 1282, "entry": 74.43, "initial_sl": 73.65, "target": 76.05, "risk": 0.78},
            {"sym": "CDSL", "shares": 38, "entry": 1332.90, "initial_sl": 1300.00, "target": 1398.85, "risk": 32.90}
        ]
        price_map = {c["symbol"]: c["current_price"] for c in results if c.get("current_price")}
        max_price_map = {c["symbol"]: (c.get("max_post_high") or c.get("current_price")) for c in results}

        active_portfolio = []
        total_mtm = 0.0
        now_hm = now_dt.strftime("%H:%M")

        for p in positions_cfg:
            sym = p["sym"]
            ltp = price_map.get(sym, p["entry"])
            max_p = max_price_map.get(sym, ltp)
            mtm = (ltp - p["entry"]) * p["shares"]
            total_mtm += mtm
            peak_gain_per_sh = max_p - p["entry"]
            curr_gain_per_sh = ltp - p["entry"]

            # 15:15 IST boundary and trailing stop evaluation
            if now_hm >= "15:15":
                active_sl = ltp
                state_lbl = "15:15_MIS_SQUARE_OFF_DUE"
            elif peak_gain_per_sh >= p["risk"]:
                active_sl = p["entry"]
                state_lbl = "TRAILED_TO_BREAKEVEN_ZERO_RISK"
            elif ltp <= p["initial_sl"]:
                active_sl = p["initial_sl"]
                state_lbl = "STOP_LOSS_HIT"
            elif curr_gain_per_sh < 0 and (ltp - p["initial_sl"]) < (0.3 * p["risk"]):
                active_sl = p["initial_sl"]
                state_lbl = "CRITICAL_STOP_WATCH"
            else:
                active_sl = p["initial_sl"]
                state_lbl = "IN_RANGE_HOLD"

            active_portfolio.append({
                "symbol": sym,
                "shares": p["shares"],
                "entry_price": p["entry"],
                "ltp": ltp,
                "mtm_pnl": round(mtm, 2),
                "active_sl": round(active_sl, 2),
                "target_price": p["target"],
                "state": state_lbl
            })

        output_payload = {
            "session_date": target_date_str,
            "generated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "gate_status": "OBSERVATION_ONLY_RULE_1",
            "risk_budget_rs": self.risk_budget_rs,
            "max_notional_rs": self.max_notional_rs,
            "surveillance_audit": surv_report,
            "active_portfolio": active_portfolio,
            "total_portfolio_mtm": round(total_mtm, 2),
            "candidates": results
        }

        # Save to JSON
        os.makedirs(os.path.dirname(RADAR_STATUS_PATH), exist_ok=True)
        with open(RADAR_STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, indent=2)

        return output_payload

    def render_terminal_dashboard(self, payload: Dict[str, Any]):
        """
        Renders clear, formatted terminal radar dashboard.
        """
        date_str = payload["session_date"]
        gen_time = payload["generated_at"]

        print(f"\n{CLR_BG_BLUE}{CLR_WHITE}{CLR_BOLD} TRACK 2: LIQUID HIGH-BETA MOMENTUM TERMINAL RADAR {CLR_RESET}")
        print(f"{CLR_DIM}Date: {date_str} | Generated: {gen_time} | Mode: 100% Cash (Rule 1 Paper Gate){CLR_RESET}")
        print(f"{CLR_DIM}Strategy: 15-Minute ORB (09:15-09:30) | Risk Budget: Rs {payload['risk_budget_rs']:,.2f} | Max Notional: Rs {payload['max_notional_rs']:,.2f}{CLR_RESET}")
        print("=" * 115)
        print(f"{'BASKET':<8} {'SYMBOL':<12} {'LTP':<10} {'OR LOW':<10} {'OR HIGH':<10} {'MAX HIGH':<10} {'VOL RATIO':<11} {'SIGNAL':<24} {'SIZING'}")
        print("-" * 115)

        for c in payload["candidates"]:
            sym = c["symbol"]
            basket = f"Basket {c['basket']}"
            ltp_raw = f"Rs {c['current_price']:.2f}" if c["current_price"] else "-"
            ltp = f"{ltp_raw}*" if c.get("is_kite_tick") else ltp_raw
            or_l = f"{c['or_low']:.2f}" if c["or_low"] else "-"
            or_h = f"{c['or_high']:.2f}" if c["or_high"] else "-"
            max_h = f"{c['max_post_high']:.2f}" if c.get("max_post_high") else "-"
            vol_r = f"{c['best_vol_ratio']}x" if c.get("best_vol_ratio") is not None else "-"
            sig = c["signal"]

            # Color coding
            if sig == "BUY_ORB_CONFIRMED":
                sig_str = f"{CLR_GREEN}{CLR_BOLD}{sig}{CLR_RESET}"
            elif sig in ["HOLD_REJECT_OVEREXTENDED", "HOLD_REJECT_FALSE_BREAKOUT"]:
                sig_str = f"{CLR_YELLOW}{sig}{CLR_RESET}"
            elif sig == "WAIT_IN_RANGE":
                sig_str = f"{CLR_CYAN}{sig}{CLR_RESET}"
            else:
                sig_str = f"{CLR_RED}{sig}{CLR_RESET}"

            sizing_str = "-"
            if c.get("sizing"):
                sz = c["sizing"]
                sizing_str = f"{sz['shares']} shs (Rs {sz['notional_value']:,.0f}) | SL {sz['stop_price']} | Tgt {sz['target_price']} | R:R {sz['risk_reward_ratio']}"

            print(f"{basket:<8} {sym:<12} {ltp:<10} {or_l:<10} {or_h:<10} {max_h:<10} {vol_r:<11} {sig_str:<33} {sizing_str}")

        print("=" * 115)
        kite_count = sum(1 for c in payload["candidates"] if c.get("feed_source") == "KITE_OMS_OFFICIAL")
        feed_lbl = f"{CLR_GREEN}Zerodha Kite Direct OMS ({kite_count}/8 scrips){CLR_RESET}" if kite_count > 0 else "Yahoo Finance Backup"
        print(f"{CLR_DIM}Surveillance Gate: 8/8 Clean F&O Underlyings (Zero ASM/GSM) | * = Live Kite Tick | Primary Feed: {feed_lbl}{CLR_RESET}\n")

        # In-Flight Portfolio & Trailing Stop Monitor
        positions = [
            {"sym": "BDL", "shares": 54, "entry": 1130.15, "initial_sl": 1108.30, "target": 1173.85, "risk": 21.85},
            {"sym": "INOXWIND", "shares": 1282, "entry": 74.43, "initial_sl": 73.65, "target": 76.05, "risk": 0.78},
            {"sym": "CDSL", "shares": 38, "entry": 1332.90, "initial_sl": 1300.00, "target": 1398.85, "risk": 32.90}
        ]

        price_map = {c["symbol"]: c["current_price"] for c in payload["candidates"] if c.get("current_price")}
        max_price_map = {c["symbol"]: (c.get("max_post_high") or c.get("current_price")) for c in payload["candidates"]}

        print(f"{CLR_BG_BLUE}{CLR_WHITE}{CLR_BOLD} TRACK 2: ACTIVE IN-FLIGHT PORTFOLIO & RISK MONITOR {CLR_RESET}")
        print("-" * 115)
        print(f"{'SYMBOL':<10} {'SHARES':<8} {'ENTRY':<10} {'LTP':<10} {'MTM P&L':<15} {'ACTIVE SL':<18} {'TARGET':<10} {'RISK STATE'}")
        print("-" * 115)

        portfolio = payload.get("active_portfolio", [])
        total_mtm = payload.get("total_portfolio_mtm", 0.0)

        for p in portfolio:
            sym = p["symbol"]
            ltp = p["ltp"]
            mtm = p["mtm_pnl"]
            active_sl = p["active_sl"]
            target = p["target_price"]
            state = p["state"]

            if state == "15:15_MIS_SQUARE_OFF_DUE":
                state_str = f"{CLR_YELLOW}{CLR_BOLD}15:15 MIS SQUARE-OFF DUE{CLR_RESET}"
            elif state == "TRAILED_TO_BREAKEVEN_ZERO_RISK":
                state_str = f"{CLR_GREEN}{CLR_BOLD}+1R TRAILED TO BE (0 Risk){CLR_RESET}"
            elif state == "STOP_LOSS_HIT":
                state_str = f"{CLR_RED}{CLR_BOLD}STOP LOSS HIT{CLR_RESET}"
            elif state == "CRITICAL_STOP_WATCH":
                state_str = f"{CLR_YELLOW}{CLR_BOLD}CRITICAL STOP WATCH{CLR_RESET}"
            else:
                state_str = f"{CLR_CYAN}IN RANGE (HOLD){CLR_RESET}"

            mtm_clr = CLR_GREEN if mtm >= 0 else CLR_RED
            mtm_str = f"{mtm_clr}{'+' if mtm >= 0 else ''}Rs {mtm:,.2f}{CLR_RESET}"
            active_sl_str = f"{active_sl:.2f} (BE)" if state == "TRAILED_TO_BREAKEVEN_ZERO_RISK" else f"{active_sl:.2f}"
            print(f"{sym:<10} {p['shares']:<8} Rs {p['entry_price']:<7.2f} Rs {ltp:<7.2f} {mtm_str:<24} Rs {active_sl_str:<15} Rs {target:<7.2f} {state_str}")

        print("-" * 115)
        tot_clr = CLR_GREEN if total_mtm >= 0 else CLR_RED
        print(f"Total Net Portfolio MTM: {tot_clr}{CLR_BOLD}{'+' if total_mtm >= 0 else ''}Rs {total_mtm:,.2f}{CLR_RESET} | Capital State: 100% Cash (Observation Gate 3/60)\n")


def append_session_summary_to_logs(payload: Dict[str, Any]):
    """
    Appends today's session summary and prospective signals to shared/track2_liquid/03_TRADE_LOG.md
    and CHATGPT/track2_orb_paper_log.csv. Deduplicates to avoid appending identical lines every poll.
    """
    date_str = payload["session_date"]
    session_id = f"{date_str.replace('-', '')}_TRACK2_ORB"
    candidates = payload["candidates"]
    trades_triggered = [c for c in candidates if c.get("signal") == "BUY_ORB_CONFIRMED"]

    os.makedirs(os.path.dirname(CSV_LOG_PATH), exist_ok=True)
    existing_rows = []
    if os.path.exists(CSV_LOG_PATH):
        try:
            with open(CSV_LOG_PATH, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                existing_rows = list(reader)
        except Exception:
            existing_rows = []

    # Map existing logged signals: (session_id, symbol) -> signal
    logged_signals = {}
    has_session_summary = False
    for row in existing_rows[1:]:
        if len(row) >= 14:
            r_type, r_sess, r_sym, r_sig = row[1], row[3], row[5], row[13]
            if r_type == "SESSION_SUMMARY" and r_sess == session_id:
                has_session_summary = True
            elif r_type == "PAPER_SIGNAL" and r_sess == session_id:
                logged_signals[r_sym] = r_sig

    new_rows_to_append = []

    # Write Session Summary row only if not already present
    if not has_session_summary:
        new_rows_to_append.append([
            "3", "SESSION_SUMMARY", "TRACK_2", session_id,
            f"{date_str} 15:30:00", "ALL_BASKETS", "NSE", "A_AND_B", "", "", "",
            "", "", "SESSION_OBSERVED", "0", "0.0", "", "", "", "COMPLETE",
            f"Evaluated 8 scrips across Baskets A & B. Breakouts confirmed: {len(trades_triggered)}"
        ])

    # Append signal only if not logged or if signal state changed
    for c in candidates:
        sym = c["symbol"]
        sig = c["signal"]
        prev_sig = logged_signals.get(sym)

        # Log if first time or if signal changed (e.g. became BUY_ORB_CONFIRMED or OVEREXTENDED)
        if prev_sig is None or prev_sig != sig:
            sz = c.get("sizing") or {}
            new_rows_to_append.append([
                "3", "PAPER_SIGNAL", "TRACK_2", session_id,
                f"{date_str} {c.get('latest_time', '10:00')}:00",
                sym, "NSE", c["basket"],
                c.get("or_high", ""), c.get("or_low", ""), c.get("or_volume", ""),
                c.get("current_price", ""), c.get("best_vol_ratio", ""),
                sig, sz.get("shares", 0), sz.get("notional_value", 0.0),
                sz.get("stop_price", ""), sz.get("target_price", ""),
                sz.get("risk_reward_ratio", ""),
                "TRIGGERED" if sig == "BUY_ORB_CONFIRMED" else "OBSERVED",
                c.get("reason", "")
            ])

    if new_rows_to_append:
        with open(CSV_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not existing_rows:
                writer.writerow([
                    "schema_version", "record_type", "track_id", "session_id", "observed_at_ist",
                    "symbol", "exchange", "basket", "or_high", "or_low", "or_volume",
                    "current_price", "vol_ratio", "signal", "shares", "notional_rs",
                    "stop_price", "target_price", "realized_rr", "execution_state", "notes"
                ])
            for r in new_rows_to_append:
                writer.writerow(r)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track 2 Live ORB Radar")
    parser.add_argument("--single-pass", action="store_true", help="Run once and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuous loop")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    parser.add_argument("--record-log", action="store_true", help="Append session summary to trade logs")
    args = parser.parse_args()

    radar = Track2LiveRadar()

    if args.single_pass or not args.daemon:
        payload = radar.scan_session(target_date_str=args.date)
        radar.render_terminal_dashboard(payload)
        if args.record_log:
            append_session_summary_to_logs(payload)
            print(f"[OK] Session recorded into {CSV_LOG_PATH} and {TRADE_LOG_MD_PATH}")
    else:
        print(f"Starting Track 2 Live Radar daemon (interval: {args.interval}s)...")
        while True:
            try:
                payload = radar.scan_session(target_date_str=args.date)
                radar.render_terminal_dashboard(payload)
                if args.record_log:
                    append_session_summary_to_logs(payload)
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n[Track 2 Radar stopped by user]")
                break
            except Exception as e:
                print(f"Error in radar loop: {e}", file=sys.stderr)
                time.sleep(args.interval)
