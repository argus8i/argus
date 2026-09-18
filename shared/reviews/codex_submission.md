## P0 / CRITICAL OBJECTIONS

1. **P0 — CONFIRMED:** Current sizing can exceed the declared loss budget for securities whose actual band exceeds 5%. The production path always divides by `0.401` at [risk_calculator.py:162](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:162>) and reports loss using the same constant at [risk_calculator.py:174](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:174>). The proposed band-aware function exists but is not connected to those paths.

2. **P0 — CONFIRMED:** A safe implementation cannot be approved from the supplied band artifact. It is dated 17 September while this review is dated 18 September, contains no archived exchange response/circular identifier, and labels ANLON and VEDAVAAG invalid at [bse_daily_bands.json:197](</C:/Users/yashw/swing trades/shared/bse_daily_bands.json:197>) and [bse_daily_bands.json:228](</C:/Users/yashw/swing trades/shared/bse_daily_bands.json:228>). A numeric `band_pct` must not override `record_valid:false`.

3. **P0 — CONFIRMED:** The live engine obtains a validated `band_pct` but discards it before sizing: it validates the field at [live_signal_engine.py:180](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:180>), then calls the calculator without it at [live_signal_engine.py:231](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:231>). This is a direct provenance break between exchange data and the capital decision.

## Claim review

### Rule 1 capability check

**CONFIRMED, repository and current-process environment scope only.**

- `RULE_1_OBSERVATION_GATE_PASSED` is `False` at [risk_calculator.py:12](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:12>), and `live_shares` is consequently zero at [risk_calculator.py:177](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:177>).
- No executable first-party import or call to Kite/other broker SDKs, `place_order`, `POST /orders/:variety`, or `api.kite.trade` was found.
- No environment-variable names associated with Kite, Zerodha, Upstox, Angel, Dhan, Fyers, Alpaca, IBKR, or generic broker credentials were present.
- The only token-like file found contains the literal placeholder `"your-api-token"`, not a credential.
- `requests.post` at [telegram_alert_bot.py:41](</C:/Users/yashw/swing trades/antigravity/daemons/telegram_alert_bot.py:41>) targets Telegram messaging, not a broker.
- Zerodha confirms that actual placement requires `POST /orders/:variety` with API authentication. No such path exists in reviewed first-party code. [Kite Connect order API](https://kite.trade/docs/connect/v3/orders/)

**UNVERIFIABLE (requires: broker account/holdings observation and a host-wide secret scan outside the permitted workspace):** absence of credentials elsewhere on the machine and actual account cash/positions.

### Band classifications and provenance

**CONFIRMED as local-file contents only:** The JSON states CHANDRIMA 2%; CROPSTER, CCDL, GATECH and KINETIC 5%; MOBIKWIK, LOVABLE, ANLON and VEDAVAAG 20%.

**UNVERIFIED — NO TRACEABLE LINEAGE:** These are not established as the official 18 September bands. `"band_source":"API_VERIFIED"` is an assertion, not provenance. The artifact lacks:

- API URL and request parameters;
- raw response or immutable response hash;
- exchange publication/circular identifier;
- ingestion timestamp tied to the current session;
- an effective-date verification.

ANLON and VEDAVAAG are additionally self-contradictory inputs: each exposes `band_pct:20` while declaring its record invalid because surveillance parsing failed.

BSE’s official framework supports fixed bands up to 20%, with surveillance revisions to 10%, 5%, or 2%; derivative-eligible securities instead use dynamic bands. [BSE Surveillance Master Circular, Item 1.1](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=9ce6001b-bcb5-4d63-a08f-26ab83e2051a&id=20250430-59)

### Q1 — Arithmetic direction and ten-session horizon

**CONFIRMED as implementation provenance:** `0.401` is documented and used as the ten-session 5% divisor at [risk_calculator.py:14](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:14>). The proposed helper computes a band-dependent ten-session loss at [risk_calculator.py:40](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:40>). Whether ten sessions is a suitable risk horizon is quantitative theory and belongs to Claude.

**UNVERIFIABLE (requires: a dated exchange rule or empirical lockout dataset establishing ten sessions for 20%-band securities):** that ten sessions is the correct horizon for MOBIKWIK, LOVABLE, ANLON, or VEDAVAAG. Neither BSE’s fixed-band framework nor the submitted files establish such a horizon. It must not be described as exchange-calibrated for 20% names.

### Q2 — Missing `band_pct`

**CONFIRMED: fail closed.**

A 20% default would invent exchange state and sever provenance. The calculator should require:

- finite, positive `band_pct`;
- current-session/effective-date validation;
- `validation.record_valid is True`;
- traceable exchange source.

Otherwise return zero shares with `INVALID_BAND_PCT` or equivalent. This matches the existing missing-volume refusal at [risk_calculator.py:145](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:145>).

Additionally, `calculate_position_size()` currently defaults `circuit_band_pct` to 5% at [risk_calculator.py:204](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:204>) and then ignores it at [risk_calculator.py:211](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:211>) and [risk_calculator.py:224](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:224>). That silent default must be removed.

### Q3 — Band changes after entry

**CONTRADICTED:** The package conflates fixed-band surveillance changes with intraday dynamic-band flexing.

- BSE fixed-band securities may be reassigned to 10%, 5%, or 2% through surveillance action.
- Dynamic intraday flexing applies to derivative-eligible securities and proceeds in 5% increments under exchange conditions. [BSE Surveillance Master Circular, Item 1.1](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=9ce6001b-bcb5-4d63-a08f-26ab83e2051a&id=20250430-59)
- NSE likewise describes system-driven intraday flexing for derivative-eligible securities, with revised ranges broadcast to members. [NSE Price Band/Operating Range Flex FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Flexing_of_Operating_Range_2.pdf)
- Published surveillance transitions ordinarily identify an explicit future effective date. For example, BSE’s ESM notice specifies effective dates for T2T, 2% bands, and periodic call auctions. [BSE ESM notice 20251023-34](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20251023-34)

Therefore a 20%→5% ASM/ESM change should not be called an “intraday dynamic revision” without a scrip-specific notice. It can still invalidate an entry-fixed divisor on a later effective session. Open positions must be reassessed against each session’s effective fixed band.

**UNVERIFIABLE (requires: the dated scrip-specific BSE/NSE notices):** the actual effective transitions for the reviewed names on 17–18 September 2026.

### Q4 — 2% case

Whether conservative under-sizing is acceptable is quantitative policy and belongs to Claude.

**CONFIRMED provenance defect:** Leaving the constant unchanged would make `calibrated_worst_case_10d_loss` factually mislabelled for 2% securities at [risk_calculator.py:188](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:188>). If the system claims band-aware loss, the 2% case must use the validated 2% input. Conservative output does not cure an incorrect label.

### Q5 — Call-site effects

**CONFIRMED:** The mandate’s “six call sites” is incomplete unless it means selected production calls only. The repository also contains tests, audit probes, and demonstrations.

Material production paths:

- [evaluate_monday_offense_and_defense.py:65](</C:/Users/yashw/swing trades/antigravity/analysis/evaluate_monday_offense_and_defense.py:65>) — supplies no band and separately recomputes loss with `0.401` at line 72.
- [live_signal_engine.py:231](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:231>) — has validated `band_pct` available but does not pass it.
- [accumulation_screener.py:195](</C:/Users/yashw/swing trades/antigravity/models/accumulation_screener.py:195>) — no band parameter is present in the sizing call.
- [pre_open_auction_engine.py:186](</C:/Users/yashw/swing trades/antigravity/models/pre_open_auction_engine.py:186>) — no band parameter; also invents a 10,000-share volume fallback at line 185.
- [risk_calculator.py:211](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:211>) and [risk_calculator.py:224](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:224>) — both internal delegation paths drop `circuit_band_pct`.

Positional callers in [audit_all_track1_files.py:69](</C:/Users/yashw/swing trades/antigravity/analysis/audit_all_track1_files.py:69>) and tests will require explicit migration. A defaulted fourth argument would allow old callers to continue silently mis-sizing; make validated `band_pct` required and preferably keyword-only.

### Live-hours assumption

**UNVERIFIABLE (requires: process/service logs covering a live session):** `runs_during_live_market_hours`.

The engine contains an unrestricted continuous loop at [live_signal_engine.py:362](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:362>), but source code does not prove that it was launched or remained healthy during market hours. It also has no internal exchange-session gate.

BLOCKED (P0: current 5%-only sizing can breach the declared loss budget on wider-band securities, while the proposed wiring lacks current traceable band provenance and multiple production callers discard or never receive band_pct)