"""Postgres helpers: DSN quoting (@ in passwords) + reachability probe."""
import sys
sys.path.insert(0, "config")
from settings import settings


def quoted_dsn(raw: str | None = None) -> str:
    """Quote a password containing reserved chars (e.g. @) so libpq parses it."""
    from urllib.parse import quote, urlsplit, urlunsplit
    raw = raw or settings.database_url
    parts = urlsplit(raw)
    if parts.password:
        netloc = f"{parts.username}:{quote(parts.password, safe='')}@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return raw


def reachable(dsn: str | None = None, timeout: int = 5) -> bool:
    try:
        import psycopg
        with psycopg.connect(quoted_dsn(dsn), connect_timeout=timeout) as conn:
            conn.execute("select 1")
        return True
    except Exception:
        return False
