"""Deterministic inbound rules (FR-7.3, FR-7.5). Runs before any LLM judge."""
import base64
import re

INJECTION_RES = [
    re.compile(r"ignore\s+(all\s+|any\s+|previous\s+|prior\s+|above\s+|your\s+)*instructions", re.I),
    re.compile(r"reveal\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"show\s+(me\s+)?your\s+(system\s+)?(prompt|instructions|rules)", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"pretend\s+(you\s+are|to\s+be)", re.I),
    re.compile(r"act\s+as\s+(a\s+)?(system|admin|developer|root|jailbreak|dan)\b", re.I),
    re.compile(r"\b(dan\s+mode|jailbreak|bypass\s+(safety|guardrail|filter))\b", re.I),
    re.compile(r"disregard\s+.*(policy|safety|rules|instructions)", re.I),
    re.compile(r"execute\s+(this|the)\s+(code|command|sql|query)", re.I),
    re.compile(r"run\s+(arbitrary|this)\s+code", re.I),
]

SENSITIVE_RES = [
    re.compile(r"\b(password|passwd|api[\s_-]?key|secret[\s_-]?key|client[\s_-]?secret)\b", re.I),
    re.compile(r"\b(credit[\s_-]?card|ssn|social[\s_-]?security|bank[\s_-]?account)\b", re.I),
    re.compile(r"(give|share|send|tell|show)\s+me\s+(someone|another|other|all|any).*?(email|phone|address|number|data)", re.I),
    re.compile(r"(another|other|someone|somebody|else|visitor|customer).{0,40}(email|phone|address|number)", re.I),
    re.compile(r"\b(leak|dump|export)\b.*\b(database|users|customers|leads)\b", re.I),
]

ABUSE_WORDS = frozenset("hate stupid idiot dumb shutup kill attack threaten".split())


def _long_base64_blob(s: str) -> bool:
    for tok in re.findall(r"[A-Za-z0-9+/=]{80,}", s):
        try:
            base64.b64decode(tok, validate=True)
            return True
        except Exception:
            continue
    return False


def check_inbound_rules(message: str) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """Return (checks, reason_codes). reason 'borderline' means call the judge."""
    checks: list[tuple[str, str, bool]] = []
    reasons: list[str] = []
    for rx in INJECTION_RES:
        if rx.search(message):
            checks.append(("injection_pattern", f"matched {rx.pattern[:40]}", False))
            reasons.append("injection")
            return checks, reasons
    for rx in SENSITIVE_RES:
        if rx.search(message):
            checks.append(("sensitive_request", f"matched {rx.pattern[:40]}", False))
            reasons.append("sensitive_data")
            return checks, reasons
    if _long_base64_blob(message):
        checks.append(("encoded_blob", "long base64 blob", False))
        reasons.append("injection")
        return checks, reasons
    words = re.findall(r"[a-z]+", message.lower())
    abuse = [w for w in words if w in ABUSE_WORDS]
    if abuse:
        if len(abuse) >= 2 or "!" in message or message.isupper() and len(message) > 20:
            checks.append(("abuse", f"abusive terms {abuse[:3]}", False))
            reasons.append("abuse")
            return checks, reasons
        checks.append(("abuse_weak", "single weak abuse term", True))
        reasons.append("borderline")
        return checks, reasons
    checks.append(("inbound_clean", "no rule hit", True))
    return checks, []
