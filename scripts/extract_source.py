"""Extract closefuture-profile.pdf into short self-contained source docs (FR-1.2).

No invented content: output text comes only from the PDF (FR-1.1).
Sections map to profile chapters so citations stay stable.
"""
import re
import fitz
from pathlib import Path

SRC_PDF = Path("closefuture-profile.pdf")
OUT_DIR = Path("data/source")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# (slug, title, doc_type, category, keywords to locate section start)
SECTIONS = [
    ("studio", "The studio", "overview", "service", ["An agency built for speed"]),
    ("founder", "Founder and contact", "contact", "faq", ["Founder — and the person", "REACH THE STUDIO"]),
    ("services", "Services and process", "service", "service", ["What CloseFuture does"]),
    ("tech", "Technology stack", "service", "service", ["The toolkit"]),
    ("work-flagship", "Selected work flagship", "case_study", "case_study", ["What they've shipped"]),
    ("work-more", "More shipped products", "case_study", "case_study", ["Three more shipped"]),
    ("blog-reputation", "Writing, markets and reputation", "article", "pricing", ["From the blog"]),
]


def clean(t: str) -> str:
    t = t.replace("\u0000", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def main() -> None:
    doc = fitz.open(SRC_PDF)
    full = "\n".join(p.get_text() for p in doc)
    full = clean(full)
    print(f"extracted {len(full)} chars, {len(doc)} pages")
    # Greedy split: find each section's first marker, slice until next marker
    markers: list[tuple[int, dict]] = []
    for slug, title, doc_type, cat, keys in SECTIONS:
        pos = min([full.find(k) for k in keys if full.find(k) >= 0], default=-1)
        markers.append((pos if pos >= 0 else 10**9, {"slug": slug, "title": title,
                                                     "doc_type": doc_type, "category": cat}))
    markers.sort()
    for i, (pos, meta) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(full)
        chunk = full[pos:end].strip() if pos < 10**9 else ""
        if len(chunk) < 200:  # fallback: whole doc slice
            chunk = full
        header = f"# {meta['title']}\n_Source: closefuture-profile.pdf › {meta['title']}_\n\n"
        (OUT_DIR / f"{meta['slug']}.md").write_text(header + chunk, encoding="utf-8")
        print(f"wrote {meta['slug']}.md ({len(chunk)} chars)")


if __name__ == "__main__":
    main()
