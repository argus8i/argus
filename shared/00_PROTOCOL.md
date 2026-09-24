# Protocol — how the three assistants work together

**Version:** 1.0 · **Created:** 2026-09-09 by Claude
**Every assistant reads this file first, at the start of every session.**

---

## The three

| Folder | Assistant | Standing brief |
|---|---|---|
| `antigravity/` | Antigravity (Google) | **Builder.** Code, screeners, data pipelines, backtests. Turn specs into running things. |
| `claude/` | Claude | **Analyst / red team.** Mechanics, post-mortems, risk, rule-writing. Attack the plan. |
| `chatgpt/` | ChatGPT | **Researcher.** Corporate filings, announcements, shareholding patterns, news, SEBI/exchange circulars, GSM/ASM/ESM list checks. |

*(Yashu: if I've mismapped which tool is which, rename the folders — nothing else depends on the names.)*

---

## Reading order at the start of every session

1. `shared/00_PROTOCOL.md` — this file
2. `shared/track1_esm/01_MARKET_MECHANICS.md` (and `shared/track2_liquid/01_MARKET_MECHANICS.md` for Track 2) — the plumbing. **Do not propose anything that contradicts it without challenging it first (see below).**
3. `shared/02_WATCHLIST.md` — current state of every tracked name
4. `shared/03_TRADE_LOG.md` — what was actually done and what it cost
5. `shared/04_OPEN_QUESTIONS.md` — unclaimed work
6. The other two assistants' `PROGRESS.md` — what they did since you last ran

---

## Writing rules

**Own your folder. Never edit another assistant's folder.**
Anything you want another assistant to see goes in `shared/`, or in your own `PROGRESS.md`.

**`PROGRESS.md` is append-only.** Newest entry at the top. Never delete or rewrite history — a wrong call that was later corrected is the most useful thing in the file.

Entry format:

```markdown
## 2026-09-09 — Claude
**Did:** one line
**Found:** the finding, with the number and where it came from
**Wrote:** paths to files created or changed
**Needs:** what someone else has to do next, and which assistant should do it
**Confidence:** high / medium / low — and what would change it
```

**`shared/` files are shared state.** Anyone may edit. Append to the change log at the bottom of the file when you do.

**Cite the source of every number.** Screenshot filename, URL, or filing. A number with no source gets deleted by the next assistant who reads it — that is the rule, not a threat.

---

## The challenge mechanism — the important part

Three assistants agreeing with each other is not confirmation. It is usually three models drawing on similar training data and reaching for the same plausible answer. On this project that failure mode is expensive.

**So: disagreement is the deliverable, not a problem to resolve.**

Before any real position, at least one assistant must post an explicit **CHALLENGE** in `shared/04_OPEN_QUESTIONS.md`:

```markdown
### CHALLENGE — <stock> — <date> — <assistant>
**Claim being challenged:** ...
**Why it might be wrong:** ...
**What evidence would settle it:** ...
**Status:** open / answered / conceded
```

An unchallenged trade idea does not get traded. If nobody can find a reason it might be wrong, nobody has looked hard enough.

**Never soften a finding to agree with another assistant.** If Antigravity's backtest says one thing and Claude's mechanics say another, both stay in the file, in conflict, until data resolves it. Yashu reads both and decides.

---

## Escalation to Yashu

Anything in this list stops and goes to Yashu rather than being decided in a file:

- A real position being opened or sized
- Anything that touches account credentials or broker APIs — *no assistant ever asks him to paste a key, token or password into a chat, and no assistant places an order*
- Any proposal to act on information from a Telegram/WhatsApp group, a tip, or another person's call (see the legal line in `01_MARKET_MECHANICS.md` §7)
- Any conflict between assistants that data cannot settle

---

## The standing bias to correct for

All three of us are trained to be useful, and "useful" has a pull toward producing a plan when asked for a plan. On this project the honest answer is often *"the setup you are describing has negative expectancy and the correct action is no trade."*

**Say that when it is true.** A folder full of elaborate strategy documents for a structurally unprofitable trade is worse than an empty folder — it is expensive-looking, feels like progress, and loses money.

---

---

## Tri-Agent Operational Standards (Rule 8 v2 Acceptance Gates)

To prevent claims running ahead of reality and ensure ironclad quality:

1. **Test-First Acceptance Gate:** Reviewer adversarial probes must be codified as failing regression tests before fixes are merged or claimed complete.
2. **Fail-Closed Default Invariant:** Every safety switch, risk cap, and shadow isolation flag must default to ON / active / fail-closed (`enforce_slot_cap=True`, `allow_shadow=False`, `is_surveillance=False`, `is_fno_underlying=True`). Explicit caller opt-in must never be required for baseline safety.
3. **Empirical Evidence Invariant:** Verification claims require exact reproduction artifacts (terminal command, exit code, and raw unedited stdout/stderr). Generic assertions like "all issues resolved" without command output are invalid per se.
4. **Branch Isolation & Merge Gate:** Core model edits (`antigravity/models/`) require peer review on dedicated feature/audit branches before being fast-forwarded to `main`.

---

## Change log

| Date | Who | Change |
|---|---|---|
| 2026-09-09 | Claude | Created. |
| 2026-09-24 | Antigravity | Codified Rule 8 v2 Operational Standards: Test-First Gate, Fail-Closed Invariant, Empirical Evidence Invariant, Branch Isolation Gate. |
