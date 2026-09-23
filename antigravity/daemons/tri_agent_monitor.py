"""Local read-only dashboard for tri-agent dispatches and verdicts."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOGS_DIR = os.path.join(WORKSPACE, "antigravity", "logs")
DIALOGUE_PATH = os.path.join(LOGS_DIR, "tri_agent_dialogue.jsonl")
EVENTS_PATH = os.path.join(LOGS_DIR, "tri_agent_dispatch_events.jsonl")
VERDICT_PATTERNS = (
    (r"\bCONDITIONALLY[_ -]?APPROVED\b", "CONDITIONALLY APPROVED"),
    (r"\bCONDITIONAL\b", "CONDITIONAL"),
    (r"\bBLOCKED\b", "BLOCKED"),
    (r"\bREJECT(?:ED)?\b", "REJECTED"),
    (r"\bCHALLENGE\b", "CHALLENGE"),
    (r"\bAPPROVED\b", "APPROVED"),
    (r"\bACCEPT(?:ED)?\b", "ACCEPTED"),
)


def read_jsonl(path: str) -> list[dict[str, Any]]:
    """Read valid objects while tolerating an incomplete final append."""
    if not os.path.exists(path):
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace") as source:
        for line in source:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def extract_verdict(response: str, exit_code: int | None = 0) -> str:
    """Return a compact explicit verdict without inventing consensus."""
    if exit_code not in (None, 0):
        return "TIMED OUT" if exit_code == 124 else "FAILED"
    normalized = response.upper()
    if any(marker in normalized for marker in (
        "FAILED TO AUTHENTICATE", "USAGE LIMIT", "INVALID API KEY", "NO OUTPUT PRODUCED"
    )):
        return "FAILED"
    for pattern, label in VERDICT_PATTERNS:
        if re.search(pattern, normalized):
            return label
    return "NO EXPLICIT VERDICT"


def response_summary(response: str, limit: int = 260) -> str:
    """Extract the first useful line for a scannable activity card."""
    for raw_line in response.splitlines():
        line = re.sub(r"^[#>*\-\s]+", "", raw_line).strip()
        if line and not line.startswith("---"):
            return line[:limit]
    return "No response text recorded."


def build_dashboard_state(limit: int = 30) -> dict[str, Any]:
    """Merge lifecycle events with completed dialogue records."""
    dialogue = read_jsonl(DIALOGUE_PATH)
    events = read_jsonl(EVENTS_PATH)
    latest_events: dict[str, dict[str, Any]] = {}
    for event in events:
        dispatch_id = str(event.get("dispatch_id", ""))
        if dispatch_id:
            latest_events[dispatch_id] = event

    active = [
        event for event in latest_events.values()
        if event.get("status") == "DISPATCHED"
    ]
    completed = []
    for record in reversed(dialogue[-limit:]):
        response = str(record.get("response", ""))
        exit_code = record.get("exit_code")
        completed.append({
            "timestamp": record.get("timestamp", "Unknown time"),
            "agent": record.get("recipient", "Unknown agent"),
            "status": "TIMED_OUT" if exit_code == 124 else ("COMPLETED" if exit_code == 0 else "FAILED"),
            "elapsed_seconds": record.get("elapsed_seconds", 0),
            "verdict": extract_verdict(response, exit_code),
            "prompt": " ".join(str(record.get("prompt", "")).split())[:400],
            "summary": response_summary(response),
            "response": response,
        })

    agent_states: dict[str, dict[str, Any]] = {}
    for agent in ("Claude Code", "OpenAI Codex", "Antigravity Model"):
        running = next((event for event in reversed(active) if event.get("recipient") == agent), None)
        recent = next((item for item in completed if item["agent"] == agent), None)
        agent_states[agent] = {
            "status": "RUNNING" if running else (recent["status"] if recent else "IDLE"),
            "verdict": "PENDING" if running else (recent["verdict"] if recent else "NO REVIEW"),
            "last_update": (running or recent or {}).get("timestamp", "—"),
        }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "agents": agent_states,
        "active": list(reversed(active)),
        "completed": completed,
        "raw_log": DIALOGUE_PATH,
    }


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tri-Agent Monitor</title>
<style>
:root{color-scheme:dark;--bg:#0a0d14;--panel:#121824;--line:#263043;--text:#eef3ff;--muted:#8f9bb3;--blue:#6ea8fe;--green:#46d39a;--red:#ff6b7a;--amber:#ffc857}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#18213a 0,#0a0d14 42%);color:var(--text);font:14px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1200px;margin:auto;padding:28px}header{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:20px}h1{margin:0;font-size:28px}h2{font-size:16px;margin:26px 0 10px}.muted{color:var(--muted)}.agents{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card,.entry{background:rgba(18,24,36,.94);border:1px solid var(--line);border-radius:14px;padding:16px}.agent{font-weight:700}.pill{display:inline-block;padding:3px 9px;border-radius:999px;background:#222d42;margin-top:8px;font-size:12px}.RUNNING{color:var(--blue)}.COMPLETED,.APPROVED,.ACCEPTED{color:var(--green)}.FAILED,.TIMED_OUT,.BLOCKED,.REJECTED{color:var(--red)}.CONDITIONAL,.CHALLENGE{color:var(--amber)}
.entry{margin-bottom:10px}.entry-head{display:flex;gap:12px;justify-content:space-between}.verdict{font-weight:800}.prompt{margin-top:10px;color:#cbd5e8}.summary{margin-top:8px}.details{display:none;margin-top:12px;padding:12px;background:#090c12;border-radius:9px;white-space:pre-wrap;max-height:430px;overflow:auto}.entry.open .details{display:block}button{background:#202b40;color:var(--text);border:1px solid #35435d;border-radius:8px;padding:6px 10px;cursor:pointer}.empty{padding:20px;border:1px dashed var(--line);border-radius:12px;color:var(--muted)}
@media(max-width:760px){.agents{grid-template-columns:1fr}header{display:block}}
</style></head><body><main>
<header><div><h1>Tri-Agent Monitor</h1><div class="muted">Live dispatch state and compact verdict ledger</div></div><div id="updated" class="muted"></div></header>
<section class="agents" id="agents"></section><h2>Currently running</h2><section id="active"></section><h2>Recent completed reviews</h2><section id="completed"></section>
</main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function cls(s){return String(s||'').replace(/[^A-Z_]/g,'')}
async function refresh(){try{const d=await fetch('/api/status',{cache:'no-store'}).then(r=>r.json());
document.getElementById('updated').textContent='Updated '+d.generated_at;
document.getElementById('agents').innerHTML=Object.entries(d.agents).map(([name,a])=>`<article class="card"><div class="agent">${esc(name)}</div><div class="pill ${cls(a.status)}">${esc(a.status)}</div><div class="verdict ${cls(a.verdict)}">${esc(a.verdict)}</div><div class="muted">${esc(a.last_update)}</div></article>`).join('');
document.getElementById('active').innerHTML=d.active.length?d.active.map(a=>`<article class="entry"><div class="entry-head"><b>${esc(a.recipient)}</b><span class="RUNNING">RUNNING</span></div><div class="prompt">${esc(a.prompt_preview)}</div><div class="muted">Dispatched ${esc(a.timestamp)}</div></article>`).join(''):'<div class="empty">No agent is currently running.</div>';
document.getElementById('completed').innerHTML=d.completed.length?d.completed.map((x,i)=>`<article class="entry" id="e${i}"><div class="entry-head"><div><b>${esc(x.agent)}</b> · <span class="verdict ${cls(x.verdict)}">${esc(x.verdict)}</span></div><button onclick="document.getElementById('e${i}').classList.toggle('open')">Show full response</button></div><div class="summary">${esc(x.summary)}</div><div class="prompt"><b>Prompt:</b> ${esc(x.prompt)}</div><div class="muted">${esc(x.timestamp)} · ${esc(x.elapsed_seconds)}s · ${esc(x.status)}</div><pre class="details">${esc(x.response)}</pre></article>`).join(''):'<div class="empty">No completed reviews recorded.</div>';
}catch(e){document.getElementById('updated').textContent='Monitor error: '+e.message}}
refresh();setInterval(refresh,2000);
</script></body></html>"""


class MonitorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/api/status":
            body = json.dumps(build_dashboard_state(), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local tri-agent monitor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Tri-Agent Monitor: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

