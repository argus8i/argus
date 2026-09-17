## Core flaw: sizing assumes exchangeability; adverse selection destroys it

Treating "how much to buy at upper circuit" as a sizing problem implicitly assumes fills are a **random (or at least exogenous) sample** of the trade opportunity — i.e., that getting filled tells you nothing bad about the trade. That assumption is false by construction here, and the falsity is *informative*, not just noisy.

**1. The conditioning trap (Groucho Marx / winner's curse)**
At a locked upper circuit, buy-side demand vastly exceeds sell-side supply. The only fills you get are from the *residual* supply curve — sellers with the lowest reservation price to hold, i.e., people exiting for reasons uncorrelated with (or adverse to) the bullish thesis: liquidity needs, profit-taking after already large gains, insiders/promoters distributing into strength, or — worst case — informed sellers who know something the circuit-driven crowd doesn't. Conditioning on "I got filled" is conditioning on "someone who wanted out badly enough to sell at the ceiling existed." E[value | filled] ≤ E[value | wanted to buy]. A sizing model calibrated on unconditional expected return will systematically overstate edge on every filled trade.

**2. 2.2% fill rate is not a liquidity nuisance — it's the adverse-selection signal itself**
A 2.2% fill rate means 97.8% of intended size never executes. Under a sizing framework, this just becomes "we're underinvested, no big deal, average up next time." But the fill rate *is* the posterior update: it tells you how thin and adversarial the counterparty pool is. Treating it as a friction to be sized around (rather than a variable that should shrink your position **and** your confidence in the thesis) is a category error. The correct move is to update P(good trade | low fill rate) downward, not just Kelly-fraction the sleeve.

**3. Selection bias compounds across the holding period, not just at entry**
Even if the entry-day adverse selection were small, you're accumulating a portfolio that is disproportionately weighted toward names where circuit-day sellers were most desperate to exit. That's a persistent, not one-time, bias — it contaminates the return distribution of the whole strategy's realized fills, meaning your backtested Sharpe on *filled* trades is not an unbiased estimate of the *strategy's* Sharpe (the strategy includes the 97.8% you never got).

**4. Sizing frameworks (Kelly, vol-targeting, ATR-scaling) assume i.i.d. or at least stationary edge conditional on entry — this is neither**
Kelly-style sizing optimizes bet size given a *known, unconditional* edge/variance. Here the edge is a hidden function of realized fill rate (endogenous), and variance is heteroskedastic in exactly the state (thin fills) where size would otherwise be increased to "make up" for small fills. Any sizing rule that increases allocation when fills are scarce (a natural instinct — "so few trades, size up on the ones you get") is directionally backwards: scarce fills are precisely the regime with the worst counterparty composition.

**5. Missing counterfactual: no unfilled-order tracking**
Without a model of the ~97.8% unfilled orders (limit price, queue position, would-be counterparty), you cannot separate "good stock, bad luck getting filled" from "filled because it was a bad stock." The strategy has no mechanism to distinguish these, so it cannot even measure the adverse-selection discount, let alone correct for it.

## Unresolved P0 objections
1. **No adverse-selection-adjusted return series exists.** Reported backtest returns are conditional-on-fill returns; the strategy's true expectancy (including opportunity cost of unfilled 97.8%) is unmeasured and likely materially lower, possibly negative net of adverse selection + slippage.
2. **No model of *why* the seller sold.** Without classifying counterparty type (informed exit vs. liquidity exit vs. promoter distribution), any probability estimate of "good outcome" is unfounded.
3. **Fill-rate-as-signal is unused.** The strategy has a directly observable adverse-selection proxy (2.2%) and is not conditioning position size or conviction on it.
4. **Sizing math (Kelly/vol-target) is being applied to a non-stationary, endogenously-selected fill distribution** — a textbook misuse that will overstate safe bet size.

**Bottom line:** this isn't "we're getting small fills, so size modestly" — it's "every fill you get is adversely selected, so the correct response is a *microstructure* fix (limit-price/queue modeling, seller-type classification, fill-rate-conditioned position throttling) not a sizing formula." Sizing optimizes within a correct distribution; here the distribution itself is corrupted by the mechanism that produces fills.