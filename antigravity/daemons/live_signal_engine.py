"""
live_signal_engine.py - Real-Time Microstructure & Fail-Closed Signal Engine
Continuously consumes shared/live_depth.json and shared/bse_daily_bands.json
to evaluate live entry candidacy, queue drain ratio rho = R / V, and exit targets.
Part of Project Swing Trades (Antigravity + Claude + ChatGPT).
"""

import json
import math
import os
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional

from antigravity.daemons.feed_validity import check_feed

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import (
    CircuitRuleEngine,
    MarketDepthSnapshot,
    EntrySignal,
    TradeSignal,
    PositionSignal
)
from antigravity.models.queue_model import QueueDrainModel
from antigravity.models.liquidity_gate import evaluate_liquidity_gate
from antigravity.models.risk_calculator import CircuitRiskCalculator

SHARED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared"))
LIVE_DEPTH_PATH = os.path.join(SHARED_DIR, "live_depth.json")
BSE_BANDS_PATH = os.path.join(SHARED_DIR, "bse_daily_bands.json")
LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "live_signals.log"))


def load_json(filepath: str) -> dict:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


TRACK1_ALIASES = {
    "AHCL": "ANLON",
    "ANLON": "AHCL",
    "KINETICENG": "KINETIC",
    "KINETIC": "KINETICENG"
}


def analyze_live_ticker(
    depth_data: dict,
    bands_data: dict,
    rupees_risk_budget: float = 5000.0
) -> Optional[Dict[str, Any]]:
    """
    Evaluates current live depth snapshot against full quantitative model suite.
    Enforces fail-closed feed staleness, record validity, and Rule 1 execution locks.
    """
    if not depth_data:
        return None

    # Gate 0A: Live Feed Staleness & Visibility Verification
    ts_str = depth_data.get("local_write_time") or depth_data.get("timestamp")
    is_timestamp_stale = False
    if ts_str:
        try:
            if "T" in ts_str:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                now_utc = datetime.now(ts_dt.tzinfo)
                if abs((now_utc - ts_dt).total_seconds()) > 120.0:
                    is_timestamp_stale = True
            else:
                ts_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if ts_dt.date() != datetime.now().date() or abs((datetime.now() - ts_dt).total_seconds()) > 120.0:
                    is_timestamp_stale = True
        except Exception:
            is_timestamp_stale = True

    # Shared gate: this previously missed data_valid and
    # STALE_DATA_FROZEN, so a frozen DOM could pass as live.
    feed_ok, feed_reason = check_feed(depth_data)
    if not feed_ok or is_timestamp_stale:
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": depth_data.get("active_stock") or "UNKNOWN",
            "ltp": None,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": f"DATA_INVALID / FAIL-CLOSED: {feed_reason or 'TIMESTAMP_STALE'}",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "FEED_STALE_OR_UNAVAILABLE"
        }

    active_stock = depth_data.get("active_stock")
    if not active_stock:
        return None

    stats = depth_data.get("stats", {})
    depth = depth_data.get("depth", {})
    bids = depth.get("bids", []) if depth else []
    offers = depth.get("offers", []) if depth else []

    # Retrieve official BSE daily limits & validate exchange record with alias fallback
    bse_info = bands_data.get(active_stock) or bands_data.get(TRACK1_ALIASES.get(active_stock, "")) or {}
    bse_validation = bse_info.get("validation", {})
    if not bse_validation.get("record_valid", False):
        anomalies = bse_validation.get("anomalies", ["RECORD_INVALID"])
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": None,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": f"DATA_INVALID / FAIL-CLOSED: BSE exchange record invalid ({anomalies}).",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "INVALID_EXCHANGE_RECORD"
        }

    # Match LTP from watchlist or stats safely
    wl = depth_data.get("watchlist", [])
    matched_item = next((w for w in wl if isinstance(w, dict) and w.get("symbol") == active_stock), None)
    ltp = matched_item.get("ltp") if matched_item and matched_item.get("ltp") is not None else stats.get("close")
    
    try:
        ltp = float(ltp) if ltp is not None else None
    except (ValueError, TypeError):
        ltp = None

    if ltp is None or ltp <= 0 or not math.isfinite(ltp):
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": None,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Missing or non-positive LTP.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "INVALID_LTP"
        }

    raw_prev_close = bse_info.get("prev_close") or stats.get("prev_close")
    if raw_prev_close is None:
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": ltp,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Missing previous close from BSE and market depth.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "DATA_INVALID"
        }

    try:
        prev_close = float(raw_prev_close)
        if prev_close <= 0 or not math.isfinite(prev_close):
            raise ValueError
    except (ValueError, TypeError):
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": ltp,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Invalid previous close.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "DATA_INVALID"
        }

    band_pct = bse_info.get("band_pct")
    if band_pct is None:
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": ltp,
            "prev_close": prev_close,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Missing circuit band percentage in BSE daily records.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "DATA_INVALID"
        }
    try:
        band_pct = float(band_pct)
        if band_pct <= 0 or not math.isfinite(band_pct):
            raise ValueError
    except (ValueError, TypeError):
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": active_stock,
            "ltp": ltp,
            "prev_close": prev_close,
            "band_pct": None,
            "entry_signal": EntrySignal.DATA_INVALID.value,
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Invalid circuit band percentage.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "DATA_INVALID"
        }

    surveillance = bse_info.get("surveillance") or "UNKNOWN"
    group = bse_info.get("group") or "UNKNOWN"

    vol_audit = (
        depth_data.get("volume_expansion_audit", {}).get(active_stock)
        or depth_data.get("volume_expansion_audit", {}).get(TRACK1_ALIASES.get(active_stock, ""), {})
        or {}
    )
    day_volume = stats.get("volume") or vol_audit.get("intraday_volume") or bse_info.get("volume_shares") or 0
    avg_20d_volume = vol_audit.get("avg_20d_volume") or bse_info.get("two_week_avg_volume_shares") or day_volume

    total_bids = sum(b.get("quantity", 0) for b in bids)
    total_offers = sum(o.get("quantity", 0) for o in offers)

    best_bid = bids[0]["price"] if bids else None
    best_ask = offers[0]["price"] if offers else None

    spread_pct = None
    if best_bid and best_ask and best_bid > 0:
        spread_pct = round(((best_ask - best_bid) / best_bid) * 100, 3)

    # 1. Calculate tentative position sizing under Rule 5 & Rule 9
    sizing_info = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
        rupees_willing_to_lose=rupees_risk_budget,
        stock_price=ltp,
        daily_volume=day_volume
    )
    tentative_shares = sizing_info.get("max_shares", 0)

    # Construct MarketDepthSnapshot
    snap = MarketDepthSnapshot(
        ticker=active_stock,
        price=ltp,
        prev_close=prev_close,
        circuit_limit_pct=band_pct,
        total_bids=total_bids,
        total_offers=total_offers,
        day_volume=day_volume,
        avg_20d_volume=avg_20d_volume,
        high=stats.get("high"),
        low=stats.get("low"),
        best_bid=best_bid,
        best_ask=best_ask,
        spread_pct=spread_pct,
        series="EQ" if group in ["A", "B", "EQ"] else group,
        surveillance_stage=surveillance
    )

    # 2. Evaluate Entry Candidacy (CircuitRuleEngine with mandatory proposed_shares)
    if tentative_shares <= 0:
        entry_signal = EntrySignal.NO_ENTRY.value
        entry_reason = f"NO_ENTRY / FAIL-CLOSED: Position sizing returned 0 shares (Constraint: {sizing_info.get('constrained_by')})."
    else:
        sig, rsn = CircuitRuleEngine.evaluate_entry_signal(
            snap=snap,
            proposed_shares=tentative_shares
        )
        entry_signal = sig.value
        entry_reason = rsn

    # 3. Evaluate Queue Drain Capacity
    queue_result = None
    if offers and ltp and tentative_shares > 0:
        queue_ahead = sum(o.get("quantity", 0) for o in offers if o.get("price") <= ltp)
        forecasted_vol = QueueDrainModel.forecast_session_volume(
            cumulative_volume=day_volume,
            observed_time=datetime.now().time()
        )
        queue_result = QueueDrainModel.evaluate_queue(
            resting_queue_ahead=queue_ahead,
            order_qty=tentative_shares,
            expected_volume=forecasted_vol
        )

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": active_stock,
        "ltp": ltp,
        "prev_close": prev_close,
        "band_pct": band_pct,
        "entry_signal": entry_signal,
        "entry_reason": entry_reason,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_pct": spread_pct,
        "total_bids": total_bids,
        "total_offers": total_offers,
        "day_volume": day_volume,
        "queue_drain": {
            "rho": queue_result.rho if queue_result else None,
            "rho_total": queue_result.rho_total if queue_result else None,
            "fill_state": queue_result.fill_state if queue_result else None,
            "estimated_fill_shares": queue_result.estimated_fill_shares if queue_result else None,
            "drain_time_hours": queue_result.drain_time_hours if queue_result else None,
        } if queue_result else None,
        "sizing": sizing_info,
        "feed_status": "FEED_ACTIVE"
    }


def format_live_dashboard(analysis: dict) -> str:
    sym = analysis.get("symbol", "UNKNOWN")
    sig = analysis.get("entry_signal", "DATA_INVALID")
    ltp = analysis.get("ltp")
    ltp_str = f"Rs. {ltp:.2f}" if (ltp is not None and isinstance(ltp, (int, float))) else "N/A"
    spread = analysis.get("spread_pct")
    spread_str = f"{spread:.3f}%" if (spread is not None and isinstance(spread, (int, float))) else "N/A"
    sz = analysis.get("sizing") or {}
    qd = analysis.get("queue_drain") or {}
    feed_status = analysis.get("feed_status", "UNKNOWN")

    sig_color = "[QUALIFIED]" if sig == "BUY_ACCUMULATION_BREAKOUT" else (
        "[CRITICAL AVOID]" if sig == "CRITICAL_AVOID" else (
            "[FAIL-CLOSED]" if sig == "DATA_INVALID" else "[NO ENTRY]"
        )
    )

    total_bids = analysis.get("total_bids")
    bids_str = f"{total_bids:,}" if isinstance(total_bids, int) else "N/A"
    total_offers = analysis.get("total_offers")
    offers_str = f"{total_offers:,}" if isinstance(total_offers, int) else "N/A"
    day_vol = analysis.get("day_volume")
    vol_str = f"{day_vol:,} shares" if isinstance(day_vol, int) else "N/A"

    lines = [
        "=" * 70,
        f"  LIVE MARKET TICK EVALUATION: {sym} | LTP: {ltp_str} | Time: {analysis.get('timestamp')}",
        "=" * 70,
        f"  FEED STATUS: {feed_status}",
        f"  SIGNAL     : {sig_color} ({sig})",
        f"  REASON     : {analysis.get('entry_reason')}",
        "-" * 70,
        f"  Order Book Depth : Bids: {bids_str} | Offers: {offers_str}",
        f"  Best Bid / Ask   : {analysis.get('best_bid', 'N/A')} / {analysis.get('best_ask', 'N/A')} | Spread: {spread_str}",
        f"  Session Volume   : {vol_str}",
        f"  Queue Ratio (rho): {qd.get('rho', 'N/A')} | Est Fill Prob: {qd.get('fill_probability', 'N/A')}",
        "-" * 70,
        f"  Position Sizing (Rule 5 & Rule 9 Combined):",
        f"    Max Shares Allowed   : {sz.get('max_shares', 0):,} shares",
        f"    Capital Deployment   : Rs. {sz.get('actual_capital_deployed', 0):,.2f}",
        f"    Worst-Case 10D Loss  : Rs. {sz.get('calibrated_worst_case_10d_loss', 0):,.2f}",
        f"    Governing Constraint : {sz.get('constrained_by')}",
        "=" * 70,
    ]
    return "\n".join(lines)


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Real-Time Live Signal Engine...")
    print(f"  Watching: {LIVE_DEPTH_PATH}")
    print(f"  Mode: Strict Observation Only (AGENTS.md Rule 1)")

    last_modified = 0
    while True:
        try:
            if os.path.exists(LIVE_DEPTH_PATH):
                mtime = os.path.getmtime(LIVE_DEPTH_PATH)
                if mtime > last_modified:
                    last_modified = mtime
                    depth_data = load_json(LIVE_DEPTH_PATH)
                    bands_data = load_json(BSE_BANDS_PATH)

                    analysis = analyze_live_ticker(depth_data, bands_data)
                    if analysis:
                        os.system('cls' if os.name == 'nt' else 'clear')
                        dashboard = format_live_dashboard(analysis)
                        print(dashboard)

                        # Append to live signal audit log
                        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
                        with open(LOG_PATH, "a", encoding="utf-8") as f:
                            f.write(json.dumps(analysis) + "\n")
            time.sleep(1.5)
        except KeyboardInterrupt:
            print("\nShutting down Live Signal Engine.")
            break
        except Exception as e:
            print(f"Error in live loop: {e}", file=sys.stderr)
            time.sleep(2)


if __name__ == "__main__":
    main()
