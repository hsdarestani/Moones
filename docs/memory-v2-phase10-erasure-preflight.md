# Phase 10 — zero-cost privacy erasure readiness and persona stress tests

**All changes are on draft PR #264 only. No production server, user data,
provider key, paid model or payment system was touched.**

## What has actually been built

### A schema-driven, read-only account erasure preflight

\`app/privacy/erasure_preflight.py\` traces **transitive foreign key edges** from
\`users\` and \`messages\`, including indirect relationships such as media
records and artifacts that could otherwise be missed by looking only for
\`user_id\`. It reports schema fingerprint, foreign-key delete rules, risky
financial/media tables, and columns likely to contain personal text.

\`preview_user_scope\` takes an internal user ID and returns only **counts**
for tables with an authoritative \`user_id\` ownership column, and clearly
lists indirect tables it cannot attribute without a separate traversal.
It deliberately does **not** read, print, export, copy, erase, anonymize or
overwrite any user's message text, payment data or media paths.

\`scripts/preview_account_erasure.py\` is operator-only and restricted to
\`localhost/mones_test\` on disposable staging PostgreSQL. It requires
\`--ack-incomplete\` plus the \`MONES_ERASURE_PREFLIGHT_CI=1\` environment
gate and has **no** destructive \`--apply\` flag. Example, in CI-like staging:

\`\`\`bash
MONES_ERASURE_PREFLIGHT_CI=1 \
DATABASE_URL='postgresql+psycopg://postgres:test-only@localhost:5432/mones_test' \
python -m scripts.preview_account_erasure --ack-incomplete

MONES_ERASURE_PREFLIGHT_CI=1 \
DATABASE_URL='postgresql+psycopg://postgres:test-only@localhost:5432/mones_test' \
python -m scripts.preview_account_erasure --ack-incomplete --user-id 1
\`\`\`

\`scripts/verify_account_erasure_preflight.py\` seeds two entirely synthetic
users in GitHub Actions' disposable PostgreSQL 16 after all migrations,
verifies row-count isolation, and confirms both users' source messages
still exist unchanged after the preview.

**Initial migrated staging inventory:** 42 relevant FK edges, 16 tables
requiring explicit FK handling, and 16 tables tagged for financial/media or
support retention review. These counts refer to **the CI schema**, not the
live user's database or amount of stored personal information.

### Why a live "delete entire account" button is still not exposed

Even \`ON DELETE CASCADE\` is unsafe without a legal/financial retention
decision: \`payment_receipts\`, usage charges, wallet transactions, and
other ledgers might be essential for reconciliation or statutory records.
Separate transcripts, file paths, attachments, generated images/voices,
support-message copies, Telegram IDs, Redis keys, external media storage and
backups may contain data beyond the \`users\` and \`messages\` tables.

A destructive workflow must have a policy for each class, validated
transaction ordering, worker cancellation, user confirmation/authorization,
auditable completion, backup expiry and provider deletion obligations.
**No account erasure function or Telegram \`/delete_account\` command has
been implemented yet.** \`/memory_clear\` continues to delete derived
memory only and explicitly warns that source chat and financial records
remain. This is intentional to prevent false privacy claims.

### Persona stress suite without model costs

\`tests/test_persona_adversarial_v2.py\` builds **288 combinations**
(2 prompt modes × 6 stages × 4 context modes × 6 untrusted injection
strings) using the real \`simple_chat._build_system_prompt\`. It checks
that the source memory, question, name, consent/age boundaries and quoted
message provenance survive both default and compact renderers. A separate
long-context test ensures relevant text is not cut off, aside from harmless
leading/trailing whitespace normalization.

These are structural checks only. They **cannot prove** that a remote LLM
will reject prompt injection, remain emotionally believable, avoid
hallucinations, or respect a particular relationship stage in generated
output. That needs authorized live testing and human grading later.

## Release gates and next work

1. Decide what billing and payment records must be retained vs pseudonymized.
2. Map all indirect media and provider copies; design cache and backup deletion.
3. Implement an authenticated two-step deletion request followed by an
   idempotent, recoverable, transaction-safe deletion worker in staging.
4. Verify this against synthetic data with all non-cascade dependencies,
   transaction faults and parallel incoming Telegram messages.
5. Only then offer self-service account deletion; keep existing Memory V2,
   semantic embedding and compact persona features disabled in production.

**Summary:** Phase 10 implements **verifiable erasure planning**, not
pretended full account removal, plus a substantial zero-cost persona
regression suite.
