# Moones Memory V2 — Server-independent first release

## Status

This branch introduces a **feature-flagged first phase** of long-term
memory improvements, including a source-linked **temporal fact/event ledger**.
It does **not** yet implement a full temporal *knowledge graph*, embedding
index, learned compression or multi-character world state.

The existing PostgreSQL `messages` table remains the canonical, unmodified
event log; `memory_items` remains the durable explicit-memory store. No
external API, server access, or paid memory provider is required
to review this change. Enabling temporal storage **does** require running
Alembic revision `0042_temporal_memory_v2` after taking a backup.

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
python -m pytest -q tests/test_episodic_context_v2.py tests/test_temporal_memory_v2.py
python -m pytest -q
```

Run with DB credentials configured by the existing test environment.

## Deploy later, only after server is provided

1. Back up and verify the existing PostgreSQL data (never reset volumes).
2. Build/stage from this branch; run `alembic heads` and verify a single
   head at `0042_temporal_memory_v2`, then apply `alembic upgrade head`.
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
