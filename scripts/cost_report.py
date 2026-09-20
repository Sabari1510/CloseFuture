"""Spend report: DB ledger (source of truth) + budget status."""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "config")
from settings import settings


def main() -> None:
    total, calls = 0.0, 0
    try:
        import psycopg
        from src.app.db.pool import quoted_dsn
        with psycopg.connect(quoted_dsn(), connect_timeout=5) as conn:
            row = conn.execute("select coalesce(sum(cost_usd),0), count(*) from llm_usage").fetchone()
            total, calls = float(row[0]), row[1]
    except Exception as e:
        print(f"(db unreachable: {type(e).__name__}; showing 0)")
    print(f"LLM spend: ${total:.6f} across {calls} calls "
          f"(soft ${settings.budget_soft_usd}, hard ${settings.budget_hard_usd})")
    print(f"Remaining under hard cap: ${max(0.0, settings.budget_hard_usd - total):.6f}")
    print("Note: provider billing (platform.openai.com -> Usage) is authoritative.")


if __name__ == "__main__":
    main()
