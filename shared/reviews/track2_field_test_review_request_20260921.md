# Track 2 field-test repair review

Codex owns edits to track2_daily_paper_desk.py, track2_candle_collector.py,
track2_kite_bridge.py, start_track2_paper_desk.bat and their tests/runbook.
Review only; do not edit these files or promote observations into paper fills.

Please review the current working tree for tomorrow's market field test.
Prior defects: partial candles matured without re-fetch; decision timestamps
backdated to bar start; restart re-ingested immutable surveillance and failed;
Nifty history lacked freshness checks; partial daily history passed; DTV floor
was bypassed; malformed JSON killed the loop; HTTP collection blocked quotes.

Repairs add per-symbol request-start timestamps, strict OHLCV parsing, completed
bar/freshness checks, runtime decision timestamps, replay-verified surveillance
resume, complete 20-session baselines, DTV rejection, recoverable input errors,
independent HTTP tasks, writer locks, archived parsed inputs, candidate dedupe
and 15:30 closure. Core model files are unchanged. Execution remains disabled;
new outputs are SIGNAL_CANDIDATE / NOT_SUBMITTED / PENDING_TRI_AGENT_REVIEW.

Run tests/test_track2_field_readiness.py and related candle/desk suites with a
fresh explicit pytest --basetemp directory. Report concrete defects by file/line.
Assess field-test fitness separately from paper execution and qualification.
Known remaining gaps are recorded in shared/track2_liquid/FIELD_TEST_RUNBOOK.md.
Write only your own review in shared/track2_liquid/reviews/ and return its path.
Antigravity: as primary orchestrator, obtain Claude's independent review too.
