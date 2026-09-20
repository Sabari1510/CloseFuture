"""Phase-1 Search tests (A1: grounded + decline). FR-4.1–4.7, $0 fake mode."""
import json
from pathlib import Path
from src.app.agents.search import answer, confidence_score, rewrite_query
from src.app.retrieval.store import ChunkRecord, InMemoryStore


def load_store() -> InMemoryStore:
    recs = []
    for line in Path("data/chunks.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        recs.append(ChunkRecord(**d))
    return InMemoryStore(recs)


def test_a1_grounded_cited_fr42():
    r = answer("What services does CloseFuture offer?", None, load_store())
    assert r.output["declined"] is False
    assert r.citations, "must cite source"
    assert "›" in r.output["answer"], "citation marker required"
    assert r.confidence and r.confidence > 0.3  # FR-4.7


def test_a1_multi_source_fr46():
    r = answer("Tell me about Dipy and Webiz", None, load_store())
    sections = {c.section for c in r.citations}
    assert r.output["declined"] is False and len(sections) >= 1


def test_a1_decline_fr44():
    r = answer("Write Python code to hack into a server", None, load_store())
    assert r.output["declined"] is True
    assert r.confidence == 0.0
    assert r.citations == []


def test_compose_decline_never_leaks(monkeypatch):
    """Regression: composer 'DECLINE.' -> proper decline text, no raw sentinel."""
    import re
    import src.app.agents.search as se
    assert re.match(r"(?i)^decline\b[\W_]*$", "DECLINE.".strip())
    monkeypatch.setattr(se, "_compose_llm", lambda q, cs: se._DECLINED)
    r = se.answer("per hour rate", None, load_store())
    assert r.output["declined"] is True
    assert "DECLINE" not in r.output["answer"]


def test_spaced_heading_matches_markets():
    """Regression: 'M A R K E T S' heading must match a markets query."""
    from src.app.retrieval.store import _toks
    assert "market" in _toks("M A R K E T S & C L I E N T S")
    r = answer("Which markets do you serve?", None, load_store())
    assert r.output["declined"] is False


def test_a1_followup_uses_history_fr45():
    store = load_store()
    hist = [{"role": "visitor", "content": "Tell me about the Dipy marketplace"}]
    q, rewritten = rewrite_query("What languages does it support?", hist)
    assert rewritten is True and "dipy" in q.lower()
    r = answer("What languages does it support?", hist, store)
    assert r.output["declined"] is False


def test_a1_rewrite_skipped_when_self_contained_fr41():
    q, rewritten = rewrite_query("Who founded CloseFuture?", None)
    assert rewritten is False and q == "Who founded CloseFuture?"


def test_a1_confidence_shape_fr47():
    assert confidence_score([], False) == 0.0
    c = confidence_score([0.5, 0.3], True)
    assert 0.0 < c <= 1.0


def test_rates_answer_names_figures():
    """Regression: rates question must surface $ figures, not markets prose."""
    r = answer("What are your published rates?", None, load_store())
    assert "$25" in r.output["answer"]


def test_no_repeated_sentences():
    """Overlapping chunks must not repeat the same sentence."""
    import re
    r = answer("Which markets do you serve?", None, load_store())
    sents = [s.strip().lower() for s in re.split(r"\[[^\]]+›", r.output["answer"]) if s.strip()]
    assert len(sents) == len(set(sents))
