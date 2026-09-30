r"""
verify_kill_recovery_trials.py - Automated Multi-Trial Kill & Recovery Protocol
=============================================================================
Runs 5 consecutive deliberate supervisor termination trials via taskkill /F /PID,
measuring automated Task Scheduler / Watchdog recovery latency, process uniqueness,
and end-to-end PING round-trip latency.
"""

import os
import sys
import time
import json
import subprocess
from datetime import datetime

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.supervised_inbox_worker import get_status, get_current_ist, _pid_is_running
from antigravity.daemons.tri_agent_bus import send_to_agent, wait_for_agent_response

NUM_TRIALS = 5


def run_trials():
    print(f"[{get_current_ist()}] Starting {NUM_TRIALS}-Trial Resilience Verification Protocol...")
    results = []

    for trial in range(1, NUM_TRIALS + 1):
        print(f"\n--- TRIAL {trial}/{NUM_TRIALS} ---")
        st_before = get_status()
        if not st_before.get("running"):
            print("Daemon not currently running, waiting up to 60s for watchdog...")
            t0 = time.time()
            while time.time() - t0 < 65.0:
                time.sleep(2.0)
                st_before = get_status()
                if st_before.get("running"):
                    break

        sup_pid_old = st_before.get("details", {}).get("supervisor_pid")
        worker_pid_old = st_before.get("details", {}).get("worker_pid")
        print(f"Initial State: Supervisor PID={sup_pid_old}, Worker PID={worker_pid_old}")

        # Kill supervisor deliberately using taskkill /F /PID
        t_kill = time.time()
        kill_cmd = ["taskkill", "/F", "/PID", str(sup_pid_old)]
        kill_res = subprocess.run(kill_cmd, capture_output=True, text=True)
        print(f"Action: taskkill /F /PID {sup_pid_old} -> Exit {kill_res.returncode}: {kill_res.stdout.strip() or kill_res.stderr.strip()}")

        # Verify immediate dead state
        time.sleep(1.0)
        st_dead = get_status()
        print(f"Immediate Post-Kill State: status={st_dead.get('status')}, running={st_dead.get('running')}")

        # Wait for automated recovery (max 75s, watchdog runs every 60s)
        recovered = False
        st_new = {}
        while time.time() - t_kill < 75.0:
            time.sleep(1.0)
            st_new = get_status()
            if st_new.get("running") and st_new.get("status") == "RUNNING" and st_new.get("worker_alive"):
                sup_pid_new = st_new.get("details", {}).get("supervisor_pid")
                if sup_pid_new != sup_pid_old:
                    recovered = True
                    break

        t_recovered = time.time()
        recovery_sec = round(t_recovered - t_kill, 2)
        sup_pid_new = st_new.get("details", {}).get("supervisor_pid")
        worker_pid_new = st_new.get("details", {}).get("worker_pid")

        print(f"Recovery Result: Recovered={recovered} in {recovery_sec}s -> New Supervisor PID={sup_pid_new}, Worker PID={worker_pid_new}")

        # End-to-end PING verification
        t_ping_start = time.time()
        msg_id, corr_id = send_to_agent(
            sender="CLAUDE",
            recipient="CODEX",
            subject="PING",
            body=f"trial_{trial}_ping",
            track="SHARED",
            timeout_sec=15.0
        )
        resp = wait_for_agent_response(
            correlation_id=corr_id,
            recipient="CODEX",
            timeout_sec=10.0,
            poll_interval_sec=0.2
        )
        ping_latency_sec = round(time.time() - t_ping_start, 3)
        ping_ok = (resp.get("status") == "COMPLETED" and resp.get("response", {}).get("output_payload", {}).get("reply") == "PONG")
        print(f"PING Verification: Success={ping_ok}, Latency={ping_latency_sec}s, CorrelationID={corr_id}")

        trial_data = {
            "trial": trial,
            "timestamp_ist": get_current_ist(),
            "target_killed": f"Supervisor PID={sup_pid_old}",
            "kill_method": f"taskkill /F /PID {sup_pid_old}",
            "kill_exit_code": kill_res.returncode,
            "immediate_status": st_dead.get("status"),
            "recovered": recovered,
            "recovery_duration_sec": recovery_sec,
            "new_supervisor_pid": sup_pid_new,
            "new_worker_pid": worker_pid_new,
            "ping_verified": ping_ok,
            "ping_latency_sec": ping_latency_sec,
        }
        results.append(trial_data)

    output_path = os.path.join(WORKSPACE_DIR, "shared", "trust", "artifacts", "nexus_resilience_5trial_kill_test.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nAll {NUM_TRIALS} trials completed. Artifact written to: {output_path}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    run_trials()
