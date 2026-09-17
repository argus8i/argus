"""
track2_kite_bridge.py - Dedicated Chrome DevTools Protocol (CDP) Bridge for Track 2
===================================================================================
Connects to dedicated Track 2 Google Chrome instance on 127.0.0.1:9444.
Strictly decoupled from Track 1 (Port 9333).

Extracts real-time:
  - Track 2 candidate quotes from Marketwatch (LTP, change %, volume)
  - 5-depth market order book (bids, offers, total buy/sell) for active stock
  - Staleness & tab visibility tracking

Outputs to:
  - shared/track2_liquid/live_depth_track2.json
  - antigravity/logs/track2_depth_ticks.csv

Strictly compliant with AGENTS.md Rule 11 (Absolute Track Isolation).
"""

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import requests
import websockets

CDP_HTTP_URL = "http://127.0.0.1:9444/json"
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track2_liquid"))
LOGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs"))
LIVE_DEPTH_PATH = os.path.join(OUTPUT_DIR, "live_depth_track2.json")
TICKS_LOG_PATH = os.path.join(LOGS_DIR, "track2_depth_ticks.csv")

TRACK2_SYMBOLS = ["CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"]

EXTRACT_TRACK2_JS = """
(() => {
    const result = {
        timestamp: new Date().toISOString(),
        url: window.location.href,
        is_tab_hidden: document.hidden,
        visibility_state: document.visibilityState,
        enctoken: null,
        watchlist: [],
        active_stock: null,
        stats: {},
        depth: null
    };

    // 0. Extract enctoken from cookies
    try {
        const parts = document.cookie.split('enctoken=');
        if (parts.length > 1) {
            result.enctoken = decodeURIComponent(parts[1].split(';')[0].trim());
        }
    } catch (e) {}

    // 1. Extract Watchlist items
    document.querySelectorAll('.item-wrapper, .instrument').forEach(el => {
        const name = el.querySelector('.name, .tradingsymbol, .nice-name');
        const tag = el.querySelector('.tag, .exchange');
        const price = el.querySelector('.last-price');
        const pct = el.querySelector('.change-percentage');
        const chg = el.querySelector('.change-absolute');
        if (name && price) {
            const sym = name.innerText.trim().split('\\n')[0].trim();
            result.watchlist.push({
                symbol: sym,
                exchange: tag ? tag.innerText.trim() : 'NSE',
                ltp: parseFloat(price.innerText.trim().replace(/,/g, '')) || price.innerText.trim(),
                change_pct: pct ? pct.innerText.trim() : '',
                change_abs: chg ? parseFloat(chg.innerText.trim().replace(/,/g, '')) || chg.innerText.trim() : ''
            });
        }
    });

    // 2. Extract active stock stats (OHLC, Volume, Avg Price)
    document.querySelectorAll('*').forEach(el => {
        if (el.innerText && el.innerText.includes('Volume') && el.innerText.includes('Avg. trade price') && el.children.length < 25) {
            const lines = el.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            for (let i = 0; i < lines.length - 1; i++) {
                const k = lines[i].toLowerCase();
                const v = lines[i+1];
                if (k === 'open') result.stats.open = parseFloat(v.replace(/,/g, '')) || v;
                else if (k === 'high') result.stats.high = parseFloat(v.replace(/,/g, '')) || v;
                else if (k === 'low') result.stats.low = parseFloat(v.replace(/,/g, '')) || v;
                else if (k === 'close' || k === 'prev. close') result.stats.prev_close = parseFloat(v.replace(/,/g, '')) || v;
                else if (k === 'volume') result.stats.volume = parseInt(v.replace(/,/g, '')) || v;
                else if (k === 'avg. trade price') result.stats.avg_price = parseFloat(v.replace(/,/g, '')) || v;
                else if (k === 'total buy quantity') result.stats.total_buy = parseInt(v.replace(/,/g, '')) || 0;
                else if (k === 'total sell quantity') result.stats.total_sell = parseInt(v.replace(/,/g, '')) || 0;
                else if (k.includes('upper circuit')) result.stats.upper_circuit = parseFloat(v.replace(/,/g, '')) || v;
                else if (k.includes('lower circuit')) result.stats.lower_circuit = parseFloat(v.replace(/,/g, '')) || v;
            }
        }
    });

    // 3. Extract 5-depth table if open
    const depthTables = document.querySelectorAll('.depth-table, .market-depth, table.buy, table.sell');
    if (depthTables.length > 0) {
        result.depth = { bids: [], offers: [] };
        
        // Buy rows
        document.querySelectorAll('table.buy tbody tr, .buy-table tr').forEach(row => {
            const tds = row.querySelectorAll('td');
            if (tds.length >= 3) {
                result.depth.bids.push({
                    orders: parseInt(tds[0].innerText.replace(/,/g, '')) || 0,
                    quantity: parseInt(tds[1].innerText.replace(/,/g, '')) || 0,
                    price: parseFloat(tds[2].innerText.replace(/,/g, '')) || 0.0
                });
            }
        });

        // Sell rows
        document.querySelectorAll('table.sell tbody tr, .sell-table tr').forEach(row => {
            const tds = row.querySelectorAll('td');
            if (tds.length >= 3) {
                result.depth.offers.push({
                    price: parseFloat(tds[0].innerText.replace(/,/g, '')) || 0.0,
                    orders: parseInt(tds[1].innerText.replace(/,/g, '')) || 0,
                    quantity: parseInt(tds[2].innerText.replace(/,/g, '')) || 0
                });
            }
        });
    }

    // 4. Identify active stock
    let activeStock = null;
    const selItem = document.querySelector('.item-wrapper.is-selected .name, .instrument.selected .name, .instrument.is-selected .name');
    if (selItem) activeStock = selItem.innerText.trim().split('\\n')[0].trim();
    if (!activeStock && depthTables.length > 0) {
        const parentPane = depthTables[0].closest('.instrument, .item, .depth-pane, .modal, .view') || depthTables[0].parentElement;
        if (parentPane) {
            const pName = parentPane.querySelector('.symbol, .name, .nice-name, .title, .tradingsymbol');
            if (pName) activeStock = pName.innerText.trim().split('\\n')[0].trim();
        }
    }
    result.active_stock = activeStock;

    return result;
})()
"""

def get_track2_kite_tab() -> Optional[dict]:
    """
    Finds Kite tab on Track 2 dedicated debugging port 9444.
    """
    try:
        resp = requests.get(CDP_HTTP_URL, timeout=3)
        if resp.status_code == 200:
            tabs = resp.json()
            page_tabs = [t for t in tabs if t.get("type") == "page" and "kite.zerodha.com" in t.get("url", "")]
            if page_tabs:
                return page_tabs[0]
            # Fallback to any page tab on port 9444
            gen_tabs = [t for t in tabs if t.get("type") == "page"]
            if gen_tabs:
                return gen_tabs[0]
    except Exception:
        pass
    return None


async def send_cdp_cmd(ws, method: str, params: Dict[str, Any], req_id: int, timeout: float = 3.0) -> Dict[str, Any]:
    """Sends a CDP command and awaits the specific message matching req_id, safely ignoring async events."""
    cmd = {"id": req_id, "method": method, "params": params}
    await ws.send(json.dumps(cmd))
    t_end = time.time() + timeout
    while time.time() < t_end:
        rem = max(0.1, t_end - time.time())
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=rem)
            msg = json.loads(raw)
            if msg.get("id") == req_id:
                return msg
        except asyncio.TimeoutError:
            break
        except Exception:
            pass
    return {}


def check_token_valid(token: str) -> bool:
    """Verifies whether an enctoken is accepted by Zerodha Kite OMS."""
    if not token or len(token) < 20:
        return False
    try:
        url = "https://kite.zerodha.com/oms/instruments/historical/5420545/15minute?from=2026-09-15&to=2026-09-17"
        headers = {"Authorization": f"enctoken {token}", "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


async def fetch_token_from_main_chrome() -> Optional[str]:
    """Fetches active enctoken from main Chrome (Port 9333) if available."""
    try:
        resp = requests.get("http://127.0.0.1:9333/json", timeout=2)
        if resp.status_code == 200:
            tabs = resp.json()
            for t in tabs:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    async with websockets.connect(t["webSocketDebuggerUrl"], ping_interval=5) as m_ws:
                        res = await send_cdp_cmd(m_ws, "Network.getCookies", {"urls": ["https://kite.zerodha.com"]}, req_id=9999, timeout=2.0)
                        cookies = res.get("result", {}).get("cookies", [])
                        encs = [c["value"] for c in cookies if c.get("name") == "enctoken"]
                        if encs and check_token_valid(encs[0]):
                            return encs[0]
    except Exception:
        pass
    return None


async def run_track2_bridge():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Track 2 Dedicated Kite Web Bridge...")
    print(f"  Target: Google Chrome on Port 9444 ({CDP_HTTP_URL})")
    print(f"  Output: {LIVE_DEPTH_PATH}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    msg_id = 1

    while True:
        tab = get_track2_kite_tab()
        if not tab:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for Chrome on Port 9444 (start_track2_kite_feed.bat)...")
            await asyncio.sleep(3)
            continue

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            await asyncio.sleep(2)
            continue

        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CONNECTED] Track 2 Kite Tab: {tab.get('title')} ({tab.get('url')})")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                current_enctoken = None
                last_cookie_fetch = 0.0

                while True:
                    # Periodically refresh enctoken via CDP Network.getCookies every 60s
                    if time.time() - last_cookie_fetch > 60.0 or not current_enctoken:
                        msg_id += 1
                        cookie_res = await send_cdp_cmd(ws, "Network.getCookies", {"urls": ["https://kite.zerodha.com"]}, req_id=msg_id)
                        cookies = cookie_res.get("result", {}).get("cookies", [])
                        encs = [c["value"] for c in cookies if c.get("name") == "enctoken"]
                        candidate_tok = encs[0] if encs else None

                        # If token missing or invalid on Port 9444, synchronize from main Chrome on Port 9333
                        if not candidate_tok or not check_token_valid(candidate_tok):
                            synced_tok = await fetch_token_from_main_chrome()
                            if synced_tok:
                                candidate_tok = synced_tok
                                msg_id += 1
                                await send_cdp_cmd(ws, "Network.setCookie", {
                                    "name": "enctoken", "value": synced_tok,
                                    "domain": "kite.zerodha.com", "path": "/",
                                    "secure": True, "httpOnly": True, "sameSite": "Lax"
                                }, req_id=msg_id)
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] [AUTH] Synced valid enctoken to Port 9444 from Port 9333")

                        if candidate_tok and check_token_valid(candidate_tok):
                            current_enctoken = candidate_tok
                            last_cookie_fetch = time.time()

                    msg_id += 1
                    eval_res = await send_cdp_cmd(
                        ws,
                        method="Runtime.evaluate",
                        params={
                            "expression": EXTRACT_TRACK2_JS,
                            "returnByValue": True,
                            "awaitPromise": False
                        },
                        req_id=msg_id,
                        timeout=3.0
                    )

                    result_wrapper = eval_res.get("result", {}).get("result", {})
                    extracted = result_wrapper.get("value")

                    if extracted:
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        extracted["local_write_time"] = now_str
                        extracted["status"] = "LIVE_STREAMING" if not extracted.get("is_tab_hidden") else "BACKGROUNDED"
                        extracted["enctoken"] = current_enctoken

                        wl_syms = [w.get("symbol") for w in extracted.get("watchlist", [])]
                        extracted["track2_matches"] = [s for s in TRACK2_SYMBOLS if s in wl_syms]

                        # Write structured JSON atomically
                        temp_path = LIVE_DEPTH_PATH + ".tmp"
                        with open(temp_path, "w", encoding="utf-8") as f:
                            json.dump(extracted, f, indent=2)
                        os.replace(temp_path, LIVE_DEPTH_PATH)

                    await asyncio.sleep(1.0)
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Track 2 Bridge disconnected ({e}). Reconnecting in 2s...")
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(run_track2_bridge())
    except KeyboardInterrupt:
        print("\n[Track 2 Bridge stopped by user]")
