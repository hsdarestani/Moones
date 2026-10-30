# Moones Memory V2 — Server-independent first release

## Status

This branch introduces a **feature-flagged first phase** of long-term
memory improvements; it does **not** yet implement a persistent temporal graph,
embedding index, learned compression or multi-character world state.

The existing PostgreSQL \`messages\` table remains the canonical, unmodified
event log; \`memory_items\` remains the durable explicit-memory store. No
migration, external API, server access, or paid memory provider is required
to review this change.

## Data path

\`\`\`
Telegram -> current simple-chat / conversation orchestrator
        -> recent dialogue + Memory V2 (when enabled)
              - per-user lexical archive retrieval (original USER messages)
              - relevance-ranked existing durable memories
              - source id + date provenance
              - bounded recent conversation
        -> existing persona, relationship, mood and safety policy
        -> existing Venice LLM / usage billing
        -> existing message archive (always)
        -> user-requested durable memory (V2 only)
\`\`\`

The memory system does not generate background summaries by LLM. It deliberately
does not infer private feelings from ordinary chat, does not treat an assistant
reply as a user fact, and does not invent past events when evidence is absent.

### Flags

- \`MONES_MEMORY_V2_SHADOW=true\`: builds comparison metadata in the main
  Telegram chat path **without changing prompt content**. Check structured log
  entry \`MONES_MEMORY_V2\` for old/new context characters and source count.
- \`MONES_MEMORY_V2_ENABLED=true\`: replaces old memory selection and recent
  prompt context in both chat entry points. It also stores statements explicitly
  marked by the user as requests to remember something.
- Keep both \`false\` until test and staging review. When enabled, shadow is
  unnecessary.

The old retrieval path is retained for disabled flags and as a fail-safe if
read-only retrieval raises unexpectedly.

### Tradeoffs and non-goals

- This is a **lexical** hybrid of recent dialogue, importance-ranked explicit
  memory and topic-matched archived user speech. It is not embedding/semantic
  search. Persian lexical variation and unstated paraphrases can still be missed.
- PostgreSQL \`ILIKE\` over historical messages can be expensive at scale;
  add a scoped GIN/trigram/FTS index or a per-user search index after measuring
  production query latency.
- Source references are included in private prompt context; they are not
  exported to other users. There is no cross-user memory sharing.
- No deletion or "forget" claim is made by this module; the product must
  provide explicit data erasure across both \`messages\` and \`memory_items\`,
  backups according to its retention policy, and derived indexes before saying
  data is forgotten.
- Archived text is **untrusted data**, not a new instruction source. A future
  security review should test adversarial quoted instructions in user history.
- The ~650-token design target is **not guaranteed**: persona, safety and
  billing prompts outside the memory context can dominate total input.
- No production rollout or old-server restart occurs in this PR.

## Testing

\`\`\`bash
python -m pytest -q tests/test_episodic_context_v2.py
python -m pytest -q
\`\`\`

Run with DB credentials configured by the existing test environment.

## Deploy later, only after server is provided

1. Back up and verify the existing PostgreSQL data (never reset volumes).
2. Build/stage from this branch; run the existing smoke and regression suite.
3. Set \`MONES_MEMORY_V2_SHADOW=true\` first on staging to capture context
   sizes without modifying user-visible prompts.
4. Switch to V2 for a small test user group only after reviewing quality,
   latency, token usage, false-recall rate and billing reconciliation.
5. If a regression occurs, set \`MONES_MEMORY_V2_ENABLED=false\`, restart only
   the app process and retain all data.

## Next technical phase (not implemented in this branch)

Add versioned, source-anchored \`memory_events\` and \`memory_facts\` tables with
valid-time windows, supersession edges and deletion hooks; build a per-user
hybrid lexical + vector retrieval index, optionally a temporal graph for
multi-character relationships; benchmark against the uncompressed archive and
select compact prompt templates based on real usage. Review consent and
retention policies before running retrospective backfills of sensitive chats.
