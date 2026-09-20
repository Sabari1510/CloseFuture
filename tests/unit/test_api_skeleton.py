"""Rate-limit, budget gate, guardrail-path tests (Phase 0 DoD)."""
from fastapi.testclient import TestClient
from src.app.api.main import app

client = TestClient(app)


def test_chat_skeleton_logs_events():
    r = client.post("/v1/chat", json={"message": "hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "session_id" in body and "visitor_id" in body
    # trace needs admin key; without it -> 403 (never leak internals, FR-7.6)
    t = client.get(f"/v1/sessions/{body['session_id']}/trace")
    assert t.status_code == 403


def test_message_length_cap():
    r = client.post("/v1/chat", json={"message": "x" * 501})
    assert r.json()["status"] == "blocked"


def test_rate_limit_session():
    sid, vid = "sess-rate-test", "vis-rate-test"
    last = None
    for _ in range(12):
        last = client.post("/v1/chat", json={"message": "hi", "session_id": sid,
                                             "visitor_id": vid})
    assert last.json()["status"] == "rate_limited"
