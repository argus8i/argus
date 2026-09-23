# Project agent access — 20 September 2026

Implemented at Yashu's explicit request for all three project agents to have
full execution/edit capability. Applies to the CLI instances launched by the
project bus; does not reconfigure existing IDE chats or global application settings.

- Claude: `--dangerously-skip-permissions`; removed read-only tool restrictions.
- Antigravity: `--dangerously-skip-permissions`; retained slash-command disabling.
- Codex: `--dangerously-bypass-approvals-and-sandbox`.
- The live inbox worker and direct model dispatchers create a fresh verified
  checkpoint before every actual model process. A failed checkpoint blocks launch.
- Existing permission-denial detection remains enabled.

Backups: `C:\Users\yashw\swing-trades-checkpoints`.
Each ZIP includes `workspace/` and `manifest.json` containing file SHA-256 values.
Tracked, untracked, ignored project files and `.git` are copied without changing
the working tree. Rebuildable `.venv`, `venv`, `node_modules`, `__pycache__`,
`.pytest_cache`, and `.mypy_cache` directories are excluded and recorded. Links
require explicit handling and stop a backup rather than silently losing content.
Files modified during their copy also stop the backup. A checkpoint is not a
transactional whole-directory snapshot if other applications keep writing.

For recovery, extract a chosen ZIP to a NEW directory, verify its manifest, and
inspect differences before restoring files. No automatic rollback or deletion
of checkpoints is performed. Checkpoints accumulate and use disk space.

Unrestricted processes can still alter files and backups accessible to the same
Windows account. These backups reduce recovery cost; they do not guarantee damage
prevention. Keep an independent/offline copy for stronger protection. Paper-only
and no-live-order instructions remain in every dispatched prompt, but are not an
OS-enforced broker block. No broker state or login is changed by this setup.

Verification: 212 first-party tests passed, including backup recovery-content,
out-of-workspace destination, and backup-failure dispatch checks. Live capability
probe results are recorded separately when completed.

## Live verification completed

All three CLI agents read AGENTS.md, executed the arithmetic command (719 * 23 =
16537), and created their expected probe files under shared/reviews. Codex checked
each file's exact contents independently.

The supervised inbox worker is running. The first worker probe exposed a missing
package import path; the next exposed Antigravity's default scratch workspace.
The worker now inserts the repository import root, and Antigravity receives
`--add-dir` plus explicit absolute workspace instructions.

Final signed background test: correlation
`corr_1789843144_236b5d253e21`, `COMPLETED`, verified HMAC, completion marker and
artifact hash. `worker_access_probe_20260920.txt` has SHA-256
`a32e9eb6db9ce24c149949e6b360926e26109ba8ccf4f4a8830fd3c7fcd7033b`.
Earlier failed probes stayed FAILED/INCOMPLETE instead of false success.

All 212 tests passed after the import fix; the final launch-path adjustment passed
the 27 directly relevant tests and the real signed background test.
No Phase 1 trading repair was dispatched as part of this access test.
No global permissions/settings were changed; running IDE sessions retain their
own settings. Broker authority remains prohibited by instructions, not technically
isolated from an unrestricted process running under this Windows account.
