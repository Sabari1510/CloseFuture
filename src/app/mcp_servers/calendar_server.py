"""Calendar MCP server: get_availability, create_event, reschedule_event,
cancel_event, get_event (FR-5.1, FR-5.2). Real Google via OAuth refresh token;
FakeCalendar ($0) with identical semantics for L0–L2: exclusion on overlaps,
deterministic event IDs (duplicate insert -> already-booked, not error),
Meet link + attendee invite fields. Invoked only via tool calling (FR-5.1).
"""
import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime


def deterministic_event_id(session_id: str, slot_start_iso: str) -> str:
    """FR-5.5 support: base32hex of hash(session+slot), stable across retries. # FR-5.5"""
    h = hashlib.sha256(f"{session_id}|{slot_start_iso}".encode()).digest()
    return base64.b32hexencode(h)[:26].decode().lower()


@dataclass
class CalEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    timezone: str
    attendee: str
    meet_url: str
    status: str = "confirmed"


class FakeCalendar:
    """In-memory Google stand-in: freebusy over stored events + injected busy."""

    def __init__(self) -> None:
        self.events: dict[str, CalEvent] = {}
        self.extra_busy: list[tuple[datetime, datetime]] = []
        self.failures: list[str] = []  # FAULT-style queue of error_codes for tests

    def _maybe_fail(self) -> str | None:
        return self.failures.pop(0) if self.failures else None

    def get_availability(self, start: datetime, end: datetime) -> tuple[str, dict]:
        if (code := self._maybe_fail()):
            return "error", {"error_code": code}
        busy = [[s.isoformat(), e.isoformat()] for s, e in self.extra_busy
                if s < end and e > start]
        for ev in self.events.values():
            if ev.status == "confirmed" and ev.start < end and ev.end > start:
                busy.append([ev.start.isoformat(), ev.end.isoformat()])
        return "ok", {"busy": busy}

    def create_event(self, *, event_id: str, summary: str, start: datetime, end: datetime,
                     timezone: str, attendee: str) -> tuple[str, dict]:
        if (code := self._maybe_fail()):
            return "error", {"error_code": code}
        if event_id in self.events:
            ev = self.events[event_id]  # idempotent retry -> already booked (FR-5.5)
            return "ok", {"event_id": ev.id, "meet_url": ev.meet_url, "duplicate": True}
        for ev in self.events.values():  # exclusion: no overlaps (FR-5.5)
            if ev.status == "confirmed" and ev.start < end and ev.end > start:
                return "error", {"error_code": "SLOT_UNAVAILABLE"}
        meet = f"https://meet.google.com/{event_id[:3]}-{event_id[3:7]}-{event_id[7:10]}"
        self.events[event_id] = CalEvent(event_id, summary, start, end, timezone,
                                         attendee, meet)
        return "ok", {"event_id": event_id, "meet_url": meet, "duplicate": False}

    def reschedule_event(self, event_id: str, start: datetime, end: datetime) -> tuple[str, dict]:
        if (code := self._maybe_fail()):
            return "error", {"error_code": code}
        ev = self.events.get(event_id)
        if not ev or ev.status != "confirmed":
            return "error", {"error_code": "NOT_FOUND"}
        for other in self.events.values():
            if other.id != event_id and other.status == "confirmed" \
                    and other.start < end and other.end > start:
                return "error", {"error_code": "SLOT_UNAVAILABLE"}
        ev.start, ev.end = start, end
        return "ok", {"event_id": event_id, "meet_url": ev.meet_url}

    def cancel_event(self, event_id: str) -> tuple[str, dict]:
        if (code := self._maybe_fail()):
            return "error", {"error_code": code}
        ev = self.events.get(event_id)
        if not ev or ev.status != "confirmed":
            return "error", {"error_code": "NOT_FOUND"}
        ev.status = "cancelled"
        return "ok", {"event_id": event_id}

    def get_event(self, event_id: str) -> tuple[str, dict]:
        ev = self.events.get(event_id)
        if not ev:
            return "error", {"error_code": "NOT_FOUND"}
        return "ok", {"event_id": ev.id, "status": ev.status, "meet_url": ev.meet_url,
                      "start": ev.start.isoformat(), "attendee": ev.attendee}


class RealCalendar:
    """Google Calendar API via refresh token (dev/demo). Same method shapes."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 calendar_id: str = "primary") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.calendar_id = calendar_id

    def _service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(None, refresh_token=self.refresh_token,
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=self.client_id, client_secret=self.client_secret,
                            scopes=["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)

    def get_availability(self, start: datetime, end: datetime) -> tuple[str, dict]:
        svc = self._service()
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "items": [{"id": self.calendar_id}]}
        res = svc.freebusy().query(body=body).execute()
        busy = res["calendars"][self.calendar_id].get("busy", [])
        return "ok", {"busy": [[b["start"], b["end"]] for b in busy]}

    def create_event(self, *, event_id: str, summary: str, start: datetime, end: datetime,
                     timezone: str, attendee: str) -> tuple[str, dict]:
        import googleapiclient.errors
        svc = self._service()
        body = {"id": event_id, "summary": summary,
                "start": {"dateTime": start.isoformat(), "timeZone": timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": timezone},
                "attendees": [{"email": attendee}],
                "conferenceData": {"createRequest": {"requestId": event_id}},
                "sendUpdates": "all"}  # Meet link + visitor invite (FR-5.6, FR-5.7)
        try:
            res = svc.events().insert(calendarId=self.calendar_id, body=body,
                                      conferenceDataVersion=1, sendUpdates="all").execute()
        except googleapiclient.errors.HttpError as e:
            if getattr(e, "status_code", None) == 409:
                cur = svc.events().get(calendarId=self.calendar_id,
                                       eventId=event_id).execute()
                return "ok", {"event_id": event_id,
                              "meet_url": cur.get("hangoutLink", ""), "duplicate": True}
            raise
        return "ok", {"event_id": event_id, "meet_url": res.get("hangoutLink", ""),
                      "duplicate": False}

    def reschedule_event(self, event_id: str, start: datetime, end: datetime) -> tuple[str, dict]:
        svc = self._service()
        cur = svc.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        cur["start"]["dateTime"], cur["end"]["dateTime"] = start.isoformat(), end.isoformat()
        res = svc.events().patch(calendarId=self.calendar_id, eventId=event_id, body=cur,
                                 sendUpdates="all").execute()  # same event, no dup (FR-5.8)
        return "ok", {"event_id": event_id, "meet_url": res.get("hangoutLink", "")}

    def cancel_event(self, event_id: str) -> tuple[str, dict]:
        svc = self._service()
        svc.events().delete(calendarId=self.calendar_id, eventId=event_id,
                            sendUpdates="all").execute()
        return "ok", {"event_id": event_id}

    def get_event(self, event_id: str) -> tuple[str, dict]:
        svc = self._service()
        res = svc.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        return "ok", {"event_id": event_id, "status": res.get("status", ""),
                      "meet_url": res.get("hangoutLink", ""),
                      "start": res["start"].get("dateTime", "")}


# --- MCP transport (FR-3.3): the same actions as wire-protocol tools. --------
# Backend chosen by env so the deployed server uses real Google while unit
# tests spawn it with fakes (mocks only in tests). Run: python -m <this>.


def _mcp_backend():
    import os
    if os.getenv("CLOSEFUTURE_MCP_BACKEND", "fake") == "real":
        return RealCalendar(os.environ["GOOGLE_CLIENT_ID"],
                            os.environ["GOOGLE_CLIENT_SECRET"],
                            os.environ["GOOGLE_REFRESH_TOKEN"],
                            os.getenv("GOOGLE_CALENDAR_ID", "primary"))
    return FakeCalendar()


try:
    from mcp.server.fastmcp import FastMCP as _FastMCP

    mcp = _FastMCP("closefuture-calendar")
    _BE = None

    def _be():
        global _BE
        if _BE is None:
            _BE = _mcp_backend()
        return _BE

    @mcp.tool()
    def get_availability(start_iso: str, end_iso: str) -> dict:
        """Busy intervals between two ISO datetimes."""
        status, payload = _be().get_availability(datetime.fromisoformat(start_iso),
                                                 datetime.fromisoformat(end_iso))
        return {"result": status, **payload}

    @mcp.tool()
    def create_event(event_id: str, summary: str, start_iso: str, end_iso: str,
                     timezone: str, attendee: str) -> dict:
        """Create a calendar event with Meet link + attendee invite."""
        status, payload = _be().create_event(
            event_id=event_id, summary=summary, start=datetime.fromisoformat(start_iso),
            end=datetime.fromisoformat(end_iso), timezone=timezone, attendee=attendee)
        return {"result": status, **payload}

    @mcp.tool()
    def reschedule_event(event_id: str, start_iso: str, end_iso: str) -> dict:
        """Move an existing event (no duplicate)."""
        status, payload = _be().reschedule_event(
            event_id, datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso))
        return {"result": status, **payload}

    @mcp.tool()
    def cancel_event(event_id: str) -> dict:
        """Cancel an event by ID."""
        status, payload = _be().cancel_event(event_id)
        return {"result": status, **payload}

    @mcp.tool()
    def get_event(event_id: str) -> dict:
        """Fetch one event by ID."""
        status, payload = _be().get_event(event_id)
        return {"result": status, **payload}
except ImportError:  # pragma: no cover - direct use without MCP installed
    mcp = None


if __name__ == "__main__":
    mcp.run()
