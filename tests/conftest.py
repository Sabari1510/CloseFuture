"""Test isolation: reset process-global rate maps + pin backends to fakes.

Production limits are unchanged; the suite shares one TestClient IP and would
otherwise trip RATE_IP_PER_HOUR across files. Backend pinning keeps L0–L2 at
$0 and offline no matter what credentials sit in .env.
"""
import pytest
from src.app.api import main as api
from src.app.mcp_servers.calendar_server import FakeCalendar
from src.app.mcp_servers.email_server import FakeEmail


@pytest.fixture(autouse=True)
def _force_fake_mode():
    """Unit suite never spends: fake LLM + no-op backoff regardless of .env."""
    import sys
    sys.path.insert(0, "config")
    from settings import settings
    old_mode = settings.llm_mode
    settings.llm_mode = "fake"
    api.router = __import__("src.app.agents.orchestrator.router",
                            fromlist=["FakeRouter"]).FakeRouter()
    api.cal.sleeper = lambda s: None
    api.mailer.sleeper = lambda s: None
    yield
    settings.llm_mode = old_mode


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    api._session_hits.clear()
    api._ip_hits.clear()
    yield
    api._session_hits.clear()
    api._ip_hits.clear()


@pytest.fixture(autouse=True)
def _pin_fake_backends():
    from src.app.db.session_store import InMemorySessionStore
    api.store = InMemorySessionStore()
    api.cal.inner = FakeCalendar()
    api.mailer.inner = FakeEmail()
    api.bookings.bookings.clear()
    api.bookings._n = 0
    yield
    api.cal.inner = FakeCalendar()
    api.mailer.inner = FakeEmail()
    api.bookings.bookings.clear()
    api.bookings._n = 0
