"""Search agent: rewrite → retrieve → grounded answer + confidence (FR-4.1–4.7).

Rules: chunks are data, never instructions; no model-memory facts (FR-4.4).
Fake-mode rewrite/answer are deterministic ($0); real LLM plugs into the same
function signatures in dev (small model, max_tokens 60 rewrite / 350 answer).
"""
import re
from ..contracts.agent_contracts import AgentResponse, Citation, Usage
from ..retrieval.store import InMemoryStore

PRONOUNS = re.compile(r"\b(it|that|they|this|these|those|he|she)\b", re.I)
DECLINE = ("I don't have that in our published company information, "
           "so I can't confirm it. I can share what we do publish — "
           "or book you a call with the team for specifics.")


def rewrite_query(message: str, history: list[dict] | None = None) -> tuple[str, bool]:
    """Return (query, rewritten). Skip when self-contained (FR-4.1, saves a call)."""
    history = history or []
    if not history or not PRONOUNS.search(message):
        return message, False
    try:  # real rewrite: resolve follow-ups against history (FR-4.5)
        from src.app.llm.client import chat, use_real
        if use_real():
            last = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in history[-4:])
            q = chat(agent="rewrite",
                     system="Rewrite the last visitor message as a standalone search query "
                            "using the history. Reply with the query only.",
                     user=f"History:\n{last}\nMessage: {message}", max_tokens=60)
            if q:
                return q[:500], True
    except Exception:
        pass
    last_user = next((m["content"] for m in reversed(history) if m.get("role") == "visitor"), "")
    return f"{last_user} {message}".strip()[:500], True


def _norm(w: str) -> str:
    from ..retrieval.store import _norm as _shared_norm
    return _shared_norm(w)


def _words(s: str) -> set[str]:
    return set(_norm(w) for w in re.findall(r"[a-z0-9]+", s.lower()))


def _key_sentences(chunk: str, query: str, limit: int = 2,
                   idf: dict[str, float] | None = None) -> list[str]:
    qtoks = _words(query)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk) if s.strip()]
    w = (lambda t: idf.get(t, 1.0)) if idf else (lambda t: 1.0)
    ranked = sorted(sents, key=lambda s: sum(w(t) for t in qtoks & _words(s)),
                    reverse=True)
    return [s for s in ranked if (qtoks & _words(s))][:limit]


_DECLINED = object()  # LLM judged the excerpts insufficient -> proper decline


def _excerpt(content: str, head: int = 1200, tail: int = 1200) -> str:
    """Head+tail trim: snapshots/summaries live at chunk ends (rates, markets,
    Galaxy Move); a head-only cut hides them and forces false DECLINEs."""
    if len(content) <= head + tail + 10:
        return content
    return content[:head] + "\n…\n" + content[-tail:]


def _compose_llm(query: str, chunks: list):
    """Tailored grounded answer (real mode).

    Returns the answer string, _DECLINED when the model judges the excerpts
    insufficient (honor it: never answer from memory), or None on technical
    failure -> extractive fallback.
    """
    try:
        from src.app.llm.client import chat, use_real
        if not use_real():
            return None
        excerpts = "\n---\n".join(
            f"[{c.source_page} › {c.section}]\n{_excerpt(c.content)}" for c in chunks)
        out = chat(agent="answer",
                   system=("Answer the visitor's question using ONLY the excerpts below. "
                           "Tailor it to exactly what was asked — no extra biography, no extra "
                           "topics. Short (≤120 words), warm, plain sentences. Put the "
                           "[page › section] tag after each claim. If the excerpts lack the "
                           "answer, reply with exactly: DECLINE."),
                   user=f"Excerpts:\n{excerpts}\n\nQuestion: {query}", max_tokens=350)
        if not out:
            return None
        if re.match(r"(?i)^decline\b[\W_]*$", out.strip()):
            return _DECLINED
        return out
    except Exception:
        return None


def confidence_score(scores: list[float], answered: bool) -> float:
    """FR-4.7: f(top-1, mean top-k, answerable flag). Calibrated Phase 1."""
    if not scores:
        return 0.0
    top1, mean_k = scores[0], sum(scores) / len(scores)
    return round(min(1.0, 0.5 * min(1.0, top1 * 3) + 0.3 * min(1.0, mean_k * 3)
                     + 0.2 * (1.0 if answered else 0.0)), 3)


def answer(query: str, history: list[dict] | None, store: InMemoryStore,
           *, top_k: int = 4, min_sim: float = 0.29,
           category: str | None = None) -> AgentResponse:
    q, _ = rewrite_query(query, history)
    results = store.search(q, top_k=top_k, min_sim=min_sim, category=category)
    if not results:  # FR-4.4 decline, never guess
        return AgentResponse(agent="search", output={"answer": DECLINE, "declined": True},
                             confidence=0.0, citations=[],
                             usage=Usage(model="fake", input_tokens=0, output_tokens=0))
    from ..retrieval.store import _idf, _toks as _store_toks
    idf = _idf(store.chunks, _store_toks(q))
    tailored = _compose_llm(q, [r.chunk for r in results])
    if tailored is _DECLINED:  # excerpts insufficient: decline honestly (FR-4.4)
        return AgentResponse(agent="search", output={"answer": DECLINE, "declined": True},
                             confidence=0.0, citations=[],
                             usage=Usage(model="llm-composed", input_tokens=0, output_tokens=0))
    if tailored:
        cites = [Citation(source_page=r.chunk.source_page, section=r.chunk.section,
                          doc_type=r.chunk.doc_type, chunk_id=r.chunk.id) for r in results]
        conf = confidence_score([r.score for r in results], answered=True)
        return AgentResponse(agent="search", output={"answer": tailored, "declined": False},
                             confidence=conf, citations=cites,
                             usage=Usage(model="llm-composed", input_tokens=0, output_tokens=0))
    parts, cites, seen = [], [], set()
    for r in results:
        for s in _key_sentences(r.chunk.content, q, idf=idf):
            key = re.sub(r"\s+", " ", s.lower())
            if key in seen:  # same sentence in overlapping chunks -> say once
                continue
            seen.add(key)
            parts.append(f"{s} [{r.chunk.source_page} › {r.chunk.section}]")
        cites.append(Citation(source_page=r.chunk.source_page, section=r.chunk.section,
                              doc_type=r.chunk.doc_type, chunk_id=r.chunk.id))
        if len(parts) >= 6:
            break
    conf = confidence_score([r.score for r in results], answered=True)
    return AgentResponse(agent="search", output={"answer": " ".join(parts[:6]), "declined": False},
                         confidence=conf, citations=cites,
                         usage=Usage(model="fake", input_tokens=0, output_tokens=0))
