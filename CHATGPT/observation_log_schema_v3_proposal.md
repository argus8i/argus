# Observation log schema v3 — peer-review proposal

**Status:** PROPOSED, NOT MIGRATED  
**Reason:** Preserve the v2 evidence table until Claude and Antigravity review the additive schema under AGENTS.md Rule 8.

The v2 columns remain unchanged. The migration appends:

`schema_version,track_id,strategy_version,parameter_set_id,session_id,signal_id,selection_mode,universe_snapshot_id,universe_asof_ist,surveillance_state,surveillance_source,surveillance_checked_at_ist,corporate_action_state,corporate_action_source,corporate_action_checked_at_ist,feature_cutoff_ist,decision_time_ist,quote_time_ist,baseline_window_end_date,orb_window_start_ist,orb_window_end_ist,orb_high,orb_low,orb_bucket_volume,orb_historical_median,orb_volume_ratio,paper_product,paper_order_type,paper_quantity,paper_limit_price,paper_trigger_price,execution_state,rms_validation_state,rms_reason,actual_order_sent,prospective_record,counts_session_gate,counts_trade_gate,exclusion_reason,review_status`

## Counting rules

- Exactly one `SESSION_SUMMARY` record per completed trading date can set `counts_session_gate=true`.
- Symbol signal rows always set `counts_session_gate=false`.
- `counts_trade_gate=true` requires a prospectively logged, rule-compliant and realistically fillable paper execution; `REJECTED_RMS`, `NO_SIGNAL`, `QUEUED` without fill, retrospective and example records are false.
- `actual_order_sent` must remain `false` throughout Rule 1 observation mode.
- Counts are derived from qualifying rows, never typed into a status report independently.

## Time and leakage rules

- Universe/version frozen before session open.
- T−1 is the latest permitted date for baseline features.
- ORB interval is `[09:15:00,09:30:00)` IST.
- Decision cannot precede the end of its feature window.
- Entry simulation uses the first observed executable quote after decision/submission.
- Unknown data is blank/`UNKNOWN`, never zero/`CLEAR`.

## Review acceptance

Migration requires: (1) a parser validation proving every old row is preserved; (2) unique IDs; (3) exactly one session-count row per date; (4) all historical rows remain ineligible; and (5) Claude and Antigravity review recorded in `review_status`.
