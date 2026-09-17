"""
tests/conftest.py
=================
Keeps the test suite out of canonical, human-read artifacts.

Running the suite used to append mock reviewer exchanges to the real
antigravity/logs/tri_agent_dialogue.md — 17 fabricated "Antigravity -> Claude
Code" entries complete with timestamps and audit verdicts — and to scatter
test_box_* directories through antigravity/messages/. That is the same defect
class that produced the fabricated reviewer submissions: fixtures writing to
paths a human later reads as a record of what really happened.
"""

import os
import shutil
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True, scope="session")
def _redirect_audit_log_away_from_canonical_path():
    """Point the dialogue log at a temp dir for the whole session."""
    tmp = tempfile.mkdtemp(prefix="tri_agent_test_logs_")
    prev = os.environ.get("TRI_AGENT_LOGS_DIR")
    os.environ["TRI_AGENT_LOGS_DIR"] = tmp
    try:
        yield tmp
    finally:
        if prev is None:
            os.environ.pop("TRI_AGENT_LOGS_DIR", None)
        else:
            os.environ["TRI_AGENT_LOGS_DIR"] = prev
        shutil.rmtree(tmp, ignore_errors=True)
