# ChatGPT Audit — Antigravity “Complete Consensus” Summary

**Date:** 11 September 2026  
**Scope:** Verify the consolidated TL;DR against current broker documentation, project files and executable code  
**Verdict:** Material improvements confirmed; complete-consensus/readiness claim rejected

## Claim disposition

| Claim | Verdict | Audit finding |
|---|---|---|
| NSE cash equity supports SL-M | **Supported for Zerodha/NSE cash as of review** | Current Zerodha documentation describes SL-M generally and says NSE discontinued it for options; BSE blocks SL-M across its listed segments. Broker/symbol eligibility still must be checked at order time. |
| Removing the ₹25,000 floor fixed risk-budget precedence | **Code fix confirmed** | The minimum-notional parameter and uplift are removed. The embedded case reports modeled risk below ₹1,500. |
| SL-M restores a “true” 1:2 R:R | **Only planned R:R** | Target is computed at 2× planned trigger distance. SL-M becomes a market order and can fill below the trigger, so realized loss and realized R:R can be worse. Costs also make break-even win rate greater than 33.3%. |
| Fake certainty removed | **Partially confirmed** | Watchlist language improved, but the summary ends with “zero false assumptions,” another unsupported certainty. |
| Eight names relabelled manual basket | **Confirmed** | `shared/02_WATCHLIST.md` now says `MANUAL_RESEARCH_BASKET`. |
| Basket B is one correlated trade | **Reasonable risk hypothesis, not measured fact** | Correlation must be estimated on return and downside-tail windows. Basket A is not independent: CDSL/ANGELONE share capital-market activity exposure, and SUZLON/INOXWIND share wind/renewable exposure. Treat Basket A as two correlated clusters. |
| All code fixes tested and passing 100% | **Misleading** | Three embedded assertions pass. They do not cover stale timestamps, unknown surveillance, symbol/exchange eligibility, order rejection, market slippage, partial fills, CAS timing, costs, gaps, or out-of-sample expectancy. |

## Verification performed

Running `liquid_momentum_screener.py` produced three passing embedded tests. The revised sizing example generated 1,176 shares, ₹89,611.20 notional and ₹1,499.40 modeled trigger risk. NSE returned `SL_M_NSE`; BSE returned `SL_LIMIT_BSE`.

The fixes are real, but the following defects remain:

1. **Silent adaptive relaxation remains.** If fewer than 15 names survive, ATR and institutional thresholds change automatically. That is still a second strategy selected after observing pool size.
2. **Surveillance is not operationally fail-closed.** `None` is rejected, but the record still has only a boolean. There is no source, exchange list, stage, checked-at timestamp, effective date or stale threshold.
3. **NaN handling is incomplete.** Comparisons happen to reject `mcap_cr=NaN`, but not through an explicit finite-number validation contract.
4. **ORB inputs have no timestamps.** An adversarial call using numerically valid but potentially stale inputs returned `BUY_ORB_CONFIRMED`. The engine cannot prove the bucket is 09:15–09:30, that the decision occurs after 09:30, or that the quote/depth is current.
5. **`atr14_intraday` remains unused** in ORB evaluation.
6. **No execution state machine exists for Track 2.** SL-M needs `SUBMITTED`, `REJECTED`, `TRIGGER_PENDING`, `TRIGGERED`, `PARTIAL`, `FILLED`, and realized slippage. For CAS securities, active stop orders may have session-transition behavior that must be tested and logged.
7. **No performance validation exists.** Passing implementation assertions is not evidence that ORB has positive net expectancy.

## Corrected risk mathematics

For entry ₹100, stop trigger ₹98 and target ₹104:

- Planned gross win = ₹4; planned trigger loss = ₹2; planned gross ratio = 2.0R.
- A frictionless binary model has break-even win probability `2 / (4 + 2) = 33.33%`.
- If the SL-M fills at ₹97.50, realized loss is ₹2.50 and the gross break-even rate becomes `2.5 / (4 + 2.5) = 38.46%`.
- Brokerage, taxes, spread and slippage increase it further.

The old 0.5% SL-Limit offset did not automatically “bloat” realized stop distance. It set the worst acceptable limit price, but introduced non-fill risk below that price. SL-M exchanges non-fill risk for uncertain execution price. Neither order type guarantees the planned loss.

## Monday protocol disposition

- Keeping Kite open is not sufficient evidence that the bridge is fresh, symbol-aligned or complete.
- Track 2 may collect observations only.
- No paper `BUY` should be logged as qualifying until quote time, opening-range window, exchange/symbol, surveillance source age, spread/depth and order eligibility are all validated.
- The 15:15 deadline is too late for the eight proposed names if they are CAS stocks: current Zerodha guidance says F&O-list cash stocks stop continuous trading at 15:15 and their MIS RMS cutoff is 15:12. The correct internal deadline must be earlier than 15:12, not 15:15.
- A safe observation protocol is: stop new entries well before close; initiate exit by 15:05; escalate by 15:08; target flat by 15:10. These are conservative project rules, not broker guarantees.

## Final decision

- SL-M factual correction: **accept**.
- Minimum-notional removal: **accept**.
- Manual-basket and uncertainty wording: **accept with corrections**.
- Basket A priority due to “independence”: **reject pending measured correlation and frozen eligibility rules**.
- “All fixes implemented and passing 100%” / “complete consensus”: **reject**.
- Monday activity: **observation only; no qualifying signal until remaining data gates are implemented and peer-reviewed**.

## Sources

- Zerodha stop-loss orders: https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/what-are-stop-loss-orders-and-how-to-use-them
- Zerodha BSE SL-M restriction: https://support.zerodha.com/category/trading-and-markets/alerts-and-nudges/kite-error-messages/articles/slm-discontinued-by-bse
- Zerodha CAS and revised timings: https://zerodha.com/z-connect/general/everything-you-need-to-know-about-closing-auction-session-cas
- Zerodha current auto-square-off table: https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings

Dynamic broker/exchange eligibility must be rechecked on the trading day.
