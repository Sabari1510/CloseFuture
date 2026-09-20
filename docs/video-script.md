# CloseFuture Multi-Agent Chatbot — 8-Minute Video Script

> How to use: speak the **bold lines** verbatim-ish; *italic lines are stage
> directions (don't read them)*. Timings assume a calm pace (~130 words/min).
> Total: ~8 minutes.

---

## 0:00 — The problem (30s)

**Every company website has the same three leaks: visitors ask questions nobody
answers, booking a call takes five emails back and forth, and interested
visitors leave without a trace. I built a backend that fixes all three at
once: a multi-agent AI chatbot that answers from the company's real content,
books discovery calls on a real Google Calendar, and emails a scored lead
summary to sales — for every conversation, including the ones visitors abandon.**

*If recording a demo alongside: have the chat widget visible now.*

---

## 0:30 — What it is: five agents, one job each (1 min)

**The system is five specialised agents sharing one session state, orchestrated
by LangGraph:**

1. **Orchestrator** — classifies intent with a confidence score, routes, and
   owns the Guardrail checkpoint.
2. **Search** — answers only from retrieved company chunks, with citations.
3. **Scheduler** — proposes real slots, re-checks, and books with a Meet link.
4. **Lead-Summary** — scores the lead and emails sales, exactly once.
5. **Guardrail** — independently checks every inbound message and every
   outbound reply.

**The stack is Python, FastAPI, LangGraph plus LangChain components, Supabase
Postgres with pgvector, and real Google Calendar and Gmail — exposed as MCP
tools over the wire protocol, not stubs.**

---

## 1:30 — A conversation, end to end (1.5 min)

**Follow one message through the pipeline. A visitor types "book a call on
25 September at 3pm." First the inbound guardrail screens it. Then the router
classifies it — booking, high confidence — and the scheduler parses the typed
date, validates it against business hours, asks for the work email, re-checks
that exact slot, reserves it, creates the calendar event with a Meet link and
an attendee invite, and confirms. The reply the visitor sees passed the
outbound guardrail first. Every step — routing decision, agent call, tool
call, guardrail check — is logged to an event trace you can open per session.**

*Demo beat: show a booking reply with Meet link, then flash the trace view.*

---

## 3:00 — Knowledge without hallucination (45s)

**Company facts come only from the company's own published profile — seven
source documents, fifteen chunks, embedded once into pgvector with source,
section and category metadata. The search agent rewrites follow-ups using chat
history, retrieves top chunks, and a composer writes a short answer tailored
to the question, citing its sources. Ask "who is the founder" and you get one
line with a citation — not a biography dump. And when the answer isn't in the
chunks — prices we don't publish, guarantees, medical or hacking questions —
it declines honestly instead of guessing. I verified this live: per-hour rate
questions return our published 25-to-49-dollar band with a citation, and
contractual-guarantee questions get a clean decline.**

---

## 3:45 — Reliability: the unglamorous part that matters (1 min)

**Three behaviours I'm proudest of. First, the calendar can die and no lead is
lost: the bot collects contact details, flags manual follow-up, and tells the
visitor honestly what happened. Email failures queue in an outbox and retry —
never dropped. Second, races are impossible by construction: a per-session
lease serialises turns, state writes are compare-and-set on a version column,
and a database exclusion constraint plus deterministic event IDs make
double-booking a database-level impossibility — proven by a concurrent booking
test that always confirms exactly one. Third, visitors who vanish still convert:
close the tab mid-chat and a presence beacon starts a three-minute grace
clock, after which the sweeper mails an incomplete lead summary. Nothing rots
in silence.**

---

## 4:45 — Proof, not promises (45s)

**Numbers. Eighty-seven unit tests pass — including race and fault-injection
tests. An eighteen-question eval covering grounded answers and decline probes
scores eighteen out of eighteen. And I scripted the acceptance criteria into a
harness that boots a real backend three times — clean, fault-injected, clean —
and it passes eight out of eight: gap reload after twelve minutes, live
prompt-injection blocked, a simulated calendar outage retried then escalated
honestly, and recovery with a real booking. That drill caught a genuine bug my
unit tests couldn't see: the MCP subprocess wasn't inheriting OAuth secrets,
so real-Google calls failed — fixed by forwarding credentials explicitly.**

---

## 5:30 — Challenges I hit (1 min)

**Three honest war stories. One: "25 September at 3pm" booked the third
suggested slot — the digit in "3pm" matched the slot picker. Fixed by
stripping time expressions before pick detection. Two: "DECLINE" leaked raw to
a visitor as the entire reply — the composer emitted it with a period, dodging
the exact-match check. Fixed with tolerant matching plus a proper decline
branch — which also exposed that my old eighteen-out-of-eighteen was partly
bogus, since a leaked sentinel counted as answered. Three: published rates
were invisible to the model because excerpts were head-truncated and the rates
sat at chunk ends — fixed with head-plus-tail excerpts, which also forced me
to handle spaced-letter headings the PDF extraction produced. Each fix added a
regression test.**

---

## 6:30 — Cost and safety discipline (45s)

**This ran on roughly two dollars of API credit. Small models everywhere,
token caps on every call — router 150, answer 350 — top-k of four, a budget
ledger on every LLM call with soft and hard circuit breakers, rate limits in
front of everything, and fake backends so plumbing tests cost zero. On safety:
minimum personal data, visitor-scoped history, admin endpoints key-gated, and
the guardrail fails closed — if its judge errors, the reply is blocked, never
waved through.**

---

## 7:15 — Close (45s)

**So: five agents, one shared state, real calendar, real email, grounded
answers with citations, honest declines, no lost leads, no double bookings,
and a trace for every turn. What I'd build next: persisting the event trace
itself across restarts — today message history survives but traces don't —
and letting the router use the chunk category filter it currently ignores.
Thank you.**

*End card: repo URL / contact if allowed.*

---

### Appendix — numbers to keep straight if asked

- 5 agents · 15 chunks · 7 source docs · top-k 4
- 87 unit tests · eval 18/18 · scripted acceptance 8/8
- Business hours Mon–Fri 10:00–17:00 Asia/Kolkata · 4h notice · 14-day window
- Published rates $25–$49/hr · $1,000+ minimum · 4–6 week turnaround
- Budget: ~$2 test credit · LLM spend for the whole build: fractions of a cent
- Scoring: booking 35 · email/company/need/timeline/budget 10 each · Hot ≥70, Warm ≥40
