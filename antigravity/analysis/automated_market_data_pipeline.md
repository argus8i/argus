# Feasibility & Architecture Report: Automated Live Market Data Pipeline

**Document ID:** `antigravity/analysis/automated_market_data_pipeline.md`  
**Date:** 2026-09-10  
**Lead Investigator:** Antigravity (Local Engineering & Quantitative Builder)  
**Curator:** Yashu (Lead Trader / Orchestrator)  
**Audience:** Claude (Quantitative Analyst), ChatGPT (Forensic Researcher)  

---

## 1. Executive Summary & Direct Answer to Yashu

### Can Claude or ChatGPT do this by themselves?
**NO.**
- **Claude** (Anthropic) and **ChatGPT** (OpenAI) live entirely inside remote cloud servers in US data centers.
- They cannot connect to your local Windows PC, cannot interact with your local Google Chrome browser (`chrome.exe`), cannot open persistent 6.5-hour WebSocket streams to Indian brokers, cannot run local background processes, and cannot write files directly into `c:\Users\yashw\swing trades`.
- Cloud AI sandboxes are strictly turn-based: they only run when you submit a prompt and shut down seconds later. They cannot run an unattended background daemon from 09:00 to 15:30 IST.

### Can Antigravity do it?
**YES. 100% yes.**
- **Antigravity** runs directly on your local Windows machine with:
  - Native Python 3.14.7 runtime;
  - Full Windows PowerShell execution permissions (`run_command`);
  - Background daemon lifecycle management (`manage_task`, `schedule`);
  - Direct read/write access to your local filesystem (`c:\Users\yashw\swing trades`);
  - Local Chrome automation (via Chrome DevTools Protocol / Playwright / Selenium);
  - The ability to maintain persistent, uninterrupted background WebSocket streams during the entire trading day.

### How does the handoff work so "the others can read it"?
* **Antigravity acts as the Data Ingestion Engine & Local Daemon Runner.**
* Antigravity ingests the live market depth, volume ticks, circuit bands, and official EOD Bhavcopy, writing structured, standardized files directly to disk:
  - `shared/live_depth.json`: Real-time snapshot of the 5-depth order book, queue ratios, and circuit limits.
  - `CHATGPT/observation_log.csv`: The standardized paper-trading ledger required by Rule 1 of `AGENTS.md`.
  - `antigravity/logs/market_ticks.csv`: Full tick-by-tick / minute-by-minute execution history.
* Once Antigravity writes this data locally:
  - **Claude** reads the exact numbers to calibrate fill probabilities and red-team execution queues.
  - **ChatGPT** reads the exact numbers to verify surveillance actions and corporate filings.
  - **You never have to take a screenshot or crop an image again.**

---

## 2. Comparative Agent Capability Matrix

| Capability / Resource | Antigravity (Google / Local PC) | Claude (Anthropic Cloud) | ChatGPT (OpenAI Cloud) |
| :--- | :--- | :--- | :--- |
| **Execution Environment** | **Local Windows PC (`c:\Users\yashw\swing trades`)** | Remote Cloud VM (Linux) | Remote Cloud Container (Linux) |
| **Local OS Shell & Python** | **YES** (PowerShell, native Python 3.14.7) | NO | NO |
| **Direct Filesystem Access** | **YES** (Direct read/write to project folder) | NO (Downloadable artifacts only) | NO (Downloadable artifacts only) |
| **Background Daemons & Timers** | **YES** (`manage_task`, `IsDaemon: true`, `schedule`) | NO (Turn-based request/reply only) | NO (Turn-based request/reply only) |
| **Persistent WebSockets (6.5 hrs)**| **YES** (Local daemon runs 09:00–15:30 IST) | NO (Timeout within 60–120 seconds) | NO (Timeout within 60–120 seconds) |
| **Local Google Chrome Access** | **YES** (Connects to `chrome.exe` via CDP / port 9222) | NO | NO |
| **Credential Safety (.env)** | **HIGH** (Tokens/passwords stored on local disk) | **RISK** (Broker credentials sent to cloud) | **RISK** (Broker credentials sent to cloud) |
| **Optimal Assigned Role** | **Data Ingestion Engine & Local Daemon Runner** | **Quantitative Analyst & Queue Red-Teamer** | **Forensic Researcher & Regulatory Auditor** |

---

## 3. Evaluation of Ingestion Options for Indian Equities

### Option 1: Direct Exchange Web Endpoints (BSE India Public APIs & Bhavcopy)
* **Cost:** ₹0.00.
* **5-Depth Market Depth:** Not available publicly via exchange web endpoints.
* **Circuit Bands & Surveillance:** Excellent. Dedicated BSE endpoint `https://api.bseindia.com/BseIndiaAPI/api/PriceBand/w?scripcode={code}` returns exact daily Upper Circuit, Lower Circuit, and Band % for all stocks (including ESM Stage 2 2% limits).
* **EOD Official Bhavcopy:** Direct daily CSV download at 18:00 IST via `https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{date}_F_0000.CSV`.

### Option 2: Indian Broker APIs (The Professional Streaming Standard)
* **Option 2A: Zerodha Kite Connect API (₹2,000 / month)**
  - *Pros:* You already trade on Zerodha Kite. Single account. Binary WebSocket ticker streams real-time 5-depth order books, tick volume, and total bid/ask queues.
  - *Cons:* Costs ₹2,000/month. Morning login requires OAuth redirect or web session token generation.
* **Option 2B: Angel One SmartAPI (100% FREE)**
  - *Pros:* Zero recurring cost. Provides full Level 2 (5-depth) binary WebSocket streaming. Dedicated headless authentication API (`loginByPassword` with TOTP) allows 100% hands-free morning startup at 08:45 AM without opening a browser.
  - *Cons:* Requires opening a free Angel One Demat account (takes ~15 mins online via Aadhaar e-KYC).

### Option 3: Local Headless Browser / MCP Automation (Zero-Cost Kite Web Bridge)
* **Cost:** ₹0.00.
* **How it works:** You launch Google Chrome locally with `--remote-debugging-port=9222` and log into Kite Web. Antigravity connects via Chrome DevTools Protocol (CDP) / Playwright, reading the 5-depth DOM order book or sniffing the WebSocket frames (`wss://ws.zerodha.com`) directly from Chrome.
* **Pros:** ₹0 cost, uses your existing Zerodha account.
* **Cons:** Requires Chrome to be open on your PC during market hours.

### Option 4: Financial Web APIs (Yahoo / Google Finance)
* **Verdict:** Completely unusable. Zero order-book depth, 15-minute exchange delay, and broken micro-cap tickers.

---

## 4. Phased Implementation Roadmap

### Phase 1: Zero-Cost Immediate Ingestion (Deployable Immediately)
1. **Pre-Open Surveillance Poller (`bse_surveillance_poller.py`):** Runs at 08:50 AM to fetch daily price bands and circuit limits for watchlist tickers.
2. **EOD Bhavcopy Automated Ingestor (`bhavcopy_downloader.py`):** Runs at 18:00 IST every day, downloads official BSE Bhavcopy, and logs exact Close, Volume, and Trades for CROPSTER, CHANDRIMA, CCDL, etc.
3. **Zero-Cost Kite Web Depth Bridge (`kite_web_depth_bridge.py`):** Connects to your open Kite Web tab via Chrome DevTools Protocol and dumps live 5-depth order books into `shared/live_depth.json` every 2 seconds.

### Phase 2: Production-Grade Hands-Free Broker API Pipeline
* Deploy either **Zerodha Kite Connect (₹2,000/mo)** or **Angel One SmartAPI (100% Free)**.
* Run headless morning authentication at 08:45 AM.
* Stream real-time 5-depth ticks from 09:00:00 to 15:30:00 IST directly into `CHATGPT/observation_log.csv` and `antigravity/logs/market_ticks.csv`.
