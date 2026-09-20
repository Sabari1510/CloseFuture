"""Model-driven router with confidence (FR-3.2–3.6). One structured call per turn.

Production: RealRouter (small chat model, structured RouteDecision, max_tokens 150).
Tests/dev ($0): FakeRouter — same RouteDecision schema with injectable decisions;
its un-injected prior is a transparent keyword heuristic marked DEV STAND-IN so
L0–L2 prove edges without spend. Never ship the heuristic as production routing.
"""
import re

from ...contracts.agent_contracts import RouteDecision

BOOK_RE = re.compile(r"\b(book|booking|call|meeting|schedule|appointment|reschedule|cancel|slot|demo)\b", re.I)
TIME_CUE_RE = re.compile(r"\b(today|tomorrow|morning|afternoon|evening|monday|tuesday|wednesday|"
                         r"thursday|friday)\b|\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", re.I)
Q_RE = re.compile(r"\?|\b(what|how|which|who|tell me|pricing|price|rate|case stud|services|offer)\b", re.I)
FINISH_RE = re.compile(r"\b(thanks|thank you|bye|that'?s all|done|no more|finish)\b", re.I)
CONFIRM_RE = re.compile(
    r"^\s*(yes|y|confirm|book it|1|2|3|first|second|third|option\s?[123]|1st|2nd|3rd"
    r"|yes\s*[, ]\s*[123]|confirm\s*[123]?)\s*[.!]?\s*$", re.I)
VAGUE_RE = re.compile(r"\b(that thing|something|stuff|help( me)?\s*(with|on)?\s*(it|that|this)?\s*\??\s*$|can you help\?*\s*$)", re.I)
GREET_RE = re.compile(r"^(hi+|hello|hey|greetings|good (morning|afternoon|evening)|"
                      r"vanakkam|namaste)[.! ]*$", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _strategy(intent: str, has_q: bool, has_book: bool, vague: bool,
              tz_known: bool, email_known: bool) -> tuple[list[str], str, str, float, bool]:
    """FR-3.5 decision table with logged rationale."""
    if vague or (not has_q and not has_book and intent == "ambiguous"):
        return [], "clarify", "ambiguous between agents -> clarify, avoid mis-routing", 0.4, False
    if has_q and has_book:
        if not tz_known or not email_known:
            return ["search", "scheduler"], "sequence", \
                "question+booking -> sequence: answer first, then start scheduling in one reply", \
                0.85, False
        return ["search", "scheduler"], "sequence", \
            "question+booking -> sequence: answer may change visitor decision", 0.85, False
    if has_book:
        # Scheduler collects missing tz/email itself, one question at a time (AGENT.md §5.2)
        if not tz_known or not email_known:
            return ["scheduler"], "single", \
                "booking -> scheduler collects missing timezone/email first", 0.8, False
        return ["scheduler"], "single", "clear booking request", 0.9, False
    if has_q:
        qs = max(1, len(re.findall(r"\?", intent)))
        if qs >= 2:
            return ["search"], "parallel", "independent questions -> parallel search runs", 0.8, False
        return ["search"], "single", "clear knowledge question", 0.9, False
    return ["search"], "single", "default to search", 0.5, False


def _continuation(message: str, flags: dict) -> RouteDecision | None:
    """Deterministic booking-continuation rules shared by both routers.

    Runs before any LLM call so mid-flow messages (slot picks, emails, acks,
    typed times) can never be misread as clarify/finish. $0.
    """
    low = message.lower()
    if GREET_RE.match(message.strip()) and len(low.split()) <= 3:
        return RouteDecision(intent="greeting", confidence=0.95, targets=["greet"],
                             strategy="single", rationale="greeting -> welcome",
                             ended=False)
    if flags.get("sched_pending") and CONFIRM_RE.match(message):
        return RouteDecision(intent="booking", confidence=0.9, targets=["scheduler"],
                             strategy="single",
                             rationale="slot confirmation for proposed times", ended=False)
    if flags.get("sched_active") and (EMAIL_RE.search(message)
                                      or low.strip() in ("ok", "okay", "yes", "yeah",
                                                         "sure", "yep", "k")):
        return RouteDecision(intent="booking", confidence=0.85, targets=["scheduler"],
                             strategy="single",
                             rationale="booking continuation mid-scheduling", ended=False)
    return None


class FakeRouter:
    """Inject decisions per test; otherwise heuristic prior (DEV STAND-IN)."""

    def __init__(self) -> None:
        self.injected: dict[str, RouteDecision] = {}

    def inject(self, message: str, decision: RouteDecision) -> None:
        self.injected[message] = decision

    def decide(self, message: str, history: list[dict] | None = None,
               state_flags: dict | None = None) -> RouteDecision:
        if message in self.injected:
            return self.injected[message]
        flags = state_flags or {}
        cont = _continuation(message, flags)
        if cont is not None:
            return cont
        low = message.lower()
        if FINISH_RE.search(low) and len(low.split()) <= 6:
            return RouteDecision(intent="finish", confidence=0.9, targets=[],
                                 strategy="single",
                                 rationale="explicit finish", ended=True)
        vague = bool(VAGUE_RE.search(low)) or len(low.split()) <= 2 and not Q_RE.search(low) \
            and not BOOK_RE.search(low) and not TIME_CUE_RE.search(low)
        has_q, has_book = bool(Q_RE.search(low)), bool(BOOK_RE.search(low))
        if TIME_CUE_RE.search(low) and not has_q and not has_book:
            # A bare typed time ("tomorrow at 3pm") is a booking attempt; the
            # scheduler asks for the email and validates hours itself.
            return RouteDecision(intent="booking", confidence=0.7, targets=["scheduler"],
                                 strategy="single",
                                 rationale="typed date/time -> scheduler collects details",
                                 ended=False)
        if not has_q and not has_book:
            return RouteDecision(intent="ambiguous", confidence=0.4, targets=[],
                                 strategy="clarify",
                                 rationale="ambiguous between agents -> clarify", ended=False)
        intent = "booking" if has_book and not has_q else ("knowledge" if has_q and not has_book
                                                           else "multi")
        targets, strategy, rationale, conf, ended = _strategy(
            intent, has_q, has_book, vague,
            flags.get("tz_known", False), flags.get("email_known", False))
        if vague and strategy != "clarify" and conf >= 0.6:
            strategy, conf = "clarify", 0.45
            rationale = "vague message -> clarifying question, no wrong-agent call"
            targets = []
        if conf < 0.6 and strategy != "clarify":
            strategy, rationale, targets = "clarify", \
                f"low confidence {conf} -> clarify instead of guessing", []
        return RouteDecision(intent=intent, confidence=conf, targets=targets,
                             strategy=strategy, rationale=rationale, ended=ended)


class RealRouter:
    """Production router: one structured-output LLM call (small model, max_tokens 150).

    Falls back to the FakeRouter prior when the key/budget/API is unavailable —
    the turn degrades to heuristics instead of hanging (FR-8.4).
    """

    SYSTEM = ("Classify the visitor message for a company-website chatbot. Reply with JSON only: "
              '{"intent": "knowledge|booking|finish|multi|ambiguous|greeting", '
              '"confidence": 0-1, "targets": subset of ["search","scheduler"], '
              '"strategy": "single|sequence|parallel|clarify", '
              '"rationale": "one line", "ended": bool}. '
              "Rules: booking words (book/call/meeting/schedule) -> scheduler; "
              "questions -> search; both -> multi/sequence; vague or bare ack -> ambiguous/clarify; "
              "thanks/bye -> finish + ended=true; slot numbers (1,2,3,yes) with sched_pending=true "
              "-> booking/scheduler; an email address with sched_active=true -> booking/scheduler; "
              "a typed date/time (tomorrow 3pm, Monday 10:30) -> booking/scheduler.")

    def decide(self, message: str, history: list[dict] | None = None,
               state_flags: dict | None = None) -> RouteDecision:
        import sys
        sys.path.insert(0, "config")
        from settings import settings
        cont = _continuation(message, state_flags or {})
        if cont is not None:
            return cont
        try:
            from src.app.llm.client import chat_json, use_real
            if not use_real():
                raise RuntimeError("llm off")
            import json
            hist = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in (history or [])[-4:])
            raw = chat_json(agent="router", system=self.SYSTEM, max_tokens=150,
                            user=f"Flags: {state_flags}\nHistory:\n{hist}\nMessage: {message}")
            out = RouteDecision.model_validate(json.loads(raw))
        except Exception:
            out = FakeRouter().decide(message, history, state_flags)
            out.rationale += " [router-fallback]"
            return out
        if out.confidence < settings.route_conf_threshold and out.strategy != "clarify":
            out.strategy, out.targets = "clarify", []
            out.rationale = f"low confidence {out.confidence} -> clarify"
        return out


def get_router(mode: str = "fake"):
    return RealRouter() if mode == "real" else FakeRouter()
