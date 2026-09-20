# Decisions (ADRs) — extend per AGENT.md §19

## 2026-09-19 — Phase 0 skeleton
- Choice: Gmail API for lead mail (user), Supabase session-pooler for DB, backend-first then minimal widget in Phase 8.
- Fake LLM default (`LLM_MODE=fake`); real OpenAI `gpt-4o-mini` only for tiny smoke runs. Spend so far $0.
- Contracts in `src/app/contracts/agent_contracts.py`; retryable derived only from `error_map.py` (FR-8.2/8.3).
- Rate limits + 500-char cap + budget breaker run before any graph run; skeleton `/v1/chat` logs guardrail/route/state events.
- Next: fix `.env` names to `.env.example`, generate ADMIN_API_KEY, run migrations, then Phase 1 chunking experiment.

## 2026-09-19 — Phase 1 Search (A1 green, $0)
- Source docs: 7 section files from `closefuture-profile.pdf` via `scripts/extract_source.py`, no invented content (FR-1.1/1.2).
- Chunking B-450 wins (hit-rate 0.87 vs 0.80/0.87, 15 chunks); justified in `docs/chunking_justification.md` (FR-1.3).
- Retrieval: `InMemoryStore` lexical + stopword filter + category filter stands in for pgvector; same record shape/SQL filters for real embeddings in dev (FR-1.4–1.6).
- Search agent: skip rewrite when self-contained, history-aware otherwise (FR-4.1/4.5); top-k=4 floor 0.12 (FR-4.3); multi-doc answers with `[page › section]` citations (FR-4.2/4.6); decline when below floor (FR-4.4); confidence = 0.5·top1 + 0.3·mean + 0.2·answerable (FR-4.7).
- Eval: 18/18 correct (15 grounded + 3 declined); full suite 17 passed, ruff clean. Spend still $0.

## 2026-09-19 — Phase 2 session + serving (A2 green, $0)
- `InMemorySessionStore` implements lease/CAS/append-first with identical semantics to `PostgresSessionStore` SQL (FR-2.6); PG is source of truth in dev via `migrations/001_init.sql`.
- `/v1/chat` hydrates/writes state every turn (FR-2.2), returning visitor recognised by `visitor_id` + session reloaded after simulated 11-min gap (FR-2.3/2.5, A2).
- Visitor-scoped `GET /messages` (403 on mismatch; hijacked session forks fresh, no leak); turn cap + busy-lease replies; TTL cleanup via `jobs/cleanup.py` (FR-2.4).
- Suite 22 passed (A2 gap, hijack, concurrent CAS counter==2, TTL, turn cap), ruff clean. Live 2-turn check: 4 msgs restored, 10 events, version 0→2.

## 2026-09-19 — Phase 3 Guardrail (A7 green, $0)
- Independent `agents/guardrail/` module (rules_inbound/outbound + service + static fallback): rules first, fake judge only on borderline/lowconf, fail closed (FR-7.1–7.7).
- Inbound blocks 12 injections + 6 PII probes; 16 benign pass with 0 false positives. Outbound blocks 10 (uncited price/timeline/guarantee, score/routing/tool leaks, unsupplied PII, tone); cited published rates ($25–49/hr + citation) still allowed per commitments policy (FR-7.2).
- Wired in `/v1/chat` on both paths: inbound block → no agent call, static fallback, status blocked; outbound fail → substituted static fallback. Every check logs `guardrail_check` with reason_codes.
- Suite 30 passed, ruff clean, Search eval still 18/18. Spend still $0.

## 2026-09-19 — Phase 4 Orchestrator graph (A3+A4 green, $0)
- `agents/orchestrator/router.py`: FakeRouter (injectable + heuristic prior, DEV STAND-IN) for $0 edges; RealRouter (structured RouteDecision, max_tokens 150) is the production path via `LLM_MODE=real`. Confidence < 0.6 -> clarify (FR-3.6).
- `graph/builder.py` StateGraph(TurnState): guardrail_in -> route -> {search, scheduler-stub, lead_summary-stub, clarify} -> compose -> guardrail_out -> write_state; safe_fallback from guardrail_in; ended triggers lead_summary node (real send in Phase 6). recursion_limit 12.
- FR-3.5 table implemented with logged rationale; `/v1/chat` runs the graph per turn and persists route/strategy/agents_run via CAS.
- Guardrail fix: published business contacts present in chunks are public info, not PII leaks (FR-7.5).
- Suite 36 passed (A3 sequence + clarify-first + A4 + ended + structural + logged rationale), ruff clean, Search eval 18/18. Spend still $0.

## 2026-09-19 — Phase 5 Scheduler + Calendar MCP (A5 green fake, real pending token, $0)
- `mcp_servers/calendar_server.py`: FakeCalendar (exclusion, deterministic event IDs, idempotent 409, Meet URL shape) + RealCalendar (freebusy, insert w/ conferenceDataVersion=1 + sendUpdates=all, 409 resolve, patch/delete). `scripts/google_auth.py` ready for the one-time OAuth consent.
- `agents/scheduler.py`: business-hours slotting (Mon–Fri 10–17 owner TZ, 30m, 14d, 4h notice), visitor-TZ rendering, one-question-at-a-time email/slot collection, re-check → reserve → create, reschedule/cancel on stored event ID, calendar-down manual-followup fallback.
- Router/guardrail fixes: booking routes to scheduler (it collects details itself); slot confirmations ("yes, 1") route via `sched_pending`; tool-generated dates exempt from chunk-grounding; previously-supplied email counts for PII check.
- A5 evidence (fake): 3 slots, re-check logged before create_event, Meet link + invite, race -> exactly 1 winner + 1 retry, cancel works, API 3-turn booking green. Suite 42 passed, ruff clean, eval 18/18. Real event + invite demo needs GOOGLE_REFRESH_TOKEN.

## 2026-09-19 — Phase 6 Lead-Summary + Email MCP + sweeper (A6 green, $0)
- `mcp_servers/email_server.py`: FakeEmail (thread + idempotency dedupe) + RealGmail (MIME send, In-Reply-To threading). `agents/lead_summary.py`: transcript-extracted signals, deterministic scoring.yaml weights (Hot≥70/Warm≥40/Cold), narrative template ($0; real LLM only for narrative in dev), FR-6.3 body incl. trace link + manual_followup flag.
- `agents/lead_store.py`: one row/session, content-hash no-op, versioned [UPDATE] in same thread, immutable sent_keys, outbox queue + drain worker (FR-6.6/6.7, FR-8.1). `jobs/sweep.py`: idle≥threshold + visitor messages -> [INCOMPLETE], session abandoned. Graph lead node sends on ended/booking; score never enters visitor reply (FR-7.6).
- A6 evidence: Hot booking mail + INCOMPLETE thin mail, repeat trigger no-op, changed content threaded update, outage queued then drained, sweeper end-to-end, booking 3-turn mail + hidden score. Suite 48 passed, ruff clean, eval 18/18. Real inbox delivery needs Gmail OAuth (same google_auth.py) + SALES_INBOX.

## 2026-09-19 — Phase 7 reliability (A8 green, $0)
- `reliability/mcp_call.py`: RetryingCalendar/RetryingMailer proxies — classify via error map, retry retryable max 2 (FAULT=calendar:503:2 / email:503:always supported), AgentError-shaped failures, `retry` event per attempt + final outcome (succeeded_after_N / failed_fell_back / failed_gave_up), read-through to wrapped backend.
- Scheduler failures surface honest manual-followup replies + `fallback` events with error codes; email outage queues outbox + fallback event, visitor reply unaffected; budget breaker + turn/busy paths unchanged.
- A8 evidence: 503x2 recovered+booked with retry trail, 401 single-attempt honest fallback, email outage queued+drained, error shape exact. Suite 52 passed, ruff clean, eval 18/18.
- Test hygiene: `tests/conftest.py` resets process-global rate maps per test (suite shares one client IP; production limits untouched).

## 2026-09-19 — Phase 8 proof + hardening + widget (project complete at $0)
- A9: `scripts/export_trace.py` runs question -> multi-intent booking -> Meet confirm -> finish goodbye, exporting `traces/<session>.jsonl` + readable `.md`; `test_trace.py` asserts all 7 event types in seq order. Fixed finish routing (`done` node; empty-target finish no longer falls into clarify).
- Hardening: pins aligned to verified set (langgraph 1.2.11), unused deps dropped (mcp SDK, text-splitters, pgvector-py, multipart), `google-auth-oauthlib` added; secrets scan clean (only `re_*` false hits); RLS + immutability triggers in migration; admin-gated trace/sweep; rate/length/turn/budget gates live.
- `frontend/` widget (explicitly requested): static index/app/styles, localStorage visitor+session, browser TZ sent, restore via visitor-scoped endpoint, status-aware replies. No secrets in frontend.
- `docs/demo_runbook.md` covers A1–A9. Suite 53 passed, ruff clean, eval 18/18, ledger $0.00 of $2.00.
- Still needs human keys for live demo: rewrite `.env` from `.env.example` (current names malformed), `ADMIN_API_KEY`, `SALES_INBOX`, `GOOGLE_REFRESH_TOKEN` (google_auth.py), provider hard spend cap; then `make migrate`, real-calendar A5 + real-inbox A6 rehearsal.

## 2026-09-19 — Booking-continuation routing fix (user-reported, $0)
- Bare email / "ok" mid-scheduling fell to clarify because the router saw only the current message. Added `sched_active` flag (sched stage collect/proposed): email or ack while scheduling routes to Scheduler. Regression test replays the exact reported transcript. Suite 54 passed, ruff clean.

## 2026-09-19 — Retrieval quality fixes from live transcript (user-reported, $0)
- Rates answer showed markets prose: no stemming ("rates"≠"rate") + sentence rank by raw count. Added plural stemming, IDF-weighted chunk + sentence scoring, sentence dedup across overlapping chunks. Rates now leads with $25–49/hr, $1,000+.
- Liya multi-intent answered founder bio: booking words ("book a call") outvoted "Liya". Search now strips the booking clause when the router co-targets the scheduler.
- Floor recalibrated 0.12 → 0.29 (grounded ≥ 0.32, probes ≤ 0.26, measured on eval set).
- Sequence compose caps the answer half at ~500 chars so the booking CTA always fits.
- Frontend: ok-status no longer renders a stray "ok" badge.
- Suite 57 passed, ruff clean, eval 18/18.

## 2026-09-19 — Slot-choice fix (user-reported, $0)
- Confirm step always booked `slots[0]` ("yes, 2" booked slot 1; bare "2" wasn't even a confirm). Added `parse_choice`: digits, ordinals, "option/slot N"; out-of-range re-asks; plain yes confirms slot 1. Suite 59 passed, ruff clean, eval 18/18.

## 2026-09-19 — Real Google wiring (user OAuth done)
- `api/main.py` picks RealCalendar/RealGmail when `GOOGLE_REFRESH_TOKEN` is set, fakes otherwise; `/healthz` reports which (`google: real|fake`).
- Tests pinned to fakes via conftest (immune to `.env` credentials) + bookings reset per test after shared-state flakiness.

## 2026-09-19 — Real-ification wave (user: all fakes -> real, spend < $1)- Real LLM layer (`llm/client.py`): bounded chat/JSON/embed calls, shared cost ledger, hard-budget gate; every site falls back to deterministic fake on failure. Wired: RealRouter (JSON mode, fake-prior fallback), LLM rewrite (60 tok), LLM guardrail judge (100 tok, fail closed), LLM lead narrative (200 tok, score stays deterministic). Extractive answers kept deliberately for citation fidelity (FR-4.2).
- Postgres sessions live when DB reachable (quoted DSN handles `@` in password; migrations applied); tests pinned to memory.
- Retry backoff real in prod, no-op in tests. Session state/sweeper use backend-agnostic accessors.
- BLOCKED: OpenAI key exhausted (429 no-credits) — embeddings + LLM flip wait for the $4 key. Budgets tightened per user: TOTAL 4.00 / SOFT 0.60 / HARD 1.00; LLM_MODE stays fake until key lands. SALES_INBOX + EMAIL_FROM set; ADMIN_API_KEY generated.
- Suite 59 passed, ruff clean, eval 18/18, spend $0.00.
- pgvector retrieval wired (`pg_store.py` + loader auto-select, lexical fallback armed, 3 fallback tests). `scripts/embed.py` still needs a funded key to fill the vectors. Suite 62 passed, eval 18/18.

## 2026-09-19 — Live key: real mode on, spend $0.0007 (cap $1.00)
- Key validated (models.list, $0), `LLM_MODE=real`, migration 002 (chunk slug), 15 embeddings upserted (~$0.0001).
- L3 smoke (`scripts/live_smoke.py`, side-effect-free prompts): real router + pgvector + rewrite + guardrail all correct, 8/8 as expected, total $0.000694 — ledger persisted to `llm_usage`.
- Suite 62 passed (fake-forced), ruff clean, eval 18/18. Remaining budget: ~$0.999 of the $1 cap; ~$3.999 of the key.

## 2026-09-19 — Slot re-offer guard (user-reported, $0)
- Booked slots are excluded via calendar freebusy, plus now a second layer: the session's own confirmed holds are force-added to busy even if freebusy lags. Regression test books slot 2 then proves it never reappears. Suite 63 passed.

## 2026-09-19 — Typed date/time booking (user request, $0)
- `parse_preferred` understands today/tomorrow/weekdays/next-, month dates, numeric dates, am/pm + 24h times, morning/afternoon/evening; visitor TZ in, owner TZ validated (weekday, 10–17, 4h notice, 14d window) with plain-language redirects; day-without-time asks once (pending date kept in sched state).
- Books through the same re-check → reserve → create path. Router treats datetime cues as booking continuation mid-flow. Suite 66 passed, eval 18/18.

## 2026-09-19 — Booking-flow robustness (user transcript, $0)
- Typed time before email is stashed (`preferred_start`) and booked when the email arrives instead of forgotten.
- Booking-continuation rules now run before the LLM router call in BOTH routers, so mid-flow messages can't be misread.
- Bare typed times ("tomorrow at 3pm") route to the scheduler even as first messages.
- Typed times beat number-picking ("today 9pm" ≠ slot 9); out-of-hours/weekend gets an explanation, never a generic clarify. Suite 70 passed, eval 18/18.

## 2026-09-19 — Time-vs-pick disambiguation (user screenshot, $0)
- "25 september at 3.pm" booked slot #3: the digit in "3.pm" matched the slot-pick pattern. Time expressions are now stripped before pick detection; dotted times ("3.pm", "9.00 pm") parse as pm. Regression test books Sep 25 3pm exactly. Suite 71 passed, eval 18/18.

## 2026-09-19 — Goodbye + mail + spend fixes (user-reported, ~$0)
- Router-set `ended` with live targets skipped the agents (email → goodbye, no booking). `_after_route` now runs the targets first; `done` only when nothing is left to do.
- No lead mail: Gmail API was never enabled in the Google project (403). Existing token already carries the scope — enabling the API is enough.
- pgvector NULL-category crash fell back silently to lexical; founder/founded stemming gap fixed in the shared normalizer. `cost_report.py` reads the DB ledger.

## 2026-09-19 — Greeting + dotted dates + tailored answers (user feedback, ~$0.0003)
- Dotted numeric dates (`30.09.2026` DD.MM.YYYY); invalid fragments no longer kill time parsing ("9.00 pm" safe).
- `hi/hello/hey` → dedicated welcome (capabilities + one question), deterministic in both routers, new graph node.
- Real-mode answers are now LLM-tailored to the question from retrieved chunks only (≤120 words, citations kept); extractive fallback on failure/fake mode. Founder query → one line, not a biography dump. Suite 74 passed, eval 18/18.

## 2026-09-19 — DECLINE leak + hidden rates (user screenshot, ~$0.01)
- `DECLINE.` (punctuated) leaked raw to visitors; the old 18/18 was partly bogus (sentinel counted as an answer). Tolerant sentinel + explicit content-decline branch → honest decline text, never leaked.
- Root causes found in the data: rates ($25–$49/hr, $1,000+ min) live at chunk ENDS → head-only 1200-char excerpts hid them (now head+tail trim); `M A R K E T S` spaced headings invisible to lexical search (de-space in shared `_toks`); `serving`≠`serve` tie lost to flagship chunks (verb synonyms). Verified live: rate/markets/Galaxy answers correct + cited. Suite 76 passed, eval 18/18.

## 2026-09-19 — Auto-sweep (user request, $0)
- Background asyncio loop in backend lifespan runs the full sweep cycle (TTL + idle + outbox) every `SWEEP_INTERVAL_MIN` (default 5, 0 = off); single-worker safe, never crashes the server. Status at `GET /internal/sweep/status` (admin-gated); frontend Admin panel shows cadence + last run + result with refresh. Suite 67 passed.

## 2026-09-19 — Process lesson: no live-side-effect debug scripts
- My dbg scripts ran TestClient against REAL calendar backends and created 6 junk events on the user's calendar (found via created-timestamps audit). Deleted all 6. Rule: debugging scripts must pin fakes (like conftest) or use read-only calls; booking-capable repros go through the fake-backed unit suite only.

## 2026-09-19 - Public/admin UI split + End-chat (user review, \)
- Visitor page (index.html) shows chat only + New/End chat; session IDs, API URL, key, trace/sweep moved to admin.html (endpoints already key-gated; now also out of sight). Shared app.js guards per-page elements.
- Early forced email/company collection REJECTED (FR-7.5 minimisation, one-question-at-a-time). Instead POST /v1/sessions/{sid}/end: no email known -> asks once (need-email, inline capture row); else marks ended + triggers the complete summary. Idempotent; abandoned sessions still go out INCOMPLETE via sweeper. Suite 78 passed, eval 18/18.

## 2026-09-19 - Presence sweep + status pill + UI refresh (user request, \ backend)
- POST /v1/sessions/{sid}/presence {away|here} (visitor-scoped, \ beacon on tab hide/close, here on return); any chat message also clears away. sweep_idle sweeps away-past-grace (AWAY_GRACE_MINUTES=3) as INCOMPLETE even before the 20-min idle mark. Ended sessions skipped.
- Conn dot -> Online/Offline/Checking pill: 15s poll (5s timeout) + browser online/offline events + send success/fail; tooltip explains CORS when unreachable.
- Full styles.css refresh (indigo theme, chips, badges, admin cards, responsive); public + admin pages share guarded app.js. Suite 81 passed, eval 18/18, node --check clean.

## 2026-09-19 - Session discovery for company side (user question, \)
- Admin could only inspect known IDs. Added GET /internal/sessions (key-gated, newest-first, flags only, no content/PII) backed by list_sessions() on both stores; admin Conversations panel renders a table with per-row Open (loads chat for reading). Manual Refresh (no auto-poll, keeps admin cheap). Suite 82 passed, eval 18/18.

## 2026-09-20 - FR-3.3 MCP wire protocol + FR-3.7 turn deadline (user request, \ transport)
- Calendar/email actions now real MCP SDK (FastMCP) stdio servers; sync client bridge (background loop thread, 8s tool timeout) with identical method shapes so proxies/agents unchanged. CLOSEFUTURE_MCP_BACKEND=fake|real; direct classes remain as startup-fallback only. Fixed envelope collision (result vs domain status).
- Whole-turn deadline enforced: asyncio.wait_for around graph invoke (TURN_DEADLINE_S); timeout -> honest degraded reply, lease released, orphaned run writes no state (late side effects stay idempotent). Suite 87 passed, eval 18/18.

## 2026-09-20 - Acceptance run 8/8 + MCP creds fix (user request)
- scripts/acceptance_run.py boots its own backend 3x (clean/fault/clean): 12-min backdated gap reload, live injection + PII blocks, FAULT=calendar:503 outage with honest manual-followup + retry trace, clean recovery booking. Artifacts under traces/acceptance/<ts>.
- Real bug found by the drill: MCP server subprocess missed OAuth secrets (.env is not os.environ) so real-backend calls failed as UPSTREAM_5XX; fixed by forwarding GOOGLE_* via env_extra. Unique-per-run slot hours stop rerun collisions.
- Suite 87 passed, eval 18/18.

## 2026-09-20 - Month-first dates (user transcript, \)
- september 28 wrongly got weekdays-only: parse_preferred and the cue regex only knew day-first (25 september) and numeric, so month-first fell back to today (a Sunday). Added month-first branch + cue with shared MONTH_RE. Regression tests book Mon 28 Sep exactly. Suite 89 passed, eval 18/18.

## 2026-09-20 - Reschedule patches same event (user transcript, \)
- Three bugs: reschedule-with-time booked a duplicate event, plain cancel killed the oldest booking, cancelling totally matched no cancel path. Fix: same-message time moves via reschedule_event (FR-5.8); pending-move flag across turns (persisted in sched state); cancel targets latest, cancel-all phrase cancels everything. 4 regression tests. Suite 93 passed, eval 18/18.
