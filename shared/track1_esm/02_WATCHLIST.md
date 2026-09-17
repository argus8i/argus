# Track 1 Watchlist: Micro-Cap Circuit & Surveillance Basket

**Last Updated:** 2026-09-12 | **Desk Head:** Yashu | **Status:** `OBSERVATION_ONLY` (AGENTS.md Rule 1)

---

## Active Surveillance Watchlist

| Stock | Exchange / Code | Price (11 Sep) | Day Chg | Band | Stage / Group | Status | Microstructure & Surveillance Notes |
|---|---|---|---|---|---|---|---|
| **CCDL** | BSE: 539091 | ₹1.32 | −4.34% | 5% | ESM Stage 1 (T) | `TRACK` | **Locked Lower Circuit on 11-Sep.** Total Bids: 0. Total Offers: 2.05 Crore (1.77 Cr at 1.32 across 925 orders). Traded Volume: 7.6 Lakh. Queue ratio $\rho = 23.3$ (0% fill probability). Yashu's T+1 exit at 1.38 saved capital from unbroken LC descent. Fails ₹10 floor (Rule 2). |
| **CROPSTER** | BSE: 523105 | ₹3.02 | −4.73% | 5% | ESM Stage 1 (T) | `BLACKLIST` | **Verified 10 consecutive LC sessions** (-39.66% unfillable drawdown) and secondary 9-day LC descent (-36.04%). Fails ₹10 floor (Rule 2). Disclosed 3.58 Cr share promoter exit by Nayanaben Shah at ₹3.05. |
| **CHANDRIMA** | BSE: 540829 | ₹14.92 | −1.97% | **2%** | **ESM Stage 2 (PCAS)** | `TRACK` | **Periodic Call Auction Session (1-hour auctions).** Price band clamped to ±2%. Traded volume collapsed to 6,355 shares. Trapped at 144% of daily volume (9.6 sessions to clear at 15% participation). Passes ₹10 floor, but frozen under Rule 6 surveillance rules. |
| **GATECH** | BSE: 531723 | ₹0.75 | −3.85% | 5% | ESM Stage 1 (T) | `BLACKLIST` | Locked at lower circuit. Total volume 2.68 Lakh shares. Fails ₹10 floor by 13×. |
| **GATECH-BE** | NSE: GATECH | ₹0.74 | −2.63% | 5% | `BE` (Trade-to-Trade) | `BLACKLIST` | Gross delivery required. No intraday squaring off permitted. Fails ₹10 floor. |

---

## Prospective Observation Candidates (Tuesday 15-Sep-2026 Session 1)
*Screened from historical BSE records and live BSE exchange price band feeds. All meet AGENTS.md Rules 1, 2, 3, 5, 6, 7, 9.*

| Scrip Code | Symbol | Group | LTP (15-Sep) | Band | Day Vol (09:47) | Headroom to UC | Rule 6/9 Gate Status | Session 1 Action (Rule 5 Sizing) |
|---|---|---|---|---|---|---|---|---|
| **544497** | **ANLON** (Anlon Healthcare) | `B` | ₹20.30 (+3.99%) | 20% | 2,248,000 sh | 15.37% | **PASS** (No surveillance; vol 15.5x) | **PAPER FILLED**: 614 shares @ ₹20.30 (₹12,464 deployed, Stop: ₹18.50) |
| **544305** | **MOBIKWIK** (One Mobikwik) | `B` | ₹208.85 (-0.33%) | 20% | 91,000 sh | 20.40% | **PASS** (No surveillance; proj 1.03M) | **PAPER FILLED**: 59 shares @ ₹208.85 (₹12,322 deployed, Stop: ₹194.00) |
| **533056** | **VEDAVAAG** (Vedavaag Systems) | `B` | ₹24.19 (+3.55%) | 20% | 8,275 sh | 15.80% | **WATCH** (Awaiting volume pickup) | Order queued; awaiting >20k volume turnover |
| **533343** | **LOVABLE** (Lovable Lingerie) | `B` | ₹71.79 (-0.51%) | 20% | 1,944 sh | 20.60% | **WATCH** (Volume thin: 1,944 sh) | Participation gate restricts entry until volume >10k |
| **500240** | **KINETIC** (Kinetic Engineering) | `X` | ₹226.15 (-1.39%) | **5%** | 2,013 sh | 6.48% | **DISQUALIFIED BY RULE 6** | **REJECTED**: Band cut to 5% + ESM Stage 1 escalation detected |

---

## Track 1 Specific Rules & Execution Filters
1. **Rule 1 (Observation Only):** Capital is 100% Cash. Minimum 60 prospective sessions / 20 realistic fills required. Current milestone: **1 / 60 Sessions | 2 / 20 Fills**.
2. **Rule 2 (Absolute ₹10 Floor):** Any security trading below ₹10.00 is immediately disqualified.
3. **Rule 3 (No UC Chasing):** Never submit buy limit orders on locked upper circuits with zero offers.
4. **Rule 5 (10-Day LC Sizing):** $\text{Max Size} = \text{Budget} / 0.401$. Standardized strictly to 10-day LC descent ($1 - 0.95^{10} = 40.126\%$).
5. **Rule 6 (Surveillance Exit):** Any band cut ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) mandates immediate exit into earliest available liquidity.
6. **Rule 7 (Pre-Circuit Accumulation):** Buy only during 2-sided base with $\ge 3\times$ volume expansion, spread $< 1\%$, range $> 3\%$.
7. **Rule 9 (Liquidity Gate):** Max 15% daily participation over 2 clearable sessions.


