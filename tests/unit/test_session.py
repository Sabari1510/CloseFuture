"""Phase-2 session tests (A2 + FR-2.x). $0, in-memory store + API."""
import asyncio
import time
from fastapi.testclient import TestClient
from src.app.api import main as api
from src.app.db.session_store import InMemorySessionStore
from src.app.jobs.cleanup import cleanup

client = TestClient(api.app)


def test_a2_state_survives_gap_fr23():
    """A2: message, simulate 11-min gap, return -> same session, context kept."""
    r1 = client.post("/v1/chat", json={"message": "Tell me about the Dipy marketplace"})
    b1 = r1.json()
    assert b1["status"] == "ok" and "dipy" in b1["reply"].lower()
    # no real waiting: backdate activity, keep state
    api.store.set_last_activity(b1["session_id"], time.time() - 11 * 60)
    r2 = client.post("/v1/chat", json={"message": "What languages does it support?",
                                       "session_id": b1["session_id"],
                                       "visitor_id": b1["visitor_id"]})
    b2 = r2.json()
    assert b2["session_id"] == b1["session_id"], "must reload, not restart (FR-2.3)"
    assert b2["status"] == "ok"


def test_visitor_scoped_restore_fr25():
    r = client.post("/v1/chat", json={"message": "Who founded CloseFuture?"})
    b = r.json()
    ok = client.get(f"/v1/sessions/{b['session_id']}/messages",
                    params={"visitor_id": b["visitor_id"]})
    assert len(ok.json()["messages"]) >= 2
    bad = client.get(f"/v1/sessions/{b['session_id']}/messages",
                     params={"visitor_id": "someone-else"})
    assert bad.status_code == 403
    # hijacked session_id with different visitor -> fresh session, no leak
    r2 = client.post("/v1/chat", json={"message": "hi", "session_id": b["session_id"],
                                       "visitor_id": "attacker"})
    assert r2.json()["session_id"] != b["session_id"]


def test_concurrent_messages_consistent_fr26():
    """Two messages, one session: nothing lost, state consistent."""
    s = InMemorySessionStore()
    vid, _ = s.ensure_visitor(None)
    sid, _ = s.ensure_session(None, vid)

    async def turn(i: int) -> None:
        owner = f"o{i}"
        while not s.acquire_lease(sid, owner, 5):
            await asyncio.sleep(0.01)
        try:
            s.append_message(sid, "visitor", f"msg-{i}", f"t{i}")
            st, ver = s.load(sid)
            st["counter"] = st.get("counter", 0) + 1
            s.save_cas(sid, st, ver)
        finally:
            s.release_lease(sid, owner)

    async def run() -> None:
        await asyncio.gather(turn(1), turn(2))
    asyncio.run(run())
    st, _ = s.load(sid)
    assert st["counter"] == 2
    assert len(s.get_messages(sid)) == 2


def test_ttl_cleanup_fr24():
    s = InMemorySessionStore()
    vid, _ = s.ensure_visitor(None)
    sid, _ = s.ensure_session(None, vid)
    s.set_last_activity(sid, time.time() - 31 * 86400)
    assert cleanup(s, ttl_days=30) == 1
    assert sid not in s.sessions


def test_turn_cap_ends_gracefully():
    r = client.post("/v1/chat", json={"message": "Who founded CloseFuture?"})
    b = r.json()
    api.store.sessions[b["session_id"]].turn_count = 9999
    r2 = client.post("/v1/chat", json={"message": "hi", "session_id": b["session_id"],
                                       "visitor_id": b["visitor_id"]})
    assert "end of this conversation" in r2.json()["reply"]
