# Track 2 — Plain-English Build Map

**Updated:** 21 September 2026  
**Current mode:** Paper observation only; no real orders  
**Qualification counters:** **0/60 prospective sessions** and **0/20 realistically fillable paper trades**

## What Track 2 is

Track 2 studies short-term momentum in liquid NSE shares that are active F&O
underlyings. Its starting idea is a 15-minute Opening Range Breakout: watch the
first 15 minutes, require unusual volume and other safety checks, and record what
the pre-written rules would have done. For now, the system records paper evidence
only. It cannot place a broker order.

## The complete phase tree

- **Track 2 — Liquid short-term momentum system**

  - **Phase 1 — Build trustworthy measurement machinery**

    - **Phase 1A — Decide what counts as proof — COMPLETE / APPROVED**
      - Plain meaning: we defined the rules for proving that a signal or paper
        fill really happened.
      - It rejects manually typed profit, manually claimed fills, caller-supplied
        clocks, and incomplete market records.
      - It distinguishes three evidence levels:
        - **E1:** the market price merely touched the paper order price.
        - **E2:** available market activity may have cleared enough queue for a
          possible fill.
        - **E3:** the evidence required for a realistically fillable paper trade
          that may count toward the 20-trade gate.
      - A price touch alone is never recorded as completed profit.

    - **Phase 1B — Join the pieces into a daily paper-session system**

      - **Phase 1B1 — Intraday recorder — COMPLETE / APPROVED**
        - Plain meaning: this is the system's notebook during market hours.
        - It stores snapshots and signals in time order, detects missing or stale
          data, resumes safely after a crash, and seals the final result.
        - It cannot invent E3 fills from browser depth.

      - **Phase 1B2 — Official NSE safety-list reader — COMPLETE / APPROVED**
        - Plain meaning: before the session, the system reads official NSE F&O,
          ASM, and GSM sources and verifies that the files genuinely came from
          the expected endpoints and were not silently altered.
        - A share must be in the active F&O list and outside ASM/GSM. Missing,
          stale, malformed, or unverified data causes a fail-closed rejection.

      - **Phase 1B3A — Evidence hardening — COMPLETE / APPROVED**
        - Plain meaning: we added tamper-evident seals and strict ownership rules.
        - The watchlist is frozen before trading hours.
        - Source roles cannot be swapped.
        - Each attempted day is registered as `PENDING`, then ends as either
          `FINALIZED` or `VOID`.
        - Stream checkpoints make later editing detectable.

      - **Phase 1B3B — Daily session coordinator — COMPLETE / APPROVED**
        - Plain meaning: this is the conductor that starts and closes all the
          components in the correct order.
        - Before 09:00 IST it verifies official sources, freezes at least four
          eligible shares, seals the rules, and starts one recorder.
        - Only one process may own a session. A restart must prove that every
          saved file still has the expected hash.
        - It can finalize only on that session's date at or after 15:30 IST.
        - Failures become visible `VOID` attempts instead of disappearing.
        - Latest debugging also repairs a crash between verdict saving and ledger
          closure: recovery now completes the missing terminal record, while a
          corrupt ledger prevents a new verdict from being committed.

    - **Phase 1C — Independent counter and daily operations report — COMPLETE / APPROVED**
      - Plain meaning: build a read-only accountant.
      - It will scan sealed daily verdicts and report how many sessions and E3
        fills genuinely qualify.
      - It must never edit a session, manufacture evidence, or count a `VOID`,
        pilot, replay, or incomplete day.
      - It should also give Yashu one short daily status: started, recording,
        finalized, void, and the exact reason.
      - Current derived status is trusted zero: **0/60**, **0/20**, gate not
        passed. Any evidence-integrity error forces both trusted counters to zero.

    - **Phase 1D — Non-counting live-market rehearsal — BUILT / LIVE RUN PENDING**
      - Plain meaning: run the full machinery once with current official inputs
        and live browser observations, but label the day as a rehearsal.
      - Purpose: prove that clocks, files, official sources, restart handling,
        and end-of-day closure work on the actual computer.
      - It does not increase either qualification counter.
      - The mode is frozen as `REHEARSAL` before collection and cannot later be
        promoted into qualifying evidence. Rehearsals use their own evidence
        and surveillance directories.

  - **Phase 2 — Prospective paper observation — BUILD IN PROGRESS**
    - **Phase 2A — Multi-symbol 15-minute evidence collector — INTEGRATED / LIVE RUN PENDING**
      - Collects OHLCV bars for the complete frozen universe using transient
        Kite authentication held only in memory.
      - Rejects missing instruments, bad timestamps, cross-date data, malformed
        OHLC relationships, and mismatched instrument-token maps.
      - Credentials are never serialized into evidence.
    - **Phase 2B — Deterministic ORB signal adapter — INTEGRATED / LIVE RUN PENDING**
      - Reads direct-Kite bars plus frozen baselines and market regime.
      - Produces a rules-hash-bound paper instruction only; it never claims a fill.
    - **Phase 2C — Paper-order and E3 fill evidence — CORE BUILT / TICK INTEGRATION PENDING**
      - A price touch never creates a fill.
      - E3 requires unique post-arrival trade prints to clear queue rank plus
        order size after the frozen queue-volume haircut.
    - **Phase 2D — Daily runner — FIELD-TEST SLICE BUILT / PEER REVIEW PENDING**
      - One launcher starts the dedicated Kite window/feed and paper desk.
      - The feed now emits current and trailing 15-minute bars for all eight
        candidates plus Nifty, without writing the Kite credential to disk.
      - The desk derives same-time historical volume, ATR and DTV, checks the
        same-day official F&O/ASM/GSM snapshot, and records candidate observations.
      - The repaired launcher writes `SIGNAL_CANDIDATE` / `NOT_SUBMITTED`;
        Rule 8 peer review remains pending and paper execution is disabled.
      - Request-start timestamps prevent partial candles becoming completed
        without re-fetch. Actual decision timestamps replace backdated bar starts.
      - Same-day restarts replay-verify preflight; malformed/stale inputs are
        logged; a 15:30 summary records capture cycles and errors.
      - This field test produces no fills, realized profit or qualification counts.
      - See FIELD_TEST_RUNBOOK.md for tomorrow's 08:45 startup instructions.
    - Run the frozen system during real market sessions.
    - Every qualifying day must be registered before 09:00 IST, captured in real
      time, and closed with a verified verdict.
    - Target: at least **60 qualifying prospective sessions** and **20 E3 paper
      fills**.
    - Rules and thresholds should remain frozen during the measurement window.
      A material strategy change starts a new version and cannot silently inherit
      the old version's statistics.

  - **Phase 3 — Statistical qualification — NOT STARTED**
    - Plain meaning: determine whether the strategy actually has an edge after
      costs, slippage, missed fills, bad days, and market-regime differences.
    - Review expectancy, drawdown, fill rate, adverse selection, confidence
      intervals, and whether results depend on only one or two lucky shares.
    - Claude red-teams the statistics, Codex audits evidence and execution
      reality, and Antigravity integrates the approved analysis.

  - **Phase 4 — Separate live-readiness decision — LOCKED**
    - This phase is not automatic and is not approved today.
    - It begins only if Phase 2 and Phase 3 pass and the three-agent review finds
      no unresolved blocking defect.
    - Broker connectivity, live position sizing, kill switches, and operational
      controls would require a new explicit human decision.

## What happens on one future paper day

1. **Before 09:00:** fetch and verify NSE lists, verify the band-policy files,
   freeze the eligible universe, freeze the strategy settings, and register one
   `PENDING` attempt.
2. **09:15 onward:** record market snapshots and paper signals. No real broker
   order is sent.
3. **During the session:** reject stale data, out-of-universe symbols, duplicate
   signals, invalid timestamps, and conflicting writers.
4. **At or after 15:30:** close the stream, verify every file and checkpoint,
   calculate the evidence-based verdict, and end as `FINALIZED` or `VOID`.
5. **After closure:** the future Phase 1C accountant reads the sealed verdict and
   decides whether the day increments 60, whether any E3 fills increment 20, or
   whether nothing counts.

## What does not count

- The four earlier pilot sessions: useful for debugging, but `PILOT_UNVERIFIED`.
- Historical data, backtests, screenshots, replays, or manually reconstructed
  sessions.
- The earlier live-source smoke test.
- A price touching the intended entry or exit.
- Browser depth without qualifying E3 evidence.
- A day started after the prospective cutoff, a day with broken evidence, or any
  day ending `VOID`.

## Current honest position

- The measurement and daily coordination code through Phase 1B3B is built.
- Antigravity's final delta review reports `APPROVED` with no P0/P1 defect.
- Claude's final delta review reports `APPROVED` with no P0/P1 defect.
- Phase 1D's executable runner, isolated evidence mode, restart guardrails, and
  runbook are built. The focused Phase 1D/aggregator suite passed **23/23**.
- The final first-party project suite passed **475/475** after stale and
  concurrent-artifact test assumptions were corrected.
- No strategy profitability has been proven yet.
- No session or fill has qualified yet.
- Real trading remains prohibited under Rule 1.

## Immediate next build

Complete independent review of Phase 1D, then run one explicitly non-counting
live-market rehearsal with authentic current inputs. Only after its evidence,
recovery, and end-of-day closure are reviewed should Phase 2 begin.
