# Codex field-test repair status

The user authorized repairs for a real-market test tomorrow. Changes are limited
to Track 2 feed/desk integration, its launcher, tests and operating documentation.
Core sizing, ORB and execution model files were not changed in this repair pass.

## Verified locally

- Request-start timestamps per symbol prevent partial candles being promoted by time alone.
- Completed-bar decisions record actual decision time plus separate bar start/close timestamps.
- Current Nifty candles are fetched on the same fast cadence as the eight stocks;
  the slower historical fetch cannot silently supply the live regime.
- Same-day surveillance restart verifies raw-source hashes and reparses lists;
  preflight executes before Kite readiness, and a missing pre-09:00 snapshot is explicit.
- Latest 20 historical sessions require all 25 normal buckets. Zero-volume buckets
  remain part of the median. DTV below Rs30 crore rejects candidates.
- Boolean/fractional-volume/malformed/timezone-free candle input is rejected.
- Input errors produce visible status and event records. HTTP tasks no longer
  block quote capture while downloading candle history. Writer locks prevent
  concurrent desk and bridge writers.
- Parsed inputs are content-addressed; one candidate per symbol/day is durable;
  the field-test loop closes with a summary or saves an interrupted summary.

Validation: 82 focused tests passed, including mocked full-loop startup to EOD,
malformed-input recovery, restart, timing, DTV and duplicate-writer cases. This is
local/synthetic validation, not an authenticated Kite live test. A live NSE/Kite
run and exact instrument identity remain unverified in this turn.

## Authority and remaining scope

The repaired launcher runs FIELD_TEST, with candidate observations only and no
paper or real execution. No qualification counters change. Older paper_orders.jsonl
records are preserved but are not read or counted by this field-test path.
ATR/DTV definitions are research approximations requiring review; complete
market-cap/series provenance, frozen strategy baselines, E3 evidence, exits and
official session qualification integration remain outstanding.

Signed Antigravity review request queued as msg_1790013278_a88f2e6f0f47,
correlation corr_1790013278_24f0ce0c7ec6. Claude review dispatched through the bus;
no independent approval is claimed by this document. Original model reviews do
not approve this new integration automatically.

Verdict: locally tested for field capture and candidate observation; independent
review and live verification pending. Paper execution/qualification remain blocked.
