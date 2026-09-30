"""Signed peer-to-peer Nexus messages through the Antigravity gateway.

This command addresses headless CLI agents, not the currently open IDE chats.
"""

import argparse
import json
import sys

from antigravity.daemons.tri_agent_bus import send_to_agent, wait_for_agent_response, get_message_status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender", choices=("ANTIGRAVITY", "CLAUDE", "CODEX", "USER"))
    parser.add_argument("--recipient", required=True, choices=("ANTIGRAVITY", "CLAUDE", "CODEX"))
    parser.add_argument("--subject")
    parser.add_argument("--body")
    parser.add_argument("--track", default="SHARED", choices=("SHARED", "TRACK_1", "TRACK_2"))
    parser.add_argument("--wait", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--execution-timeout", type=float, default=300, metavar="SECONDS")
    parser.add_argument("--poll", metavar="CORRELATION_ID", help="Collect a previous reply without resending")
    args = parser.parse_args(argv)

    if args.poll:
        correlation_id = args.poll
        message_id = None
    else:
        if not all((args.sender, args.subject, args.body)):
            parser.error("--sender, --subject and --body are required when sending")
        if not 1 <= args.execution_timeout <= 900:
            parser.error("--execution-timeout must be between 1 and 900 seconds")
        message_id, correlation_id = send_to_agent(
            sender=args.sender, recipient=args.recipient, subject=args.subject,
            body=args.body, track=args.track, timeout_sec=args.execution_timeout,
        )
        print(json.dumps({"message_id": message_id, "correlation_id": correlation_id, "recipient": args.recipient}))
    if args.wait <= 0 and not args.poll:
        return 0
    result = wait_for_agent_response(correlation_id, args.recipient, timeout_sec=max(0, args.wait))
    if result["status"] == "TIMED_OUT":
        state = get_message_status(message_id, correlation_id) if message_id else None
        if state and state["status"] in ("CREATED", "CLAIMED", "PROCESSING"):
            result["status"] = "PENDING"
            result["error"] = None
        elif not message_id:
            result["status"] = "PENDING_OR_UNKNOWN"
            result["error"] = None
    response = result.get("response") or {}
    print(json.dumps({
        "success": result["success"],
        "status": result["status"],
        "error": result.get("error"),
        "output_payload": response.get("output_payload"),
        "correlation_id": correlation_id,
    }, ensure_ascii=False))
    return 0 if result["success"] or result["status"] in ("PENDING", "PENDING_OR_UNKNOWN") else 1


if __name__ == "__main__":
    sys.exit(main())
