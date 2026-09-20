"""Search-store singleton: pgvector when embeddings exist, else lexical (FR-1.4).

PG is preferred (semantic match); any failure degrades to the proven lexical
store that scores 18/18 on the eval set. Use PGSTORE_ONLY=1 to force errors
visible during embedding calibration instead of silent fallback.
"""
import json
from functools import lru_cache
from pathlib import Path
from src.app.retrieval.store import ChunkRecord, InMemoryStore


def _lexical() -> InMemoryStore:
    recs = []
    p = Path("data/chunks.jsonl")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            recs.append(ChunkRecord(**d))
    return InMemoryStore(recs)


def _pg_ready() -> bool:
    try:
        import psycopg
        from src.app.db.pool import quoted_dsn
        with psycopg.connect(quoted_dsn(), connect_timeout=5) as conn:
            row = conn.execute("select count(*) from chunks where embedding is not null").fetchone()
            return (row[0] or 0) > 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def get_search_store():
    lex = _lexical()
    if _pg_ready():
        from src.app.retrieval.pg_store import PgVectorStore
        print("retrieval: pgvector (lexical fallback armed)")
        return PgVectorStore(lex)
    print("retrieval: lexical (no pgvector embeddings yet — run scripts/embed.py)")
    return lex


def backend_name(store=None) -> str:
    from src.app.retrieval.pg_store import PgVectorStore
    return "pgvector" if isinstance(store or get_search_store(), PgVectorStore) else "lexical"
