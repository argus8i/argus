# Nexus Bus: signed peer messages

Antigravity is the central gateway. Any of `ANTIGRAVITY`, `CLAUDE`, `CODEX`, or
`USER` can send a signed request to `ANTIGRAVITY`, `CLAUDE`, or `CODEX` with
`send_to_agent()`. The gateway writes one signed response for each request.
Claude and Codex are invoked as **headless CLI sessions**, not as messages
appearing in the currently open IDE chat windows. A gateway signature attests
the CLI output; it is not a signature independently produced by that model.

From the repository root, for a route-only check:

```powershell
.venv\Scripts\python.exe -m antigravity.daemons.nexus_cli --sender CODEX --recipient CLAUDE --subject PING --body "route check" --wait 15
```

For a quick model conversation, use `--subject CHAT --body "Discuss this idea: ..."`.
Claude has no tools on this route, and Codex uses its read-only sandbox. They
answer only from the message text; no repository inspection occurs. For a
repository-reading review, use `--subject REVIEW`. That route uses the existing
verified-checkpoint dispatch. This repository is large, so a review can spend
substantial time creating a checkpoint. `PING` checks only the signed gateway
route; it does **not** prove model availability, login, credit, or a completed
review. Antigravity model requests still use the checkpoint route.

Use `TRACK_1`, `TRACK_2`, or `SHARED` explicitly when relevant. Peer messages
are discussion/review only; the gateway does not accept expected artifacts for
Claude or Codex routes. Existing `send_to_antigravity()` and
`wait_for_antigravity_response()` callers remain supported.
