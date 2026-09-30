# 05. Capital Sizing, Risk Budgeting & The Adjusted A1 Blueprint

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **IN PROGRESS** (Adjusted A1 codified; legacy ₹58,333 / ₹1,75,000 deprecation remediation staged)  
**Governing Decision:** Confirmed by Yashu on 25 September 2026  

---

## 1. Plain-English Summary

In quantitative trading, position sizing determines whether a system survives market turbulence or goes bankrupt. Even a strategy with a 70% win rate will destroy an account if positions are too large during an inevitable string of losses.

Project ARGUS operates under a conservative portfolio sizing framework called **Adjusted A1**:
- We start with an authorized capital pool of **₹2,50,000**.
- We never invest all our money at once. More than half of the money (**₹1,36,000 or 54.4%**) sits untouched in cash as a permanent safety buffer.
- We divide the remaining capital into **exactly 3 independent trading slots**, with each slot strictly capped at **₹38,000**.
- For every individual trade, we calibrate our stop-loss so that we plan to lose no more than **₹1,500** if the stop is triggered.
- We never pretend that a stop-loss is a guarantee. If the market crashes overnight and stocks open down −10% or −20%, our capital caps guarantee that the entire account survives without catastrophic damage.

---

## 2. Yashu's Approved Adjusted A1 Sizing Specifications

Confirmed by Project Owner Yashu on 25 September 2026:

| Parameter | Approved Value | Description & Purpose |
|---|---|---|
| **Total Portfolio Corpus** | **₹2,50,000.00** | Total authorized equity base dedicated to Track 2 operations. |
| **Max Concurrent Slots** | **3 Positions** | Maximum number of active positions allowed simultaneously. |
| **Per-Slot Notional Cap (`SLOT_CAP_RS`)** | **₹38,000.00** | Hard upper limit on the rupee purchase value of any single position. |
| **Max Aggregate Exposure (`AGGREGATE_EXPOSURE_CAP_RS`)** | **₹1,14,000.00** | Strict combined cap ($3 \times ₹38,000$) across **active positions PLUS pending orders**. |
| **Mandatory Cash Buffer** | **₹1,36,000.00** | Unallocated cash reserve (54.4% of corpus) providing total drawdown resilience. |
| **Planned Risk Budget (`RISK_BUDGET_RS`)** | **₹1,500.00** | Target rupee loss per trade if exited cleanly at the intended stop-loss price. |
| **Sector Allocation Limit** | **$\le 1$ Slot (₹38,000)** | Maximum 1 position allowed within any single GICS / NSE industry sector. |

### Stress Loss Scenarios vs "Guaranteed" Stops
A critical principle emphasized across all audits: **A ₹1,500 stop-loss calculation is not a guarantee.** In gap-down opens or circuit locks, market orders fill well below the stop price.

Adjusted A1 calibrates tail risk against full ₹1,14,000 portfolio utilization:
- **Normal Planned Stop:** ₹1,500 per trade ($3 \times ₹1,500 = ₹4,500$ across 3 positions, or **−1.8%** of total corpus).
- **−10% Adverse Scenario Budget:** If all 3 positions gap down −10%, the unrealized loss is **₹11,400** (calibrated against the ₹12,000 scenario budget, or **−4.56%** of corpus).
- **−15% Severe Gap Scenario:** Loss equals **₹17,100** (**−6.84%** of corpus).
- **−20% Extreme Black Swan Scenario:** Loss equals **₹22,800** (**−9.12%** of corpus).

Because of the mandatory ₹1,36,000 cash buffer, even a devastating −20% overnight market gap consumes less than 10% of total capital.

---

## 3. Position Sizing Formula

The number of shares to purchase for any trade is governed by a dual-constraint formula:

$$\text{Shares}_{\text{Risk}} = \left\lfloor \frac{\text{₹1,500.00}}{\max(\text{Entry Price} - \text{Stop Price}, \text{Tick Size})} \right\rfloor$$

$$\text{Shares}_{\text{Slot}} = \left\lfloor \frac{\text{₹38,000.00}}{\text{Worst-Case Entry Price}} \right\rfloor$$

$$\text{Final Position Shares} = \min\left(\text{Shares}_{\text{Risk}}, \text{Shares}_{\text{Slot}}, \text{Shares}_{\text{Liquidity}}\right)$$

Where:
- $\text{Worst-Case Entry Price} = \text{Entry Ref} \times (1 + \text{Slippage Clamp})$ accounts for worst-case fill prices.
- $\text{Shares}_{\text{Liquidity}} = 0.15 \times \text{Daily Volume} \times 2.0 \text{ sessions}$ enforces Rule 9 participation limits.

---

## 4. Code Enforcement Map

The locations in code where Adjusted A1 constraints are implemented:

```
c:\Users\yashw\swing trades (and swing-trades-track2)
├─ research/decision/sizing.py          -> Enforces Rs 1,500 risk budget & Rs 38,000 slot clamp
├─ research/decision/allocator.py       -> Enforces 3-slot max & sector concentration limits
├─ research/decision/ledger.py          -> Enforces Rs 1,14,000 aggregate exposure check
└─ antigravity/models/                  -> Production operational execution models
```

---

## 5. Deprecation Audit: Old Hardcoded Limits

Audit Finding #1 and #2 ([38_findings_raw.txt:20-41](file:///c:/Users/yashw/swing%20trades/38_findings_raw.txt#L20-L41)) identified that legacy production code still contained references to the outdated **₹58,333.33** slot cap and **₹1,75,000** aggregate ceiling (from an obsolete 3-slot allocation of ₹1.75L).

### Legacy Locations Requiring Remediation

| Deprecated Parameter | Location | Offending Code Snippet | Remediation Required |
|---|---|---|---|
| **₹58,333.33** Slot Cap | [`execution_policy.py:124`](file:///c:/Users/yashw/swing%20trades/antigravity/models/execution_policy.py#L124) | `candidate.get("max_slot_notional_rs", 58333.33)` | Update default to `38000.00` |
| **₹58,333.33** Slot Cap | [`liquid_momentum_screener.py:348`](file:///c:/Users/yashw/swing%20trades/antigravity/models/liquid_momentum_screener.py#L348) | `max_notional_rs: float = 58333.33` | Update default to `38000.00` |
| **₹58,333.00** Slot Cap | [`track2_compass_strategy.py:81`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_compass_strategy.py#L81) | `max_notional_rs: float = 58333.0` | Update default to `38000.00` |
| **₹58,333.00** Slot Cap | [`track2_last_light_strategy.py:74`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_last_light_strategy.py#L74) | `max_slot_notional: float = 58333.0` | Update default to `38000.00` |
| **₹58,333.00** Slot Cap | [`track2_recoil_strategy.py:79`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_recoil_strategy.py#L79) | `max_slot_notional: float = 58333.0` | Update default to `38000.00` |
| **₹58,333.00** Slot Cap | [`track2_trapdoor_strategy.py:70`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_trapdoor_strategy.py#L70) | `max_notional_rs: float = 58333.0` | Update default to `38000.00` |
| **₹58,333.33** Slot Cap | [`track2_volatility_squeeze_strategy.py:92`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_volatility_squeeze_strategy.py#L92) | `max_notional_rs: float = 58333.33` | Update default to `38000.00` |
| **₹58,333.33** Slot Cap | [`track2_vwap_reclaim_strategy.py:97`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_vwap_reclaim_strategy.py#L97) | `max_notional_rs: float = 58333.33` | Update default to `38000.00` |
| **₹1,75,000.00** Ceiling | [`track2_portfolio_risk_governor.py:156`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L156) | `250000.0 - 75000.0` (= ₹1,75,000) | Update to `114000.00` |
| **₹1,75,000.00** Ceiling | [`hybrid_execution_oms.py:95-100`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/hybrid_execution_oms.py#L95-L100) | Enforces ₹1,75,000 deployable cap | Update to `114000.00` |

### Remediation Status
Fixes have been implemented and tested on branch `track2/antigravity-adjusted-a1-staging` in test file [`tests/test_antigravity_adjusted_a1_and_feed.py`](file:///c:/Users/yashw/swing%20trades/tests/test_antigravity_adjusted_a1_and_feed.py). Per Rule 8, these fixes remain staged pending formal cross-agent peer review before being merged into `main`.
