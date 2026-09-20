"""Auto-sweep tests: status endpoint + cycle wiring."""
import sys
sys.path.insert(0, "config")
from fastapi.testclient import TestClient
from settings import settings
from src.app.api import main as api

client = TestClient(api.app)


def test_away_grace_sweeps_before_idle():
    """Tab closed 4 min ago, last message 1 min ago -> summary via away grace."""
    import time
    from src.app.agents.lead_store import LeadStore
    from src.app.db.session_store import InMemorySessionStore
    from src.app.jobs.sweep import sweep_idle
    from src.app.mcp_servers.email_server import FakeEmail
    store = InMemorySessionStore()
    vid, _ = store.ensure_visitor(None)
    sid, _ = store.ensure_session(None, vid, "Asia/Kolkata")
    store.append_message(sid, "visitor", "what are your rates?", "t1")
    state, ver = store.load(sid)
    state["away_since"] = time.time() - 240
    store.save_cas(sid, state, ver)
    store.set_last_activity(sid, time.time())
    leads = LeadStore()
    swept = sweep_idle(store, leads, idle_minutes=20, away_grace_minutes=3,
                       mailer=FakeEmail(), sales_inbox="sales@x.io")
    assert swept == [sid] and sid in leads.summaries


def test_sweep_status_gated_and_shaped(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    try:
        assert client.get("/internal/sweep/status").status_code == 403
        r = client.get("/internal/sweep/status", headers={"X-Admin-Key": "test-key"}).json()
        assert {"last_run", "last_result", "interval_min", "auto"} <= set(r)
        s = client.post("/internal/sweep", headers={"X-Admin-Key": "test-key"}).json()
        assert {"expired", "swept", "outbox_drained"} <= set(s)
        r2 = client.get("/internal/sweep/status", headers={"X-Admin-Key": "test-key"}).json()
        assert         r2["last_run"] is not None and r2["last_result"]["expired"] == s["expired"]
    finally:
        monkeypatch.setattr(settings, "admin_api_key", "")


def test_session_list_gated_and_shaped(monkeypatch):
    """Company-side discovery: recent chats listed newest-first with flags."""
    monkeypatch.setattr(settings, "admin_api_key", "test-key")
    try:
        assert client.get("/internal/sessions").status_code == 403
        c = client.post("/v1/chat", json={"message": "hi"}).json()
        r = client.get("/internal/sessions?limit=10",
                       headers={"X-Admin-Key": "test-key"}).json()
        ids = [s["session_id"] for s in r["sessions"]]
        assert c["session_id"] in ids
        row = next(s for s in r["sessions"] if s["session_id"] == c["session_id"])
        assert {"visitor_id", "status", "turns", "booked",
                "email_known", "summary_sent"} <= set(row)
    finally:
        monkeypatch.setattr(settings, "admin_api_key", "")
