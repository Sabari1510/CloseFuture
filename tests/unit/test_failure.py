"""Phase-7 failure tests (A8 + FR-8.x). Fault injection, real policy, honest replies."""
from fastapi.testclient import TestClient
from src.app.api import main as api

INBOX = "sales@example.com"
client = TestClient(api.app)


def _book_turns(email="me@corp.io", tz="Asia/Dubai"):
    b1 = client.post("/v1/chat", json={"message": "Book a call", "timezone": tz}).json()
    b2 = client.post("/v1/chat", json={"message": f"Book it, {email}",
                                       "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": tz}).json()
    return b1, b2


def _retries(sid, tool=None):
    return [e for e in api.events.for_session(sid)
            if e.type == "retry" and (tool is None or e.payload.get("tool") == tool)]


def test_a8_retryable_recovers_fr82_85():
    """calendar:503 twice -> retried, then booked; every attempt logged."""
    api.sales_inbox = INBOX
    b1, b2 = _book_turns()
    assert "open times" in b2["reply"]
    api.cal.inner.failures.extend(["UPSTREAM_5XX", "UPSTREAM_5XX"])
    try:
        b3 = client.post("/v1/chat", json={"message": "yes 1", "session_id": b1["session_id"],
                                           "visitor_id": b1["visitor_id"],
                                           "timezone": "Asia/Dubai"}).json()
    finally:
        api.cal.inner.failures.clear()
    assert "meet.google.com" in b3["reply"].lower()  # recovered, honest success
    rs = _retries(b1["session_id"], "create_event") + _retries(b1["session_id"], "get_availability")
    assert any(r.payload.get("outcome") == "retrying" for r in rs)
    assert any(r.payload.get("outcome") == "succeeded_after_N" for r in rs)


def test_a8_non_retryable_not_retried_honest_fr82_84():
    """calendar:401 -> no retry, immediate honest fallback with next step."""
    api.sales_inbox = INBOX
    b1, b2 = _book_turns(email="other@corp.io")
    api.cal.inner.failures.append("INVALID_CREDENTIALS")
    try:
        b3 = client.post("/v1/chat", json={"message": "yes 1", "session_id": b1["session_id"],
                                           "visitor_id": b1["visitor_id"],
                                           "timezone": "Asia/Dubai"}).json()
    finally:
        api.cal.inner.failures.clear()
    assert "team will contact you" in b3["reply"]  # what happened + what next
    rs = _retries(b1["session_id"])
    assert rs and all(r.payload.get("attempts") == 1 for r in rs)  # never retried
    fbs = [e for e in api.events.for_session(b1["session_id"]) if e.type == "fallback"]
    assert any(f.payload.get("reason") == "calendar_unavailable" for f in fbs)


def test_a8_fault_env_calendar_outage_then_outbox(monkeypatch):
    """FAULT=email:503:always -> lead queued in outbox, drained later, lead kept."""
    api.sales_inbox = INBOX
    monkeypatch.setenv("FAULT", "email:503:always")
    try:
        b1, b2 = _book_turns(email="faulty@corp.io")
        b3 = client.post("/v1/chat", json={"message": "yes 1", "session_id": b1["session_id"],
                                           "visitor_id": b1["visitor_id"],
                                           "timezone": "Asia/Dubai"}).json()
    finally:
        monkeypatch.delenv("FAULT", raising=False)
    assert "meet.google.com" in b3["reply"].lower()  # visitor unaffected
    assert len(api.leads.outbox) >= 1  # lead queued, not lost (FR-8.1)
    fbs = [e for e in api.events.for_session(b1["session_id"]) if e.type == "fallback"]
    assert any(f.payload.get("reason") == "email_queued_outbox" for f in fbs)
    monkeypatch.delenv("FAULT", raising=False)
    assert api.leads.drain_outbox(api.mailer) >= 1


def test_a8_error_shape_never_raw():
    """Proxy failures keep the exact AgentError shape (FR-8.3)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.app.mcp_servers.calendar_server import FakeCalendar
    from src.app.reliability.mcp_call import RetryingCalendar
    cal = FakeCalendar()
    cal.failures.append("INVALID_CREDENTIALS")
    proxy = RetryingCalendar(cal, events=None, sleeper=lambda s: None)
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    status, payload = proxy.get_availability(now, now)
    assert status == "error"
    assert payload == {"status": "error", "error_code": "INVALID_CREDENTIALS",
                       "message": "INVALID_CREDENTIALS", "retryable": False,
                       "agent": "scheduler"}
