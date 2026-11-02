# Companion experience V3

Implementation based on `feature/episodic-memory-v2` at `d9b40fc`.
No production credentials, customer records or paid provider calls are used in
the local validation. Model quality, real image identity fidelity and listening
quality are not established by mocks or synthetic fixtures.

## Chat and continuity

- The live simple-chat path sends fixed instructions as `system`, source-grounded
  memory as quoted reference data, original conversation roles as `user` /
  `assistant`, and the current user turn last. The legacy orchestrator also
  preserves roles. Existing renderers remain available for historical benchmarks.
- Normal turns share one budget of **at most two provider requests** and a
  default 14-second provider deadline. The provider client never hides a second
  reasoning-only request. Timeouts are not blindly retried.
- An application-owned HTTP connection pool avoids reconnecting for every turn.
  Artificial delivery delay is at most 0.6 seconds, minus model processing time.
- Re-delivered chat update/message IDs reuse the stored answer or failure without
  another model request. Same-account webhook updates serialize within a worker;
  different accounts remain concurrent. This is not a durable cross-worker queue.
- Legacy memory now ranks topic overlap before importance. Memory V2 semantic
  consent, erasure behavior and opt-in gates remain intact.
- A narrow deterministic event index records direct first-person dated plans for
  interviews, exams, trips and presentations, plus explicit personal outcomes.
  It understands today/tomorrow/day after tomorrow, not arbitrary calendar text.
  Questions, quotes, hypothetical/third-person statements and translation requests
  are not treated as plans. Unreported outcomes remain unknown. These events are
  removed by `/memory_clear` together with other derived memory; raw chat remains.
- Character life uses a persistent weekly project theme with daily variation,
  computed lazily without an LLM. Scheduled daily creation selects recently active
  accounts that have not already received an event, rather than repeatedly
  selecting the first accounts. `PARTNER_LIFE_LLM_ENABLED` defaults to false.
- Purchased intimacy/cooperative style remains a prompt overlay. New purchases
  no longer manufacture earned trust, attachment or relationship milestones.
  Existing historical relationship values are not reset.
- Failed generations do not advance the relationship, increment successful-chat
  counters or trigger voice, stickers or human-style follow-up extras.

## Models, accounting and observability

- `llm.primary_persian_model` is respected by simple chat. Supported, priced
  candidates are `qwen-3-6-plus`, `qwen3-235b-a22b-instruct-2507`, and
  `qwen-3-8-flash`. The current baseline remains the default. There is no
  per-message model lottery or automatic promotion of an untested candidate.
- Every chat provider attempt has its own usage event, including unsuccessful
  and internal repair attempts. Internal repairs are operating costs: the user
  settles only the final usable answer. Re-delivery does not create another charge.
- Actual provider token counts determine settlement. UTF-8 reservation bounds
  are explicitly estimates, not measured usage. Missing usage/pricing is NULL,
  not a fabricated zero. Unknown final usage is refunded.
- Cache reads/writes have separate rates and snapshots. Historical settlements
  remain untouched. Rates are a dated estimate, not reconciliation with invoices.
- Financial reporting includes known metered attempts (including internal repairs),
  adds historical charges without a linked metered event, and avoids counting
  linked events twice. Unknown-cost events are visible in the dashboard.
- These records improve coverage; they do not retroactively prove complete
  historical provider accounting or meter every legacy media/vision path.

## Images

- After a successful ordinary single-partner delivery, retain a canonical pointer
  to that account's own delivered fictional character image, not raw full image
  bytes. Never silently replace the canonical image with each new scene.
- For an eligible later ordinary Seedream job, use actual reference image bytes
  with `seedream-v5-lite-edit` through `/image/multi-edit`. Reference reads are
  scoped to user, chat, identity fingerprint and a delivered normal source job.
  Telegram bot-token URLs are never sent to the image provider.
- `image_generation.reference_edit_enabled` controls this route. The edit price
  is registered separately. Actual execution model and reference source are
  recorded. Existing composition/safety checks still run on the output.
- Existing accounts without a stored canonical reference establish it after their
  next successful eligible delivery; no private historical bulk import occurs.
- Adult Krea routing and mandatory safety/QA gates remain intact. Image transport
  defaults to one attempt; explicit callers can still configure controlled retries.
- Reference conditioning improves the mechanism; **actual face consistency is
  not validated by the mocked HTTP or worker tests**.

## Speech

- The same speaker ID is used across moods. `TTS_STABLE_VOICE=false` restores
  the legacy selector for compatibility. Emotion may affect delivery independently.
- Venice remains the default. `TTS_PROVIDER=google` explicitly selects the direct
  `gemini-3.8-flash-lite-tts` backend and requires `GEMINI_API_KEY`.
  There is no automatic cross-provider fallback or paid read-timeout retry.
- Google requests use `store=false`, a stable voice, a bounded output size and
  the documented Interactions REST shape. The output is converted to Telegram Ogg.
- Google speech settles from reported text/audio tokens. Missing usage is recorded
  as unknown and refunded. The documented 2026 promotion expiry is represented
  in the rate snapshot; the registered rates double from 2027-01-01.
- Delivery/listening/latency, account availability and model-specific content
  compatibility still need a funded isolated provider smoke test.

## Verification and release

Run without provider credentials:

```bash
python -m pytest -q tests/test_companion_experience_v3.py tests/test_companion_media_v3.py
python -m scripts.evaluate_companion_models --out artifacts/companion-preflight.json
python -m alembic heads
```

The comparison CLI defaults to zero API calls. Live mode requires both `--live`
and `--accept-provider-cost`, a privately configured key, verified account-specific
model rates, `--max-calls` and a bounded `--max-usd` spend guard. A local guard is
not a guaranteed provider-side spending cap. Human review scores remain blank;
no naturalness, hallucination or retention score is invented.

The `Companion experience` workflow runs the new and directly related contracts.
The `Memory V2 tests` workflow runs the existing synthetic/privacy tests and clean
PostgreSQL migrations, schema verification and consent-race checks.

Local validation on Python 3.12: 96 directly related tests and 340 memory/privacy
tests pass. The full-suite comparison has no newly failing test against
`d9b40fc`: the baseline has 75 failures and this revision has 73 existing failures
(primarily legacy image-policy/vision fixtures and product-copy expectations).
This is not a claim that the repository's full release suite is green. The
final deadline and replay edge cases also pass the directly related suite.

Apply migrations through **0046_usage_cost_unknown** before deploying the new
accounting path. Migration 0046 makes usage costs nullable without rewriting
existing costs. A downgrade refuses to turn unknown charges into zero.
Keep existing memory/semantic/compact flags at their current default-OFF values
until the separate real-model quality gate passes. Schema changes do not
authorize deleting customer history, spending provider credits or changing the
default production chat/speech provider.

Primary API references (checked 2026-11-02):

- https://docs.venice.ai/overview/pricing
- https://docs.venice.ai/guides/features/prompt-caching
- https://docs.venice.ai/api-reference/endpoint/image/multi-edit
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://ai.google.dev/gemini-api/docs/pricing
