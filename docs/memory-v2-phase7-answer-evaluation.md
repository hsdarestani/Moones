# Moones Memory V2 — generated-answer evaluation (Phase 7)

## What is implemented

`scripts/evaluate_memory_answers.py` constructs **36 entirely synthetic
Persian conversations** in six groups: factual recall, semantic paraphrase,
emotional continuity, time-versioned memories, first messages, and
unanswerable recollection questions.

For each case it creates **two actual Moones simple_chat system prompts**,
calling the project's real `_build_system_prompt`:
- **Legacy**: current `_load_recent_messages(... limit=12)`,
  `_format_recent_messages` and `_load_long_term_memories(... limit=8)`.
- **V2**: `build_memory_context` with bounded lexical evidence and temporal
  history (where relevant).

The partner identity, user question, model choice, and generation parameters
are the same for both arms. Their order alternates by case to reduce simple
order effects. This harness **does not call the Telegram webhook**, attach
real user records, run semantic embedding queries or exercise relationship
state updates and post-generation filters. Its purpose is paired model-answer
evaluation, not full product E2E certification.

## Confirmed no-cost CI preflight

```bash
DATABASE_URL='sqlite:///:memory:' python -m scripts.evaluate_memory_answers \
  --out artifacts/answer-preflight.json
```

- **36** synthetic cases, **72** possible provider completions.
- Mean **entire simple_chat system prompt** length: old **5,925**
  characters, V2 **5,776** characters (about **2.5% shorter**). The
  fixed persona/system instructions dominate. This is not measured tokens.
- This preflight invokes **no LLM** and produces **no answer-accuracy or
  hallucination scores**. It does not count embedding overhead.
- Fixed prompt optimization is likely more important for major token
  savings than memory-window changes alone. Keep quality gates mandatory.

## Optional paid real-model test (not run by default)

**Never put API keys in the repository, CI output, or screenshots.** Use a
private staging machine with a Venice key and a disposable SQLite database.
The script reads the project's existing `VENICE_API_KEY` and
`VENICE_MODEL` environment settings and uses `LLMClient.complete_result`
rather than hand-building a separate chat API request.

```bash
export DATABASE_URL='sqlite:///:memory:'
export VENICE_API_KEY='configured privately, not committed'
export VENICE_MODEL='qwen-3-6-plus'
python -m scripts.evaluate_memory_answers \
  --live --accept-provider-cost --max-calls 8 --max-usd 0.10 \
  --out artifacts/memory-live-eval.json \
  --review-csv artifacts/memory-blind-review.csv
```

- `--live`, `--accept-provider-cost`, verified live chat-model
  input/output USD rates, and a key are **all required**.
- An explicit even number of calls from 2–72 and USD spend guard are
  required. The default allowance is 8 calls and 0.10 USD.
- Before each pair the script reserves 2x the conservative UTF-8
  bytes-based prompt-input estimate, 256 wrapper tokens and up to
  `max_tokens=110` per answer. It will not start a pair when the reserve
  exceeds the remaining local budget.
- **Important:** the spend guard is **not a guaranteed billing ceiling**;
  hidden retries, tokenization, upstream billing rules, errors and provider
  overhead can affect real charges. Use a low-balance, capped staging
  provider account if a hard monetary cap is needed.
- If the provider fails to return both prompt/completion token counts,
  stops immediately; it does not invent a cost. It calculates USD from
  the live model catalog and provider-reported usage; stores rate,
  per-response latency, results and paired comparisons in a local JSON.
- Not integrated with consumer coins; the synthetic evaluation does not
  authorize deductions from user wallets or spend any customer's credits.

## Reviewer guide

The CSV deliberately hides `legacy` vs `v2` arm names, and presents
answers as A/B, while the private JSON contains the mapping.

A human reviewer should score **both answers independently on 0, 1, 2**:
- factual accuracy grounded in synthetic transcript;
- non-fabrication and appropriate uncertainty;
- consistency with Mونا's personality/relationship;
- naturalness and emotional appropriateness in Persian.

Automated `grade()` merely detects presence of the exact expected string,
a few uncertainty expressions, leaks of internal prompt terms, and short
text formatting. These **are heuristics**, and are not equivalent to
hallucination rate, empathy, or professional quality judgments. The
unanswerable-category heuristic errs conservatively on unexpected
affirmative responses; blind review resolves ambiguity.

## Release threshold

Do **not** switch on `MONES_MEMORY_V2_ENABLED` merely because synthetic
retrieval showed 100% in an oracle fixture.

Advance only after:
1. Running paid model answers in isolated staging with an authorized,
   budgeted provider account;
2. Human blind reviews of generated responses, including unsafe
   certainty and incorrect historical claims;
3. Actual input/output token totals and model+embedding cost **for the same
   paired cases**, including provider retry overhead;
4. Measured p95 latency and no regression against legacy on verified
   factual/historical recall or natural conversational quality;
5. Explicit permission for any eventual evaluation of real/redacted
   historical user interactions (none are in this benchmark).

Phase 7 currently delivers the evaluation **tooling and no-cost
preflight**, not live-provider quality or price results.
