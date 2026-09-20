"""Static safe fallbacks — never LLM-generated (FR-7.4)."""
FALLBACKS = {
    "injection": ("I can't help with that request. I can answer questions about "
                  "CloseFuture's services and published work, or book you a call with the team."),
    "sensitive_data": ("I can't share private or sensitive data. If you leave your work email, "
                       "the team can follow up through the proper channel."),
    "abuse": ("Let's keep this professional. Tell me what you'd like to know about "
              "CloseFuture, or I can book you a call."),
    "ungrounded": ("I don't have that in our published company information, so I can't confirm it. "
                   "I can share what we do publish — or book you a call for specifics."),
    "commitment": ("I can't promise specific prices, timelines or outcomes here — those depend on scope. "
                   "Our published starting point is $25–49/hr with $1,000+ minimums. "
                   "Book a call and the team will scope yours properly."),
    "tone_or_leak": ("Let me rephrase that properly: how can I help with CloseFuture's services, "
                     "case studies, or booking a call?"),
    "unavailable": ("Something isn't working on my side right now — it isn't your fault. "
                    "I've noted your details and the team will contact you."),
}

GENERIC = FALLBACKS["ungrounded"]


def fallback_for(reason_codes: list[str]) -> str:
    for rc in reason_codes:
        if rc in FALLBACKS:
            return FALLBACKS[rc]
    return GENERIC
