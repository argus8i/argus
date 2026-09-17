# Master Watchlist Index — Decoupled Dual-Track Architecture

**Last updated:** 2026-09-12 | **Governance:** AGENTS.md Rule 11 (Absolute Track Isolation)

To eliminate agent confusion between micro-cap circuit trading and liquid F&O momentum, the watchlists are permanently partitioned into two dedicated tracks:

---

## [Track 1: Micro-Cap Circuit & Surveillance Watchlist](file:///c:/Users/yashw/swing%20trades/shared/track1_esm/02_WATCHLIST.md)
*Dedicated Location:* [`shared/track1_esm/02_WATCHLIST.md`](file:///c:/Users/yashw/swing%20trades/shared/track1_esm/02_WATCHLIST.md)
- **Scope:** BSE/NSE Micro-Caps (Mcap < ₹500 Cr, Price ≥ ₹10.00 Floor) under exchange surveillance (ESM Stage 1/2, GSM, ASM, Trade-to-Trade).
- **Current Scrips:**
  - `CCDL` (BSE: 539091) — Locked Lower Circuit on 11-Sep (2.05 Cr offers, 0 bids).
  - `CROPSTER` (BSE: 523105) — Blacklisted (10 consecutive LC descent).
  - `CHANDRIMA` (BSE: 540829) — ESM Stage 2 Periodic Call Auction (PCAS), ±2% band.
  - `GATECH` / `GATECH-BE` — Blacklisted (sub-₹10 floor violation).
- **Governing Rules:** Rules 2, 3, 4, 5, 6, 7, 9, 10.

---

## [Track 2: Liquid High-Beta Momentum Watchlist](file:///c:/Users/yashw/swing%20trades/shared/track2_liquid/02_WATCHLIST.md)
*Dedicated Location:* [`shared/track2_liquid/02_WATCHLIST.md`](file:///c:/Users/yashw/swing%20trades/shared/track2_liquid/02_WATCHLIST.md)
- **Scope:** Liquid Midcap 150 / Smallcap 250 (`EQ` Series) active F&O Underlyings trading with dynamic flexing bands and continuous two-sided liquidity.
- **Current Scrips:**
  - **Basket A (Primary Screen-Qualified):** `CDSL`, `ANGELONE`, `SUZLON`, `INOXWIND` (All $\ge 15\%$ institutional holding, ₹4,000–₹75,000 Cr Mcap).
  - **Basket B (Manual Sovereign PSU Satellite):** `IREDA`, `RVNL`, `COCHINSHIP`, `BDL` (Exempt from 15% institutional screen due to 70–75% GOI promoter ownership).
- **Governing Rules:** 15-minute Opening Range Breakout (ORB), strict ₹1,500 rupee risk budget, dynamic R:R (SL-Limit 1:1.55 or SL-M 1:2.0).

---

## Change log

| Date | Who | Change |
|---|---|---|
| 2026-09-09 | Claude | Created from the 20 screenshots in `data screenshots/`. |
| 2026-09-11 | Antigravity | Added Track 2 Liquid High-Beta Momentum Watchlist. |
| 2026-09-11 | Tri-Agent | Peer-review reconciliation: corrected NSE SL-M availability, labeled Track 2 as MANUAL_RESEARCH_BASKET. |
| 2026-09-12 | Antigravity | Decoupled architecture under AGENTS.md Rule 11: created dedicated `shared/track1_esm/` and `shared/track2_liquid/` watchlists. |
