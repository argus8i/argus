"""
tests/test_reviewer_dispatch_validation.py
==========================================
Regression tests for the reviewer dispatch failure-detection bug.

Claude Code prints "Failed to authenticate: OAuth session expired and could
not be refreshed" on stdout and exits 0. The dispatchers used
`success = (proc.returncode == 0)`, so that string was returned as a
successful review, signed, and written to claude_submission.md as an
authenticated reviewer finding.

These tests pin the rule: a review is accepted only when the process
succeeded AND actually said something.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from antigravity.daemons.tri_agent_bus import (
    MIN_REVIEW_CHARS,
    validate_reviewer_output,
)

VALID_REVIEW = (
    "## Claude Red-Team Findings\n"
    "Adverse selection is material: the only fills arrive from sellers exiting."
)


def test_accepts_a_real_review():
    assert validate_reviewer_output("CLAUDE", VALID_REVIEW, 0) is None


def test_rejects_oauth_failure_that_exits_zero():
    """The exact observed failure: real text, exit code 0, not a review."""
    out = "Failed to authenticate: OAuth session expired and could not be refreshed"
    err = validate_reviewer_output("CLAUDE", out, 0)
    assert err is not None
    assert "CLAUDE_DISPATCH_FAILED" in err
    assert "OAuth session expired" in err


@pytest.mark.parametrize("text", [
    "Not logged in. Please run /login to continue.",
    "Invalid API key provided",
    "Your credit balance is too low to proceed",
    "Usage limit reached, try again later",
    "API Error: rate limit exceeded",
])
def test_rejects_known_failure_signatures_at_exit_zero(text):
    assert validate_reviewer_output("CODEX", text, 0) is not None


def test_rejects_empty_output_at_exit_zero():
    err = validate_reviewer_output("CODEX", "   \n  ", 0)
    assert err is not None and "EMPTY_OUTPUT" in err


def test_rejects_nonzero_exit_even_with_good_text():
    err = validate_reviewer_output("CLAUDE", VALID_REVIEW, 1)
    assert err is not None and "NONZERO_EXIT" in err


def test_rejects_output_too_short_to_be_a_review():
    err = validate_reviewer_output("CLAUDE", "LGTM", 0)
    assert err is not None and "TOO_SHORT" in err


def test_short_output_allowed_when_caller_lowers_the_floor():
    """Health probes legitimately return short strings like BRIDGE_OK."""
    assert validate_reviewer_output("CODEX", "BRIDGE_OK", 0, min_chars=5) is None
    assert len("BRIDGE_OK") < MIN_REVIEW_CHARS


def test_failure_signature_check_is_case_insensitive():
    assert validate_reviewer_output("CLAUDE", "FAILED TO AUTHENTICATE with the API", 0) is not None


def test_reviewers_are_dispatched_read_only():
    """Reviewers must not be able to edit the workspace; the adapter writes."""
    import inspect
    from antigravity.daemons import tri_agent_bus as bus

    claude_src = inspect.getsource(bus.ask_claude_detailed)
    assert '"--allowedTools", "Read,Grep,Glob"' in claude_src
    assert "Write,Edit,NotebookEdit,Bash" in claude_src

    codex_src = inspect.getsource(bus.ask_codex_detailed)
    assert '"--sandbox", "read-only"' in codex_src

    # No permission-bypass flags anywhere in the dispatch layer.
    full = inspect.getsource(bus)
    for flag in ("--dangerously-skip-permissions",
                 "--dangerously-bypass-approvals-and-sandbox",
                 "danger-full-access"):
        assert flag not in full, f"permission-bypass flag present: {flag}"
