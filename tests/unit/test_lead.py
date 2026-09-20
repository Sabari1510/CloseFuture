"""Phase-6 Lead-Summary tests (A6 + FR-6.x). $0 fake mailer."""
import time
from fastapi.testclient import TestClient
from src.app.agents.lead_store import LeadStore
from src.app.agents.lead_summary import score_signals, tier_of
from src.app.api import main as api
from src.app.jobs.sweep import sweep_idle
from src.app.mcp_servers.email_server import FakeEmail

INBOX = "sales@example.com"
BOOKED_MSGS = [
    {"role": "visitor", "content": "Hi, I'm Arun, CEO at BrightKart. Need a marketplace MVP like Dipy."},
    {"role": "assistant", "content": "Great ..."},
    {"role": "visitor", "content": "My work email is arun@brightkart.io. Timeline is next month, budget $8k."},
    {"role": "assistant", "content": "Here are 3 open times ..."},
    {"role": "visitor", "content": "Yes, option 1 please."},
]
THIN_MSGS = [{"role": "visitor", "content": "What services do you offer?"}]
BOOKING = {"status": "confirmed", "event_id": "abc", "meet_url": "https://meet.google.com/x"}


def test_a6_scoring_tiers_fr65():
    hot = {"booking_confirmed": True, "work_email_provided": True, "company_named": True,
           "need_matches_service": True, "timeline_within_3mo": True, "budget_signal": True,
           "four_plus_turns": True, "asked_pricing_or_cases": True, "decision_maker_signal": True}
    s, t = score_signals(hot)
    assert s >= 70 and t == "Hot"
    assert tier_of(50) == "Warm" and tier_of(10) == "Cold"


def test_a6_completed_and_abandoned_emails_fr64():
    store, mail = LeadStore(), FakeEmail()
    r1 = store.trigger(session_id="s1", messages=BOOKED_MSGS, booking=BOOKING,
                       incomplete=False, mailer=mail, sales_inbox=INBOX)
    assert r1["mailed"] and r1["kind"] == "initial" and r1["tier"] == "Hot"
    assert "INCOMPLETE" not in mail.sent[0].subject
    r2 = store.trigger(session_id="s2", messages=THIN_MSGS, booking=None,
                       incomplete=True, mailer=mail, sales_inbox=INBOX)
    assert r2["mailed"] and "[INCOMPLETE]" in mail.sent[1].subject
    body = mail.sent[0].body
    assert "arun@brightkart.io" in body and "/v1/sessions/s1/trace" in body  # FR-6.3


def test_a6_no_duplicate_update_threaded_fr66_67():
    store, mail = LeadStore(), FakeEmail()
    store.trigger(session_id="s1", messages=THIN_MSGS, booking=None,
                  incomplete=False, mailer=mail, sales_inbox=INBOX)
    again = store.trigger(session_id="s1", messages=THIN_MSGS, booking=None,
                          incomplete=False, mailer=mail, sales_inbox=INBOX)
    assert again == {"mailed": False, "reason": "unchanged"} and len(mail.sent) == 1
    more = THIN_MSGS + [{"role": "visitor", "content": "Also my email is sam@corp.io"}]
    upd = store.trigger(session_id="s1", messages=more, booking=None,
                        incomplete=False, mailer=mail, sales_inbox=INBOX)
    assert upd["mailed"] and upd["kind"] == "update"
    assert mail.sent[1].subject.startswith("[UPDATE]")
    assert mail.sent[1].thread_id == mail.sent[0].thread_id  # same thread
    assert len(store.sent_keys) == 2  # immutable send records


def test_a6_email_failure_queues_outbox_fr81():
    store, mail = LeadStore(), FakeEmail()
    mail.failures.append("UPSTREAM_5XX")
    r = store.trigger(session_id="s9", messages=THIN_MSGS, booking=None,
                      incomplete=False, mailer=mail, sales_inbox=INBOX)
    assert r == {"mailed": False, "reason": "queued", "version": 1}
    assert len(store.outbox) == 1 and not mail.sent
    assert store.drain_outbox(mail) == 1 and len(mail.sent) == 1  # lead never lost


def test_a6_sweeper_marks_incomplete():
    api.sales_inbox = INBOX
    client = TestClient(api.app)
    b = client.post("/v1/chat", json={"message": "Tell me about the Vigo app"}).json()
    api.store.set_last_activity(b["session_id"], time.time() - 99 * 60)
    swept = sweep_idle(api.store, api.leads, idle_minutes=20, mailer=api.mailer,
                       sales_inbox=INBOX)
    assert b["session_id"] in swept
    row = api.leads.summaries[b["session_id"]]
    assert row.content["incomplete"] is True
    assert api.store.sessions[b["session_id"]].status == "abandoned"


def test_a6_booking_sends_scored_email_score_hidden_fr76():
    api.sales_inbox = INBOX
    n0 = len(api.mailer.sent)
    client = TestClient(api.app)
    b1 = client.post("/v1/chat", json={"message": "Book a call", "timezone": "Asia/Dubai"}).json()
    b2 = client.post("/v1/chat", json={"message": "Book it, me@corp.io", "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": "Asia/Dubai"}).json()
    assert "open times" in b2["reply"]
    b3 = client.post("/v1/chat", json={"message": "yes 1", "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"],
                                       "timezone": "Asia/Dubai"}).json()
    assert "meet.google.com" in b3["reply"].lower()
    assert len(api.mailer.sent) == n0 + 1  # happy-path trigger (FR-3.4)
    assert "[INCOMPLETE]" not in api.mailer.sent[-1].subject
    assert "score" not in b3["reply"].lower() and "hot" not in b3["reply"].lower()
