# Observation log — schema v2, 10 September 2026

The original 35 columns retain their order. Eleven provenance/accounting columns are appended. Read by column name, not position. The malformed legacy example is archived in `archive/observation_log_before_20260910_reconciliation.csv` and repaired in the current log.

- One retrospective row represents one completed historical position, not a prospective observation session.
- `record_type=RETROSPECTIVE_LIVE_USER_REPORTED` means the user reported execution; it is not independently broker-verified and never counts toward the paper gate.
- `record_type=EXAMPLE` is illustrative and excluded from every performance or session count.
- `counts_toward_paper_gate=false` explicitly excludes all four current rows. A future `true` value requires prospective evidence and peer review; a flag alone does not establish eligibility.
- `position_qty` is historical trade quantity; `remaining_qty` is current unclosed quantity. `order_status=FILLED` describes the reported completed exit, not measured queue simulation.
- `source_ref` identifies the source; `entry_date` and `exit_date` are date-only fields. Unknown timestamps remain blank. `observed_at_ist` is not backfilled with a trade date.
- `exit_delay_days` is order-to-execution delay, not holding duration. The approximate one-hour CROPSTER report stays in notes; no exact timestamp is invented.
- `reported_pnl_inr` preserves the user's amount. `pnl_basis` identifies whether charges are known to be excluded or unspecified. Unknown `charges_inr` and `net_pnl_inr` remain blank, not zero.
- `price_precision_notes` explains approximate average exit prices. `final_fill_price` is populated only for CCDL's user-confirmed price; approximate exits are in `exit_price` with explicit qualification.
- Never sum `reported_pnl_inr` and call it verified net profit when cost bases differ. Never infer max adverse excursion from the final loss.

Validation: PowerShell `Import-Csv` by column names; assert 46 fields per row, four unique IDs, three retrospective trades, zero paper-eligible rows, remaining quantities zero, and reported P&L sum -3950. The repaired CSV is not a new model implementation and does not increment the 60-session/20-trade gate.
