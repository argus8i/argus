# Track 2 Phase 1D — Non-Counting Market Rehearsal

## Purpose

Phase 1D observes one complete live market session with the production-like
Track 2 pipeline. It **cannot qualify**, increment the 60-session gate, increment
the 20-fill gate, place broker orders, or create realised P&L.

## Before 09:00 IST

1. Keep Kite logged in only for the read-only depth bridge. Do not enable any
   broker order API or live-order route.
2. Confirm `shared/track2_liquid/live_depth_track2.json` is updating while its
   browser tab is visible. Its timezone-naive `local_write_time` is interpreted
   as IST. The runner rejects data older than five seconds.
3. Create the working configuration from
   `shared/track2_liquid/rehearsal_config.example.json` and verify at least four
   candidates remain active F&O underlyings and outside ASM/GSM.
4. Supply the authentic, hash-verified NSE dynamic-band policy artifact. Fixture
   or placeholder circulars are rejected.

## Command

```powershell
.\.venv\Scripts\python.exe -m antigravity.daemons.track2_rehearsal_runner `
  --config shared\track2_liquid\rehearsal_config.json `
  --band-policy shared\track2_liquid\band_policy\band_policy.json
```

If the process was cleanly interrupted with Ctrl+C, restart the same day with
the same inputs and append `--resume`. Never use `--resume` to alter the frozen
candidate set, parameters, policy, or qualification mode.

## Evidence and verdict

- Immutable rehearsal evidence: `shared/track2_liquid/rehearsals/YYYY-MM-DD/`
- Rehearsal source evidence: `shared/track2_liquid/rehearsal_surveillance/`
- Derived status: `shared/track2_liquid/track2_rehearsal_status.json`

A successful run ends with `SessionStatus.REHEARSAL`, a terminal `REHEARSAL`
attempt record, a trusted reproducibility audit, and exactly zero qualification
counters. Any missing, stale, hidden, malformed, changed, or unverifiable input
voids or stops the rehearsal; it never silently becomes qualifying data.
