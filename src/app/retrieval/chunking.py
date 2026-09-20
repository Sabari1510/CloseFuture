"""Section-aware chunker (FR-1.3). Deterministic, $0, no LLM."""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    content: str
    source_page: str
    section: str
    doc_type: str
    category: str
    token_count: int


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts


def chunk_text(text: str, *, target_tokens: int, overlap_tokens: int,
               source_page: str, section: str, doc_type: str, category: str) -> list[Chunk]:
    """Greedily pack paragraphs to ~target_tokens with overlap. # FR-1.3"""
    paras = split_paragraphs(text)
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_tok = 0
    for p in paras:
        t = approx_tokens(p)
        if cur and cur_tok + t > target_tokens * 1.25:
            body = "\n\n".join(cur).strip()
            chunks.append(Chunk(body, source_page, section, doc_type, category, approx_tokens(body)))
            # overlap: keep tail paragraphs totalling overlap_tokens
            tail: list[str] = []
            tail_tok = 0
            for q in reversed(cur):
                tail.insert(0, q)
                tail_tok += approx_tokens(q)
                if tail_tok >= overlap_tokens:
                    break
            cur, cur_tok = tail, tail_tok
        cur.append(p)
        cur_tok += t
    if cur:
        body = "\n\n".join(cur).strip()
        if body:
            chunks.append(Chunk(body, source_page, section, doc_type, category, approx_tokens(body)))
    return chunks


CONFIGS = {
    "A-300": {"target_tokens": 300, "overlap_tokens": 40},
    "B-450": {"target_tokens": 450, "overlap_tokens": 60},
    "C-600": {"target_tokens": 600, "overlap_tokens": 90},
}
