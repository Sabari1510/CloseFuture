"""Error-code -> retryable map (AGENT.md §7). FR-8.2, FR-8.3."""
from ..contracts.agent_contracts import AgentError

# Single source of truth: nobody sets retryable by hand.
ERROR_RETRYABLE: dict[str, bool] = {
    "TIMEOUT": True,
    "RATE_LIMITED": True,
    "UPSTREAM_5XX": True,
    "NETWORK": True,
    "SLOT_UNAVAILABLE": False,
    "BUDGET_EXCEEDED": False,
    "INVALID_CREDENTIALS": False,
    "BAD_REQUEST": False,
    "NOT_FOUND": False,
    "VALIDATION": False,
}


def make_error(agent: str, error_code: str, message: str) -> AgentError:
    """Build AgentError with retryable derived from the map. # FR-8.3"""
    return AgentError(
        agent=agent,
        error_code=error_code,
        message=message,
        retryable=ERROR_RETRYABLE.get(error_code, False),
    )


def classify_http_status(status: int) -> tuple[str, bool]:
    """Map HTTP status to (error_code, retryable). # FR-8.2"""
    if status == 429:
        return "RATE_LIMITED", True
    if status in (408,) or 500 <= status <= 599:
        return "UPSTREAM_5XX", True
    if status in (400, 401, 403, 404, 422):
        code = {400: "BAD_REQUEST", 401: "INVALID_CREDENTIALS",
                403: "BAD_REQUEST", 404: "NOT_FOUND", 422: "VALIDATION"}[status]
        return code, False
    return "BAD_REQUEST", False
