"""TurnState per AGENT.md §6.1."""
from typing import Any
from typing_extensions import TypedDict


class TurnState(TypedDict, total=False):
    session_id: str
    visitor_id: str
    turn_id: str
    trace_id: str
    messages: list[dict]
    rolling_summary: str
    user_message: str
    history: list[dict]
    transcript: list[dict]
    timezone: str
    tz_known: bool
    email_known: bool
    guardrail_in: dict | None
    route: dict | None
    agent_outputs: dict[str, Any]
    retrieved_chunks: list[dict]
    search_confidence: float | None
    qualification: dict
    agents_run: list[str]
    booking: dict | None
    sched: dict
    sched_pending: bool
    sched_active: bool
    summary_state: dict
    draft_reply: str
    final_reply: str
    guardrail_out: dict | None
    flags: dict
    _blocked: bool
    _out_blocked: bool
