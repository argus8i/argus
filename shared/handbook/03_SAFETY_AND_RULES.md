# 03. System Safety, Operating Rules & Tri-Agent Governance

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **IN PROGRESS** (Governance framework active; Rule 8 ledger enforced; Constitution draft v0.3 pending owner signature)  
**Governing Documents:** [AGENTS.md](file:///c:/Users/yashw/swing%20trades/AGENTS.md) | [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl)  

---

## 1. Plain-English Summary

In trading system development, the greatest threat is not bad market luck; it is **self-delusion and operational carelessness**. It is easy to write a backtest that appears to make millions if you assume orders always fill at the best price, ignore transaction taxes, or trade stocks that are halted or suspended.

To guarantee that Project ARGUS only trades genuine, durable market edges, the project enforces a strict, multi-agent checks-and-balances framework:
- **Red-Teaming First:** No strategy or code change is accepted just because the author claims it works. A separate agent (Claude or Codex) must write adversarial test probes to attempt to break it before it is approved.
- **Fail-Closed Safety:** If any security check, data feed, or risk limit fails, the system refuses to trade. Safety switches must never require manual opt-in.
- **The Absolute Rule:** Real capital deployment is strictly locked at ₹0.00 until the desk proves itself across 60 prospective paper trading sessions.

---

## 2. AGENTS.md Operating Rules (Rules 1–11)

The complete operating rules governing all agents in the workspace are defined in [AGENTS.md](file:///c:/Users/yashw/swing%20trades/AGENTS.md).

| Rule | Title | Plain-English Mandate | Governing Lines |
|---|---|---|---|
| **Rule 1** | **Mandatory Paper-Trading Gate** | 100% Cash; ₹0 real capital. Must complete $\ge 60$ prospective trading sessions and $\ge 20$ realistically fillable entries with verified positive net expectancy before live capital is considered. | [AGENTS.md:52-59](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L52-L59) |
| **Rule 2** | **Absolute ₹10.00 Price Floor** | Immediate disqualification of any stock trading below ₹10.00 to avoid extreme tick distortion and liquidity evaporation. | [AGENTS.md:61-64](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L61-L64) |
| **Rule 3** | **Prohibition of Locked-Circuit Chasing** | Never buy or submit limit orders for stocks locked at Upper Circuit with zero offers ("buying the exit"). | [AGENTS.md:66-69](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L66-L69) |
| **Rule 4** | **Discrete Execution Modeling** | Never assume guaranteed fills. All simulators must model queue states: `LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, and `FILLED`. Quote touches never count as fills. | [AGENTS.md:71-84](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L71-L84) |
| **Rule 5** | **10-Day Lower-Circuit Risk Calibration** | Sizing must include an unbroken 10-day LC exit lockout stress scenario (−40.1% on 5% bands, −18.3% on 2% bands). Stop-losses never execute when bid depth is zero. | [AGENTS.md:86-96](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L86-L96) |
| **Rule 6** | **Surveillance Pre-emption & Band Monitoring** | Daily pre-open check comparing circuit bands and surveillance classifications. Any band cut or entry into ESM/GSM/ASM/T2T triggers an immediate entry freeze. | [AGENTS.md:98-102](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L98-L102) |
| **Rule 7** | **Pre-Circuit Accumulation Breakouts** | Buy only during two-sided accumulation bases where 20-day volume is expanding $\ge 3\times$, spread $<1\%$, and a valid stop can be placed. | [AGENTS.md:104-108](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L104-L108) |
| **Rule 8** | **Tri-Agent Consensus Protocol (v2 Gates)** | Core model changes require cross-agent review. Enforces 4 acceptance gates: Test-First, Fail-Closed Defaults, Empirical Command Artifacts, and Branch Isolation. | [AGENTS.md:110-141](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L110-L141) |
| **Rule 9** | **Liquidity Participation Gate** | Position size must never exceed 15% maximum participation of realistic daily volume over a 2-session clearable horizon. | [AGENTS.md:143-153](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L143-L153) |
| **Rule 10** | **Precedence Hierarchy of Gates** | Strict conflict resolution order: Rule 1 (Paper Gate) > Rule 6 (Surveillance) > Rule 2 (₹10 Floor) > Rule 9 (Volume Gate) > Rule 3/4 (Circuits) > Rule 7 (Setup). | [AGENTS.md:155-165](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L155-L165) |
| **Rule 11** | **Absolute Track Isolation** | Treat Track 1 (micro-caps / ESM) and Track 2 (liquid F&O underlyings) as completely independent. Track 1 is permanently shelved. Cross-track contamination is prohibited. | [AGENTS.md:167-195](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L167-L195) |

---

## 3. Rule 8 Independent Review Ledger (`shared/trust/reviews.jsonl`)

The hash-chained cryptographic ledger at [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl) records all formal peer reviews. Each entry binds the reviewed commit SHA, parent commit, patch hash, reviewer identity, test commands, exit codes, and output hashes.

### Complete Historical Review Register

| Review ID | Reviewed Commit | Reviewer | Verdict | Target & Key Findings |
|---|---|---|---|---|
| **`CODEX-TRUST-P1-001`** | `94dbd8d` | Codex | `CHANGES_REQUIRED` | Trust test evidence verification flaws; missing JSON/CSV from governed scope; exit code 1. |
| **`CODEX-TRUST-P1-002`** | `ff7f978` | Codex | **`APPROVED`** | Verified test evidence retained, governed scope expanded, exit code 0. **(Sole valid APPROVED commit in history).** |
| **`CODEX-FRAMEWORK-001`** | `3fd1ab7` | Codex | `CHANGES_REQUIRED` | Incomplete archive PASS flaw; journal concurrency race; missing ASM/GSM evidence gate. |
| **`CODEX-DATA-001`** | `8a54ac8` | Codex | `CHANGES_REQUIRED` | Cross-process pacing flaws in archive downloader; retries exceeding daily cap; missing weekend handling. |
| **`CODEX-FRAMEWORK-002`** | `c816589` | Codex | `CHANGES_REQUIRED` | Mutable clock injection flaws; stale lock stealing; archive audit passing date-only Bhavcopies. |
| **`CODEX-FRAMEWORK-003`** | `dc82ca0` | Codex | `CHANGES_REQUIRED` | Archive audit accepted NaN price values; manifest accepted `MISSING_404` despite HTTP 200. |
| **`CODEX-FRAMEWORK-004`** | `ce2a7f0` | Codex | `CHANGES_REQUIRED` | Fail-open flaw: empty ASM/GSM treated as all-clear; null symbol converted to `"None"`; path mismatch. |
| **`CODEX-T2-01`** | `0020e8c` | Codex | `APPROVED` *(Superseded)* | Initial review of surveillance session alignment; passed targeted suite. |
| **`CODEX-T2-01-RECHECK`** | `0020e8c` | Codex | **`CHANGES_REQUIRED`** | **Superseded `CODEX-T2-01` 15 mins later:** paper plan permitted unpinned surveillance; shadow journal append race. |

> [!IMPORTANT]
> As of 30 September 2026, **no core strategy framework commit since `ff7f978` holds a valid `APPROVED` verdict**. All recent commits are classified as `CHANGES_REQUIRED` or `UNREVIEWED`.

---

## 4. Security Boundaries & Authorization Standards

Per Yashu's explicit security instructions ([AGENTS.md:3-13](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L3-L13)):
1. **Permanent Ban on Remote Debugging Ports:** Chrome remote debugging ports (`9333`, `9444`) and active browser credential scraping are permanently revoked.
2. **Revocation of Bypass Flags:** All execution permission-bypass flags (`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`) are permanently revoked.
3. **No Network Evasion:** Agents must never route around exchange rate limits or IP blocks using VPNs, proxies, mobile hotspots, or rotating IPs. Such actions risk permanent IP bans on Yashu's home connection.
4. **Secret Key Isolation:** Raw API keys, JWT access tokens, and cryptographic signing keys must never be committed to Git or output in plaintext logs.

---

## 5. Comprehensive 38-Finding Defect Reconciliation Matrix

Forensic audit of all 38 defects documented in [38_findings_raw.txt](file:///c:/Users/yashw/swing%20trades/38_findings_raw.txt):

| Finding ID & Summary | Severity | Offending File & Line | Status | Review Status |
|---|---|---|---|---|
| **#1. Deprecated ₹58,333 Slot Cap** | **CRITICAL** | [`execution_policy.py:124`](file:///c:/Users/yashw/swing%20trades/antigravity/models/execution_policy.py#L124), [`liquid_momentum_screener.py:348`](file:///c:/Users/yashw/swing%20trades/antigravity/models/liquid_momentum_screener.py#L348) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#2. Deprecated ₹1,75,000 Portfolio Ceiling** | **CRITICAL** | [`track2_portfolio_risk_governor.py:156`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L156) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#3. Allocator Has No Exposure Guards** | **HIGH** | [`track2_multi_strategy_engine.py:439-480`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_multi_strategy_engine.py#L439-L480) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#4. Pending Orders Understate Entry Price** | **HIGH** | [`track2_portfolio_risk_governor.py:297-304`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L297-L304) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#5. Plaintext API Secrets in Repository** | **CRITICAL** | [`telegram_config.json:2`](file:///c:/Users/yashw/swing%20trades/antigravity/config/telegram_config.json#L2), [`dhan_config.json:4`](file:///c:/Users/yashw/swing%20trades/antigravity/config/dhan_config.json#L4) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#6. Daemons Depend on Kite OMS Scraping** | **HIGH** | [`kite_web_depth_bridge.py:589`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/kite_web_depth_bridge.py#L589), [`track2_candle_collector.py:133`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_candle_collector.py#L133) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#7. Active Chrome CDP Ports (9333/9444)** | **HIGH** | [`kite_web_depth_bridge.py:33`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/kite_web_depth_bridge.py#L33), [`bring_to_front.py:5`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/bring_to_front.py#L5) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#8. Fail-Open Band Revision Monitor** | **CRITICAL** | [`band_revision_monitor.py:31-38`](file:///c:/Users/yashw/swing%20trades/antigravity/models/band_revision_monitor.py#L31-L38) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#9. Live Universe Omits Surveillance** | **CRITICAL** | [`research/shadow/run_day.py:272-274`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/run_day.py#L272-L274) | **VERIFIED ACTIVE** | OPEN (Not fixed by 0020e8c) |
| **#10. Screener Accepts 30-Day Stale Snapshot** | **CRITICAL** | [`track2_premarket_screener.py:317-322`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_premarket_screener.py#L317-L322) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#11. Ingestor `MAX_EVIDENCE_AGE_DAYS=1`** | **CRITICAL** | [`track2_official_source_ingestor.py:57`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_official_source_ingestor.py#L57) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#12. Paper Desk Rejects Evening Snapshot** | **CRITICAL** | [`track2_daily_paper_desk.py:279-281`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_daily_paper_desk.py#L279-L281) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#13. Surveillance Bridge Writes Bare Files** | **CRITICAL** | [`track2_surveillance_bridge.py:137-142`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L137-L142) | **ALREADY FIXED** | Tested in `e80eb49` |
| **#14. Paper Desk Omits `EQ` Series Check** | **HIGH** | [`track2_daily_paper_desk.py:297-305`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_daily_paper_desk.py#L297-L305) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#15. Fail-Open HMAC in Track 2 Bus** | **CRITICAL** | `swing-trades-track2/antigravity/daemons/tri_agent_bus.py:787` | **VERIFIED ACTIVE** | UNREVIEWED |
| **#16. BSE Poller Falls Back to Group "B"** | **HIGH** | [`bse_price_band_poller.py:54-66`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/bse_price_band_poller.py#L54-L66) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#17. Risk Governor Defaults Margin Gate OFF**| **MEDIUM** | [`track2_portfolio_risk_governor.py:105`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L105) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#18. Signal Sim Fills Stop on Lower Circuit**| **CRITICAL** | [`research/studies/signal_sim.py:240-253`](file:///C:/Users/yashw/swing-trades-track2/research/studies/signal_sim.py#L240-L253) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#19. Short Trades Default to CNC Rollover** | **CRITICAL** | [`track2_paper_execution.py:425`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_paper_execution.py#L425) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#20. Engine Emits Inverted Stops for Shorts** | **HIGH** | [`track2_multi_strategy_engine.py:353-371`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_multi_strategy_engine.py#L353-L371) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#21. Journal Append Missing File Lock** | **CRITICAL** | [`research/shadow/run_day.py:329-339`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/run_day.py#L329-L339) | **VERIFIED ACTIVE** | `CHANGES_REQUIRED` (T2-01-RECHECK) |
| **#22. Non-Atomic Journal Writes** | **CRITICAL** | [`research/shadow/run_day.py:313-316`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/run_day.py#L313-L316) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#23. Downloader Releases Pacing Lock Pre-GET**| **CRITICAL** | [`scripts/download_nse_archive.py:364`](file:///c:/Users/yashw/swing%20trades/scripts/download_nse_archive.py#L364) | **VERIFIED ACTIVE** | `CHANGES_REQUIRED` (CODEX-DATA-001) |
| **#24. Stale Lock Recovery Steals Active Lock** | **CRITICAL** | [`inbox_worker.py:118-181`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py#L118-L181) | **ALREADY FIXED** | Patched via `_pid_is_running` |
| **#25. Orphan Recovery Cleans Active Tasks** | **CRITICAL** | [`inbox_worker.py:542-573`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py#L542-L573) | **ALREADY FIXED** | Patched via `_pid_is_running` |
| **#26. Corrupt Universe Drops to 8 Stocks** | **CRITICAL** | [`track2_daily_paper_desk.py:50-63`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_daily_paper_desk.py#L50-L63) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#27. Non-Atomic Writes in Reconcile** | **HIGH** | [`research/shadow/reconcile.py:97-98`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/reconcile.py#L97-L98) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#28. Request Cap In-Memory Only** | **HIGH** | [`scripts/download_nse_archive.py:503`](file:///c:/Users/yashw/swing%20trades/scripts/download_nse_archive.py#L503) | **ALREADY FIXED** | Shared counter file implemented |
| **#29. Expiry Checked on Expiry Day Not Entry** | **CRITICAL** | [`research/shadow/expiry_desk.py:124-128`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/expiry_desk.py#L124-L128) | **ALREADY FIXED** | Fixed in `0020e8c` |
| **#30. `entry_session` Skips Saturday Sessions**| **CRITICAL** | [`research/framework/market.py:421-427`](file:///C:/Users/yashw/swing-trades-track2/research/framework/market.py#L421-L427) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#31. Dhan Fetcher Omits `end_date` Boundary**| **HIGH** | [`research/data/dhan_historical_fetcher.py:337`](file:///C:/Users/yashw/swing-trades-track2/research/data/dhan_historical_fetcher.py#L337) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#32. Manifest Logs Run Date Not Effective Date**| **HIGH**| [`scripts/daily_pipeline.py:824`](file:///c:/Users/yashw/swing%20trades/scripts/daily_pipeline.py#L824) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#33. Ban Calculation Blind to Holidays** | **HIGH** | [`scripts/daily_pipeline.py:113-122`](file:///c:/Users/yashw/swing%20trades/scripts/daily_pipeline.py#L113-L122) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#34. Snapshot Age Uses `abs()` Skew Mask** | **HIGH** | [`antigravity/daemons/feed_validity.py:56`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/feed_validity.py#L56) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#35. Forming Candle In Baseline** | **HIGH** | [`kite_web_depth_bridge.py:587-608`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/kite_web_depth_bridge.py#L587-L608) | **VERIFIED DORMANT** | Module slated for deletion |
| **#36. Ban List Gap Marked `AFTER_DEADLINE`** | **MEDIUM** | [`research/framework/desk.py:217`](file:///C:/Users/yashw/swing-trades-track2/research/framework/desk.py#L217) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#37. Symbol Regex Mismatch Rejects Valid Scrips**| **MEDIUM**| [`research/framework/market.py:386`](file:///C:/Users/yashw/swing-trades-track2/research/framework/market.py#L386) | **VERIFIED ACTIVE** | UNREVIEWED |
| **#38. Dhan Scrip Master Cache 7 Days Stale** | **MEDIUM** | [`dhan_feed_bridge.py:105-107`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/dhan_feed_bridge.py#L105-L107) | **VERIFIED ACTIVE** | UNREVIEWED |

---

## 6. Branch & Merge Discipline

To ensure auditability and prevent accidental overwrites between operations and research:
1. **Branch Ownership:** Operational tasks are developed on dedicated feature branches (e.g. `ops/nexus-scheduled-task`, `ops/daily-pipeline`) in `c:\Users\yashw\swing trades`. Research algorithms reside in `C:\Users\yashw\swing-trades-track2` on `track2/decision-engine`.
2. **Prohibition of Destructive Operations:** Agents are strictly forbidden from running `git reset --hard`, `git clean -fd`, or force-pushing to shared branches.
3. **Exact Commit Identity:** Peer reviews bind to exact 40-character Git commit SHAs. Reviewing a branch name or floating HEAD is strictly invalid.

---

## 7. Constitution Status

On 27 September 2026, Claude drafted `CONSTITUTION.md` (v0.1, v0.2, and v0.3 at commit `23c98a0`).
- **Current Legal State:** **DRAFT / NOT IN FORCE.**
- **Verification Command:** Running `python -m research.trust.constitution status` returns status `UNREVIEWED` with exit code `2`.
- **Enactment Requirement:** Per Rule 8, the Constitution requires Yashu's explicit written approval and sovereign cryptographic signature. No agent may sign on his behalf. Until enacted, [AGENTS.md](file:///c:/Users/yashw/swing%20trades/AGENTS.md) remains the sole binding operational rulebook.
