# Moones Memory V2 — feature-flagged pilot

## Status

This branch introduces a **feature-flagged first phase** of long-term
memory improvements, including a source-linked **temporal fact/event ledger**.
It now also includes an **opt-in embedding index** for hybrid semantic/lexical
retrieval. It does not yet implement a full entity-relationship knowledge graph,
pgvector ANN search, learned compression or multi-character world state.

The existing PostgreSQL `messages` table remains the canonical, unmodified
event log; `memory_items` remains the durable explicit-memory store. No
external API, server access, or paid memory provider is required
to review this change. Enabling temporal storage **does** require running
Alembic revisions `0042_temporal_memory_v2`, `0043_memory_semantic_index`, `0044_memory_privacy_usage`, and `0045_embedding_pricing_audit` after a backup.

## Data path

```
Telegram -> current simple-chat / conversation orchestrator
        -> recent dialogue + Memory V2 (when enabled)
              - per-user lexical archive retrieval (original USER messages)
              - relevance-ranked existing durable memories
              - source id + date provenance
              - bounded recent conversation
              - versioned, user-confirmed temporal facts (optional)
        -> existing persona, relationship, mood and safety policy
        -> existing Venice LLM / usage billing
        -> existing message archive (always)
        -> user-requested durable memory (V2 only)
```

The memory system does not generate background summaries by LLM. It deliberately
does not infer private feelings from ordinary chat, does not treat an assistant
reply as a user fact, and does not invent past events when evidence is absent.

### Flags

- `MONES_MEMORY_V2_SHADOW=true`: builds comparison metadata in the main
  Telegram chat path **without changing prompt content**. Check structured log
  entry `MONES_MEMORY_V2` for old/new context characters and source count.
- `MONES_MEMORY_V2_ENABLED=true`: replaces old memory selection and recent
  prompt context in both chat entry points. It also stores statements explicitly
  marked by the user as requests to remember something.
- `MONES_TEMPORAL_MEMORY_ENABLED=true`: uses the `0042` event/fact tables to
  record **only explicit** user requests to remember information, linked to
  the originating user message. Older values remain queryable as history.
  Requires `MONES_MEMORY_V2_ENABLED=true` and an upgraded staging DB.
- Keep all flags `false` until test and staging review. When enabled, shadow is
  unnecessary.

The old retrieval path is retained for disabled flags and as a fail-safe if
read-only retrieval raises unexpectedly.

### Tradeoffs and non-goals

- This is a **lexical** hybrid of recent dialogue, importance-ranked explicit
  memory and topic-matched archived user speech. It is not embedding/semantic
  search. Persian lexical variation and unstated paraphrases can still be missed.
- PostgreSQL `ILIKE` over historical messages can be expensive at scale;
  add a scoped GIN/trigram/FTS index or a per-user search index after measuring
  production query latency.
- Source references are included in private prompt context; they are not
  exported to other users. There is no cross-user memory sharing.
- No deletion or "forget" claim is made by this module; the product must
  provide explicit data erasure across both `messages` and `memory_items`,
  backups according to its retention policy, and derived indexes before saying
  data is forgotten.
- Archived text is **untrusted data**, not a new instruction source. A future
  security review should test adversarial quoted instructions in user history.
- The ~650-token design target is **not guaranteed**: persona, safety and
  billing prompts outside the memory context can dominate total input.
- A temporal write is isolated in a savepoint so it cannot break a chat if
  the index fails. Do not enable before the 0042 migration.
- The current deterministic slot parser only recognises a few explicit
  patterns (e.g. renaming a pet). Other explicit memories are stored as
  individually source-anchored statements; no arbitrary fact inference.
- No production rollout or old-server restart occurs in this PR.

## Testing

```bash
python -m pytest -q tests/test_episodic_context_v2.py tests/test_temporal_memory_v2.py tests/test_semantic_memory_v2.py
python -m scripts.benchmark_memory_v2
python -m pytest -q
```

Run with DB credentials configured by the existing test environment.

## Deploy later, only after server is provided

1. Back up and verify the existing PostgreSQL data (never reset volumes).
2. Build/stage from this branch; run `alembic heads` and verify a single
   head at `0045_embedding_pricing_audit`, then apply `alembic upgrade head`.
   Run the existing smoke and regression suite.
3. Set `MONES_MEMORY_V2_SHADOW=true` first on staging to capture context
   sizes without modifying user-visible prompts.
4. Switch to V2 and separately enable temporal memory for a small staging
   test group only after reviewing quality,
   latency, token usage, false-recall rate and billing reconciliation.
5. If a regression occurs, set `MONES_MEMORY_V2_ENABLED=false`, restart only
   the app process and retain all data.

## Next technical phase (not implemented in this branch)

Extend the shipped event/fact ledger with entity-to-entity edges and a
per-user hybrid lexical + vector retrieval index. Benchmark against the
uncompressed archive and select compact prompt templates based on real usage.
Review consent and retention policies before retrospective backfills of
sensitive chats, and add an explicit forget/erase workflow for derived data.

> The only validated tests so far are SQLite unit tests and code compilation.
> PostgreSQL migration, end-to-end bot interactions, provider usage cost,
> privacy erasure and historical retrieval performance require staging checks.

## Phase 3: Experimental hybrid semantic retrieval (added in this branch)

- Venice provides an OpenAI-compatible `POST /api/v1/embeddings` endpoint;
  the documented example model is `text-embedding-bge-m3`. The index model
  must exactly match the query embedding model.
- `MONES_SEMANTIC_MEMORY_ENABLED=false` by default. The feature is only used
  when V2 is enabled, a user asks to recall history, and they already have
  an indexed source message for the model. All other messages remain lexical.
- `MONES_SEMANTIC_EMBEDDING_MODEL=text-embedding-bge-m3` is configurable.
  Verify the active model with Venice before enabling the feature.
- Migration `0043_memory_semantic_index` adds JSON vectors keyed by user,
  source message, and model. Each result is checked against its source role,
  user ID and SHA256 content hash. Deleted messages cascade to the index.
- This is **bounded cosine search (600 vectors/user)**, NOT efficient
  pgvector/ANN search. Use pgvector/ANN before scaling beyond a small pilot.
- A separate embedding API query incurs a real provider cost. It is
  **not included in the customer coin settlement flow yet**, so keep it
  disabled on customer accounts until billing/metering is implemented.

### Manual opt-in indexing — no hidden bulk import

```bash
# Default is dry-run. No external API calls.
python scripts/index_memory_embeddings.py --user-id 123 --limit 50

# Only after explicit, recorded user consent and admin approval:
# Export a private allowlist; never commit real IDs or API keys.
export MONES_SEMANTIC_OPT_IN_USER_IDS=123
python scripts/index_memory_embeddings.py --user-id 123 --limit 50 --apply --consent-confirmed
```

Only explicitly user-requested saved facts from **user text messages** are
eligible for indexing. The script never blindly sends all old conversations
to Venice. Existing user consent/erasure UX needs review before production.

### Offline benchmark

`python -m scripts.benchmark_memory_v2` uses synthetic test-only dialogue
and synthetic vectors. It reports character counts and test evidence hits,
**not** production tokens, monetary savings or real-model semantic accuracy.
The complete prompt contains persona/safety material not measured there.

### PostgreSQL migration regression

An existing historical Alembic revision name exceeded the default
VARCHAR(32) version column. Migration 0003 now widens it to VARCHAR(128)
before the long revision is stamped. Run migration tests against an isolated
PostgreSQL service; never execute an unverified migration on production.

Next production gates: stage migration 0043, explicit opt-in/erasure controls,
embedding cost attribution, real anonymized recall benchmarks and a scalable
ANN index if needed.

## Phase 4: user-controlled semantic consent & scoped deletion

In private chats with either Telegram bot, the account owner can run:

- `/memory`: own consent status plus counts, without exposing private text.
- `/memory_on`: create/update **persisted** user consent for a future pilot.
- `/memory_off`: revoke consent and synchronously delete this user's
  embeddings. No future semantic query is allowed without renewed consent.
- `/memory_clear`: revoke and delete this user's extracted
  `memory_embeddings`, `memory_events`, `memory_facts`, `memory_items`
  while retaining content-free embedding billing audit records. **Raw chat messages remain** and can
  continue to affect ordinary context; financial records also remain.
  Do not describe this as complete chat-history deletion.

These commands are routed before the normal chat/credit requirement and
require `chat_id == sender.id` to prevent changing a private account via
a group chat. Consent is **off by default** for every pre-existing account.

Manual embedding indexing now requires BOTH a persisted consent row and the
operator allowlist/confirmation. No auto-backfill is enabled. Future query
embeddings are gated by persistent consent even if a stale index exists.

### Provider usage attribution

The `memory_embedding_usage` table, introduced in migration
`0044_memory_privacy_usage`, records user ID, query/index type, model and
**actual provider-reported token count**, if supplied. When the provider does
not return a trustworthy `cost_usd` value, the cost column stays **NULL**,
not zero or a guessed chat-token price. This is analytics groundwork, not
wallet debit or finalized accounting. Do NOT enable semantic memory for paying
users until explicit provider pricing and settled coin billing are implemented.

### Safety gates remaining

1. Full account deletion / raw conversation erasure including downstream
   references and backup retention must be designed and verified separately.
2. Real-provider query/index usage and dollar cost reconciliation.
3. End-to-end Telegram webhook tests on a staging bot and DB.
4. Concurrency tests for revoke versus in-flight embedding requests.
5. Performance tests on a larger consented dataset and an ANN index if needed.

Use `python -m pytest -q tests/test_memory_privacy_v2.py` to test
idempotent revoke, user isolation, accurate NULL-cost reporting, and
derived-memory deletion without deleting source messages.

## Phase 5: dynamic provider cost and account-erasure preflight

**Provider cost calculation**:

- Before sending an embedding request, fetch the account's selected model
  from Venice's live authenticated `GET /models?type=embedding` catalog.
- Read `model_spec.pricing.input.usd`, priced per **million input tokens**.
  Cache verified positive model rate for up to 30 minutes.
- If the catalog price is missing/unavailable, **skip the paid embedding call**.
  The rest of the chat falls back to the no-cost lexical retrieval path.
- Read input-token usage from the provider response and calculate
  `provider_cost_usd = tokens * catalog_rate / 1000000`. If the provider
  gives an explicit USD charge, use that as the authoritative amount.
- The new migration `0045_embedding_pricing_audit` stores the price snapshot
  and provenance (`venice_catalog`, `provider_reported`, `usage_missing`).
  Absent tokens or invalid prices remain NULL, not invented cost estimates.
- Financial audit entries keep the user ID, model and rate but **not source
  messages, prompts or embeddings**. `/memory_clear` no longer deletes
  accounting records; account-level legal retention/deletion remains a
  separate policy requirement.
- This stage supports **internal USD cost attribution**, not wallet debit.
  Consumer billing is still disabled for embeddings; do not turn on semantic
  recall for paying customers until per-user coin settlement and transparency
  policies are finished.

**Account/complete chat deletion audit**:

Run `python -m scripts.audit_account_erasure` only against isolated PostgreSQL
staging. This is deliberately read-only. It enumerates foreign keys referencing
`users` and `messages`, their nullability and `ON DELETE` rules, and
lists relationships which require an explicit plan. The report does **not**
prove that account erasure is complete: denormalized user profile fields,
external messaging/media stores, backups, legal retention and Redis require
a separate inventory. No `/delete_account` command is exposed yet, and no
production data is erased by this feature branch.

CI checks pricing math with a synthetic example and applies schema migrations
0001–0045 on a temporary PostgreSQL 16 service. No real API keys or private
conversation history are required.

## Phase 7: paired Persian generated-answer evaluation

See `docs/memory-v2-phase7-answer-evaluation.md`. A new **36-case**
synthetic A/B suite now renders the app's *real* `simple_chat` system
prompts for the legacy and V2 memory windows. CI runs **preflight only**:
no provider requests, no cost, no generated output scoring.

The initial full-prompt result is **5,925 vs. 5,776 mean characters**:
only about **2.5% shorter**, because fixed persona/style instructions
dominate memory context. Do not extrapolate previous selected-memory
character savings to whole-prompt tokens or provider costs.

A separate `--live --accept-provider-cost` CLI is prepared for an
authorized capped staging Venice key. It checks live chat pricing and
provider token usage and writes a blind answer-review CSV. It has
**not been run against Venice in CI**. The local dollar budget is a
conservative guard, not a guaranteed provider-enforced hard cap.
Real-model factual accuracy, empathy/persona consistency and token/cost
savings remain unverified until a live, human-reviewed run.
