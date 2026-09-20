"""Lexical retrieval store ($0 stand-in for pgvector). FR-1.4–1.6, FR-4.2, FR-4.3, FR-4.6.

Production path uses the same record shape against Supabase pgvector with real
embeddings; this module keeps ranking/filter/citation logic identical so L0–L2
tests prove wiring without spend. Real SQL lives in store_sql (Phase 1 dev).
"""
import re
from collections import Counter
from dataclasses import dataclass

STOP = frozenset("""a an and are as at be but by can do for from had has have in into is it its
of on or that the to was with you your will we our they them this what when who how which write code into server me my hack""".split())


@dataclass
class ChunkRecord:
    id: str
    content: str
    source_page: str = "closefuture-profile.pdf"
    section: str = ""
    doc_type: str = ""
    category: str = ""
    token_count: int = 0


SYNONYMS = {"founded": "founder", "founders": "founder", "founding": "founder",
            "serving": "serve", "served": "serve", "serves": "serve"}


def _norm(w: str) -> str:
    """Naive plural stemming so 'rates' matches 'rate' (lexical stand-in)."""
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    return SYNONYMS.get(w, w)


def _toks(s: str) -> Counter:
    # Collapse spaced-letter headings ("M A R K E T S" -> "markets") so they match.
    despace = re.sub(r"(?<=\b[a-z]) (?=[a-z]\b)", "", s.lower())
    return Counter(_norm(w) for w in re.findall(r"[a-z0-9@.]+", despace)
                   if w not in STOP and len(w) > 2)


def lexical_score(query: str, doc: str, *, idf: dict[str, float] | None = None) -> float:
    """Overlap weighted by IDF so rare terms (rate, liya) beat common ones (published)."""
    q = _toks(query)
    if not q:
        return 0.0
    d = _toks(doc)
    w = (lambda t: idf.get(t, 1.0)) if idf else (lambda t: 1.0)
    denom = sum(q[t] * w(t) for t in q)
    return sum(min(q[t], d[t]) * w(t) for t in q) / denom if denom else 0.0


def _idf(pool: list, query_terms) -> dict[str, float]:
    import math
    n = max(1, len(pool))
    idf = {}
    for t in query_terms:
        df = sum(1 for c in pool if t in _toks(c.content))
        idf[t] = 1.0 + math.log(n / (1.0 + df))
    return idf


@dataclass
class SearchResult:
    chunk: ChunkRecord
    score: float


class InMemoryStore:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks

    def search(self, query: str, *, top_k: int = 4, min_sim: float = 0.29,
               category: str | None = None) -> list[SearchResult]:
        pool = [c for c in self.chunks if not category or c.category == category]
        idf = _idf(pool, _toks(query))
        ranked = sorted((SearchResult(c, lexical_score(query, c.content, idf=idf)) for c in pool),
                        key=lambda r: r.score, reverse=True)
        # FR-4.6: allow multiple documents; de-dupe identical sections
        seen: set[str] = set()
        out: list[SearchResult] = []
        for r in ranked:
            if r.score < min_sim:
                break
            if r.chunk.section in seen and len(out) >= 2:
                continue
            seen.add(r.chunk.section)
            out.append(r)
            if len(out) >= top_k:
                break
        return out

    # Real pgvector path (dev): same filters, cosine over embedding column.
    SQL = """select id, content, source_page, section, doc_type, category,
             1 - (embedding <=> $1::vector) as sim from chunks
             where ($2::text is null or category = $2) and 1 - (embedding <=> $1::vector) >= $3
             order by sim desc limit $4"""
