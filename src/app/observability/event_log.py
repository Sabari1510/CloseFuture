"""In-memory event log (Phase 0); Postgres-backed in Phase 2. FR-3.9, FR-7.7, FR-8.5."""
import uuid
from dataclasses import dataclass, field


@dataclass
class Event:
    session_id: str
    turn_id: str
    trace_id: str
    seq: int
    type: str
    agent: str = ""
    payload: dict = field(default_factory=dict)


class EventLog:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq = 0

    def append(self, session_id: str, turn_id: str, trace_id: str,
               type: str, agent: str = "", payload: dict | None = None) -> Event:
        self._seq += 1
        ev = Event(session_id, turn_id, trace_id, self._seq, type, agent, payload or {})
        self._events.append(ev)
        return ev

    def for_session(self, session_id: str) -> list[Event]:
        return [e for e in self._events if e.session_id == session_id]

    @staticmethod
    def new_trace() -> str:
        return str(uuid.uuid4())
