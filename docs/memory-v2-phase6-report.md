# Moones Memory V2 — Phase 6 Persian synthetic evaluation

**Status:** reproducible local/CI fixture, not live-provider validation.

**Dataset:** 140 deterministic synthetic Persian users (20 per scenario family), 32 distractor messages each. No real users, real chat exports or provider API requests. The benchmark executes the existing simple-chat `_load_recent_messages(..., 12)` + `_load_long_term_memories(..., 8)` as the legacy baseline, and `build_memory_context` for V2 lexical and optional hybrid modes.

## Results after correcting the unrelated-source bug

| Scenario (20 each) | Legacy fact present | V2 lexical fact present | V2 hybrid fact present |
|---|---:|---:|---:|
| Direct factual recall | 50% | 100% | 100% |
| Semantic paraphrase | 0% | 0% | 100%* |
| Emotional continuity | 50% | 100% | 100% |
| Changed fact over time | 0% | 100% | 100% |
| First message | 0% | 100% | 100% |
| Missing historical evidence | n/a | 0% false source | 0% false source |
| Separate users / foreign secret | n/a | 0% false source | 0% false source |

*Semantic paraphrase uses **hand-assigned oracle embeddings** `[1, 0]`. The 100% synthetic score is an upper-bound fixture showing that retrieval plumbing works; **it is NOT an evaluation of Venice embeddings, natural-language understanding, or real-world semantic accuracy**.

For temporal-change cases, **100%** of fixtures had separately labeled current and historical values in V2 context. The benchmark does not judge narrative coherence.

## Selected context size (characters, NOT tokens)

| Scenario | Legacy recent + memories | V2 lexical |
|---|---:|---:|
| Direct | 940 | 559 |
| Semantic paraphrase | 924 | 529 |
| Emotional | 966 | 622 |
| Temporal | 924 | 643 |
| First message | 924 | 736 |
| Missing evidence | 936 | 535 |
| Cross-user | 936 | 535 |

For these evenly weighted fixtures, the mean legacy selected-context length is **936 characters** vs **594 characters** for V2 lexical (about **36.5% fewer characters**). These lengths *exclude fixed persona, safety instructions, current message, provider input wrapping, and embedding calls*. This figure must **not** be represented as production token or monetary savings.

## Actual regression discovered and corrected

The first run selected irrelevant source messages in **20/20 cross-user/unknown-secret scenarios**, because the question shared a generic word like “شماره” with unrelated conversations. This was **unrelated same-user evidence**, not direct leakage of another user's records. We updated `_recalled_messages` to require at least two independent token overlaps in longer questions, while allowing short single-topic queries. After the change, false source selection in this family measured **0/20**. A regression test now guards both no-evidence scenarios and ordinary direct/emotional retrieval.

This remains a heuristic tradeoff: shorter legitimate paraphrases may be missed. It needs evaluation on user-consented, anonymized historical queries before production.

## What this evaluation cannot establish

- No generated response was evaluated. **Hallucination rate, emotional believability, persona continuity, user satisfaction and quality under real LLM prompts were NOT measured.**
- No real Venice embeddings were called. Hybrid recall accuracy outside the oracle fixture is **unknown**.
- No provider token counts or invoices were available. True token, latency, and USD reductions are **unknown**.
- Scenarios are formulaically generated rather than sampled from real Moones traffic; confidence intervals and real coverage are not inferable.
- The benchmark measures *presence of expected fact in context*, not answer correctness; negative evidence metrics only count selected original source messages.
- PostgreSQL migrations and unit tests are separate from live Telegram end-to-end and concurrency tests.

## Reproduction

```bash
python -m scripts.benchmark_memory_140 \
  --out-json artifacts/memory-140.json \
  --out-md artifacts/memory-140.md
python -m pytest -q tests/test_memory_benchmark_140.py
```

The CI workflow uploads both machine-readable per-case results and a report as `memory-140-synthetic-report`; no user content or secrets are included.

## Next release gate

1. Run a consented, redacted set of genuine conversational paraphrases through the **actual embedding model**, independently labeled by a reviewer.
2. Score grounded answer accuracy and hallucinations after **generation** with a fixed model, prompt and sampled responses.
3. Record actual provider input/output tokens, embedding charges and p95 latency; compare identical queries in A/B configurations.
4. Review difficult cases and false negatives; tune source threshold before enabling Memory V2 for any live users.
5. Keep this PR in draft and all memory activation flags OFF until those gates are met.
