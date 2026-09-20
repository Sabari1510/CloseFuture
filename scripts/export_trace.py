"""A9: scripted full conversation -> traces/<session>.jsonl + .md timeline.

Covers question, multi-intent(booking), slot confirm (Meet link), finish
(lead mail) — proving E1–E10 edges in one ordered stream. $0 fake backends.
Usage: python scripts/export_trace.py [--session ID]
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from src.app.api import main as api

SCRIPT = [
    "What services does CloseFuture offer?",
    "Tell me about the Dipy marketplace. Also book me a call, me@corp.io",
    "yes, 1",
    "Thanks, that's all!",
]


def run_conversation() -> tuple[str, list]:
    api.sales_inbox = "sales@example.com"
    c = TestClient(api.app)
    sid, vid = None, None
    replies = []
    for msg in SCRIPT:
        body = {"message": msg, "timezone": "Asia/Dubai"}
        if sid:
            body.update(session_id=sid, visitor_id=vid)
        r = c.post("/v1/chat", json=body).json()
        sid, vid = r["session_id"], r["visitor_id"]
        replies.append((msg, r["reply"][:120], r["status"]))
    return sid, replies


def export(session_id: str, outdir: str = "traces") -> tuple[Path, Path]:
    evs = api.events.for_session(session_id)
    out = Path(outdir)
    out.mkdir(exist_ok=True)
    jl = out / f"{session_id}.jsonl"
    jl.write_text("\n".join(json.dumps({"seq": e.seq, "type": e.type, "agent": e.agent,
                                        "payload": e.payload}) for e in evs), encoding="utf-8")
    lines = [f"# Trace {session_id}", ""]
    for e in evs:
        lines.append(f"## {e.seq}. {e.type} ({e.agent or '-'})")
        lines.append(f"```json\n{json.dumps(e.payload, indent=1)[:800]}\n```")
    md = out / f"{session_id}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return jl, md


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    if args.session:
        jl, md = export(args.session)
    else:
        sid, replies = run_conversation()
        for q, a, s in replies:
            print(f"[{s}] Q: {q[:60]}\n     A: {a}")
        jl, md = export(sid)
    print(f"wrote {jl} + {md}")
