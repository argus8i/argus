# Track 2 market field test — 22 September 2026

> [!WARNING]
> **DEPRECATED / RETIRED INSTRUCTION (23 September 2026 Red-Team Security Hardening):**
> Do NOT launch or log into a dedicated Kite Chrome window or use remote-debugging ports (9333, 9444).
> Browser credential scraping and CDP ports are permanently prohibited under AGENTS.md Security Authorization.
> Market data ingestion must use compliant headless API bridges (Dhan v2 / Upstox v2) with explicit provenance.

The terminal displays state changes.
Leave the computer awake and connected through 15:30 IST.

The same launcher opens the read-only Track 2 Control Room at
`http://127.0.0.1:8766/`. It refreshes every two seconds and shows whether the
Kite window, quote feed, current candles, historical baseline, NSE preflight,
decision engine and evidence recorder are healthy. Red cards include the exact
reason; yellow means waiting or incomplete; green means that stage is current.

This launcher runs a **field test**: capture current market data, calculate ORB
candidate decisions, and preserve problems for debugging. Core changes still
need Claude and Antigravity review under Rule 8. No broker order or paper fill
is submitted/claimed, and neither qualification counter increases.

## Daily operation

- Before 09:00: official F&O/ASM/GSM preflight runs independently of Kite bars.
  A restart verifies and reuses that day's original snapshot. If no valid
  preflight exists by 09:00, the test can capture inputs but candidates stay blocked.
- At 09:45 onward: only candles requested **after** their closing time can
  generate candidate decisions. Stale Nifty or symbol candles block decisions.
- Before 14:45: candidate window closes. One candidate per symbol/day is retained;
  further observations remain in the event log without creating extra positions.
- At 15:30: the test writes its summary and exits. Chrome/feed may still be open;
  close the feed terminal when finished. Ctrl+C in the desk terminal saves an
  interrupted summary; launching again resumes the same day's evidence directory.

## Files to inspect

- `shared/track2_liquid/paper_desk_status.json`: current state and exact input error.
- `shared/track2_liquid/field_tests/YYYY-MM-DD/events.jsonl`: observed cycles and interruptions.
- `.../inputs/`: content-hashed copies of the parsed candle inputs used by the desk.
- `.../candidate_SYMBOL.json`: first candidate per symbol with real decision time,
  bar start/close time, input hashes, calculated sizing, and review-pending status.
- `.../summary.json`: captured cycles, candidates and observed problems; fills remain
  zero and realized profit remains unknown.

## Remaining acceptance work

The authenticated Kite endpoint and configured instrument tokens must be checked
in the live session. HTTP errors or login expiry can prevent capture. The field
test stores parsed feed evidence, not an exchange trade-by-trade tape. Its DTV is
an approximation from candle close times volume; ATR is a simple mean of daily
true ranges. These definitions, market-cap/series provenance, baseline freezing,
fill evidence, exits and qualification integration need separate peer approval.
Signal collection is not proof of strategy profit or a completed trade.

Historical sessions lacking all 25 normal-session bars are rejected. The latest
20 prior sessions must be complete; shortened/special sessions need an explicit
calendar policy later. An absent signal is a valid result when inputs or gates fail.
