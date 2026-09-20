"""pgvector retrieval (FR-1.4): cosine search over Supabase chunks.

Same SearchResult shape as the lexical store, so agents are backend-blind.
Falls back to lexical whenever embeddings are missing, the DB is unreachable,
or the query embedding fails — retrieval never takes down a turn (FR-8.1).
"""
from .store import ChunkRecord, InMemoryStore, SearchResult


class PgVectorStore:
    def __init__(self, lexical_fallback: InMemoryStore, *, min_sim: float = 0.35) -> None:
        self.fallback = lexical_fallback
        self.min_sim = min_sim

    @property
    def chunks(self):  # agent code reading .chunks (guardrail grounding) keeps working
        return self.fallback.chunks

    def _query_vec(self, query: str) -> list[float] | None:
        try:
            from src.app.llm.client import embed
            return embed([query])[0]
        except Exception:
            return None

    def search(self, query: str, *, top_k: int = 4, min_sim: float | None = None,
               category: str | None = None) -> list[SearchResult]:
        floor = self.min_sim if min_sim is None else min_sim
        vec = self._query_vec(query)
        if vec is None:
            return self.fallback.search(query, top_k=top_k, min_sim=floor,
                                        category=category)
        try:
            import psycopg
            from src.app.db.pool import quoted_dsn
            seen: set[str] = set()
            out: list[SearchResult] = []
            with psycopg.connect(quoted_dsn()) as conn:
                rows = conn.execute(
                    """select id, content, source_page, section, doc_type, category,
                       1 - (embedding <=> %s::vector) as sim from chunks
                       where (%s::text is null or category = %s)
                       and embedding is not null
                       and 1 - (embedding <=> %s::vector) >= %s
                       order by sim desc limit %s""",
                    (str(vec), category, category, str(vec), floor, top_k)).fetchall()
            for r in rows:
                rec = ChunkRecord(id=str(r[0]), content=r[1], source_page=r[2] or "",
                                  section=r[3] or "", doc_type=r[4] or "",
                                  category=r[5] or "")
                if rec.section in seen and len(out) >= 2:
                    continue
                seen.add(rec.section)
                out.append(SearchResult(rec, float(r[6])))
            if out:
                return out
        except Exception:
            pass
        return self.fallback.search(query, top_k=top_k, min_sim=floor, category=category)
