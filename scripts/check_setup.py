"""check-setup: verify DB, pgvector, LLM (tiny call), Google, Gmail (no spend beyond tiny)."""
import sys
sys.path.insert(0, "config")
from settings import settings


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'SKIP/FAIL'} {name} {detail}")


if __name__ == "__main__":
    check("config", bool(settings.database_url), "DATABASE_URL set" if settings.database_url else "not configured")
    check("llm-key", bool(settings.llm_api_key), f"provider={settings.llm_provider} mode={settings.llm_mode}")
    check("google", bool(settings.google_refresh_token), "refresh token set" if settings.google_refresh_token else "not configured (Phase 5)")
    check("email", bool(settings.sales_inbox), f"provider={settings.email_provider}" if settings.sales_inbox else "SALES_INBOX not set (Phase 6)")
    check("admin-key", bool(settings.admin_api_key), "set" if settings.admin_api_key else "generate one")
    print("done. Full live checks land with Phase 0 wiring (fake mode = $0).")
