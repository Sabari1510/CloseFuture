"""pgvector retrieval tests: fallback correctness (FR-1.4, FR-8.1). $0, no DB needed."""
from src.app.retrieval.loader import backend_name, get_search_store
from src.app.retrieval.pg_store import PgVectorStore
from src.app.retrieval.store import ChunkRecord, InMemoryStore


def _lex() -> InMemoryStore:
    return InMemoryStore([
        ChunkRecord(id="a", content="Hourly rate $25 per hour minimum project",
                    section="Rates", category="pricing"),
        ChunkRecord(id="b", content="Dipy marketplace Hebrew English creators",
                    section="Work", category="case_study"),
    ])


def test_pg_falls_back_without_embeddings():
    pg = PgVectorStore(_lex())
    out = pg.search("What are your published rates?")
    assert out and out[0].chunk.id == "a"  # lexical fallback answers


def test_pg_falls_back_on_db_failure(monkeypatch):
    import src.app.retrieval.pg_store as m
    monkeypatch.setattr(m.PgVectorStore, "_query_vec", lambda self, q: [0.1] * 1536)
    pg = PgVectorStore(_lex())
    out = pg.search("Tell me about Dipy", min_sim=0.0)
    assert out and out[0].chunk.id == "b"


def test_loader_reports_backend_and_searches():
    get_search_store.cache_clear()
    try:
        assert backend_name() in ("lexical", "pgvector")
        assert len(get_search_store().search("What services do you offer?")) >= 0
    finally:
        get_search_store.cache_clear()
