"""MCP transport tests (FR-3.3): real wire-protocol tools over stdio.

Spawns the actual server modules with fake backends (mocks only in tests).
Covers tool discovery, booking semantics through the transport, idempotent
retries, and the mail path.
"""
from datetime import datetime, timedelta, timezone

from src.app.mcp_servers.mcp_client import McpCalendar, McpMailer

IST = timezone(timedelta(hours=5, minutes=30))

_cal = None
_mail = None


def cal():
    global _cal
    if _cal is None:
        _cal = McpCalendar(backend="fake")
    return _cal


def mail():
    global _mail
    if _mail is None:
        _mail = McpMailer(backend="fake")
    return _mail


def test_tools_discoverable_over_mcp():
    assert set(cal()._conn.tools()) == {"get_availability", "create_event",
                                        "reschedule_event", "cancel_event",
                                        "get_event"}
    assert set(mail()._conn.tools()) == {"send_lead_summary", "get_send_status"}


def test_booking_round_trip_over_mcp():
    start = datetime(2026, 9, 25, 15, 0, tzinfo=IST)
    end = datetime(2026, 9, 25, 15, 30, tzinfo=IST)
    s, free = cal().get_availability(start, end)
    assert s == "ok" and free["busy"] == []
    s, created = cal().create_event(event_id="mcp-test-event-001", summary="t",
                                    start=start, end=end,
                                    timezone="Asia/Kolkata", attendee="a@x.io")
    assert s == "ok" and created["duplicate"] is False
    assert created["meet_url"].startswith("https://meet.google.com/")
    s, dup = cal().create_event(event_id="mcp-test-event-001", summary="t",
                                start=start, end=end,
                                timezone="Asia/Kolkata", attendee="a@x.io")
    assert s == "ok" and dup["duplicate"] is True  # idempotent retry
    s, got = cal().get_event("mcp-test-event-001")
    assert s == "ok" and got["status"] == "confirmed"
    s, moved = cal().reschedule_event("mcp-test-event-001", start + timedelta(hours=1),
                                      end + timedelta(hours=1))
    assert s == "ok"
    s, cancelled = cal().cancel_event("mcp-test-event-001")
    assert s == "ok" and cancelled["event_id"] == "mcp-test-event-001"


def test_slot_conflict_reported_over_mcp():
    start = datetime(2026, 9, 26, 10, 0, tzinfo=IST)
    end = datetime(2026, 9, 26, 10, 30, tzinfo=IST)
    cal().create_event(event_id="mcp-conflict-a", summary="t", start=start, end=end,
                       timezone="Asia/Kolkata", attendee="a@x.io")
    s, payload = cal().create_event(event_id="mcp-conflict-b", summary="t", start=start,
                                    end=end, timezone="Asia/Kolkata", attendee="b@x.io")
    assert s == "error" and payload["error_code"] == "SLOT_UNAVAILABLE"
    cal().cancel_event("mcp-conflict-a")


def test_mail_round_trip_over_mcp():
    s, sent = mail().send_lead_summary(to="sales@x.io", subject="lead",
                                       body="hello", idempotency_key="mcp-mail-001")
    assert s == "ok" and sent["duplicate"] is False
    s, dup = mail().send_lead_summary(to="sales@x.io", subject="lead",
                                      body="hello", idempotency_key="mcp-mail-001")
    assert s == "ok" and dup["duplicate"] is True  # never duplicate (FR-6.6)
    s, st = mail().get_send_status("mcp-mail-001")
    assert s == "ok" and st["message_id"] == sent["message_id"]
