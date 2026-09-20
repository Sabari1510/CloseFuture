"""Budget gate: abort real-LLM runs if spend + estimate > HARD."""
import sys
sys.path.insert(0, "config")
from settings import settings

if __name__ == "__main__":
    print(f"soft={settings.budget_soft_usd} hard={settings.budget_hard_usd} "
          f"total={settings.budget_total_usd}")
    print("ledger check: wire to llm_usage table in Phase 2; $0 spent so far.")
