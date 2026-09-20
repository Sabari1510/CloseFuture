"""Central settings via pydantic-settings (no magic numbers in code)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model_small: str = "gpt-4o-mini"
    llm_model_main: str = "gpt-4o-mini"
    embed_model: str = "text-embedding-3-small"
    embed_dim: int = 1536
    embed_api_key: str = ""
    llm_mode: str = "fake"  # fake | real

    # DB
    database_url: str = ""
    test_database_url: str = "postgresql://postgres:postgres@localhost:5433/postgres"

    # Google / calendar
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_calendar_id: str = "primary"
    calendar_tz: str = "Asia/Kolkata"
    business_hours: str = "Mon-Fri 10-17"
    slot_minutes: int = 30
    lookahead_days: int = 14
    min_notice_hours: int = 4

    # Email (gmail per user choice)
    email_provider: str = "gmail"
    email_from: str = ""
    sales_inbox: str = ""

    # App
    company_name: str = "CloseFuture"
    admin_api_key: str = ""
    allowed_origins: str = "http://localhost:3000"
    budget_total_usd: float = 2.00
    budget_soft_usd: float = 1.20
    budget_hard_usd: float = 1.70
    idle_abandon_minutes: int = 20
    away_grace_minutes: int = 3  # tab-closed visitor -> summary after this long
    session_ttl_days: int = 30
    route_conf_threshold: float = 0.6
    search_conf_threshold: float = 0.5
    top_k: int = 4
    min_sim: float = 0.29  # calibrated Phase 1+fix: grounded >= 0.32, probes <= 0.26
    max_retries: int = 2
    tool_timeout_s: int = 8
    turn_deadline_s: int = 25
    lease_seconds: int = 30
    lease_wait_s: int = 8
    llm_concurrency: int = 4
    rate_session_per_min: int = 10
    rate_ip_per_hour: int = 30
    max_message_chars: int = 500
    max_turns_per_session: int = 30
    fault: str = ""
    sweep_interval_min: int = 5  # background auto-sweep cadence (0 = off)
    mcp_transport: str = "stdio"  # stdio (FR-3.3 MCP tools) | direct (fallback)


settings = Settings()
