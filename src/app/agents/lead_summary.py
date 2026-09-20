"""Lead-Summary agent: structured summary + deterministic score + send (FR-6.1–6.7).

Score is config-driven from scoring.yaml; the LLM writes only the narrative
(fake template at $0, real small model in dev). One row per session, content
hash dedupe, threaded [UPDATE] on change, [INCOMPLETE] for abandoned, immutable
send record, outbox queue on email failure (FR-8.1). Never shown to visitor.
"""
import hashlib
import json
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FREE_MAIL = re.compile(r"@(gmail|yahoo|hotmail|outlook|icloud)\.", re.I)
COMPANY_RE = re.compile(r"\b(?:at|from|with|for)\s+([A-Z][A-Za-z0-9&.\- ]{1,40}?)(?:\s*[,.]|$)", re.I)
SERVICE_WORDS = ("bubble", "flutterflow", "framer", "supabase", "marketplace", "mvp",
                 "mobile app", "web app", "ai", "automation", "dashboard", "booking")
TIMELINE_RE = re.compile(r"\b(urgent|asap|this month|next month|\d+\s?(weeks?|months?)|q[1-4])\b", re.I)
BUDGET_RE = re.compile(r"\b(budget|\$\s?\d|k\s?(usd|dollars)?|price|cost|afford)\b", re.I)
ROLE_RE = re.compile(r"\b(ceo|cto|founder|co-founder|manager|director|vp|head of|lead)\b", re.I)
PRICE_CASE_RE = re.compile(r"\b(pric|rates?|cost|case stud|portfolio|reviews?|rating)\b", re.I)

WEIGHTS = {"booking_confirmed": 35, "work_email_provided": 10, "company_named": 10,
           "need_matches_service": 10, "timeline_within_3mo": 10, "budget_signal": 10,
           "four_plus_turns": 5, "asked_pricing_or_cases": 5, "decision_maker_signal": 5}


def tier_of(score: int) -> str:
    return "Hot" if score >= 70 else ("Warm" if score >= 40 else "Cold")


def extract_signals(messages: list[dict], booking: dict | None) -> dict:
    visitor_text = " ".join(m.get("content", "") for m in messages
                            if m.get("role") == "visitor")
    turns = sum(1 for m in messages if m.get("role") == "visitor")
    email_m = EMAIL_RE.search(visitor_text)
    email = email_m.group(0) if email_m else None
    comp_m = COMPANY_RE.search(visitor_text)
    low = visitor_text.lower()
    signals = {
        "booking_confirmed": bool(booking and booking.get("status") == "confirmed"),
        "work_email_provided": bool(email and not FREE_MAIL.search(email)),
        "company_named": bool(comp_m),
        "need_matches_service": any(w in low for w in SERVICE_WORDS),
        "timeline_within_3mo": bool(TIMELINE_RE.search(visitor_text)),
        "budget_signal": bool(BUDGET_RE.search(visitor_text)),
        "four_plus_turns": turns >= 4,
        "asked_pricing_or_cases": bool(PRICE_CASE_RE.search(visitor_text)),
        "decision_maker_signal": bool(ROLE_RE.search(visitor_text)),
    }
    info = {"email": email,
            "company": comp_m.group(1).strip() if comp_m else None,
            "turns": turns,
            "key_questions": [m.get("content", "")[:160] for m in messages
                              if m.get("role") == "visitor"][-6:]}
    return {"signals": signals, "info": info}


def score_signals(signals: dict) -> tuple[int, str]:
    score = sum(WEIGHTS[k] for k, v in signals.items() if v)
    return score, tier_of(score)


def _narrative(questions: list[str], signals: dict, score: int, tier: str) -> str:
    base = (f"Visitor asked {len(questions)} question(s), most recently: "
            f"{(questions or ['—'])[-1][:140]}. "
            f"Signals hit: {', '.join(k for k, v in signals.items() if v) or 'none'}.")
    try:  # LLM writes only the narrative; score stays deterministic (FR-6.5)
        from src.app.llm.client import chat, use_real
        if use_real():
            out = chat(agent="lead_summary",
                       system="Write a 2-sentence sales handover note from these facts. "
                              "Plain text, no scores, no invented details.",
                       user=f"Questions: {questions[-4:]}\nSignals: "
                            f"{[k for k, v in signals.items() if v]}",
                       max_tokens=200)
            if out:
                return out
    except Exception:
        pass
    return base


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def build_content(*, session_id: str, messages: list[dict], booking: dict | None,
                  incomplete: bool, company: str = "CloseFuture") -> dict:
    """Full summary content incl. trace link (FR-6.3). Score derived, narrative templated."""
    ext = extract_signals(messages, booking)
    score, tier = score_signals(ext["signals"])
    narrative = _narrative(ext["info"]["key_questions"], ext["signals"], score, tier)
    return {"session_id": session_id, "visitor": ext["info"], "signals": ext["signals"],
            "meeting": booking or None, "score": score, "tier": tier,
            "incomplete": incomplete, "narrative": narrative,
            "trace": f"/v1/sessions/{session_id}/trace",
            "company": company, "manual_followup": bool((booking or {}).get("manual_followup"))}


def subject_for(content: dict, kind: str) -> str:
    base = f"[{content['tier']}] New lead ({content['score']}) — {company_of(content)}"
    if content.get("incomplete"):
        base = "[INCOMPLETE] " + base
    if kind == "update":
        base = "[UPDATE] " + base
    return base


def company_of(content: dict) -> str:
    return content.get("visitor", {}).get("company") or content.get("visitor", {}).get("email") \
        or content["session_id"][:8]


def render_body(content: dict) -> str:
    lines = [f"Lead summary ({'INCOMPLETE — visitor went quiet' if content['incomplete'] else 'complete'})",
             f"Score: {content['score']} ({content['tier']})",
             f"Visitor: {content['visitor'].get('email') or 'no email'} | "
             f"Company: {content['visitor'].get('company') or 'unknown'}",
             f"Signals: {', '.join(k for k, v in content['signals'].items() if v) or 'none'}",
             f"Meeting: {content['meeting'] or 'none'}",
             f"Trace: {content['trace']}", "", "Key questions:"]
    lines += [f"- {q}" for q in content["visitor"]["key_questions"]]
    lines += ["", content["narrative"]]
    if content.get("manual_followup"):
        lines.append("NOTE: manual_followup=true — a tool failure needs human action.")
    return "\n".join(lines)
