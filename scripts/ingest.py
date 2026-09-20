"""Build data/chunks.jsonl with chosen config B-450 (embed once, FR-1.4)."""
import json
import sys
from pathlib import Path
sys.path.insert(0, ".")
from src.app.retrieval.chunking import chunk_text, CONFIGS
from scripts.extract_source import SECTIONS

CFG = CONFIGS["B-450"]


def main() -> None:
    out = Path("data/chunks.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for idx, (slug, title, doc_type, cat, _) in enumerate(SECTIONS):
            text = Path(f"data/source/{slug}.md").read_text(encoding="utf-8")
            for ci, ch in enumerate(chunk_text(text, source_page="closefuture-profile.pdf",
                                               section=title, doc_type=doc_type,
                                               category=cat, **CFG)):
                f.write(json.dumps({"id": f"{slug}-{ci}", "content": ch.content,
                                    "source_page": ch.source_page, "section": ch.section,
                                    "doc_type": ch.doc_type, "category": ch.category,
                                    "token_count": ch.token_count}) + "\n")
                n += 1
    print(f"wrote {n} chunks -> {out} (config B-450)")


if __name__ == "__main__":
    main()
