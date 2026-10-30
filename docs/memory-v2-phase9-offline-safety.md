# Phase 9 offline privacy and Telegram reliability

No credit, API key, account recharge, paid provider call, server, Telegram bot,
or production database is required for this phase.

## Fixed: consent can change while the embedding provider is responding

The semantic query records the persisted consent revision immediately before a
paid request. Once the response returns, it checks the **database-backed**
revision again. If the user revoked/re-enabled consent in the meantime, the
old response is dropped instead of being reused. This also prevents
`MemoryEmbeddingUsage` being recorded for an invalidated response in this
implementation. It does **not** undo a provider call already transmitted
before revocation; a real provider may still charge for that call.

## Fixed: indexing cannot resurrect erased derived memories

The manual indexing script now performs two consent checks: before each
provider request, and **after** the response immediately before writing its
vectors. The final PostgreSQL check obtains a `FOR UPDATE` lock on the
consent row, and checks its original revision plus operator allowlist.

The `/memory_off` / `/memory_clear` paths lock the same consent row.
Thus the ordered outcomes are:

- Revocation commits first: in-flight index aborts without saving.
- Index commits first: revocation subsequently deletes the saved vectors.

It also freezes the original message text/hash before sending it to the
provider and verifies source row IDs, roles and hashes again afterwards,
preventing vectors of deleted/edited messages from being stored under
the wrong source. These checks do not imply that all information held
by the provider can be recalled or deleted.

## Telegram command tests without external network

`tests/test_memory_telegram_webhook_v2.py` creates synthetic account,
message and privacy tables in SQLite and replaces Telegram delivery, billing
and forward batching with local fakes. The tests route through the actual
`app.api.telegram._handle` for **both chat and management bots**:

- `/memory_on`, `/memory`, `/memory_off` in private chat
- Rejection of group-origin memory commands
- `/memory_clear` removes extracted memory, **not raw chat** or
  content-free billing audit.
- The memory command path does not generate an LLM response or charge coins.

`tests/test_memory_privacy_v2.py` adds simulated mid-flight revocation,
revocation + re-consent, indexing after revocation and source edits.

## Remaining before release

- A real **staging PostgreSQL** two-connection concurrency stress test under
  actual transaction isolation, including slow provider responses. SQLite
  simulations are useful regression fixtures but do not prove all concurrent
  executions are serializable on the production database.
- Full incoming Telegram webhook signature/security testing with a private
  staging bot. Current tests are handler-level and do not use Telegram APIs.
- An independently reviewed complete account deletion/retention policy
  covering raw chat, cached prompts, media, backups, Redis and accounting.
- Actual paid Venice A/B quality and cost testing remains deferred until
  authorized budget is available.

All feature flags stay OFF, PR remains draft, and no production users or
servers are affected.
