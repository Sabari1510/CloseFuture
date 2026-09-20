"""TTL cleanup job (FR-2.4). Wired to /internal/sweep in Phase 6."""
import sys
sys.path.insert(0, "config")
from settings import settings


def cleanup(store, ttl_days: int | None = None) -> int:
    return store.cleanup_ttl(ttl_days or settings.session_ttl_days)
