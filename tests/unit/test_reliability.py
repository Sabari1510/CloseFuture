"""Retry + ledger tests (FR-8.2, FR-8.5, budget)."""
import pytest
from src.app.reliability.retry import run_with_retry
from src.app.observability.cost_ledger import CostLedger


@pytest.mark.asyncio
async def test_retryable_recovers(monkeypatch):
    monkeypatch.delenv("FAULT", raising=False)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            return "error", {"error_code": "UPSTREAM_5XX"}
        return "ok", {"value": 1}

    status, payload, attempts = await run_with_retry(flaky, target="calendar", max_retries=2)
    assert status == "ok" and attempts == 3


@pytest.mark.asyncio
async def test_non_retryable_no_retry():
    async def bad():
        return "error", {"error_code": "BAD_REQUEST"}

    status, _, attempts = await run_with_retry(bad, target="calendar", max_retries=2)
    assert status == "error" and attempts == 1


def test_budget_breaker():
    ledger = CostLedger()
    ledger.record("s", "t", "router", "gpt-4o-mini", 1000000, 1000000)
    assert ledger.blocked(hard=0.5) is True
    assert ledger.blocked(hard=5000.0) is False
