"""Cost ledger: tokens + cost per call, prices from config/pricing.yaml. Budget gate."""
import yaml
from dataclasses import dataclass


@dataclass
class UsageRecord:
    session_id: str
    turn_id: str
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostLedger:
    def __init__(self, pricing_path: str = "config/pricing.yaml") -> None:
        with open(pricing_path) as f:
            self.prices = yaml.safe_load(f).get("models", {})
        self.records: list[UsageRecord] = []

    def price_for(self, model: str) -> dict:
        return self.prices.get(model, {"input_per_1m": 0.0, "output_per_1m": 0.0})

    def record(self, session_id: str, turn_id: str, agent: str, model: str,
               in_tok: int, out_tok: int) -> UsageRecord:
        p = self.price_for(model)
        cost = in_tok / 1e6 * p["input_per_1m"] + out_tok / 1e6 * p["output_per_1m"]
        rec = UsageRecord(session_id, turn_id, agent, model, in_tok, out_tok, cost)
        self.records.append(rec)
        try:  # best-effort persist to llm_usage; never break a turn on ledger failure
            import sys
            sys.path.insert(0, "config")
            from settings import settings
            if settings.database_url:
                import psycopg
                from src.app.db.pool import quoted_dsn
                with psycopg.connect(quoted_dsn(), connect_timeout=5) as conn:
                    conn.execute("insert into llm_usage (session_id, turn_id, agent, model,"
                                 " input_tokens, output_tokens, cost_usd) values "
                                 "(%s,%s,%s,%s,%s,%s,%s)",
                                 (session_id[:36] if len(session_id) > 36 else session_id,
                                  turn_id[:36] if len(turn_id) > 36 else turn_id,
                                  agent, model, in_tok, out_tok, cost))
                    conn.commit()
        except Exception:
            pass
        return rec

    def total(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def blocked(self, hard: float, estimate: float = 0.0) -> bool:
        """FR-8.x budget circuit breaker: hard stop. # BUDGET"""
        return self.total() + estimate >= hard
