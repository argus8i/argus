execution_realism: Track 2 execution-realism reference implementation (paper only, Rule 1).
Specification: the "ORB Execution Realism" artifact page.

Run (from the folder that contains execution_realism/ and tests/):
    .venv\Scripts\python.exe -m pytest tests -q

Standard library only in the package. Tests also use numpy; the JSON Schema test uses jsonschema and skips without it.
Nothing here closes a Codex finding until the probe script passes against changed code in antigravity/ (Rule 8 review first).
