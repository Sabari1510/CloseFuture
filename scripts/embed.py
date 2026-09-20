"""Embed chunks.jsonl once into Supabase pgvector (FR-1.4). Run: python scripts/embed.py."""
import json
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "config")
from pathlib import Path
from settings import settings
from src.app.db.pool import quoted_dsn
from src.app.llm.client import embed


def main() -> None:
    assert settings.embed_dim == 1536, "EMBED_DIM must match vector(1536)"
    rows = [json.loads(line) for line in Path("data/chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    print(f"embedding {len(rows)} chunks with {settings.embed_model} ...")
    vecs = []
    for i in range(0, len(rows), 32):
        vecs += embed([r["content"] for r in rows[i:i + 32]])
    import psycopg
    with psycopg.connect(quoted_dsn()) as conn:
        doc = conn.execute(
            "insert into documents (title, source_page, doc_type, category) values "
            "('CloseFuture profile','closefuture-profile.pdf','profile','service') "
            "returning id").fetchone()[0]
        for ci, (r, v) in enumerate(zip(rows, vecs)):
            conn.execute(
                "insert into chunks (document_id, chunk_index, slug, content, embedding,"
                " source_page, section, doc_type, category, token_count) values "
                "(%s,%s,%s,%s,%s::vector,%s,%s,%s,%s,%s) on conflict (slug) do update set "
                "embedding=excluded.embedding, content=excluded.content",
                (doc, ci, r["id"], r["content"], str(v), r["source_page"], r["section"],
                 r["doc_type"], r["category"], r["token_count"]))
        conn.commit()
    print(f"upserted {len(rows)} chunk embeddings")


if __name__ == "__main__":
    main()
