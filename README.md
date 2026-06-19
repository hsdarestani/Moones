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
| `SUPPORT_USERNAME` | empty | Deprecated; user support now happens in-bot and sends messages to `ADMIN_TELEGRAM_IDS`. |
| `DEFAULT_FREE_DAILY_LIMIT` | `30` | Daily message limit for free users. |
| `DAILY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for daily pass users. |
| `WEEKLY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for weekly pass users. |
| `MONTHLY_PASS_MESSAGE_LIMIT` | `500` | Daily backend cap for monthly users. |
| `PREMIUM_MESSAGE_LIMIT` | `1000` | Daily backend cap for premium users. |

## Mones V4: Venice, dual bots, manual wallet payments, settings, emoji and stickers

### Venice direct API

Mones now uses the direct Venice chat completions API as the primary LLM provider:

```env
VENICE_API_KEY=
VENICE_API_BASE_URL=https://api.venice.ai/api/v1
VENICE_MODEL=qwen-3-6-plus
# Primary Venice model slug for production Persian chat: qwen-3-6-plus
LLM_DEBUG=false
PROMPT_MODE=simple_partner_v2
VENICE_TIMEOUT_SECONDS=6
```

`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENAI_API_KEY`, and `OPENAI_MODEL` are no longer required for the app to start. The app posts chat requests to `{VENICE_API_BASE_URL}/chat/completions` and records the last provider/model/status/error/token usage on the admin user detail page.

### Two Telegram bots

Configure a management bot and a chat bot:

```env
TELEGRAM_MANAGEMENT_BOT_TOKEN=
TELEGRAM_MANAGEMENT_BOT_USERNAME=
TELEGRAM_CHAT_BOT_TOKEN=
TELEGRAM_CHAT_BOT_USERNAME=
ADMIN_TELEGRAM_IDS=123456789,987654321
```

If `TELEGRAM_MANAGEMENT_BOT_TOKEN` is missing, the app falls back to `TELEGRAM_BOT_TOKEN` for the management bot.

Webhook setup:

```bash
curl -X POST "https://api.telegram.org/bot<MANAGEMENT_BOT_TOKEN>/setWebhook?url=https://YOUR_DOMAIN/telegram/management/webhook"
curl -X POST "https://api.telegram.org/bot<CHAT_BOT_TOKEN>/setWebhook?url=https://YOUR_DOMAIN/telegram/chat/webhook"
```

The legacy `/telegram/webhook` endpoint remains as an alias for the management bot. The management bot handles onboarding, partner editing, wallet, subscriptions, manual payment receipts, support, settings, and admin approval callbacks. The chat bot handles AI partner conversation only and redirects users who have not completed onboarding back to the management bot.

### Manual payment and wallet-based subscriptions

Users top up their wallet manually before activating subscriptions:

1. User opens `➕ افزایش موجودی`.
2. The bot shows the configurable `payment.link` (default: `https://www.coffeebede.com/gotomarket`).
3. User taps `پرداخت کردم` and sends a receipt photo/document.
4. Admins listed in `ADMIN_TELEGRAM_IDS` receive the receipt in the management bot.
5. Admin approves with a coin amount or rejects with a note.
6. Approved receipts credit the user wallet through `WalletService` and create wallet ledger transactions.
7. User activates daily, weekly, or monthly subscription from wallet balance.

Public paid plans are daily, weekly, and monthly. Free remains internal/default.

### Admin settings and limits

Run the migration below, then use `/admin/settings` to edit prices and limits without redeploying:

- `subscription.daily.price_coins`
- `subscription.weekly.price_coins`
- `subscription.monthly.price_coins`
- `limits.free.daily_messages`
- `limits.daily.daily_messages`
- `limits.weekly.daily_messages`
- `limits.monthly.daily_messages`
- `payment.link`
- `support.username` (deprecated; support replies are handled in-bot)
- `llm.venice.model`
- `emoji.enabled`, `emoji.probability`, `emoji.max_per_message`
- `stickers.enabled`, `stickers.probability`, `stickers.max_per_day_per_user`

Daily chat limits are read dynamically from `AppSetting` before every chat-bot LLM call.

### Payment receipts in admin

Use `/admin/receipts` to view pending/all receipts, filter by status, approve with coin amount, or reject with a note. User detail pages also show recent payment receipts, wallet/subscription/usage, and the latest Venice LLM diagnostics.

### Sticker engine

Use `/admin/stickers` to add sticker packs and sticker file IDs with usage contexts such as `greeting`, `affection`, `playful`, `sad_support`, `goodnight`, `miss_you`, `celebration`, `apology`, `thinking`, `romantic`, and `comfort`.

Admins can also send `/addsticker` to the management bot and then send a Telegram sticker. The bot captures the sticker `file_id` and stores it for later contextual sending. Stickers are occasional, gated by relationship stage and daily per-user caps, and are never sent after failed LLM responses.

### Emoji and Iranian tone

The prompt builder and response post-processor now push natural casual Iranian Persian, avoid assistant-like closings/bullets, and add a configurable emoji engine that appends a small number of Persian-compatible emotional emojis in a controlled percentage of chat responses.

### Migration

Run:

```bash
alembic upgrade head
```

The V4 migration is `0004_venice_dual_bots_payment_settings_stickers.py`; it creates `app_settings`, `payment_receipts`, `sticker_packs`, `sticker_items`, adds `daily_usage.daily_stickers_sent`, and adds user fields for LLM diagnostics/payment/admin state.
