"""Scheduler agent: slots -> re-check -> reserve -> book (FR-5.1–5.8).

One question at a time; slots in visitor TZ, booked in owner TZ (FR-5.4);
2–3 concrete slots, never blind picks (FR-5.3); re-check before create (FR-5.5);
Meet link + invite via MCP (FR-5.6/5.7); reschedule/cancel patch the stored
event (FR-5.8). Calendar + bookings backends are fake ($0) in tests, real in dev.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..mcp_servers.calendar_server import deterministic_event_id

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CANCEL_RE = re.compile(r"\bcancel\b|\bcancelling\b", re.I)
CANCEL_ALL_RE = re.compile(r"\bcanc?ell?ing\b.*\b(totally|everything|all\b.*\bmeetings?|all)\b|\bcancel\b.*\b(all|everything|both)\b", re.I)
RESCHED_RE = re.compile(r"\breschedul", re.I)
YES_RE = re.compile(r"\b(yes|confirm|book it|first|1st|option\s?1)\b", re.I)
ORDINAL = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}
WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
MONTH_RE = (r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|"
            r"sep\w*|oct\w*|nov\w*|dec\w*")
TIME_RE = re.compile(r"\b(\d{1,2})(?:(?::|[.])(\d{2}))?\.?\s*(am|pm)?\b", re.I)
DAYPART = {"morning": 10, "afternoon": 14, "evening": 16}


def parse_preferred(message: str, *, visitor_tz: str, now: datetime) -> tuple | None:
    """Parse a typed date/time into (start, end, missing) datetimes in visitor TZ.

    Returns (start_aware_or_None, end_or_None, missing_hint). missing is None
    when fully parsed, else 'date' or 'time' telling what to ask next.
    """
    low = (message or "").lower()
    vz = ZoneInfo(visitor_tz)
    today = now.astimezone(vz).date()
    day = None
    if "day after tomorrow" in low:
        day = today + timedelta(days=2)
    elif "tomorrow" in low:
        day = today + timedelta(days=1)
    elif "today" in low:
        day = today
    else:
        for name, idx in WEEKDAYS.items():
            if re.search(rf"\b{name}\b", low):
                delta = (idx - today.weekday()) % 7
                if delta == 0:
                    delta = 7
                if re.search(rf"\bnext\s+{name}\b", low):
                    delta += 7
                day = today + timedelta(days=delta)
                break
        if day is None:
            m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + MONTH_RE + r")\b", low)
            if m:
                mon = next(v for k, v in MONTHS.items() if k.startswith(m.group(2)[:3]))
                try:
                    day = today.replace(year=today.year + (1 if (mon, int(m.group(1))) < (today.month, today.day) else 0),
                                        month=mon, day=int(m.group(1)))
                except ValueError:
                    return None
            else:
                # Month-first order ("september 28", "sep 28"): same meaning.
                m = re.search(r"\b(" + MONTH_RE + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
                if m:
                    mon = next(v for k, v in MONTHS.items() if k.startswith(m.group(1)[:3]))
                    try:
                        day = today.replace(year=today.year + (1 if (mon, int(m.group(2))) < (today.month, today.day) else 0),
                                            month=mon, day=int(m.group(2)))
                    except ValueError:
                        return None
                else:
                    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", low)
                    if m:
                        d, mo = int(m.group(1)), int(m.group(2))
                        if 1 <= mo <= 12 and 1 <= d <= 31:
                            yr = int(m.group(3) or today.year)
                            yr += 2000 if yr < 100 else 0
                            try:
                                day = today.replace(year=yr, month=mo, day=d)
                            except ValueError:
                                pass  # invalid date: ignore, time-only may still parse
                        # else: likely a dotted time ("9.00 pm") — leave day unset
    hour, minute, has_time = None, 0, False
    for m in TIME_RE.finditer(low):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if h > 23 or mi > 59:
            continue
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if ap or ":" in m.group(0) or re.search(
                r"(at|@|:|\bam\b|\bpm\b)",
                low[max(0, m.start() - 4):m.start()] + ap):
            hour, minute, has_time = h, mi, True
            break
    if not has_time:
        for part, h in DAYPART.items():
            if re.search(rf"\b{part}\b", low):
                hour, has_time = h, True
                break
    if day is None and not has_time:
        return None
    if day is None:
        day = today
        missing = "date"
    elif not has_time:
        missing = "time"
    else:
        missing = None
    start = datetime(day.year, day.month, day.day, hour or 10, minute, tzinfo=vz)
    return start, start + timedelta(minutes=30), missing


def parse_choice(message: str, n_slots: int) -> int | None:
    """Which proposed slot the visitor picked (0-based); None if no choice found."""
    low = f" {(message or '').lower()} "
    m = re.search(r"option\s?([0-9])|slot\s?([0-9])|[^a-z0-9]([0-9])[^a-z0-9]", low)
    if m:
        idx = int(next(g for g in m.groups() if g)) - 1
        return idx if 0 <= idx < n_slots else -1  # -1 = out of range
    for word, idx in ORDINAL.items():
        if re.search(rf"\b{word}\b", low):
            return idx if idx < n_slots else -1
    if YES_RE.search(low):
        return 0
    return None


@dataclass
class Booking:
    id: str
    session_id: str
    start: datetime
    end: datetime
    status: str = "pending"  # pending|confirmed|cancelled|failed
    event_id: str | None = None
    meet_url: str | None = None
    email: str | None = None


class BookingStore:
    """Exclusion semantics mirror the DB gist constraint (FR-5.5)."""

    def __init__(self) -> None:
        self.bookings: dict[str, Booking] = {}
        self._n = 0

    def overlaps(self, start: datetime, end: datetime, skip: str | None = None) -> bool:
        return any(b.id != skip and b.status in ("pending", "confirmed")
                   and b.start < end and b.end > start for b in self.bookings.values())

    def reserve(self, session_id: str, start: datetime, end: datetime,
                email: str | None) -> Booking | None:
        if self.overlaps(start, end):
            return None
        self._n += 1
        b = Booking(f"b{self._n}", session_id, start, end, "pending", None, None, email)
        self.bookings[b.id] = b
        return b

    def confirmed_for_session(self, session_id: str) -> Booking | None:
        found = [b for b in self.bookings.values()
                 if b.session_id == session_id and b.status == "confirmed"]
        return found[-1] if found else None  # latest: the visitor's current call

    def confirmed_all_for_session(self, session_id: str) -> list:
        return [b for b in self.bookings.values()
                if b.session_id == session_id and b.status == "confirmed"]


def _business_day_slots(day: datetime, owner_tz: str, slot_min: int) -> list[tuple[datetime, datetime]]:
    z = ZoneInfo(owner_tz)
    out = []
    cur = day.replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=z)
    end = day.replace(hour=17, minute=0, second=0, microsecond=0, tzinfo=z)
    while cur + timedelta(minutes=slot_min) <= end:
        out.append((cur, cur + timedelta(minutes=slot_min)))
        cur += timedelta(minutes=slot_min)
    return out


def generate_slots(*, now: datetime, owner_tz: str, slot_min: int = 30,
                   lookahead_days: int = 14, min_notice_h: int = 4,
                   busy: list[tuple[datetime, datetime]] | None = None) -> list[tuple[datetime, datetime]]:
    """Candidates inside business hours minus busy, spread across days. # FR-5.3"""
    busy = busy or []
    start_from = now + timedelta(hours=min_notice_h)
    per_day: dict[str, list] = {}
    for d in range(lookahead_days):
        day = (now + timedelta(days=d)).date()
        if datetime(day.year, day.month, day.day).weekday() >= 5:  # Mon–Fri
            continue
        for s, e in _business_day_slots(datetime(day.year, day.month, day.day), owner_tz, slot_min):
            if e <= start_from:
                continue
            if any(bs < e and be > s for bs, be in busy):
                continue
            per_day.setdefault(str(day), []).append((s, e))
    out = []
    for day in sorted(per_day)[:3]:  # spread across days
        out += per_day[day][:1]
        if len(out) >= 3:
            break
    return out[:3]


def render_slots(slots: list[tuple[datetime, datetime]], visitor_tz: str) -> str:
    z = ZoneInfo(visitor_tz)
    lines = []
    for i, (s, e) in enumerate(slots, 1):
        ls, le = s.astimezone(z), e.astimezone(z)
        lines.append(f"{i}. {ls:%A %d %b, %I:%M %p}–{le:%I:%M %p} ({visitor_tz})")
    return "\n".join(lines)


@dataclass
class SchedState:
    stage: str = "collect"  # collect|proposed|booked
    slots: list = field(default_factory=list)
    booking_id: str | None = None
    event_id: str | None = None
    preferred_date: str | None = None  # ISO date when visitor gave day but no time
    preferred_start: str | None = None  # ISO datetime typed before email arrived
    reschedule_of: str | None = None  # booking id being moved (FR-5.8)


def _has_datetime_cue(message: str) -> bool:
    low = (message or "").lower()
    if any(k in low for k in ("today", "tomorrow", "morning", "afternoon", "evening")):
        return True
    if any(re.search(rf"\b{d}\b", low) for d in WEEKDAYS):
        return True
    if re.search(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:" + MONTH_RE + r")\b"
                  r"|\b(?:" + MONTH_RE + r")\s+\d{1,2}(?:st|nd|rd|th)?\b"
                  r"|\b\d{1,2}[/.-]\d{1,2}\b", low):
        return True
    return bool(re.search(r"\b\d{1,2}(?:(?::|[.])\d{2})?\.?\s*(am|pm)\b", low))


def sched_from_dict(d: dict | None) -> SchedState:
    """Rebuild state across turns (ISO strings -> datetimes)."""
    d = d or {}
    slots = []
    for s, e in d.get("slots", []):
        slots.append((datetime.fromisoformat(s), datetime.fromisoformat(e)))
    return SchedState(stage=d.get("stage", "collect"), slots=slots,
                      booking_id=d.get("booking_id"), event_id=d.get("event_id"),
                      preferred_date=d.get("preferred_date"),
                      preferred_start=d.get("preferred_start"),
                      reschedule_of=d.get("reschedule_of"))


def _check_slot(start_owner: datetime, now_owner: datetime) -> str | None:
    """Business rules (FR-5.3/5.4). Returns an explanation or None when OK."""
    if start_owner.weekday() >= 5:
        return "weekend"
    if not (10 <= start_owner.hour + start_owner.minute / 60 <= 16.5):
        return "hours"
    if start_owner < now_owner + timedelta(hours=4):
        return "notice"
    if start_owner > now_owner + timedelta(days=14):
        return "far"
    return None


def _confirm_booking(*, session_id: str, start: datetime, end: datetime, email: str,
                     st: SchedState, cal, bookings: BookingStore, owner_tz: str,
                     visitor_tz: str, log: list) -> tuple[str, SchedState, dict]:
    """Shared re-check -> reserve -> create path (FR-5.5)."""
    evid = deterministic_event_id(session_id, start.isoformat())
    log.append({"tool": "get_availability", "recheck": True})  # FR-5.5 re-check
    s1, avail = cal.get_availability(start, end)
    if s1 != "ok":
        return ("I couldn't reach the calendar just now — I've noted your details and the team will contact you.",
                st, {"manual_followup": True,
                     "error_code": avail.get("error_code", "UPSTREAM_5XX")})
    if avail.get("busy"):
        return ("That time just got taken — here are fresh alternatives.",
                SchedState(stage="collect"), {"retry": True})
    res = bookings.reserve(session_id, start, end, email)
    if not res:  # exclusion lost the race
        return ("That time just got taken — here are fresh alternatives.",
                SchedState(stage="collect"), {"retry": True})
    log.append({"tool": "create_event", "args": {"slot": start.isoformat()}})
    s2, created = cal.create_event(event_id=evid, summary="CloseFuture discovery call",
                                   start=start, end=end, timezone=owner_tz, attendee=email)
    if s2 != "ok":
        res.status = "failed"
        return ("I couldn't reach the calendar just now — I've noted your details and the team will contact you.",
                st, {"manual_followup": True,
                     "error_code": created.get("error_code", "UPSTREAM_5XX")})
    res.status, res.event_id, res.meet_url = "confirmed", created["event_id"], created["meet_url"]
    local = start.astimezone(ZoneInfo(visitor_tz))
    return (f"Booked for {local:%A %d %b, %I:%M %p} ({visitor_tz}). "
            f"Meet link: {created['meet_url']} — invite sent to {email}.",
            SchedState(stage="booked", booking_id=res.id, event_id=res.event_id),
            {"booked": True, "event_id": res.event_id, "meet_url": res.meet_url})


def _move_booking(*, session_id: str, booking, start: datetime, end: datetime,
                  st: SchedState, cal, bookings: BookingStore,
                  owner_tz: str, visitor_tz: str, log: list) -> tuple[str, SchedState, dict]:
    """Reschedule the SAME event (FR-5.8): re-check, patch, update the record."""
    log.append({"tool": "get_availability", "recheck": True})
    s1, avail = cal.get_availability(start, end)
    if s1 != "ok":
        return ("I couldn't reach the calendar just now — I've noted your details and the team will contact you.",
                st, {"manual_followup": True,
                     "error_code": avail.get("error_code", "UPSTREAM_5XX")})
    if avail.get("busy"):
        return ("That new time just got taken — here are fresh alternatives.",
                SchedState(stage="collect"), {"retry": True})
    if bookings.overlaps(start, end, skip=booking.id):
        return ("That new time just got taken — here are fresh alternatives.",
                SchedState(stage="collect"), {"retry": True})
    log.append({"tool": "reschedule_event", "args": {"event_id": booking.event_id}})
    s2, moved = cal.reschedule_event(booking.event_id or "", start, end)
    if s2 != "ok":
        return ("I couldn't reach the calendar just now — I've noted your details and the team will contact you.",
                st, {"manual_followup": True,
                     "error_code": moved.get("error_code", "UPSTREAM_5XX")})
    booking.start, booking.end = start, end
    local = start.astimezone(ZoneInfo(visitor_tz))
    return (f"Moved your call to {local:%A %d %b, %I:%M %p} ({visitor_tz}) — "
            f"same invite link: {moved.get('meet_url', booking.meet_url or '')}.",
            SchedState(stage="booked", booking_id=booking.id, event_id=booking.event_id),
            {"rescheduled": True, "event_id": booking.event_id,
             "meet_url": moved.get("meet_url", booking.meet_url or "")})


def run_scheduler(message: str, st: SchedState, *, session_id: str, cal,
                  bookings: BookingStore, owner_tz: str, visitor_tz: str,
                  known_email: str | None = None, now: datetime | None = None,
                  log: list | None = None) -> tuple[str, SchedState, dict]:
    """One scheduler turn. Returns (reply, state, info). Tool calls appended to log."""
    log = log if log is not None else []
    now = now or datetime.now(ZoneInfo(owner_tz))
    m = EMAIL_RE.search(message or "")
    email = m.group(0) if m else known_email

    existing = bookings.confirmed_for_session(session_id)
    if CANCEL_RE.search(message or "") and existing:
        if CANCEL_ALL_RE.search(message or ""):
            doomed = bookings.confirmed_all_for_session(session_id)
            for b in doomed:
                log.append({"tool": "cancel_event", "args": {"event_id": b.event_id}})
                s, _ = cal.cancel_event(b.event_id or "")
                if s == "ok":
                    b.status = "cancelled"
            if all(b.status == "cancelled" for b in doomed):
                return (f"Done — cancelled all {len(doomed)} of your calls.",
                        SchedState(), {"cancelled": True})
            return ("I couldn't reach the calendar just now — the team will confirm cancellation by email.",
                    st, {"manual_followup": True})
        log.append({"tool": "cancel_event", "args": {"event_id": existing.event_id}})
        status, _ = cal.cancel_event(existing.event_id or "")
        if status == "ok":
            existing.status = "cancelled"
            return ("Your call is cancelled. Want a different time instead?",
                    SchedState(), {"cancelled": True})
        return ("I couldn't reach the calendar just now — the team will confirm cancellation by email.",
                st, {"manual_followup": True})
    if RESCHED_RE.search(message or "") and existing:
        if _has_datetime_cue(message):  # new time in the same message: move at once
            parsed = parse_preferred(message, visitor_tz=visitor_tz, now=now)
            if parsed is not None and parsed[2] is None:
                pstart = parsed[0].astimezone(ZoneInfo(owner_tz))
                now_owner = now.astimezone(ZoneInfo(owner_tz)) if now.tzinfo else now
                if _check_slot(pstart, now_owner) is None:
                    return _move_booking(
                        session_id=session_id, booking=existing, start=pstart,
                        end=pstart + timedelta(minutes=30), st=st, cal=cal,
                        bookings=bookings, owner_tz=owner_tz,
                        visitor_tz=visitor_tz, log=log)
        keep = SchedState(stage="collect", reschedule_of=existing.id)
        return ("Sure — which new time works? Name a weekday time, Mon–Fri 10:00–17:00.",
                keep, {"reschedule": True})

    if not email:
        # Stash a typed time even before the email arrives so it isn't forgotten.
        if _has_datetime_cue(message):
            parsed = parse_preferred(message, visitor_tz=visitor_tz, now=now)
            if parsed is not None and parsed[2] is None:
                keep = SchedState(stage=st.stage, slots=st.slots,
                                  preferred_start=parsed[0].isoformat(),
                                  reschedule_of=st.reschedule_of)
                return ("Got it — and what's the best work email address?",
                        keep, {"need": "email"})
        return ("To book a call I just need your work email — what's the best address?",
                st, {"need": "email"})
    if email and st.preferred_start and not _has_datetime_cue(message):
        # Email arrived after a stashed time: validate and book it directly.
        try:
            pstart = datetime.fromisoformat(st.preferred_start)
            start_owner = pstart.astimezone(ZoneInfo(owner_tz))
            now_owner = now.astimezone(ZoneInfo(owner_tz)) if now.tzinfo else now
            if _check_slot(start_owner, now_owner) is None:
                return _confirm_booking(
                    session_id=session_id, start=start_owner,
                    end=start_owner + timedelta(minutes=30), email=email, st=st,
                    cal=cal, bookings=bookings, owner_tz=owner_tz,
                    visitor_tz=visitor_tz, log=log)
        except ValueError:
            pass
        st = SchedState(stage=st.stage, slots=st.slots,
                        reschedule_of=st.reschedule_of)  # stale/invalid: fall through
    if st.stage == "proposed" and st.slots and not (
            _has_datetime_cue(message) and not re.search(
                r"option|slot\s?\d|first|second|third|[123](st|nd|rd)|\b[123]\b",
                re.sub(r"\d{1,2}(?:(?::|[.])\d{2})?\.?\s*(?:am|pm)\b", " ",
                       (message or "").lower()))):
        # A typed time ("today 9pm", "25 September at 3.pm") is not a slot pick
        # ("2"): time expressions are stripped before looking for a pick.
        choice = parse_choice(message, len(st.slots))
        if choice == -1:
            return (f"We only have {len(st.slots)} open times — reply with "
                    f"{' or '.join(str(i + 1) for i in range(len(st.slots)))} to book.",
                    st, {"retry": True})
        if choice is not None:
            return _confirm_booking(session_id=session_id, start=st.slots[choice][0],
                                    end=st.slots[choice][1], email=email, st=st, cal=cal,
                                    bookings=bookings, owner_tz=owner_tz,
                                    visitor_tz=visitor_tz, log=log)

    # Typed date/time ("tomorrow at 3pm", "Monday 10:30") — book it directly (FR-5.3)
    if _has_datetime_cue(message):
        parsed = parse_preferred(message, visitor_tz=visitor_tz, now=now)
        if parsed is not None:
            pstart, _, missing = parsed
            if missing == "date" and st.preferred_date:
                pstart = datetime.fromisoformat(st.preferred_date).replace(
                    hour=pstart.hour, minute=pstart.minute, tzinfo=ZoneInfo(visitor_tz))
                missing = None
            if missing == "time":
                day_name = pstart.astimezone(ZoneInfo(visitor_tz)).strftime("%A %d %b")
                keep = SchedState(stage=st.stage, slots=st.slots,
                                  preferred_date=pstart.date().isoformat(),
                                  reschedule_of=st.reschedule_of)
                return (f"What time on {day_name} works for you? "
                        f"We meet Mon–Fri 10:00–17:00 {owner_tz}.", keep, {"need": "time"})
            start_owner = pstart.astimezone(ZoneInfo(owner_tz))
            now_owner = now.astimezone(ZoneInfo(owner_tz)) if now.tzinfo else now
            problem = _check_slot(start_owner, now_owner)
            if problem == "weekend":
                return ("We meet on weekdays only — pick a Monday to Friday, "
                        "or reply with 1, 2 or 3 from the open times.", st, {"retry": True})
            if problem == "hours":
                return ("We meet 10:00–17:00 on weekdays — name a time inside "
                        "business hours, or pick from the open times.", st, {"retry": True})
            if problem in ("notice", "far"):
                return ("I need at least 4 hours notice and book max two weeks out — "
                        "pick a nearer time, or reply with 1, 2 or 3.", st, {"retry": True})
            if st.reschedule_of:  # pending move: patch the same event (FR-5.8)
                target = bookings.bookings.get(st.reschedule_of)
                if target is not None and target.status == "confirmed":
                    return _move_booking(
                        session_id=session_id, booking=target, start=start_owner,
                        end=start_owner + timedelta(minutes=30), st=st, cal=cal,
                        bookings=bookings, owner_tz=owner_tz,
                        visitor_tz=visitor_tz, log=log)
                st = SchedState(stage=st.stage, slots=st.slots)  # gone: fresh book
            return _confirm_booking(session_id=session_id, start=start_owner,
                                    end=start_owner + timedelta(minutes=30), email=email,
                                    st=st, cal=cal, bookings=bookings, owner_tz=owner_tz,
                                    visitor_tz=visitor_tz, log=log)

    window_end = now + timedelta(days=14)
    s1, avail = cal.get_availability(now, window_end)
    log.append({"tool": "get_availability", "args": {"window": "14d"}})
    if s1 != "ok":
        return ("I couldn't reach the calendar just now — I've noted your details and the team will contact you.",
                st, {"manual_followup": True,
                     "error_code": avail.get("error_code", "UPSTREAM_5XX")})
    busy = [(datetime.fromisoformat(b[0]), datetime.fromisoformat(b[1]))
            for b in avail.get("busy", [])]
    for b in bookings.bookings.values():  # belt-and-braces: own confirmed holds
        if b.status == "confirmed":       # even if freebusy hasn't caught up
            busy.append((b.start, b.end))
    slots = generate_slots(now=now, owner_tz=owner_tz, busy=busy)
    if not slots:
        return ("No open slots in the next two weeks — leave your email and the team will offer times manually.",
                st, {"manual_followup": True})
    return (f"Here are 3 open times:\n{render_slots(slots, visitor_tz)}\n"
            "Reply with 1, 2 or 3 to book.",
            SchedState(stage="proposed", slots=slots), {"proposed": True})
