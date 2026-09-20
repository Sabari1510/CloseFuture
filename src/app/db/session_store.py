"""Session store: source of truth per AGENT.md §8 + §8.1 concurrency (FR-2.1–2.6).

Two backends share one interface:
- InMemorySessionStore: L0/L1 tests, $0, same lease/CAS/append-only semantics.
- PostgresSessionStore: Supabase Postgres via psycopg; used in dev/demo.
State is hydrated at turn start and written back at turn end (FR-2.2).
"""
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class SessionRec:
    id: str
    visitor_id: str
    state: dict = field(default_factory=dict)
    version: int = 0
    lease_owner: str | None = None
    lease_until: float = 0.0
    timezone: str | None = None
    turn_count: int = 0
    last_activity: float = field(default_factory=time.time)
    created: float = field(default_factory=time.time)
    expires: float = field(default_factory=lambda: time.time() + 30 * 86400)
    status: str = "active"


class InMemorySessionStore:
    """Thread-safe enough for tests (single process, asyncio)."""

    def __init__(self) -> None:
        self.visitors: set[str] = set()
        self.sessions: dict[str, SessionRec] = {}
        self.messages: dict[str, list[dict]] = {}

    # -- identity (FR-2.5) --
    def ensure_visitor(self, vid: str | None) -> tuple[str, bool]:
        if vid and vid in self.visitors:
            return vid, False
        vid = vid or str(uuid.uuid4())
        self.visitors.add(vid)
        return vid, True

    def ensure_session(self, sid: str | None, vid: str, tz: str | None = None) -> tuple[str, bool]:
        rec = self.sessions.get(sid or "")
        if rec and rec.visitor_id == vid:
            return rec.id, False
        # new session: new id when sid missing, unknown, or hijacked (visitor mismatch)
        sid = str(uuid.uuid4())
        now = time.time()
        self.sessions[sid] = SessionRec(id=sid, visitor_id=vid, timezone=tz,
                                        last_activity=now, created=now)
        self.messages[sid] = []
        return sid, True

    # -- lease (FR-2.6 step 2) --
    def acquire_lease(self, sid: str, owner: str, seconds: int = 30) -> bool:
        rec = self.sessions[sid]
        now = time.time()
        if rec.lease_owner and rec.lease_until > now and rec.lease_owner != owner:
            return False
        rec.lease_owner, rec.lease_until = owner, now + seconds
        return True

    def release_lease(self, sid: str, owner: str) -> None:
        rec = self.sessions.get(sid)
        if rec and rec.lease_owner == owner:
            rec.lease_owner, rec.lease_until = None, 0.0

    # -- state CAS (FR-2.6 step 3) --
    def load(self, sid: str) -> tuple[dict, int]:
        rec = self.sessions[sid]
        return dict(rec.state), rec.version

    def save_cas(self, sid: str, new_state: dict, seen_version: int) -> tuple[bool, int]:
        """Merge by key on conflict, retry once. Returns (ok, version)."""
        rec = self.sessions[sid]
        if rec.version == seen_version:
            rec.state = dict(new_state)
            rec.version += 1
            return True, rec.version
        # conflict: merge new keys over current, bump once
        merged = dict(rec.state)
        merged.update(new_state)
        rec.state = merged
        rec.version += 1
        return False, rec.version

    # -- messages append-only (FR-2.6 step 1) --
    def append_message(self, sid: str, role: str, content: str, turn_id: str | None) -> None:
        self.messages[sid].append({"role": role, "content": content, "turn_id": turn_id,
                                   "ts": time.time()})

    def get_messages(self, sid: str) -> list[dict]:
        return list(self.messages.get(sid, []))

    def touch(self, sid: str) -> None:
        rec = self.sessions[sid]
        rec.last_activity = time.time()
        rec.turn_count += 1

    # -- test hooks --
    def set_last_activity(self, sid: str, ts: float) -> None:
        self.sessions[sid].last_activity = ts

    def session_visitor(self, sid: str) -> str | None:
        rec = self.sessions.get(sid)
        return rec.visitor_id if rec else None

    # -- attribute-style access used by the API (works for both backends) --
    def get_rec(self, sid: str) -> SessionRec:
        return self.sessions[sid]

    def set_timezone(self, sid: str, tz: str) -> None:
        self.sessions[sid].timezone = tz

    def version(self, sid: str) -> int:
        return self.sessions[sid].version

    def idle_sessions(self, idle_minutes: int) -> list[dict]:
        import time as _time
        now = _time.time()
        return [{"session_id": sid, "state": dict(r.state), "status": r.status,
                 "last_activity": r.last_activity}
                for sid, r in self.sessions.items()
                if now - r.last_activity >= idle_minutes * 60
                and r.status in ("active", "ended")]

    def mark_abandoned(self, sid: str) -> None:
        self.sessions[sid].status = "abandoned"

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """Admin discovery: newest first, no message content, no PII beyond IDs."""
        recs = sorted(self.sessions.values(), key=lambda r: r.last_activity, reverse=True)
        out = []
        for r in recs[:max(1, limit)]:
            booking = (r.state.get("booking") or {})
            out.append({"session_id": r.id, "visitor_id": r.visitor_id,
                        "status": r.status, "turns": r.turn_count,
                        "last_activity": r.last_activity,
                        "booked": booking.get("status") == "confirmed",
                        "email_known": bool((r.state.get("qualification") or {}).get("email"))})
        return out

    # -- TTL cleanup (FR-2.4) --
    def cleanup_ttl(self, ttl_days: int = 30) -> int:
        cutoff = time.time() - ttl_days * 86400
        expired = [sid for sid, r in self.sessions.items() if r.last_activity < cutoff]
        for sid in expired:
            del self.sessions[sid]
            self.messages.pop(sid, None)
        return len(expired)


class PostgresSessionStore:
    """Supabase-backed implementation (dev/demo). Same semantics, real SQL.

    Uses compare-and-set on version, lease via conditional UPDATE, append-only
    messages. Long tool calls run outside a DB transaction; the lease protects
    the turn (AGENT.md §8.1).
    """

    # Lease acquire: only when free or expired (FR-2.6)
    ACQUIRE_LEASE = """update sessions set lease_owner=%s, lease_until=now()+make_interval(secs=>%s)
        where id=%s and (lease_until is null or lease_until < now() or lease_owner=%s)"""
    LOAD = "select state, version from sessions where id=%s"
    SAVE_CAS = "update sessions set state=%s::jsonb, version=version+1 where id=%s and version=%s"
    APPEND_MSG = """insert into messages (session_id, turn_id, role, content)
        values (%s, %s::uuid, %s, %s)"""
    TOUCH = "update sessions set last_activity_at=now(), turn_count=turn_count+1 where id=%s"
    CLEANUP = "delete from sessions where last_activity_at < now() - make_interval(days=>%s)"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.sessions: dict[str, SessionRec] = {}

    def _connect(self):
        import psycopg
        from .pool import quoted_dsn
        return psycopg.connect(quoted_dsn(self.dsn))

    def ensure_visitor(self, vid: str | None) -> tuple[str, bool]:
        import uuid as _uuid
        with self._connect() as conn:
            if vid:
                row = conn.execute("select id from visitors where id=%s", (vid,)).fetchone()
                if row:
                    return vid, False
            vid = vid or str(_uuid.uuid4())
            conn.execute("insert into visitors (id) values (%s) on conflict do nothing", (vid,))
            conn.commit()
            return vid, True

    def ensure_session(self, sid: str | None, vid: str,
                       tz: str | None = None) -> tuple[str, bool]:
        import uuid as _uuid
        with self._connect() as conn:
            if sid:
                row = conn.execute("select visitor_id from sessions where id=%s", (sid,)).fetchone()
                if row and str(row[0]) == vid:
                    self._refresh(sid)
                    return sid, False
            sid = str(_uuid.uuid4())
            conn.execute("insert into sessions (id, visitor_id, timezone) values (%s,%s,%s)",
                         (sid, vid, tz))
            conn.commit()
            # keep an in-memory handle for attribute-style access used by the API
            self.sessions[sid] = SessionRec(id=sid, visitor_id=vid, timezone=tz)
            return sid, True

    def acquire_lease(self, sid: str, owner: str, seconds: int = 30) -> bool:
        with self._connect() as conn:
            cur = conn.execute(self.ACQUIRE_LEASE, (owner, seconds, sid, owner))
            conn.commit()
            return (cur.rowcount or 0) > 0

    def release_lease(self, sid: str, owner: str) -> None:
        with self._connect() as conn:
            conn.execute("update sessions set lease_owner=null, lease_until=null "
                         "where id=%s and lease_owner=%s", (sid, owner))
            conn.commit()

    def load(self, sid: str) -> tuple[dict, int]:
        import json as _json
        with self._connect() as conn:
            row = conn.execute(self.LOAD, (sid,)).fetchone()
            if not row:
                return {}, 0
            state = row[0] if isinstance(row[0], dict) else _json.loads(row[0] or "{}")
            if sid in self.sessions:
                self.sessions[sid].state, self.sessions[sid].version = state, row[1]
            return state, row[1]

    def save_cas(self, sid: str, new_state: dict, seen_version: int) -> tuple[bool, int]:
        import json as _json
        with self._connect() as conn:
            cur = conn.execute(self.SAVE_CAS, (_json.dumps(new_state), sid, seen_version))
            conn.commit()
            if (cur.rowcount or 0) > 0:
                if sid in self.sessions:
                    self.sessions[sid].state, self.sessions[sid].version = \
                        new_state, seen_version + 1
                return True, seen_version + 1
            cur_state, ver = self.load(sid)
            merged = dict(cur_state)
            merged.update(new_state)
            cur2 = conn.execute(self.SAVE_CAS, (_json.dumps(merged), sid, ver))
            conn.commit()
            return False, ver + ((cur2.rowcount or 0) > 0)

    def append_message(self, sid: str, role: str, content: str, turn_id: str | None) -> None:
        with self._connect() as conn:
            conn.execute(self.APPEND_MSG, (sid, turn_id, role, content))
            conn.commit()

    def get_messages(self, sid: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("select role, content from messages where session_id=%s "
                                "order by id", (sid,)).fetchall()
            return [{"role": r[0], "content": r[1]} for r in rows]

    def touch(self, sid: str) -> None:
        with self._connect() as conn:
            conn.execute(self.TOUCH, (sid,))
            conn.commit()
        if sid in self.sessions:
            self.sessions[sid].turn_count += 1

    def set_last_activity(self, sid: str, ts: float) -> None:
        import datetime as _dt
        with self._connect() as conn:
            conn.execute("update sessions set last_activity_at=%s where id=%s",
                         (_dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc), sid))
            conn.commit()

    def session_visitor(self, sid: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("select visitor_id from sessions where id=%s", (sid,)).fetchone()
            return str(row[0]) if row else None

    def _refresh(self, sid: str) -> SessionRec:
        with self._connect() as conn:
            row = conn.execute("select visitor_id, state, version, timezone, turn_count,"
                               " status from sessions where id=%s", (sid,)).fetchone()
        import json as _json
        rec = SessionRec(id=sid, visitor_id=str(row[0]),
                         state=row[1] if isinstance(row[1], dict) else _json.loads(row[1] or "{}"),
                         version=row[2], timezone=row[3], turn_count=row[4], status=row[5])
        self.sessions[sid] = rec
        return rec

    def get_rec(self, sid: str) -> SessionRec:
        return self.sessions.get(sid) or self._refresh(sid)

    def set_timezone(self, sid: str, tz: str) -> None:
        with self._connect() as conn:
            conn.execute("update sessions set timezone=%s where id=%s", (tz, sid))
            conn.commit()
        self.get_rec(sid).timezone = tz

    def version(self, sid: str) -> int:
        return self.get_rec(sid).version

    def idle_sessions(self, idle_minutes: int) -> list[dict]:
        import json as _json
        with self._connect() as conn:
            rows = conn.execute(
                "select id, state, status, extract(epoch from last_activity_at) "
                "from sessions where last_activity_at < "
                "now() - make_interval(mins=>%s) and status in ('active','ended')",
                (idle_minutes,)).fetchall()
        return [{"session_id": str(r[0]),
                 "state": r[1] if isinstance(r[1], dict) else _json.loads(r[1] or "{}"),
                 "status": r[2], "last_activity": float(r[3])} for r in rows]

    def mark_abandoned(self, sid: str) -> None:
        with self._connect() as conn:
            conn.execute("update sessions set status='abandoned' where id=%s", (sid,))
            conn.commit()
        if sid in self.sessions:
            self.sessions[sid].status = "abandoned"

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """Admin discovery: newest first, no message content, no PII beyond IDs."""
        import json as _json2
        with self._connect() as conn:
            rows = conn.execute(
                "select id, visitor_id, status, turn_count, "
                "extract(epoch from last_activity_at), state "
                "from sessions order by last_activity_at desc limit %s",
                (max(1, limit),)).fetchall()
        out = []
        for r in rows:
            st = r[5] if isinstance(r[5], dict) else _json2.loads(r[5] or "{}")
            booking = st.get("booking") or {}
            out.append({"session_id": str(r[0]), "visitor_id": str(r[1]),
                        "status": r[2], "turns": r[3], "last_activity": float(r[4]),
                        "booked": booking.get("status") == "confirmed",
                        "email_known": bool((st.get("qualification") or {}).get("email"))})
        return out

    def cleanup_ttl(self, ttl_days: int = 30) -> int:
        with self._connect() as conn:
            cur = conn.execute(self.CLEANUP, (ttl_days,))
            conn.commit()
            return cur.rowcount or 0
