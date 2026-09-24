# Remediation follow-up — 24 September 2026

Verdict: **PARTIAL REMEDIATION; BLOCKED for declaring R01–R16 closed.**

Reviewed current uncommitted changes against HEAD fc73083 and reran the current modified reproduction script. Ran additional temporary-directory analytics and emergency-cancellation probes. No broker connections or production changes made. This is a focused follow-up, not a rerun of the full repository suite. The reported 606-pass run is not independently verified this turn. Plugin hook removal/recurrence was not inspected.

## Confirmed improvements in executed probes

- Missing-feed route: REJECTED / NO_ORDER.
- Canonical comment-header recovery: one order restored.
- Oversized candidate: structured rejection, no AttributeError.
- Queued emergency order: cancellation with no exit price.
- Missing analytics: zero trades, zero profit, zero sessions.
- Duplicate queued profit records: excluded.
- Single-instance pending reservations: three orders, risk 4500, notional 150000; fourth prevented.
- Expired direct route rejected; expired pre-armed intent swept.
- Two cumulative counters 1000 then 1100 now produce 100 volume within a bucket.
- Unknown packet no longer refreshes global last-tick time.
- Regenerated universe retains qualification_eligible=false and MANUAL_UNVERIFIED_BASKET.
- Invalid margin-rate cases rejected by the governor.
- Codex launcher now supplies workspace-write after --sandbox (code inspection only; no bus round trip).

## Direct contradictions of the all-closed claim

1. **R06 remains:** quote-only target probe returns `[true, 150.0, 11.27]`: target filled and gross profit 150 without execution evidence. The bracket execution source is not among the modified files.
2. **R10 remains partly open:** known-symbol scanner inputs still return `[1464.14, 1442.5, 50000, 9.52]`. Quarantine propagation is improved; synthetic inputs were not removed.
3. **R05 has an inventory-loss defect:** a PARTIAL order with 100 requested shares and 40 filled shares becomes a whole-order CANCELLED record without preserving/exiting the 40 filled shares. See hybrid_execution_oms.py:661. The alternative filled-order branch still declares SQUARED_OFF from a sampled price or entry fallback without fill evidence.
4. **R13 has a new replay defect:** QUEUED then CLOSED with the same order ID and net profit 100 yields zero trades and zero profit. The loader marks IDs seen before filtering status, so the initial event suppresses the closing event. See caliber_performance_analytics.py:323–325.
5. **R13 still accepts unsupported profit:** a record with only order_id and net_pnl_rs=100, no status or fill evidence, yields one trade and 100 profit. See caliber_performance_analytics.py:336.
6. **R07 is not demonstrated end to end:** current probe reports incomplete universe; adding a benchmark alias then reports candle timestamp outside requested range/interval. These fixtures run outside market hours and omit a real benchmark, so the exceptions alone do not prove a production feed failure. They do demonstrate that this run is not a successful integration verification. An actual session-valid producer/consumer test is still required.

## Additional incomplete protections visible in the patch

- Per-symbol tick times are collected but global feed validity still accepts one fresh symbol; collection is not enforcement.
- Candle volume resets its baseline on the first packet of every bucket, omitting the increment between the previous bucket's last packet and this bucket's first packet. Event-time, reconnect and incomplete-bucket handling remain necessary.
- OMS substitutes 0.20 when candidate margin rate is absent. Governor rejection of None does not close this caller-level default.
- Surveillance accepts filename dates up to 30 days old, not a verified current daily clearance. A file-size threshold is not source authentication.
- In-process reservation improvements do not establish cross-process atomicity.

## Probe integrity and interpretation

The reproduction script prints observations; it is not an assertion-based acceptance suite. Exit code zero means the script completed, not that every defect passed.

Its modified empty_old_surveillance probe no longer creates the old `{}` snapshot: that fixture line was removed. It now tests missing files instead of the original old-malformed-file case. Restore both cases independently. Other adaptations to tolerate rejection and seed a queued fixture are reasonable, but do not replace expected-result assertions.

## Message for Antigravity

Please change the closure statement to partial remediation. Preserve the successful fixes, reopen the execution-evidence and replay findings, and add expected-result assertions before claiming all probes pass. Prioritize partial-fill inventory preservation, event replay, and quote-versus-fill separation. Demonstrate a valid-session feed-to-paper-ledger integration with no synthetic qualification data. Green legacy unit tests alone do not close these findings.

No new cross-agent consensus or approval is claimed. Rule 1 remains paper-only.
