# Chunking justification (FR-1.3)

Experiment: `python scripts/chunk_experiment.py` — 3 section-aware configs on a
15-question grounded eval set (`tests/eval_questions.yaml`), lexical hit-rate@4 ($0, no LLM).

| Config | Target / overlap | hit-rate@4 | Chunks | Avg tok |
|---|---|---|---|---|
| A-300 | 300 / 40 | 0.80 | 21 | ~391 |
| **B-450** | **450 / 60** | **0.87** | **15** | **~459** |
| C-600 | 600 / 90 | 0.87 | 11 | ~583 |

Decision: **B-450 (target ~450 tokens, overlap ~60)**.
- Highest recall (13/15) tied with C-600, clearly above A-300: small chunks split
  Dipy/Webiz/Vigo details from their identifying names, hurting retrieval.
- vs C-600: same recall but 15 vs 11 chunks gives tighter citations per claim
  (FR-4.2) and fits top-k=4 comfortably in context with the rolling summary.
- Overlap ~13% preserves cross-paragraph context (e.g. price + timeline stay together).
- Lexical scoring filters stopwords/short tokens; out-of-scope probes (medical,
  hacking, guarantees) score < floor and decline correctly (FR-4.4).
- Note: published rates ($25–49/hr, $1,000+ min) ARE in the profile, so rate
  questions answer with citation; specific quotes/promises stay blocked by the
  Guardrail commitments policy (FR-7.2, Phase 3).

`MIN_SIM` calibrated on the same set: grounded top-1 lexical scores cluster ≥ 0.18,
out-of-scope probes score < 0.10 → floor set to **0.12** (decline path FR-4.4).
Revisited if real embeddings shift the distribution in dev.

Update (retrieval fix): with plural stemming + IDF weighting, grounded top-1 ≥ 0.32
and probes ≤ 0.26 → floor recalibrated to **0.29** (`settings.min_sim`).
