# Regulatory Review — TASK_FIRST_REAL_REVIEW

**Verdict: REJECT / P0**

The biggest flaw is **conditioning entry on toxic order flow**: with a 2.2% fill rate, execution occurs only when an informed or urgent holder chooses to sell into an otherwise locked upper circuit. Thus, being filled is itself a negative signal—likely distribution or weakening demand. Smaller sizing limits loss severity; it cannot repair negative conditional expectancy or selection bias.

**Unresolved P0 objections:**

- Direct violation of Rule 3: locked-UC buys with zero/negligible offers are prohibited.
- Direct violation of Rule 7: this is not a two-sided accumulation entry with a valid executable stop.
- Exit liquidity remains unbounded; a stop cannot execute during zero-bid lower circuits.
- No code, order-book snapshots, queue ranks, surveillance history, or fill-level forward returns were supplied, so Rules 4, 6, and 9 cannot be verified.
- If the security is T2T/ESM, compulsory delivery and enhanced surveillance restrictions further amplify—not cure—the risk. [NSE ESM framework](https://www.nseindia.com/static/regulations/enhanced-surveillance-measure-esm), [Zerodha T2T rules](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-are-trade-to-trade-stocks)

**Required disposition:** freeze the setup. Do not submit or recommend locked-UC buys, including paper orders modeled as realistically fillable. Any research must separately estimate returns **conditional on fill**, not unconditional returns or fill rate.