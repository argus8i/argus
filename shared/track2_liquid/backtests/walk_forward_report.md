# Purged Rolling Walk-Forward Backtesting & Sensitivity Report

**Date of Execution:** 2026-10-02 01:31:51 IST  
**Evaluator:** Antigravity (Quantitative Modeling & Infrastructure Orchestrator)  
**Governing Authority:** ARGUS 8i Track 2 Liquid Desk (Sprint Day 4 Verification)  
**Execution Environment:** Python 3.14.7 | Commit Head: `feature/day4-backtest-and-stress-testing`  
**Corpus Parameters:** Initial Capital: ₹2,50,000.00 | Slot Cap: ₹38,000.00 | Planned Risk: ₹1,500.00 | Concurrent Slots: 3  

---

## 1. Executive Summary & Hurdle Gate Verdict

In strict compliance with `AGENTS.md` Rule 1 (Mandatory Paper Gate), Rule 8 v2 (Tri-Agent Consensus), Rule 9 (15% Volume Participation Cap), and Rule 11 (Track 2 F&O Isolation), this report documents the out-of-sample rolling walk-forward simulation across verified historical market archives.

### Formal Day 4 Hurdle Evaluation (Tier 2 Realistic Baseline):
| Hurdle Metric | Mandated Threshold | Realized Backtest Result | Gate Verdict |
| :--- | :--- | :--- | :--- |
| **Profit Factor** | $\ge 1.30$ | **1.00** | **FAIL** |
| **Win Rate** | $\ge 45.0\%$ | **47.1\%** | **PASS** |
| **Net Expectancy ($R$)** | $> +0.250R$ | **-0.005R** | **FAIL** |
| **Max Portfolio Drawdown** | $\le 6.00\%$ (Rs 15,000) | **7.14\%** (Rs 17,851.41 / 11.90R) | **FAIL** |
| **Cash Buffer Inviolability** | $\ge ₹1,36,000.00$ | **₹138,698.29 (100% Maintained)** | **PASS** |

**Overall Day 4 Gate Verdict:** **FAIL**

### 1.1 Empirical Interpretation & Mandatory Rule 1 Enforcement
In strict compliance with `AGENTS.md` Rule 1 (Mandatory Paper-Trading Gate) and Rule 8 v2 (Empirical Evidence Invariant):
- The out-of-sample backtest under realistic Tier 2 friction (statutory taxes + 7.5 bps normal / 25.0 bps gap slippage + flat ₹15.93 DP charges) yields a Net Profit Factor of **1.00**, Win Rate of **47.1%**, Net Expectancy of **-0.005R**, and Max Drawdown of **7.14%**.
- These metrics **FAIL** the qualification hurdle criteria.
- **Capital Gate Status:** Real capital deployment is strictly refused per Rule 1.
- **Significance:** This unvarnished result demonstrates the immense value of realistic transaction modeling over naive backtests. In theoretical gross terms (Tier 1), the strategy appears significantly more forgiving, but statutory friction and gap slippage reveal true net expectancy. Paper observation across live forward sessions (Rule 1) is mandatory before any capital allocation.

---

## 2. Purged Rolling Fold Architecture

Information boundaries strictly enforced via `PurgedFoldManager`:
1. **Fold 1 (2023 Out-of-Sample Evaluation):**
   - **Training/Warmup Window:** `2022-01-03` to `2022-12-15` (237 sessions)
   - **Purge Embargo Gap:** `2022-12-16` to `2022-12-30` (10 trading sessions)
   - **Out-of-Sample Test Window:** `2023-01-02` to `2023-12-29` (245 trading sessions)
2. **Fold 2 (2024 Out-of-Sample Evaluation):**
   - **Training/Warmup Window:** `2023-01-02` to `2023-12-14` (235 sessions)
   - **Purge Embargo Gap:** `2023-12-15` to `2023-12-29` (10 trading sessions)
   - **Out-of-Sample Test Window:** `2024-01-01` to `2024-09-30` (187 trading sessions)
3. **Untouched Benchmark Holdout (2025–2026):**
   - **Window:** `2025-01-01` to `2026-09-24` (422 trading sessions)
   - **Status:** **SEALED & UNTOUCHED**. Guarded fail-closed via `allow_holdout=False` in `WalkForwardEngine` and `run_fold_simulation`.

### 2.1 Fold Independence & Boundary Accounting
Folds 1 and 2 are evaluated as independent out-of-sample walk-forward slices, each initialized with ₹250,000.00 starting cash. At the end of each fold, any active positions are marked to market as of the final session (`as_of_session`) and logged as `UNRESOLVED` with explicit MTM valuations in `trades.csv`. Unresolved positions are not artificially liquidated or carried across independent fold boundaries. Daily equity and cash series are tracked per fold in `daily_equity.csv`. Pooled metrics summarize all closed trades across both folds.

---

## 3. Fold Performance Breakdown (Tier 2 Realistic Baseline)

| Metric | Fold 1 (2023 Evaluation) | Fold 2 (2024 Evaluation) | Pooled Combined |
| :--- | :--- | :--- | :--- |
| **Total Trades** | 79 | 57 | 136 |
| **Win Rate** | 46.8% | 47.4% | 47.1% |
| **Gross Profit** | ₹47,284.67 | ₹31,176.62 | ₹78,461.29 |
| **Gross Loss** | ₹44,347.39 | ₹33,813.51 | ₹78,160.90 |
| **Net Realized PnL** | ₹2,937.29 | ₹-2,636.89 | ₹300.39 |
| **Profit Factor** | 1.07 | 0.92 | 1.00 |
| **Mean Expectancy ($R$)** | +0.006R | -0.021R | -0.005R |
| **Max Drawdown (₹)** | ₹17,851.41 | ₹6,908.60 | ₹17,851.41 |
| **Max Drawdown (%)** | 7.14% | 2.76% | 7.14% |

---

## 4. Multi-Tier Friction Sensitivity & Monotonicity Verification

Paired repricing of the identical fill ledger proving monotonic net PnL degradation under progressive friction hurdles:

| Friction Tier | Execution Slippage | Statutory Taxes & Fees | Net Realized PnL | Degradation vs Gross |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Theoretical Gross)** | 0.0 bps | Zero | **₹16,311.98** | 0.0% (Baseline) |
| **Tier 2 (Realistic Baseline)** | 7.5 bps normal / 25.0 bps gap | Full Itemized + ₹15.93 DP | **₹300.39** | -98.2% |
| **Tier 3 (Severe Stress)** | 20.0 bps normal / 50.0 bps gap | Full Itemized + ₹15.93 DP | **₹-9,003.11** | -155.2% |

**Monotonic Invariant Check:** `Tier 1 (Gross) > Tier 2 (Realistic) > Tier 3 (Severe)` **CONFIRMED**.

---

## 5. Execution Realism Invariants Verified
- **No Same-Bar Inverted Exit Bias:** Stop-loss verified before target on ambiguous same-bar touches.
- **Gap-Down Fill Realism:** Orders opening below stop loss fill at opening price minus adverse gap slippage (>1R loss).
- **Claude Rule 9 Participation Cap:** Orders capped at 15% of daily turnover across all sleeves.
- **DP Charge Grouping:** Flat ₹15.93 applied once per symbol per sell session.
- **Adjusted A1 Allocation:** 3 concurrent slots, ₹38,000 slot cap, ₹114,000 exposure ceiling, and ₹136,000 unencumbered cash buffer maintained fail-closed.
