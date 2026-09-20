"""API: rate limit -> length cap -> budget gate -> Orchestrator graph turn.

Turn lifecycle (AGENT.md §5.1, §8.1): gates -> identity -> turn cap ->
append visitor msg -> lease -> graph run (guardrail_in -> route -> agents ->
compose -> guardrail_out) -> CAS write -> reply. FR-2.x, FR-3.9.
Frontend-ready (§4.1): /v1 versioning, OpenAPI, CORS allowlist, visitor/session
contract, visitor-scoped history restore, stable status values.
"""
import time
import uuid
import asyncio
from collections import defaultdict

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, "config")
from settings import settings  # noqa: E402
from src.app.agents.orchestrator.router import get_router  # noqa: E402
from src.app.agents.scheduler import BookingStore  # noqa: E402
from src.app.db.session_store import InMemorySessionStore, PostgresSessionStore  # noqa: E402
from src.app.db.pool import reachable as _db_reachable  # noqa: E402
from src.app.graph.builder import build_graph  # noqa: E402
from src.app.agents.lead_store import LeadStore  # noqa: E402
from src.app.mcp_servers.calendar_server import FakeCalendar, RealCalendar  # noqa: E402
from src.app.mcp_servers.email_server import FakeEmail, RealGmail  # noqa: E402
from src.app.reliability.mcp_call import RetryingCalendar, RetryingMailer  # noqa: E402
from src.app.llm.client import ledger  # noqa: E402  (shared: real calls record here)
from src.app.observability.event_log import EventLog  # noqa: E402
from src.app.retrieval.loader import get_search_store  # noqa: E402

app = FastAPI(title="CloseFuture Lead Agent", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

def _calendar_backend():
    """MCP calendar tools over stdio (FR-3.3); direct classes only as fallback."""
    use_real = bool(settings.google_refresh_token)
    google_env = {"GOOGLE_CLIENT_ID": settings.google_client_id,
                  "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
                  "GOOGLE_REFRESH_TOKEN": settings.google_refresh_token,
                  "GOOGLE_CALENDAR_ID": settings.google_calendar_id}
    if settings.mcp_transport == "stdio":
        try:
            from src.app.mcp_servers.mcp_client import McpCalendar
            # OAuth secrets live in .env (not os.environ): forward them so the
            # server subprocess can construct the real backend.
            return McpCalendar(backend="real" if use_real else "fake",
                               env_extra=google_env)
        except Exception as e:  # transport down -> degraded direct path (FR-8.1)
            events.append("startup", "boot", EventLog.new_trace(), "fallback", "api",
                          {"transport": "direct-calendar", "error": type(e).__name__})
    if use_real:
        return RealCalendar(settings.google_client_id, settings.google_client_secret,
                            settings.google_refresh_token, settings.google_calendar_id)
    return FakeCalendar()


def _mailer_backend():
    """MCP email tools over stdio (FR-3.3); direct classes only as fallback."""
    use_real = bool(settings.google_refresh_token)
    google_env = {"GOOGLE_CLIENT_ID": settings.google_client_id,
                  "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
                  "GOOGLE_REFRESH_TOKEN": settings.google_refresh_token}
    if settings.mcp_transport == "stdio":
        try:
            from src.app.mcp_servers.mcp_client import McpMailer
            return McpMailer(backend="real" if use_real else "fake",
                             env_extra=google_env)
        except Exception as e:
            events.append("startup", "boot", EventLog.new_trace(), "fallback", "api",
                          {"transport": "direct-mailer", "error": type(e).__name__})
    if use_real:
        return RealGmail(settings.google_client_id, settings.google_client_secret,
                         settings.google_refresh_token)
    return FakeEmail()


events = EventLog()
if settings.database_url and _db_reachable():
    store = PostgresSessionStore(settings.database_url)  # Supabase is source of truth
    print("session store: postgres")
else:
    store = InMemorySessionStore()  # local fallback when DB unreachable
    print("session store: memory (DB unreachable)")
router = get_router(settings.llm_mode if settings.llm_api_key else "fake")
cal = RetryingCalendar(_calendar_backend(), events=events)  # real exp backoff
bookings = BookingStore()  # bookings table in Postgres in dev (same exclusion)
mailer = RetryingMailer(_mailer_backend(), events=events)
leads = LeadStore()  # lead_summaries + email_events + outbox tables in dev
sales_inbox = settings.sales_inbox  # tests override api.sales_inbox
using_real_google = bool(settings.google_refresh_token)


def _graph():
    return build_graph({"events": events, "search_store": get_search_store(),
                        "router": router, "cal": cal, "bookings": bookings,
                        "mailer": mailer, "leads": leads, "sales_inbox": sales_inbox,
                        "owner_tz": settings.calendar_tz, "top_k": settings.top_k,
                        "min_sim": settings.min_sim, "route_threshold": settings.route_conf_threshold})


graph = _graph()
_session_hits: dict[str, list[float]] = defaultdict(list)
_ip_hits: dict[str, list[float]] = defaultdict(list)


class ChatIn(BaseModel):
    message: str
    visitor_id: str | None = None
    session_id: str | None = None
    timezone: str | None = None


def _prune(hits: list[float], window: float) -> list[float]:
    now = time.time()
    return [t for t in hits if now - t < window]


class TurnTimeout(Exception):
    pass


async def _invoke_with_deadline(init: dict) -> dict:
    """Whole-turn deadline (FR-3.7): only invoke() runs in the worker thread, so
    a timed-out run writes no state — the winner below is the sole writer.
    Late side effects inside the orphaned run stay idempotent (deterministic
    event IDs, email idempotency keys)."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_graph().invoke, init, config={"recursion_limit": 12}),
            timeout=settings.turn_deadline_s)
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise TurnTimeout from e


def check_rate(session_id: str, ip: str) -> tuple[bool, int]:
    now = time.time()
    s = _prune(_session_hits[session_id or ip], 60)
    if len(s) >= settings.rate_session_per_min:
        return False, 60 - int(now - s[0])
    i = _prune(_ip_hits[ip], 3600)
    if len(i) >= settings.rate_ip_per_hour:
        return False, 3600 - int(now - i[0])
    s.append(now)
    i.append(now)
    _session_hits[session_id or ip] = s
    _ip_hits[ip] = i
    return True, 0


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "google": "real" if using_real_google else "fake"}


@app.post("/v1/chat")
async def chat(body: ChatIn, request: Request) -> JSONResponse:
    turn_id = str(uuid.uuid4())
    owner = f"turn-{turn_id}"
    trace_id = EventLog.new_trace()
    ip = request.client.host if request.client else "unknown"

    def reply(text: str, sid: str, vid: str, status: str,
              retry_after: int | None = None) -> JSONResponse:
        return JSONResponse({"reply": text, "session_id": sid, "visitor_id": vid,
                             "turn_id": turn_id, "status": status,
                             "retry_after_seconds": retry_after})

    if len(body.message) > settings.max_message_chars:  # FR-7.3 length cap
        vid = body.visitor_id or "unknown"
        sid = body.session_id or "unknown"
        events.append(sid, turn_id, trace_id, "rate_limited", "api",
                      {"reason": "message_too_long"})
        return reply("Please keep messages under 500 characters.", sid, vid, "blocked")
    ok, retry_after = check_rate(body.session_id or body.visitor_id or ip, ip)
    if not ok:
        events.append(body.session_id or "unknown", turn_id, trace_id,
                      "rate_limited", "api", {"ip": ip})
        return reply("You're sending messages too quickly. Please wait a moment.",
                     body.session_id or "", body.visitor_id or "", "rate_limited", retry_after)
    if ledger.blocked(settings.budget_hard_usd):
        events.append(body.session_id or "unknown", turn_id, trace_id, "budget_block", "api", {})
        return reply("Our assistant is temporarily unavailable. Please try again shortly.",
                     body.session_id or "", body.visitor_id or "", "degraded")

    # Identity (FR-2.5): returning visitor recognised, session reloaded not restarted
    visitor_id, _ = store.ensure_visitor(body.visitor_id)
    session_id, _ = store.ensure_session(body.session_id, visitor_id, body.timezone)
    rec = store.get_rec(session_id)
    if body.timezone:
        store.set_timezone(session_id, body.timezone)

    # Turn cap (CLAUDE.md §6)
    if rec.turn_count >= settings.max_turns_per_session:
        return reply("We've reached the end of this conversation. The team will follow up by email.",
                     session_id, visitor_id, "ok")

    # Append first: never lose the visitor message (FR-2.6)
    store.append_message(session_id, "visitor", body.message, turn_id)

    # Lease (FR-2.6): serialize turns per session
    if not store.acquire_lease(session_id, owner, settings.lease_seconds):
        events.append(session_id, turn_id, trace_id, "fallback", "orchestrator",
                      {"reason": "turn_busy"})
        return reply("Your previous message is still being handled — one moment.",
                     session_id, visitor_id, "busy")
    try:
        state, version = store.load(session_id)  # hydrate every turn (FR-2.2)
        history = [{"role": m["role"], "content": m["content"]}
                   for m in store.get_messages(session_id)[-6:]]
        qual = state.get("qualification", {})
        init = {"session_id": session_id, "visitor_id": visitor_id, "turn_id": turn_id,
                "trace_id": trace_id, "user_message": body.message, "history": history,
                "agent_outputs": {}, "agents_run": list(state.get("agents_run", [])),
                "route": {}, "tz_known": bool(body.timezone or rec.timezone),
                "email_known": bool(qual.get("email")),
                "sched_pending": (state.get("sched", {}).get("stage") == "proposed"),
                "sched_active": (state.get("sched", {}).get("stage") in ("collect", "proposed")),
                "timezone": body.timezone or rec.timezone or "Asia/Kolkata",
                "sched": state.get("sched", {}), "qualification": qual,
                "booking": state.get("booking"),
                "transcript": [{"role": m["role"], "content": m["content"]}
                               for m in store.get_messages(session_id)],
                "summary_state": state.get("summary_state", {})}
        try:
            out = await _invoke_with_deadline(init)
        except TurnTimeout:  # FR-3.7: honest degraded reply, lease still released
            events.append(session_id, turn_id, trace_id, "fallback", "orchestrator",
                          {"reason": "turn_deadline", "timeout_s": settings.turn_deadline_s})
            return reply("This is taking longer than usual on our side — I've noted "
                         "your message and the team will follow up by email.",
                         session_id, visitor_id, "degraded")
        final = str(out.get("final_reply", ""))
        blocked = bool(out.get("_blocked") or out.get("_out_blocked"))
        route = out.get("route", {})
        store.append_message(session_id, "assistant", final, turn_id)
        new_state = dict(state, agents_run=out.get("agents_run", []),
                         last_route=route, sched=out.get("sched", {}),
                         qualification=out.get("qualification", qual),
                         booking=out.get("booking"),
                         summary_state=out.get("summary_state",
                                               state.get("summary_state", {})),
                         rolling_summary="; ".join(
                             m["content"][:80] for m in store.get_messages(session_id)[-4:]))
        new_state.pop("away_since", None)  # fresh visitor message cancels away-grace
        if route.get("ended"):
            new_state["status"] = "ended"
        store.save_cas(session_id, new_state, version)  # CAS, merge on conflict
        store.touch(session_id)
        events.append(session_id, turn_id, trace_id, "state_write", "orchestrator",
                      {"turn_id": turn_id, "version": store.version(session_id),
                       "strategy": route.get("strategy", "")})
        return reply(final, session_id, visitor_id, "blocked" if blocked else "ok")
    finally:
        store.release_lease(session_id, owner)


@app.get("/v1/sessions/{sid}/messages")
async def get_messages(sid: str, visitor_id: str = Query(default="")) -> dict:
    """Visitor-scoped restore (FR-2.3/2.5): needs matching visitor_id, no LLM cost."""
    owner = store.session_visitor(sid)
    if owner is None or owner != visitor_id:
        raise HTTPException(status_code=403, detail="forbidden")
    visible = [{"role": m["role"], "content": m["content"]}
               for m in store.get_messages(sid) if m["role"] in ("visitor", "assistant")]
    return {"session_id": sid, "messages": visible}


class EndIn(BaseModel):
    visitor_id: str = ""
    email: str | None = None


class PresenceIn(BaseModel):
    visitor_id: str = ""
    state: str = "away"  # away | here


@app.post("/v1/sessions/{sid}/presence")
async def presence(sid: str, body: PresenceIn) -> dict:
    """Tab presence beacon ($0, no LLM). 'away' starts the away-grace clock so a
    visitor who closes the tab mid-chat gets their lead summary within minutes;
    'here' (or any new chat message) cancels it. Visitor-scoped like history."""
    owner = store.session_visitor(sid)
    if owner is None or owner != body.visitor_id:
        raise HTTPException(status_code=403, detail="forbidden")
    state, version = store.load(sid)
    if state.get("status") == "ended":
        return {"session_id": sid, "presence": "ended"}
    if body.state == "here":
        state.pop("away_since", None)
    else:
        state.setdefault("away_since", time.time())
    store.save_cas(sid, state, version)
    return {"session_id": sid,
            "presence": "here" if "away_since" not in state else "away"}


@app.post("/v1/sessions/{sid}/end")
async def end_session(sid: str, body: EndIn) -> dict:
    """Visitor closes the chat (FR-3.4, FR-3.7).

    If no email is known yet, ask once (need-email) so the lead summary
    carries contact details instead of 'no email'. Otherwise mark ended and
    trigger the complete (not INCOMPLETE) summary. Idempotent: a repeat call
    is a no-op once the summary exists. Abandoned sessions are still covered
    by the idle sweeper (FR-6.4).
    """
    owner = store.session_visitor(sid)
    if owner is None or owner != body.visitor_id:
        raise HTTPException(status_code=403, detail="forbidden")
    state, version = store.load(sid)
    if state.get("status") == "ended" and sid in leads.summaries:
        return {"session_id": sid, "status": "already-ended"}
    qual = dict(state.get("qualification", {}))
    email = ((body.email or "").strip() or qual.get("email") or "").strip()
    if "@" not in email:
        return {"session_id": sid, "status": "need-email",
                "reply": "Before you go — what's the best email address "
                         "for the team to follow up?"}
    qual["email"] = email
    turn_id = str(uuid.uuid4())
    closing = "Thanks for chatting — the team has your details and will follow up by email."
    store.append_message(sid, "assistant", closing, turn_id)
    state = dict(state, qualification=qual, status="ended")
    store.save_cas(sid, state, version)
    store.touch(sid)
    msgs = [{"role": m["role"], "content": m["content"]}
            for m in store.get_messages(sid) if m["role"] in ("visitor", "assistant")]
    info = leads.trigger(session_id=sid, messages=msgs, booking=state.get("booking"),
                         incomplete=False, mailer=mailer, sales_inbox=sales_inbox)
    trace_id = EventLog.new_trace()
    events.append(sid, turn_id, trace_id, "session_end", "api",
                  {"mailed": info.get("mailed"), "reason": info.get("reason", "")})
    return {"session_id": sid, "status": "ended", "reply": closing,
            "mailed": info.get("mailed", False), "reason": info.get("reason", "")}


@app.get("/v1/sessions/{sid}/trace")
async def get_trace(sid: str, x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> dict:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="forbidden")
    return {"session_id": sid, "events": [e.__dict__ for e in events.for_session(sid)]}


@app.get("/internal/sessions")
async def list_sessions(limit: int = Query(default=50, le=200),
                        x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> dict:
    """Company-side discovery: newest sessions with status flags (multi-user safe:
    every visitor has their own IDs; this lists them all). Key-gated, no message
    content and no PII beyond IDs — open a session to read it."""
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="forbidden")
    rows = store.list_sessions(limit)
    for row in rows:
        row["summary_sent"] = row["session_id"] in leads.summaries
    return {"sessions": rows}


@app.post("/internal/sweep")
async def sweep(x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> dict:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="forbidden")
    return run_sweep_cycle()


def run_sweep_cycle() -> dict:
    """One sweep pass: TTL cleanup + idle sweeper + outbox drain (FR-2.4, FR-6.4)."""
    from src.app.jobs.cleanup import cleanup as run_cleanup
    from src.app.jobs.sweep import sweep_idle
    expired = run_cleanup(store)
    idle = sweep_idle(store, leads, idle_minutes=settings.idle_abandon_minutes,
                      away_grace_minutes=settings.away_grace_minutes,
                      mailer=mailer, sales_inbox=sales_inbox) if sales_inbox else []
    drained = leads.drain_outbox(mailer)
    sweep_status.update({"last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "last_result": {"expired": expired, "swept": idle,
                                         "outbox_drained": drained}})
    return {"expired": expired, "swept": idle, "outbox_drained": drained}


sweep_status: dict = {"last_run": None, "last_result": None,
                      "interval_min": settings.sweep_interval_min, "auto": False}


@app.get("/internal/sweep/status")
async def sweep_status_view(
        x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> dict:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="forbidden")
    return {**sweep_status, "interval_min": settings.sweep_interval_min,
            "idle_abandon_min": settings.idle_abandon_minutes}


async def _sweep_loop() -> None:
    import asyncio
    while settings.sweep_interval_min > 0:
        await asyncio.sleep(settings.sweep_interval_min * 60)
        try:
            run_sweep_cycle()
        except Exception:
            pass  # never crash the server on a background pass


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(app) -> object:
    import asyncio
    task = None
    if settings.sweep_interval_min > 0:
        sweep_status["auto"] = True
        task = asyncio.create_task(_sweep_loop())
    yield
    sweep_status["auto"] = False
    if task:
        task.cancel()

app.router.lifespan_context = _lifespan
