"""Contracts (AGENT.md §7). FR-3.1, FR-8.3."""
from typing import Literal
from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_page: str = ""
    section: str = ""
    doc_type: str = ""
    chunk_id: str = ""


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class AgentRequest(BaseModel):
    session_id: str
    turn_id: str
    trace_id: str
    agent: str
    input: dict = Field(default_factory=dict)
    state_ref: dict = Field(default_factory=dict)


class AgentResponse(BaseModel):
    status: Literal["ok"] = "ok"
    agent: str
    output: dict = Field(default_factory=dict)
    confidence: float | None = None
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class AgentError(BaseModel):
    """FR-8.3 exact shape. retryable comes from error_map, never set by hand."""

    status: Literal["error"] = "error"
    error_code: str
    message: str
    retryable: bool
    agent: str


class RouteDecision(BaseModel):
    """FR-3.5, FR-3.6."""

    intent: str
    confidence: float
    targets: list[str] = Field(default_factory=list)
    strategy: Literal["single", "sequence", "parallel", "clarify"] = "single"
    rationale: str = ""
    ended: bool = False


class CheckResult(BaseModel):
    check: str
    passed: bool
    detail: str = ""


class GuardrailVerdict(BaseModel):
    stage: Literal["inbound", "outbound"]
    passed: bool
    action: Literal["allow", "block", "fallback"] = "allow"
    checks: list[CheckResult] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
