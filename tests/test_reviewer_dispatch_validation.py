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


def test_reviewers_require_checkpoint_before_authorized_dispatch():
    """User authorized full project access on 20-Sep; every real route backs up."""
    import inspect
    from antigravity.daemons import tri_agent_bus as bus

    claude_src = inspect.getsource(bus.ask_claude_detailed)
    assert 'prepare_dispatch("CLAUDE")' in claude_src

    # Codex cannot use --sandbox read-only: it hangs on this Windows host.
    # Its boundary is adapter-side path validation, so assert only that no
    # permission-bypass flag is present.
    codex_src = inspect.getsource(bus.ask_codex_detailed)
    assert 'prepare_dispatch("CODEX")' in codex_src
    assert 'prepare_dispatch("ANTIGRAVITY")' in inspect.getsource(bus.ask_antigravity_detailed)

    # No permission-bypass flags anywhere in the dispatch layer.
    full = inspect.getsource(bus)
    for flag in ("--dangerously-skip-permissions",
                 "--dangerously-bypass-approvals-and-sandbox",
                 "danger-full-access"):
        assert flag not in full, f"permission-bypass flag present: {flag}"


# Observed verbatim from codex.exe on 2026-09-18 when the ChatGPT account hit
# its cap. The old signature list contained "usage limit reached", which does
# NOT appear in this string: the phrasing guess missed, and quota exhaustion was
# caught only incidentally because the exit code happened to be 1.
CODEX_QUOTA_MESSAGE = (
    "ERROR: You've hit your usage limit. Upgrade to Pro "
    "(https://chatgpt.com/explore/pro), visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits or "
    "try again at 3:22 AM."
)


def test_real_codex_quota_message_is_rejected_at_exit_zero():
    """Must not depend on the exit code to catch exhausted quota."""
    err = validate_reviewer_output("CODEX", CODEX_QUOTA_MESSAGE, 0)
    assert err is not None
    assert "CODEX_DISPATCH_FAILED" in err


@pytest.mark.parametrize("text", [
    "You've hit your usage limit.",
    "Usage limit reached for this account",
    "You have exceeded your quota",
    "Insufficient credit remaining",
    "Error: model overloaded, retry later",
    "503 Service Unavailable",
])
def test_capacity_and_quota_phrasings_all_rejected(text):
    """Match the noun phrase rather than one guessed sentence."""
    assert validate_reviewer_output("CODEX", text, 0) is not None


@pytest.mark.parametrize("text", [
    'jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.',
    'jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied.',
])
def test_antigravity_headless_permission_denial_is_rejected_at_exit_zero(text):
    """A tool denial is a failed task even when agy.exe exits successfully."""
    err = validate_reviewer_output("ANTIGRAVITY", text, 0)
    assert err is not None
    assert "ANTIGRAVITY_DISPATCH_FAILED" in err


def test_nonzero_exit_reports_what_the_cli_said():
    """A bare "exited 1" cannot be told apart from a crash or an auth failure,
    which sends the reader off retrying something that cannot yet succeed."""
    err = validate_reviewer_output("CODEX", CODEX_QUOTA_MESSAGE, 1)
    assert "exited 1" in err
    assert "usage limit" in err.lower(), "the reason must survive into the error"


def test_nonzero_exit_with_no_output_still_reports_cleanly():
    err = validate_reviewer_output("CLAUDE", "", 1)
    assert err == "CLAUDE_NONZERO_EXIT: exited 1"
