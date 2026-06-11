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
