# Mones — AI Romantic Companion for Telegram

Mones is a Telegram-based relational AI companion. It is designed as a relationship state machine plus memory and emotional policy engine where the LLM executes a carefully controlled relational plan.

## Product Goals

- Build stable, long-running user relationships.
- Preserve daily interaction loops and retention signals.
- Grow intimacy gradually from `STRANGER` to `PARTNER`.
- Maintain long-term relational memory.
- Keep responses emotionally supportive while avoiding unsafe dependency.

## Architecture

```text
User Message (Telegram)
        ↓
Telegram Webhook (FastAPI)
        ↓
Conversation Orchestrator
        ↓
State Engine (Relationship + Emotion)
        ↓
Memory Retrieval Layer
        ↓
Prompt Builder
        ↓
LLM API (OpenRouter)
        ↓
Post Processor (tone + safety + memory update)
        ↓
Telegram Response
```

### Core Modules

1. **Telegram Interface Layer** — webhook endpoint and Telegram send APIs.
2. **Conversation Orchestrator** — central flow controller.
3. **Relationship State Engine** — ARES-style relational scoring and stage transitions.
4. **Memory System** — structured Postgres memory plus vector/graph extension points.
5. **Persistence + Analytics Layer** — SQLAlchemy models for users, messages, relationships, and memories.

## Relationship State

Each user has a persistent relationship state:

- `intimacy`
- `attachment`
- `trust`
- `dependency`
- `attraction`
- `volatility`
- `stage`: `STRANGER → FAMILIAR → FRIEND → ROMANTIC → PARTNER`

State transitions are driven by interaction frequency, emotional depth, memory activation, and return behavior.

## Safety Principles

- No medical, legal, or crisis advice beyond supportive redirection.
- No self-harm encouragement.
- Controlled emotional dependency.
- No explicit sexual escalation unless explicitly enabled by configuration.
- No hallucinated memories; prompts may only use retrieved memories.

## Local Development

### 1. Create environment

```bash
cp .env.example .env
```

Fill in at least:

```dotenv
TELEGRAM_BOT_TOKEN=
OPENROUTER_API_KEY=
ADMIN_USER=
ADMIN_PASSWORD=
SECRET_KEY=
```

If `OPENROUTER_API_KEY` is empty, the app uses a deterministic Persian fallback response for local testing.

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run locally

```bash
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

## Docker Deployment

### Step 1: Clone

```bash
git clone <repo>
cd Moones
```

### Step 2: Set environment

```bash
cp .env.example .env
```

### Step 3: Build containers

```bash
docker-compose build
```

### Step 4: Run system

```bash
docker-compose up -d
```

### Step 5: Database migration

```bash
docker exec -it mones-app alembic upgrade head
```

### Step 6: Set Telegram webhook

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.com/telegram/webhook"
```

## Environment Variables

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token. |
| `OPENROUTER_API_KEY` | OpenRouter API key. |
| `OPENROUTER_MODEL` | OpenRouter chat model name; defaults to `cognitivecomputations/dolphin-mistral-24b-venice-edition:free`. |
| `ADMIN_USER` | Basic-auth username for `/admin`. |
| `ADMIN_PASSWORD` | Basic-auth password for `/admin`. |
| `DATABASE_URL` | SQLAlchemy database URL. |
| `REDIS_URL` | Redis URL for future session/cache work. |
| `SECRET_KEY` | Secret used for signing/hashing utilities. |
| `ALLOW_EXPLICIT_CONTENT` | Optional feature flag for stricter content control. |

## Production Success Criteria

- Latency under 2 seconds for normal replies.
- Memory recall accuracy above 70%.
- Daily retention above 25%.
- Average session length above 8 minutes.

## Wallet, Subscription, and Bot Menu

Mones includes a Telegram reply-keyboard main menu after onboarding with shortcuts for chat, partner profile, subscriptions, wallet, test top-up, relationship status, settings, and support.

### Persian onboarding

The onboarding wizard is fully Persian and collects partner gender, name, age range, personality, and multi-select interests. Users cannot enter normal chat until onboarding is complete. Completing onboarding also ensures a wallet and default free subscription state exist.

### Wallet

Each user has one wallet with:

- current coin balance,
- total added coins,
- total spent coins,
- a transaction ledger for every credit, debit, adjustment, or refund.

Normal chat does **not** deduct coins yet. The wallet service already exposes `credit`, `debit`, `get_balance`, and `can_afford` so future pay-per-message or hybrid plans can be added without bypassing the ledger.

### Temporary test top-up

Real payment gateways are not connected yet. When `ENABLE_TEST_WALLET_TOPUP=true`, Telegram users can use **➕ افزایش موجودی** to add 100, 500, or 1000 test coins. Each test top-up creates a `credit` transaction with reason `test_topup`.

When `ENABLE_TEST_WALLET_TOPUP=false`, users only see a Persian “coming soon” message and no coins are added.

### Subscriptions and soft limits

Subscription plans are configured as metadata and enforce soft daily message caps in the backend:

| Plan | Public positioning | Duration | Daily backend cap |
| --- | --- | --- | --- |
| Free | 30 daily messages, limited memory | none | 30 |
| Daily | normal unlimited-style daily access | 1 day | 500 |
| Weekly | multi-day access | 7 days | 500 |
| Monthly | deeper relationship and better memory | 30 days | 500 |
| Premium | highest quality, full memory, priority | 30 days | 1000 |

Purchase buttons currently show a payment placeholder only; they do not activate paid plans. Admins can activate plans manually from the dashboard for testing.

### Daily usage tracking

Before each LLM call, Mones checks the active/free subscription limit and today's usage. If the user has reached the daily cap, the LLM is not called and a Persian limit message is returned. Usage increments only after a successful LLM response is sent through the orchestrator.

### Admin dashboard additions

The admin dashboard shows wallet balance, subscription plan/status/expiry, today's usage, total coins added, and total coins spent. User detail pages include actions to:

- add coins,
- subtract coins,
- activate daily/weekly/monthly/premium plans,
- cancel a subscription,
- reset daily usage.

All admin wallet changes go through the wallet service and create ledger transactions.

### Deployment notes

Run migrations before deploying the wallet/subscription feature:

```bash
alembic upgrade head
```

The `0003_wallet_subscription_usage.py` migration creates `wallets`, `wallet_transactions`, `subscriptions`, and `daily_usage`, and backfills free wallets/subscriptions for existing users. Wallets are also created lazily in application code for safety.

### Additional environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `ENABLE_TEST_WALLET_TOPUP` | `false` | Enables Telegram test-only wallet top-up buttons. Keep disabled in production unless intentionally testing. |
| `SUPPORT_USERNAME` | empty | Support/admin Telegram username shown in the support menu. If empty, the bot shows `@YOUR_SUPPORT_USERNAME`. |
| `DEFAULT_FREE_DAILY_LIMIT` | `30` | Daily message limit for free users. |
| `DAILY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for daily pass users. |
| `WEEKLY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for weekly pass users. |
| `MONTHLY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for monthly users. |
| `PREMIUM_MESSAGE_LIMIT` | `1000` | Daily backend cap for premium users. |
