import json

from antigravity.daemons import tri_agent_monitor as monitor


def test_extract_verdict_prefers_conditional_over_approved():
    assert monitor.extract_verdict("Verdict: CONDITIONALLY_APPROVED") == "CONDITIONALLY APPROVED"


def test_extract_verdict_reports_timeout_from_exit_code():
    assert monitor.extract_verdict("anything", 124) == "TIMED OUT"


def test_read_jsonl_ignores_incomplete_final_record(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"ok": 1}) + "\n{" + "\n", encoding="utf-8")
    assert monitor.read_jsonl(str(path)) == [{"ok": 1}]


def test_dashboard_reports_dispatched_event_as_running(tmp_path, monkeypatch):
    dialogue = tmp_path / "dialogue.jsonl"
    events = tmp_path / "events.jsonl"
    dialogue.write_text("", encoding="utf-8")
    events.write_text(json.dumps({
        "dispatch_id": "dispatch_12345678",
        "timestamp": "2026-09-20 14:00:00 IST",
        "recipient": "Claude Code",
        "status": "DISPATCHED",
        "prompt_preview": "Review Track 2",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "DIALOGUE_PATH", str(dialogue))
    monkeypatch.setattr(monitor, "EVENTS_PATH", str(events))

    state = monitor.build_dashboard_state()

    assert state["agents"]["Claude Code"]["status"] == "RUNNING"
    assert state["active"][0]["prompt_preview"] == "Review Track 2"

