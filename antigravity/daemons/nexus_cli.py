"""Signed peer-to-peer Nexus messages through the Antigravity gateway.

This command addresses headless CLI agents, not the currently open IDE chats.
"""

import argparse
import json
import sys

from antigravity.daemons.tri_agent_bus import send_to_agent, wait_for_agent_response


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender", required=True, choices=("ANTIGRAVITY", "CLAUDE", "CODEX", "USER"))
    parser.add_argument("--recipient", required=True, choices=("ANTIGRAVITY", "CLAUDE", "CODEX"))
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--track", default="SHARED", choices=("SHARED", "TRACK_1", "TRACK_2"))
    parser.add_argument("--wait", type=float, default=0, metavar="SECONDS")
    args = parser.parse_args(argv)

    message_id, correlation_id = send_to_agent(
        sender=args.sender,
        recipient=args.recipient,
        subject=args.subject,
        body=args.body,
        track=args.track,
    )
    print(json.dumps({"message_id": message_id, "correlation_id": correlation_id, "recipient": args.recipient}))
    if args.wait <= 0:
        return 0
    result = wait_for_agent_response(correlation_id, args.recipient, timeout_sec=args.wait)
    response = result.get("response") or {}
    print(json.dumps({
        "success": result["success"],
        "status": result["status"],
        "error": result.get("error"),
        "output_payload": response.get("output_payload"),
    }, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
