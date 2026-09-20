"""A9 trace test: full conversation exports ordered routing/tool/guardrail evidence."""
import json
from scripts.export_trace import export, run_conversation


def test_a9_full_trace_has_all_edges():
    sid, _ = run_conversation()
    jl, md = export(sid, outdir="traces")
    assert jl.exists() and md.exists()
    lines = jl.read_text(encoding="utf-8").splitlines()
    joined = "\n".join(lines)
    for must in ("route_decision", "agent_call", "tool_call", "tool_result",
                 "guardrail_check", "state_write", "email_sent"):
        assert must in joined, f"missing {must} in A9 trace"
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == sorted(seqs)  # strict ordering
    assert any("inbound" in line for line in lines)
    assert any("outbound" in line for line in lines)
