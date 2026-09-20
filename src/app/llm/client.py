"""Real LLM layer: OpenAI chat + embeddings with ledger + budget gate (FR-8.x).

Every real call records tokens/cost; the hard budget blocks before spend.
Any failure (no key, budget hit, API error) falls back to the deterministic
fake path — the turn never hangs and the visitor gets an honest reply.
Costs stay tiny: small model everywhere + max_tokens on every call.
"""
import sys
sys.path.insert(0, "config")
from settings import settings  # noqa: E402
from src.app.observability.cost_ledger import CostLedger  # noqa: E402

ledger = CostLedger()  # process-global; api.main re-exports this instance
SESSION_ID, TURN_ID = "n/a", "n/a"


class BudgetExceeded(RuntimeError):
    pass


def use_real() -> bool:
    return settings.llm_mode == "real" and bool(settings.llm_api_key)


def check_budget(estimate_usd: float = 0.01) -> None:
    if ledger.blocked(settings.budget_hard_usd, estimate_usd):
        raise BudgetExceeded(f"hard budget ${settings.budget_hard_usd} reached")


def _model() -> str:
    return settings.llm_model_small


def chat(*, agent: str, system: str, user: str, max_tokens: int,
         model: str | None = None, session_id: str = "", turn_id: str = "") -> str:
    """One bounded chat call. Raises BudgetExceeded / RuntimeError on any failure."""
    check_budget()
    model = model or settings.llm_model_small
    from openai import OpenAI
    client = OpenAI(api_key=settings.llm_api_key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        timeout=8)
    choice = resp.choices[0].message.content or ""
    usage = resp.usage
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    ledger.record(session_id or SESSION_ID, turn_id or TURN_ID, agent, model, in_tok, out_tok)
    return choice.strip()


def chat_json(*, agent: str, system: str, user: str, max_tokens: int,
              model: str | None = None, session_id: str = "", turn_id: str = "") -> str:
    """JSON-mode chat for structured outputs (router). Returns raw JSON text."""
    check_budget()
    model = model or settings.llm_model_small
    from openai import OpenAI
    client = OpenAI(api_key=settings.llm_api_key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        timeout=8)
    choice = resp.choices[0].message.content or "{}"
    usage = resp.usage
    ledger.record(session_id or SESSION_ID, turn_id or TURN_ID, agent, model,
                  getattr(usage, "prompt_tokens", 0) or 0,
                  getattr(usage, "completion_tokens", 0) or 0)
    return choice.strip()


def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embedding call (ingest-time; once per chunk)."""
    check_budget(estimate_usd=0.001)
    model = model or settings.embed_model
    from openai import OpenAI
    client = OpenAI(api_key=settings.embed_api_key or settings.llm_api_key)
    resp = client.embeddings.create(model=model, input=texts, timeout=15)
    in_tok = getattr(resp.usage, "prompt_tokens", 0) or 0
    ledger.record("ingest", "ingest", "embed", model, in_tok, 0)
    return [d.embedding for d in resp.data]
