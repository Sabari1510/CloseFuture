"""Phase-0 contract tests (L0). FR-3.1, FR-8.3."""
from src.app.contracts.agent_contracts import (
    AgentResponse, GuardrailVerdict, RouteDecision,
)
from src.app.reliability.error_map import classify_http_status, make_error


def test_agent_error_shape_fr83():
    e = make_error("scheduler", "TIMEOUT", "timed out")
    assert e.status == "error"
    assert e.retryable is True
    assert e.model_dump() == {"status": "error", "error_code": "TIMEOUT",
                              "message": "timed out", "retryable": True, "agent": "scheduler"}


def test_non_retryable_not_retried_fr82():
    e = make_error("scheduler", "BAD_REQUEST", "bad")
    assert e.retryable is False
    code, retryable = classify_http_status(401)
    assert retryable is False
    code, retryable = classify_http_status(503)
    assert retryable is True


def test_route_decision_contract_fr31():
    r = RouteDecision(intent="booking", confidence=0.9, targets=["scheduler"],
                      strategy="single", rationale="clear booking", ended=False)
    assert r.strategy == "single"


def test_guardrail_verdict_contract():
    v = GuardrailVerdict(stage="inbound", passed=False, action="fallback",
                         reason_codes=["injection"])
    assert v.passed is False


def test_agent_response_citations():
    r = AgentResponse(agent="search", output={"answer": "hi"}, confidence=0.8)
    assert r.citations == []
