"""Orchestrator StateGraph (AGENT.md §6). FR-3.1–3.9.

Nodes: load handled by caller (lease/append in API) -> guardrail_in -> route ->
{search, scheduler, lead_summary, clarify} -> compose -> guardrail_out ->
write_state. safe_fallback reachable from guardrail_in and error paths.
Every path to the visitor goes through guardrail_out or the static fallback.
recursion_limit <= 12; retries <= 2 live in reliability/retry (tool loops).
"""
from langgraph.graph import END, START, StateGraph

from ..agents.guardrail.service import check_inbound, check_outbound, fallback_text
from ..agents.search import answer as search_answer
from ..contracts.agent_contracts import GuardrailVerdict
from .state import TurnState

CLARIFY_Q = ("Could you tell me a bit more about what you need — for example, "
             "service details, a case study, or booking a call?")
GREET_Q = ("Hi there! I'm CloseFuture's assistant. I can walk you through our services, "
           "share case studies like Dipy or Liya AI, explain how we typically work — "
           "or book you a discovery call. What's on your mind?")
SCHED_STUB = ("To book a call I need two things: your work email and a preferred "
              "time window. We meet Mon–Fri 10:00–17:00 Asia/Kolkata — "
              "what works for you?")


def build_graph(deps):
    events = deps["events"]
    search_store = deps["search_store"]
    router = deps["router"]
    top_k = deps.get("top_k", 4)
    min_sim = deps.get("min_sim", 0.29)
    route_threshold = deps.get("route_threshold", 0.6)

    def ev(state, type, agent, payload):
        return events.append(state["session_id"], state["turn_id"], state["trace_id"],
                             type, agent, payload)

    def n_guardrail_in(state):
        v = check_inbound(state["user_message"])
        ev(state, "guardrail_check", "guardrail",
           {"stage": "inbound", "passed": v.passed, "reason_codes": v.reason_codes})
        return {"guardrail_in": v.model_dump(), "_blocked": not v.passed}

    def n_route(state):
        if state.get("_blocked"):
            return {}
        flags = {"tz_known": bool(state.get("tz_known")),
                 "email_known": bool(state.get("email_known")),
                 "sched_pending": bool(state.get("sched_pending")),
                 "sched_active": bool(state.get("sched_active"))}
        d = router.decide(state["user_message"], state.get("history", []), flags)
        if d.confidence < route_threshold and d.strategy != "clarify":  # FR-3.6
            d.strategy, d.targets = "clarify", []
            d.rationale = f"low confidence {d.confidence} -> clarify, no wrong-agent call"
        ev(state, "route_decision", "orchestrator",
           {"intent": d.intent, "confidence": d.confidence, "strategy": d.strategy,
            "targets": d.targets, "rationale": d.rationale, "ended": d.ended})
        return {"route": d.model_dump()}

    def n_search(state):
        import re
        msg = state["user_message"]
        if "scheduler" in (state.get("route", {}).get("targets") or []):
            # multi-intent: search the question part, not the booking clause (FR-4.1)
            msg = re.sub(r"\s*(also|and)\s*\b(book|schedule|reschedule)\b[^.?]*[.?]?\s*$",
                         "", msg, flags=re.I).strip() or msg
        r = search_answer(msg, state.get("history", []), search_store,
                          top_k=top_k, min_sim=min_sim)
        ev(state, "agent_call", "search",
           {"confidence": r.confidence, "citations": [c.model_dump() for c in r.citations]})
        outs = dict(state.get("agent_outputs", {}))
        outs["search"] = r.model_dump()
        ran = list(state.get("agents_run", [])) + ["search"]
        return {"agent_outputs": outs, "agents_run": ran}

    def n_scheduler(state):
        import re
        from ..agents.scheduler import run_scheduler, sched_from_dict
        sched = sched_from_dict(state.get("sched"))
        qual = dict(state.get("qualification", {}))
        log: list = []
        deps["cal"].set_ctx(state["session_id"], state["turn_id"], state["trace_id"])
        reply, sched, info = run_scheduler(
            state["user_message"], sched, session_id=state["session_id"], cal=deps["cal"],
            bookings=deps["bookings"], owner_tz=deps.get("owner_tz", "Asia/Kolkata"),
            visitor_tz=state.get("timezone") or "Asia/Kolkata",
            known_email=qual.get("email"), log=log)
        for entry in log:
            ev(state, "tool_call", "calendar_mcp", entry)
            ev(state, "tool_result", "calendar_mcp", {"tool": entry.get("tool")})
        if (m := re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                             state.get("user_message", ""))):
            qual["email"] = m.group(0)
        outs = dict(state.get("agent_outputs", {}))
        failed = bool(info.get("manual_followup"))
        if failed:  # honest fallback after retries exhausted (FR-8.1, FR-8.4)
            ev(state, "fallback", "scheduler",
               {"reason": "calendar_unavailable", "error_code": info.get("error_code")})
        outs["scheduler"] = {"status": "error" if failed else "ok", "agent": "scheduler",
                              "output": {"reply": reply, **{k: v for k, v in info.items()
                                       if k in ("booked", "proposed", "need",
                                                "retry", "cancelled", "rescheduled",
                                                "reschedule", "manual_followup")}},
                             **({"error_code": info.get("error_code", "UPSTREAM_5XX"),
                                 "message": "calendar unavailable; manual follow-up queued",
                                 "retryable": False} if failed else {})}
        ran = list(state.get("agents_run", [])) + ["scheduler"]
        update: dict = {"agent_outputs": outs, "agents_run": ran,
                         "sched": {"stage": sched.stage,
                                   "slots": [(s.isoformat(), e.isoformat()) for s, e in sched.slots],
                                   "booking_id": sched.booking_id, "event_id": sched.event_id,
                                   "preferred_date": sched.preferred_date,
                                   "preferred_start": sched.preferred_start,
                                   "reschedule_of": sched.reschedule_of},
                        "qualification": qual}
        if info.get("booked"):
            update["booking"] = {"status": "confirmed", "event_id": info.get("event_id"),
                                 "meet_url": info.get("meet_url")}
            route = dict(state.get("route", {}))
            route["ended"] = True  # happy-path trigger for Lead-Summary (FR-3.4)
            update["route"] = route
        if info.get("cancelled"):
            update["booking"] = {"status": "cancelled"}
        return update

    def n_lead_summary(state):
        outs = dict(state.get("agent_outputs", {}))
        inbox = deps.get("sales_inbox") or ""
        if not inbox:  # no inbox configured yet -> log skip, never crash the turn
            ev(state, "agent_call", "lead_summary", {"skipped": "no-sales-inbox"})
            outs["lead_summary"] = {"status": "ok", "agent": "lead_summary",
                                    "output": {"skipped": True}}
            return {"agent_outputs": outs}
        deps["mailer"].set_ctx(state["session_id"], state["turn_id"], state["trace_id"])
        info = deps["leads"].trigger(
            session_id=state["session_id"],
            messages=state.get("transcript") or state.get("history", []),
            booking=state.get("booking"), incomplete=False,
            mailer=deps["mailer"], sales_inbox=inbox)
        ev(state, "agent_call", "lead_summary",
           {"mailed": info.get("mailed"), "kind": info.get("kind"),
            "version": info.get("version"), "score": info.get("score")})
        if info.get("mailed"):
            ev(state, "tool_call", "email_mcp", {"tool": "send_lead_summary"})
            ev(state, "tool_result", "email_mcp", {"tool": "send_lead_summary"})
            ev(state, "email_sent", "lead_summary",
               {"kind": info.get("kind"), "version": info.get("version")})
        elif info.get("reason") == "queued":  # email down -> outbox, lead kept (FR-8.1)
            ev(state, "fallback", "lead_summary", {"reason": "email_queued_outbox"})
        outs["lead_summary"] = {"status": "ok", "agent": "lead_summary",
                                "output": {k: info.get(k) for k in
                                           ("mailed", "kind", "version", "score", "tier", "reason")}}
        return {"agent_outputs": outs,
                "summary_state": {"sent": True, "version": info.get("version", 1),
                                  "incomplete": False}}

    def n_clarify(state):
        ev(state, "agent_call", "clarify", {"reason": "low_confidence_or_ambiguous"})
        outs = dict(state.get("agent_outputs", {}))
        outs["clarify"] = {"status": "ok", "agent": "clarify",
                           "output": {"reply": CLARIFY_Q}}
        ran = list(state.get("agents_run", [])) + ["clarify"]
        return {"agent_outputs": outs, "agents_run": ran}

    def n_done(state):
        ev(state, "agent_call", "done", {"reason": "explicit finish"})
        outs = dict(state.get("agent_outputs", {}))
        outs["done"] = {"status": "ok", "agent": "done",
                        "output": {"reply": "You're welcome — the team has your details "
                                            "and will follow up by email."}}
        ran = list(state.get("agents_run", [])) + ["done"]
        return {"agent_outputs": outs, "agents_run": ran}

    def n_greet(state):
        ev(state, "agent_call", "greet", {"reason": "greeting"})
        outs = dict(state.get("agent_outputs", {}))
        outs["greet"] = {"status": "ok", "agent": "greet",
                         "output": {"reply": GREET_Q}}
        ran = list(state.get("agents_run", [])) + ["greet"]
        return {"agent_outputs": outs, "agents_run": ran}

    def n_compose(state):
        route = state.get("route", {})
        outs = state.get("agent_outputs", {})
        strategy = route.get("strategy", "single")
        if route.get("ended") and "done" in outs:
            draft = outs["done"]["output"]["reply"]
        elif "greet" in outs:
            draft = outs["greet"]["output"]["reply"]
        elif strategy == "clarify" or not outs:
            draft = outs.get("clarify", {}).get("output", {}).get("reply", CLARIFY_Q)
        elif strategy in ("sequence", "parallel"):
            search_part = (outs.get("search", {}).get("output", {}).get("answer", "") or "")[:500]
            sched_part = outs.get("scheduler", {}).get("output", {}).get("reply", "") or ""
            draft = f"{search_part} {sched_part}".strip()[:900]
        elif route.get("ended") and "done" in outs:
            draft = outs["done"]["output"]["reply"]
        else:
            sole = (route.get("targets", ["search"]) or ["search"])[0]
            o = outs.get(sole, {}).get("output", {})
            draft = (o.get("reply") or o.get("answer", "") or CLARIFY_Q)[:900]
        return {"draft_reply": draft}

    def n_guardrail_out(state):
        if state.get("_blocked"):
            return {}
        chunks_text = " ".join(c.content for c in search_store.chunks)
        outs = state.get("agent_outputs", {})
        cites = []
        conf = None
        if "search" in outs:
            cites = outs["search"].get("citations", [])
            conf = outs["search"].get("confidence")
        known = state.get("qualification", {}).get("email", "")
        supplied = f"{state.get('user_message', '')} {known}"  # email given earlier still counts
        v = check_outbound(state.get("draft_reply", ""), chunks_text=chunks_text,
                           citations=cites, confidence=conf,
                           visitor_supplied=supplied,
                           tool_grounded="scheduler" in outs)
        ev(state, "guardrail_check", "guardrail",
           {"stage": "outbound", "passed": v.passed, "reason_codes": v.reason_codes})
        if not v.passed:
            return {"draft_reply": fallback_text(v), "_out_blocked": True}
        return {"guardrail_out": v.model_dump()}

    def n_write(state):
        return {"final_reply": state.get("draft_reply", CLARIFY_Q)}

    def n_fallback(state):
        v = GuardrailVerdict.model_validate(state["guardrail_in"])
        return {"final_reply": fallback_text(v), "draft_reply": fallback_text(v)}

    g = StateGraph(TurnState)
    g.add_node("guardrail_in", n_guardrail_in)
    g.add_node("route", n_route)
    g.add_node("search", n_search)
    g.add_node("scheduler", n_scheduler)
    g.add_node("lead_summary", n_lead_summary)
    g.add_node("clarify", n_clarify)
    g.add_node("done", n_done)
    g.add_node("greet", n_greet)
    g.add_node("compose", n_compose)
    g.add_node("guardrail_out", n_guardrail_out)
    g.add_node("write_state", n_write)
    g.add_node("safe_fallback", n_fallback)

    g.add_edge(START, "guardrail_in")
    g.add_conditional_edges("guardrail_in",
                            lambda s: "safe_fallback" if s.get("_blocked") else "route")
    g.add_conditional_edges("route", lambda s: _after_route(s))
    g.add_conditional_edges("search", lambda s: _after_search(s))
    g.add_edge("scheduler", "compose")
    g.add_edge("clarify", "compose")
    g.add_edge("done", "compose")
    g.add_edge("greet", "compose")
    g.add_conditional_edges("compose", lambda s: _after_compose(s))
    g.add_edge("lead_summary", "guardrail_out")
    g.add_edge("guardrail_out", "write_state")
    g.add_edge("write_state", END)
    g.add_edge("safe_fallback", "write_state")
    return g.compile()


def _after_route(s) -> str:
    r = s.get("route", {})
    if r.get("ended") and not r.get("targets"):
        return "done"  # explicit finish with nothing left to do
    if r.get("intent") == "greeting" or r.get("targets") == ["greet"]:
        return "greet"
    # ended WITH targets (e.g. booking email given): run the agents first;
    # the lead-mail node still fires after compose because ended is set
    if r.get("strategy") == "clarify" or not r.get("targets"):
        return "clarify"
    t = r["targets"]
    if t == ["search"]:
        return "search"
    if t == ["scheduler"]:
        return "scheduler"
    return "search"  # sequence/parallel: search first, then scheduler


def _after_search(s) -> str:
    r = s.get("route", {})
    if "scheduler" in (r.get("targets") or []) and "scheduler" not in s.get("agent_outputs", {}):
        return "scheduler"
    return "compose"


def _after_compose(s) -> str:
    if s.get("route", {}).get("ended"):
        return "lead_summary"
    return "guardrail_out"
