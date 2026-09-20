"""Phase-5 Scheduler tests (A5 + FR-5.x). $0 fake calendar. Real Google in dev."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from src.app.agents.scheduler import (BookingStore, SchedState, generate_slots,
                                      parse_choice, render_slots, run_scheduler)
from src.app.api import main as api
from src.app.mcp_servers.calendar_server import FakeCalendar, deterministic_event_id

OWNER, VIS = "Asia/Kolkata", "Asia/Dubai"
NOW = datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo(OWNER))  # a Monday


def test_a5_slots_proposed_fr53_54():
    cal, books = FakeCalendar(), BookingStore()
    reply, st, info = run_scheduler("Book a call, my email is founder@startup.io",
                                    SchedState(), session_id="s1", cal=cal, bookings=books,
                                    owner_tz=OWNER, visitor_tz=VIS, now=NOW)
    assert info.get("proposed") is True and len(st.slots) == 3
    assert VIS in reply  # slots rendered in visitor TZ
    assert all(s[0].weekday() < 5 for s in st.slots)  # business hours, weekdays


def test_a5_booking_recheck_meet_invite_fr55_57():
    cal, books = FakeCalendar(), BookingStore()
    r1, st1, _ = run_scheduler("book me a call, reach me at founder@startup.io",
                               SchedState(), session_id="s1", cal=cal, bookings=books,
                               owner_tz=OWNER, visitor_tz=VIS, now=NOW)
    assert "1, 2 or 3" in r1
    r2, st2, info = run_scheduler("yes, option 1", st1, session_id="s1", cal=cal,
                                  bookings=books, owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                                  known_email="founder@startup.io")
    assert info.get("booked") is True
    assert info["meet_url"].startswith("https://meet.google.com/")
    assert "founder@startup.io" in r2  # invite sent
    assert st2.event_id == deterministic_event_id("s1", st1.slots[0][0].isoformat())


def test_a5_no_double_book_race_fr55():
    """Two concurrent bookings for one slot -> exactly one confirmed."""
    cal, books = FakeCalendar(), BookingStore()
    _, st, _ = run_scheduler("book, email a@x.io", SchedState(), session_id="sA",
                             cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS, now=NOW)
    slot = st.slots[0]

    async def book(sid: str) -> dict:
        return run_scheduler("yes 1", SchedState(stage="proposed", slots=[slot]),
                             session_id=sid, cal=cal, bookings=books,
                             owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                             known_email=f"{sid}@x.io")[2]

    async def run() -> list[dict]:
        return list(await asyncio.gather(book("sA"), book("sB")))
    infos = asyncio.run(run())
    assert sum(1 for i in infos if i.get("booked")) == 1
    assert sum(1 for i in infos if i.get("retry")) == 1


def test_a5_reschedule_cancel_fr58():
    cal, books = FakeCalendar(), BookingStore()
    _, st1, _ = run_scheduler("book, email a@x.io", SchedState(), session_id="s1",
                              cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS, now=NOW)
    _, st2, info = run_scheduler("yes 1", st1, session_id="s1", cal=cal, bookings=books,
                                 owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                                 known_email="a@x.io")
    assert info.get("booked")
    evid = st2.event_id
    r, _, info2 = run_scheduler("cancel my call", st2, session_id="s1", cal=cal,
                                bookings=books, owner_tz=OWNER, visitor_tz=VIS, now=NOW)
    assert info2.get("cancelled") is True
    assert cal.events[evid].status == "cancelled"
    s, _ = cal.reschedule_event("missing", NOW, NOW)
    assert s == "error"  # reschedule of unknown id fails honestly, no dup


def test_a5_api_end_to_end_booking():
    client = TestClient(api.app)
    b1 = client.post("/v1/chat", json={"message": "Book a call next week",
                                       "timezone": VIS}).json()
    assert "work email" in b1["reply"]
    b2 = client.post("/v1/chat", json={"message": "Book it, me@startup.io",
                                       "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": VIS}).json()
    assert "open times" in b2["reply"]
    b3 = client.post("/v1/chat", json={"message": "yes, 1",
                                       "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": VIS}).json()
    assert "meet.google.com" in b3["reply"].lower()
    tools = [e for e in api.events.for_session(b1["session_id"]) if e.type == "tool_call"]
    assert any(e.payload.get("tool") == "create_event" for e in tools)
    assert any(e.payload.get("recheck") for e in tools)  # re-check before create


def test_slot_render_tz_and_business_hours():
    slots = generate_slots(now=NOW, owner_tz=OWNER, busy=[])
    assert 1 <= len(slots) <= 3
    text = render_slots(slots, "America/New_York")
    assert "America/New_York" in text


def test_bare_email_mid_booking_proposes_slots():
    """Regression: Book -> ok -> bare email must propose slots, not clarify."""
    from fastapi.testclient import TestClient
    from src.app.api import main as api
    client = TestClient(api.app)
    b1 = client.post("/v1/chat", json={"message": "Book a call next week",
                                       "timezone": VIS}).json()
    assert "work email" in b1["reply"]
    b_ok = client.post("/v1/chat", json={"message": "ok", "session_id": b1["session_id"],
                                         "visitor_id": b1["visitor_id"],
                                         "timezone": VIS}).json()
    assert "work email" in b_ok["reply"]  # still scheduling, re-asks once
    b2 = client.post("/v1/chat", json={"message": "lead@example.com",
                                       "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": VIS}).json()
    assert "open times" in b2["reply"]


def test_parse_choice_variants():
    assert parse_choice("yes, 1", 3) == 0
    assert parse_choice("2", 3) == 1
    assert parse_choice("yes, 2", 3) == 1
    assert parse_choice("option 2", 3) == 1
    assert parse_choice("the second one", 3) == 1
    assert parse_choice("3rd", 3) == 2
    assert parse_choice("5", 3) == -1  # out of range
    assert parse_choice("maybe later", 3) is None


def test_second_slot_books_second_slot():
    """Regression: choosing 2 books slots[1], not slots[0]."""
    cal, books = FakeCalendar(), BookingStore()
    _, st, _ = run_scheduler("book, a@x.io", SchedState(), session_id="s2",
                             cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                             now=NOW)
    _, _, info = run_scheduler("2", st, session_id="s2", cal=cal, bookings=books,
                               owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                               known_email="a@x.io")
    assert info.get("booked") is True
    assert info["event_id"] == deterministic_event_id("s2", st.slots[1][0].isoformat())


def test_parse_preferred_variants():
    from src.app.agents.scheduler import parse_preferred
    # NOW = Mon 2026-09-21 09:00 Asia/Kolkata; visitor Asia/Dubai (+4)
    s, _, missing = parse_preferred("tomorrow at 3pm", visitor_tz=VIS, now=NOW)
    assert missing is None and (s.day, s.hour) == (22, 15 - 4 + 4)  # 3pm Dubai
    assert s.hour == 15
    s, _, missing = parse_preferred("Monday 10:30", visitor_tz=VIS, now=NOW)
    assert missing is None and s.weekday() == 0 and (s.hour, s.minute) == (10, 30)
    s, _, missing = parse_preferred("25 Sep 2pm", visitor_tz="Asia/Kolkata", now=NOW)
    assert missing is None and (s.day, s.month, s.hour) == (25, 9, 14)
    _, _, missing = parse_preferred("Friday", visitor_tz=VIS, now=NOW)
    assert missing == "time"
    assert parse_preferred("maybe next week sometime", visitor_tz=VIS, now=NOW) is None


def test_typed_time_books_directly():
    cal, books = FakeCalendar(), BookingStore()
    r, st, info = run_scheduler("tomorrow at 3pm, a@x.io", SchedState(), session_id="sT",
                                cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                                now=NOW)
    assert info.get("booked") is True
    assert "Meet link" in r


def test_outside_hours_redirects():
    cal, books = FakeCalendar(), BookingStore()
    r, _, info = run_scheduler("tomorrow at 9pm, a@x.io", SchedState(), session_id="sH",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                               now=NOW)
    assert not info.get("booked") and "10:00" in r


def test_today_9pm_midflow_redirects_not_clarify():
    """Regression: 'today 9 pm' after slots offered must explain hours, not clarify."""
    from fastapi.testclient import TestClient
    from src.app.api import main as api
    client = TestClient(api.app)
    b1 = client.post("/v1/chat", json={"message": "Book a call", "timezone": VIS}).json()
    b2 = client.post("/v1/chat", json={"message": "a@x.io", "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": VIS}).json()
    assert "open times" in b2["reply"]
    b3 = client.post("/v1/chat", json={"message": "today 9 pm", "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": VIS}).json()
    assert "bit more" not in b3["reply"]  # explained, not clarified
    assert "10:00" in b3["reply"] or "weekdays" in b3["reply"]


def test_9pm_weekday_redirects_to_hours():
    cal, books = FakeCalendar(), BookingStore()
    r, _, info = run_scheduler("Monday 9pm, a@x.io", SchedState(), session_id="sW",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                               now=NOW)  # NOW is a Monday
    assert not info.get("booked") and "10:00" in r


def test_dotted_time_books_typed_date():
    """Regression: '25 september at 3.pm' books Sep 25 3pm, not slot #3."""
    cal, books = FakeCalendar(), BookingStore()
    _, st, _ = run_scheduler("book a call, a@x.io", SchedState(), session_id="sD",
                             cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                             now=NOW)
    r, _, info = run_scheduler("book a call at 25 september at 3.pm", st,
                               session_id="sD", cal=cal, bookings=books,
                               owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                               known_email="a@x.io")
    assert info.get("booked") is True
    assert "25 Sep" in r and "03:00 PM" in r


def test_dotted_numeric_date():
    """Regression: 30.09.2026 parses as 30 Sep 2026 (DD.MM.YYYY)."""
    from src.app.agents.scheduler import parse_preferred
    s, _, missing = parse_preferred("book a call on 30.09.2026 at 12:00 pm",
                                    visitor_tz="Asia/Kolkata", now=NOW)
    assert missing is None and (s.day, s.month, s.year, s.hour) == (30, 9, 2026, 12)


def test_month_first_books_monday_sep28():
    """Regression: 'september 28 3 pm' (month-first) books Mon 28 Sep, not 'weekdays only'."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.app.agents.scheduler import parse_preferred
    now = datetime(2026, 9, 20, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # a Sunday
    s, _, missing = parse_preferred("september 28 3 pm",
                                    visitor_tz="Asia/Kolkata", now=now)
    assert missing is None and (s.day, s.month, s.hour) == (28, 9, 15)
    assert s.weekday() == 0  # Monday
    cal, books = FakeCalendar(), BookingStore()
    r, _, info = run_scheduler("september 28 3 pm", SchedState(), session_id="sM",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                               now=now, known_email="a@x.io")
    assert info.get("booked") is True and "28 Sep" in r


def test_month_first_without_time_asks_time():
    """'book september 28' -> asks for time, naming the Monday."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 9, 20, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    cal, books = FakeCalendar(), BookingStore()
    r, _, info = run_scheduler("book september 28", SchedState(), session_id="sM2",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                               now=now, known_email="a@x.io")
    assert info.get("booked") is not True and "28 Sep" in r


def _book_sep28_noon(session_id="sR"):
    """Helper: confirmed 12:00 booking on Mon 28 Sep (single TZ for clarity)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    cal, books = FakeCalendar(), BookingStore()
    r, st, info = run_scheduler("september 28 12 pm", SchedState(), session_id=session_id,
                                cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                                now=now, known_email="a@x.io")
    assert info.get("booked") is True
    return cal, books, st, info, now


def test_reschedule_same_message_moves_same_event():
    """'reschedule ... to Sep 28 3pm' patches the event; noon slot freed."""
    cal, books, _, info, now = _book_sep28_noon()
    first_event = info["event_id"]
    r, _, info2 = run_scheduler("reschedule the meeting to Sep 28 3 pm", SchedState(),
                                session_id="sR", cal=cal, bookings=books,
                                owner_tz=OWNER, visitor_tz=OWNER, now=now,
                                known_email="a@x.io")
    assert r.startswith("Moved") and info2.get("rescheduled") is True
    assert info2["event_id"] == first_event  # same event, no duplicate
    assert len(cal.events) == 1
    rec = books.bookings[books.confirmed_for_session("sR").id]
    assert (rec.start.hour, rec.start.day) == (15, 28)


def test_reschedule_ask_then_time_moves():
    """'reschedule my call' asks; the named time then moves the same event."""
    cal, books, _, info, now = _book_sep28_noon(session_id="sR2")
    first_event = info["event_id"]
    r, st, _ = run_scheduler("reschedule my call", SchedState(), session_id="sR2",
                             cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                             now=now, known_email="a@x.io")
    assert "which new time" in r and st.reschedule_of is not None
    r2, _, info2 = run_scheduler("September 28 3 pm", st, session_id="sR2",
                                 cal=cal, bookings=books, owner_tz=OWNER,
                                 visitor_tz=OWNER, now=now, known_email="a@x.io")
    assert r2.startswith("Moved") and info2["event_id"] == first_event
    assert len(cal.events) == 1


def test_cancel_kills_latest_call():
    """Two bookings, plain 'cancel my call' cancels the current (latest) one."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    cal, books = FakeCalendar(), BookingStore()
    run_scheduler("september 28 12 pm", SchedState(), session_id="sC",
                  cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                  now=now, known_email="a@x.io")
    _, _, info2 = run_scheduler("september 28 3 pm", SchedState(), session_id="sC",
                                cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                                now=now, known_email="a@x.io")
    r, _, info = run_scheduler("cancel my call", SchedState(), session_id="sC",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                               now=now, known_email="a@x.io")
    assert info.get("cancelled") is True
    by_hour = {b.start.hour: b.status for b in books.bookings.values()}
    assert by_hour[15] == "cancelled" and by_hour[12] == "confirmed"


def test_cancel_totally_cancels_all():
    cal, books, _, _, now = _book_sep28_noon(session_id="sC2")
    run_scheduler("september 28 3 pm", SchedState(), session_id="sC2",
                  cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                  now=now, known_email="a@x.io")
    r, _, info = run_scheduler("cancelling totally", SchedState(), session_id="sC2",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=OWNER,
                               now=now, known_email="a@x.io")
    assert info.get("cancelled") is True and "2" in r
    assert all(b.status == "cancelled" for b in books.bookings.values())


def test_dotted_time_not_a_date():
    """9.00 pm is a time, not 9/00 date — must not kill parsing."""
    from src.app.agents.scheduler import parse_preferred
    s, _, missing = parse_preferred("today 9.00 pm", visitor_tz="Asia/Kolkata", now=NOW)
    assert missing is None and s.hour == 21


def test_datetime_before_email_is_remembered():
    """Regression: time typed before email is booked once the email arrives."""
    cal, books = FakeCalendar(), BookingStore()
    r1, st1, _ = run_scheduler("book a call on 23 Sep 2pm", SchedState(), session_id="sR",
                               cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                               now=NOW)
    assert "email" in r1 and st1.preferred_start is not None
    r2, _, info = run_scheduler("a@x.io", st1, session_id="sR", cal=cal, bookings=books,
                                owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                                known_email="a@x.io")
    assert info.get("booked") is True and "Meet link" in r2


def test_booked_slot_never_reoffered():
    """Regression: after booking, the same slot must not be proposed again."""
    cal, books = FakeCalendar(), BookingStore()
    _, st1, _ = run_scheduler("book, a@x.io", SchedState(), session_id="s9",
                              cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                              now=NOW)
    booked_start = st1.slots[1][0]
    _, _, info = run_scheduler("2", st1, session_id="s9", cal=cal, bookings=books,
                               owner_tz=OWNER, visitor_tz=VIS, now=NOW,
                               known_email="a@x.io")
    assert info.get("booked") is True
    r, st2, _ = run_scheduler("book another call, a@x.io", SchedState(), session_id="s9",
                              cal=cal, bookings=books, owner_tz=OWNER, visitor_tz=VIS,
                              now=NOW, known_email="a@x.io")
    assert info and all(s[0] != booked_start for s in st2.slots)
