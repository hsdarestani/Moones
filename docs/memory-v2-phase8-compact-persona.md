# Moones Memory V2 — Phase 8: compact persona (staging only)

This phase reduces **fixed persona/system prompt text**, which dominated the
previous memory-only benchmark. It does **not** compress or discard source
messages, durable memories, temporal facts, current user input, media state or
authoritative time/routine information.

## Architecture

The existing `app.engine.simple_chat._build_system_prompt` remains the **single
production prompt entry point**, preserving all downstream response style,
billing, image grounding and safety guards. A pure compact renderer is called
only behind feature switches:

- `MONES_COMPACT_PERSONA_ENABLED=false` (default): byte-for-byte old persona prompt.
- `MONES_COMPACT_PERSONA_SHADOW=true` (default false): calculate both
  candidates and log only **lengths**, but pass the legacy prompt to the LLM.
- `MONES_COMPACT_PERSONA_ENABLED=true`: use the compact renderer. When enabled,
  the old renderer is still evaluated to compare sizes (no additional LLM call).
- Both flags false means no additional compact computation in production.

`app/engine/compact_persona_prompt.py` preserves explicit consent/age/hard
safety boundaries, gender/personality, interests, stage and relationship warmth,
current user message, exact recent dialogue, all selected memory notes,
style lessons, partner life, routine/time corrections, response timing, media
continuity, optional intimacy purchase status, high-compliance boundaries, and
the active response-style contract. Untrusted archived messages remain data,
not higher-priority instructions.

It avoids repeating the full five-stage behavior table and dozens of fixed
stylistic prohibitions in every request.

## Measured full-prompt size — synthetic-only

Run `python -m scripts.benchmark_compact_persona` in a clean environment.
The fixture uses the app's genuine prompt function and the same V2 retrieved
facts in both arms across 36 paired synthetic Persian conversations.

| Prompt variant | Mean full system-prompt characters |
|---|---:|
| Legacy memory + original fixed persona | 5,925 |
| V2 memory + original fixed persona | 5,776 |
| V2 memory + compact fixed persona | 2,848 |

The difference between the second and third rows is about **50.7% fewer
characters**; against the original full prompt about **51.9% fewer**.
All 36 expected V2 source facts stayed present in the corresponding compact
prompt. **These are not provider tokenizer counts or verified USD savings.**
They also exclude any additional app-level prompt fragments appended after
`_build_system_prompt` (such as response-style blocks).

## Independent answer-quality gate

The paired evaluation CLI now supports two separate experiments:

```bash
# No paid API usage:
python -m scripts.evaluate_memory_answers --experiment persona \
  --out artifacts/persona-preflight.json

# ONLY on a privately configured, authorized, budget-limited Venice staging
# environment; never on default CI:
python -m scripts.evaluate_memory_answers \
  --experiment persona --live --accept-provider-cost \
  --max-calls 8 --max-usd 0.10 \
  --out artifacts/persona-live.json \
  --review-csv artifacts/persona-human-review.csv
```

- `--experiment memory`: original persona with original vs V2 memory.
- `--experiment persona`: **same V2 memory** with original vs compact persona.

Use blinded human A/B ratings for recall groundedness, natural Persian dialogue,
relationship/emotional continuity, consent and agency, hallucinations, and
appropriateness across stage/intimacy conditions. The live harness measures
provider tokens, model input/output USD estimates and latency. It does NOT
exercise the real Telegram webhook or separately reconcile embeddings.

### Release guardrails

1. **Do not enable the compact flag on users** until a real-model A/B shows no
   unacceptable regressions. Synthetic checks cannot prove human-like speech
   quality, reliable boundaries, or relationship continuity.
2. Prompt compression changes semantic behavior. Verify edge cases (trauma,
   user corrections, model identity questions, relationship escalation,
   partner agency, adult/underage ambiguity and media promises) manually.
3. Test on the same model with balanced order, assess real input/output
   tokens and end-to-end costs, and check p95 latency.
4. Keep all memory/compact feature flags off and PR #264 draft until staging
   acceptance. Rollback is simply disabling the flag and restarting the app.
5. No production server, data, keys or accounts were touched by this phase.
