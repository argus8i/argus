# Measured facts (single source)

Every value here is **MEASURED**. It was re-run on 25 Sep 2026 in the worktree at `e139829` (Windows, `.venv` Python 3.14).

**Data:** `shared/track2_liquid/historical_candles_track2.json`:
- 8 stocks plus NIFTY50;
- 32 sessions, 10 Aug–23 Sep 2026;
- one regime (post-CAS);
- source is the Kite web-session endpoint (see `research/notes/p0_baseline.md`).

**Conventions:**
- Entries are assumed at the signal-bar close (optimistic).
- R uses the **trigger** basis.
- Cost is 0.106% of entry notional.
- If a bar touches both stop and target, the stop wins.
- Positions are flat at the open of the 15:00 bar.

Bootstrap intervals resample stock-days and ignore same-day correlation, so they are too narrow.

**Main reproduction command** (from the repo root, read-only). Sections A–F refer to its printed output:

```
python "Claude outputs/2026-09-24_beacon_probes/probe_empirical.py"
```

| # | Fact | Value | Source / reproduction |
|---|---|---|---|
| F1 | First ORB breaks: first close above the 09:15 high before 14:45, no volume gate; two-tranche 1.5R/3R with breakeven | 103 events. 18 stops (17.5%), 10 T1 (9.7%), 75 time exits (72.8%). Mean net R −0.084; gross −0.003 plus 0.080 cost. q = P(T2 given T1) = 1/10 | probe §D (counts, mean, q). The exit split and the gross/cost decomposition are asserted by P2 regression test 1 (`research/tests/test_p2_regressions.py`) |
| F2 | First signal per stock-day through the production strategy code (trend gates disabled) | ORB −0.003 [−0.20, +0.22] n=41 · VWAP_RECLAIM −0.258 [−0.45, −0.05] n=89 · VOL_SQUEEZE −0.530 [−0.79, −0.27] n=13 · TRAPDOOR −0.077 [−0.35, +0.19] n=18 · LAST_LIGHT +0.226 [−0.20, +0.73] n=11 · RECOIL −0.381 [−0.86, +0.09] n=7 · COMPASS −0.095 [−0.27, +0.11] n=17 | probe §C2. RECOIL was n=5 in the 24-Sep audit; commit `d62c7e6` changed it |
| F3 | Sensitivity sweep | 21 configurations (`trials_registry.csv`). None is significantly positive. VWAP_RECLAIM and VOL_SQUEEZE are negative at every threshold | probe §F |
| F4 | Stop distance to the OR low | median 1.45%, p10 0.83%, p90 2.25%. The ₹58,333 cap binds on 96.1%, so median realised risk is about ₹832 (58,333 × stop%, over capped events) | probe §D; the ₹832 is asserted by P2 regression test 1 |
| F5 | Passive bid at close − 0.25 × bar range | Fills 81% of losers and 39% of winners. Win share among fills is 26.2%, against 42.7% for all signals | probe §D2 |
| F6 | Outcome dispersion of the F1 events | σ_R = 0.741 (trigger basis) | asserted by P2 regression test 1 |
| F7 | Mean pairwise within-session 15m log-return correlation | 0.32. Top pairs: IREDA–RVNL 0.50, INOXWIND–SUZLON 0.47, RVNL–SUZLON 0.47, BDL–COCHINSHIP 0.42 | probe §E |
| F8 | Worst 5% of NIFTY 15m bars (36 bars) | 89% of the 8 stocks fall in the same bar; average move −0.95 of each stock's own 15m sd | probe §E |
| F9 | Session-only ATR20 vs the typical range of the bar being judged | 1.75× at 10:45; 1.63× at 12:00 and 14:00 | probe §B |
| F10 | Median 15m bar range | 09:15 bar 1.25% of price; 12:30 bar 0.25% (4.98×) | probe §A |
| F11 | ER8 of a driftless Gaussian random walk (200,000 paths) | mean 0.357; P(ER8 ≥ 0.35) = 0.453 | Monte Carlo from the 24-Sep audit session. Not re-run in P1. Label: MEASURED (simulation) |

**Not measured here (do not cite as MEASURED):**
- The ≈0.53% one-hour σ in plan §3.2. It is an input quoted from the MC audit (`Claude outputs/2026-09-24_monte_carlo_ensemble_audit.md`) and has not been re-derived.
- The Monte Carlo audit's 48% false pass rate. It comes from that audit, not re-run here.
