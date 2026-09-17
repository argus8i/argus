# Track 2 Watchlist: Liquid High-Beta Momentum Basket

**Last Updated:** 2026-09-12 | **Desk Head:** Yashu | **Status:** `MANUAL_RESEARCH_BASKET` (AGENTS.md Rule 1)

---

## 1. Basket A: Primary Automated Universe (Strict Screen Qualified ≥ 15% Institutional)
*These 4 scrips pass all strict quantitative gates in `antigravity/models/liquid_momentum_screener.py`: Market Cap ₹4,000–₹75,000 Cr, 20-day DTV ≥ ₹30 Cr, Beta ≥ 1.3, 14-day ATR ≥ 3.5%, and Institutional Holding ≥ 15.0%. All are active F&O underlyings with dynamic flexing price bands.*  
*Provenance Metadata: Mcap & Institutional % sourced from NSE quarterly shareholding disclosures (Q1-FY25, MEASURED); 20D DTV, Beta (1-yr vs Nifty 50), and 14D ATR% derived from NSE daily Bhavcopy & historical OHLC (DERIVED).*

| Stock | Series | F&O? | Mcap (₹ Cr) | 20D DTV | Beta | ATR% | Inst. % | Strategy Horizon | Microstructure Notes |
|---|---|---|---|---|---|---|---|---|---|
| **CDSL** | NSE: EQ | **Yes** | ~28,000 | ₹120 Cr | 1.45 | 4.1% | **25.5%** | MIS Intraday & CNC Swing | Clean VWAP respect, high market-turnover beta, depository monopoly. |
| **ANGELONE** | NSE: EQ | **Yes** | ~27,200 | ₹180 Cr | 1.62 | 4.8% | **34.0%** | MIS Intraday & CNC Swing | Highest institutional holding in universe (~34%). Rapid morning volume discovery. |
| **SUZLON** | NSE: EQ | **Yes** | ~62,500 | ₹550 Cr | 1.39 | 4.8% | **26.0%** | MIS Intraday & CNC Swing | Massive DTV ₹500Cr+. Fits comfortably within ₹75,000 Cr screener ceiling. High retail/DII volume velocity. |
| **INOXWIND** | NSE: EQ | **Yes** | ~13,000 | ₹95 Cr | 1.55 | 5.2% | **24.5%** | MIS Intraday & CNC Swing | Strong relative volume breakouts, clean multi-day momentum trends. |

---

## 2. Basket B: Experimental Sovereign PSU Satellite (Manual Research / Screen Exempt)
*Note (Claude Red-Team Audit & Tri-Agent Consensus): Sovereign PSUs with 70–75% Government of India (GOI) promoter ownership cannot mathematically meet a 15% institutional float screen. They are classified as a manual research satellite and excluded from the automated screener to avoid triggering artificial threshold relaxation.*  
*Provenance Metadata: Mcap & Institutional % sourced from NSE quarterly shareholding disclosures (Q1-FY25, MEASURED); 20D DTV, Beta (1-yr vs Nifty 50), and 14D ATR% derived from NSE daily Bhavcopy & historical OHLC (DERIVED).*

| Stock | Series | F&O? | Mcap (₹ Cr) | 20D DTV | Beta | ATR% | Inst. % | Strategy Horizon | Surveillance & Microstructure Notes |
|---|---|---|---|---|---|---|---|---|---|
| **IREDA** | NSE: EQ | **Yes** | ~31,900 | ₹250 Cr | 2.10 | 5.2% | ~4.9% | MIS Intraday Only | High ATR (5%+). Low institutional float due to 75% GOI holding. Correlated PSU energy co-mover. |
| **RVNL** | NSE: EQ | **Yes** | ~42,800 | ₹320 Cr | 1.70 | 4.2% | ~9.0% | MIS Intraday Only | Exited Short-Term ASM in Jan 2026. Strong morning volume clusters; gaps heavily with PSU news. |
| **COCHINSHIP**| NSE: EQ | **Yes** | ~40,200 | ₹210 Cr | 1.85 | 4.9% | ~9.8% | MIS Intraday & CNC Swing | F&O underlying since 1-Apr-2026 (lot 400). Exited Long-Term ASM Sep 2025. Defence momentum play. |
| **BDL** | NSE: EQ | **Yes** | ~45,500 | ₹140 Cr | 1.50 | 3.9% | ~13.2%| CNC Swing Only | Defence swing candidate. Highest institutional float in PSU basket (13.2%). |

---

## 3. Execution & Sizing Baseline for Track 2
- **Capital State:** 100% Cash (Rule 1). Paper-trading observation only.
- **Risk per Trade:** ₹1,500.00 fixed rupee risk budget.
- **Stop-Loss Mode:** Baseline engine is configured for **SL-Limit** with a 0.5% buffer (1:1.55 R:R, 39.2% breakeven).
- **Q13 Trigger:** If Yashu verifies that Zerodha accepts SL-M on NSE cash, true 1:2.0 R:R (33.3% breakeven) will be activated for Basket A.

