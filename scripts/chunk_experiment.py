"""Chunking experiment: 3 configs x eval set, lexical hit-rate@k ($0). FR-1.3."""
import re
import yaml
from pathlib import Path
from collections import Counter

import sys
sys.path.insert(0, ".")
from src.app.retrieval.chunking import CONFIGS, chunk_text
from scripts.extract_source import SECTIONS


def tokens(s: str) -> Counter:
    return Counter(re.findall(r"[a-z0-9]+", s.lower()))


def lexical_score(q: str, c: str) -> float:
    qt, ct = tokens(q), tokens(c)
    if not qt:
        return 0.0
    return sum(min(qt[w], ct[w]) for w in qt) / sum(qt.values())


def load_sources() -> list[dict]:
    out = []
    for slug, title, doc_type, cat, _ in SECTIONS:
        p = Path(f"data/source/{slug}.md")
        if p.exists():
            out.append({"text": p.read_text(encoding="utf-8"), "title": title,
                        "doc_type": doc_type, "category": cat})
    return out


def main() -> None:
    evalq = yaml.safe_load(Path("tests/eval_questions.yaml").read_text())["questions"]
    grounded = [q for q in evalq if not q["out_of_scope"]]
    sources = load_sources()
    print(f"sources={len(sources)} grounded_q={len(grounded)}")
    results = {}
    for name, cfg in CONFIGS.items():
        chunks = []
        for s in sources:
            chunks += chunk_text(s["text"], source_page="closefuture-profile.pdf",
                                 section=s["title"], doc_type=s["doc_type"],
                                 category=s["category"], **cfg)
        hits = 0
        for item in grounded:
            ranked = sorted(chunks, key=lambda c: lexical_score(item["q"], c.content), reverse=True)[:4]
            top = " ".join(c.content for c in ranked[:2]).lower()
            if all(k.lower() in top for k in item["expect_keywords"]):
                hits += 1
        rate = hits / max(1, len(grounded))
        avg_tok = sum(c.token_count for c in chunks) / max(1, len(chunks))
        results[name] = (rate, len(chunks), round(avg_tok))
        print(f"{name}: hit-rate@4={rate:.2f} chunks={len(chunks)} avg_tok={avg_tok:.0f}")
    best = max(results, key=lambda k: results[k][0])
    print(f"BEST={best}")
    Path("docs").mkdir(exist_ok=True)


if __name__ == "__main__":
    main()
