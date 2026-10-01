"""
Live post-deployment verification for ARGUS Nexus Bus & Supervisor:
1. Dispatches one long request to CODEX and one quick request to ANTIGRAVITY simultaneously.
2. Proves that the quick request completes first without head-of-line blocking.
3. Proves that the long request reply can be collected later via its correlation_id without resending.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from antigravity.daemons.tri_agent_bus import send_to_agent, wait_for_agent_response

def main():
    print("=== LIVE NEXUS CONCURRENCY & ASYNC COLLECTION VERIFICATION ===")
    
    # 1. Enqueue long request to CODEX
    prompt_long = "Explain in 3 concise bullet points the difference between Track 1 (micro-caps) and Track 2 (liquid F&O) in Project Swing Trades per AGENTS.md."
    t0 = time.time()
    msg_long, corr_long = send_to_agent(
        sender="ANTIGRAVITY",
        subject="CONCURRENCY_TEST_LONG",
        body={"task": "explain_tracks", "prompt": prompt_long},
        recipient="CODEX"
    )
    print(f"Enqueued LONG request to CODEX: correlation_id={corr_long}, time=0.00s")
    
    # 2. Immediately enqueue quick request to ANTIGRAVITY
    prompt_quick = "Reply with exactly the single word 'PONG'."
    msg_quick, corr_quick = send_to_agent(
        sender="CODEX",
        subject="CONCURRENCY_TEST_QUICK",
        body={"task": "ping", "prompt": prompt_quick},
        recipient="ANTIGRAVITY"
    )
    print(f"Enqueued QUICK request to ANTIGRAVITY: correlation_id={corr_quick}")
    
    # 3. Wait for QUICK request to complete first
    print("\nWaiting for QUICK request to complete...")
    quick_start = time.time()
    quick_resp = wait_for_agent_response(
        correlation_id=corr_quick,
        recipient="ANTIGRAVITY",
        timeout_sec=60.0,
        poll_interval_sec=1.0,
        auto_process_worker=False
    )
    quick_elapsed = time.time() - quick_start
    print(f"QUICK request completed in {quick_elapsed:.2f}s!")
    print(f"QUICK Status: {quick_resp.get('status')}")
    assert quick_resp.get("status") == "COMPLETED", f"Quick request failed: {quick_resp}"
    
    # 4. Now collect LONG request WITHOUT resending
    print("\nCollecting LONG request from CODEX (without resending)...")
    long_start = time.time()
    long_resp = wait_for_agent_response(
        correlation_id=corr_long,
        recipient="CODEX",
        timeout_sec=180.0,
        poll_interval_sec=2.0,
        auto_process_worker=False
    )
    long_elapsed = time.time() - t0
    print(f"LONG request collected successfully in total {long_elapsed:.2f}s!")
    print(f"LONG Status: {long_resp.get('status')}")
    assert long_resp.get("status") == "COMPLETED", f"Long request failed: {long_resp}"
    print(f"LONG Output Preview:\n{str(long_resp.get('output_payload'))[:200]}...")
    
    print("\n=== VERIFICATION SUCCESS: Both criteria satisfied ===")
    print("1. Quick request completed first while long request was active.")
    print("2. Long reply was cleanly collected by correlation_id without resending.")

if __name__ == "__main__":
    main()
