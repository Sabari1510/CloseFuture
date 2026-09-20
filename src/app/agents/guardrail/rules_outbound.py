"""Deterministic outbound rules (FR-7.2, FR-7.5, FR-7.6)."""
import re

CURRENCY_RE = re.compile(r"(\$|₹|USD|INR)\s?\d[\d,]*| \d[\d,]*\s?(dollars|rupees)", re.I)
TIMELINE_RE = re.compile(r"\b(\d+\s?(weeks?|days?|months?)|guarantee[sd]?|promised?|contractual|penalt\w*|100%\s?(sure|guarantee))\b", re.I)
LEAK_RES = [
    re.compile(r"lead[\s_-]?score", re.I),
    re.compile(r"\btier\s*[:=]", re.I),
    re.compile(r"\b(hot|warm|cold)\s+lead\b", re.I),
    re.compile(r"route_decision|routing\s+reason", re.I),
    re.compile(r"\bintent\s*[:=]", re.I),
    re.compile(r"tool_call|tool\s+name|create_event|send_lead_summary", re.I),
    re.compile(r"system\s+prompt", re.I),
    re.compile(r"\b(gpt-|langgraph|langchain|trace_id|session state)\b", re.I),
]
TONE_RES = [
    re.compile(r"\b(you('re| are) (wrong|stupid|an idiot)|shut up|idiot|moron)\b", re.I),
    re.compile(r"\b(i['’]m (not )?a (human|bot)|as an ai\b.{0,20}human)\b", re.I),
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
PROFANITY = frozenset("fuck shit bitch damn crap hell".split())


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d[\d,.]*", s))


def check_outbound_rules(reply: str, *, chunks_text: str = "",
                         citations: list | None = None,
                         confidence: float | None = None,
                         visitor_supplied: str = "",
                         tool_grounded: bool = False) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """Return (checks, reason_codes). 'borderline'/'lowconf' means call the judge."""
    checks: list[tuple[str, str, bool]] = []
    reasons: list[str] = []
    for rx in LEAK_RES:
        if rx.search(reply):
            checks.append(("leak", f"internal token {rx.pattern[:30]}", False))
            reasons.append("tone_or_leak")
            return checks, reasons
    for rx in TONE_RES:
        if rx.search(reply):
            checks.append(("tone", f"tone pattern {rx.pattern[:30]}", False))
            reasons.append("tone_or_leak")
            return checks, reasons
    words = set(re.findall(r"[a-z]+", reply.lower()))
    if words & PROFANITY:
        checks.append(("profanity", "profanity term", False))
        reasons.append("tone_or_leak")
        return checks, reasons
    if reply.isupper() and len(reply) > 40:
        checks.append(("shouting", "all caps reply", False))
        reasons.append("tone_or_leak")
        return checks, reasons
    # PII the visitor didn't supply and isn't published info (FR-7.5).
    # Published business contacts (present in retrieved chunks) are public, not leaks.
    for m in EMAIL_RE.findall(reply) + PHONE_RE.findall(reply):
        if m.strip() in (visitor_supplied or ""):
            continue
        if chunks_text and m.strip() in chunks_text:
            continue
        checks.append(("pii_exposure", f"unsupplied contact {m[:12]}…", False))
        reasons.append("sensitive_data")
        return checks, reasons
    # Commitments: figures allowed only when cited from chunks (FR-7.2)
    has_money = bool(CURRENCY_RE.search(reply))
    has_time = bool(TIMELINE_RE.search(reply))
    if has_money or has_time:
        cited_ok = False
        if citations and chunks_text:
            for num in _numbers(reply):
                if num.replace(",", "") in chunks_text.replace(",", ""):
                    cited_ok = True
                    break
        if not cited_ok:
            checks.append(("commitment", "uncited price/timeline/guarantee", False))
            reasons.append("commitment")
            return checks, reasons
        checks.append(("commitment_cited", "figure grounded in chunk + citation", True))
    # Grounding: factual numbers must appear in chunks/state (FR-7.2).
    # Tool-generated data (calendar slots, Meet links) is grounded by the tool call.
    if _numbers(reply) and chunks_text and not tool_grounded:
        alien = [n for n in _numbers(reply)
                 if n.replace(",", "") not in chunks_text.replace(",", "")]
        if alien and not citations:
            checks.append(("ungrounded", f"numbers not in chunks {alien[:3]}", False))
            reasons.append("ungrounded")
            return checks, reasons
    if (confidence is not None and confidence < 0.35
            and (has_money or has_time or _numbers(reply))):
        checks.append(("lowconf_claim", f"conf {confidence} with factual claim", True))
        reasons.append("lowconf")
        return checks, reasons
    checks.append(("outbound_clean", "no rule hit", True))
    return checks, []
