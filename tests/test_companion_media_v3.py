from __future__ import annotations

import asyncio
import base64
import importlib
from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from sqlalchemy import Column, Integer, MetaData, Numeric, Table, create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.llm.google_tts import SpeechBytes, google_speech
from app.llm.image_client import VeniceImageClient
from app.llm.tts_client import TTSFailure
from app.models.image_generation import ImageGenerationArtifact, ImageGenerationJob, PartnerVisualProfile
from app.models.user import User
from app.models.wallet import Wallet
from app.services.coin_pricing_service import CoinPricingService
from app.services.image_references import reference_for_job, remember_reference
from app.services.provider_pricing_registry import get_price
from app.services.speech_billing import reserve_google_speech, settle_google_speech


@pytest.fixture(autouse=True)
def clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def account():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(telegram_id=1, onboarding_step="complete")
    db.add(user); db.flush()
    yield db, user
    db.close(); engine.dispose()


def png(color="blue"):
    stream = BytesIO()
    Image.new("RGB", (512, 640), color).save(stream, format="PNG")
    return stream.getvalue()


def reference_fixture(db, user):
    source = ImageGenerationJob(idempotency_key="source", correlation_id="source", user_id=user.id,
        chat_id=1, status="sent", model="seedream-v5-lite", content_mode="normal", identity_fingerprint="face")
    profile = PartnerVisualProfile(user_id=user.id, profile_json={})
    db.add_all([source, profile]); db.flush()
    remember_reference(db, source, "telegram-file-id")
    db.add(ImageGenerationArtifact(job_id=source.id, image_bytes=png(), mime_type="image/png", checksum="hash", byte_size=len(png())))
    current = ImageGenerationJob(idempotency_key="current", correlation_id="current", user_id=user.id,
        chat_id=1, status="processing", model="seedream-v5-lite", content_mode="normal", identity_fingerprint="face",
        prompt="same person, different scene", metadata_json={"identity_fingerprint": "face", "expected_subject_count": 1})
    db.add(current); db.flush()
    return profile, source, current


def test_canonical_reference_is_stable_and_user_scoped(account):
    db, user = account
    profile, source, current = reference_fixture(db, user)
    data, source_id = asyncio.run(reference_for_job(db, current, object()))
    assert data == png() and source_id == source.id
    current.status = "sent"
    remember_reference(db, current, "second-file")
    assert profile.profile_json["canonical_reference"]["job_id"] == source.id
    source.user_id = user.id + 1; db.flush()
    assert asyncio.run(reference_for_job(db, current, object())) == (None, None)


def test_reference_generation_sends_actual_image_bytes_to_edit_endpoint():
    calls = []
    def handle(request):
        import json
        payload = json.loads(request.content)
        calls.append((str(request.url), payload))
        assert base64.b64decode(payload["images"][0]) == png()
        assert "telegram" not in request.content.decode()
        return httpx.Response(200, content=png("red"), headers={"content-type": "image/png"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            result = await VeniceImageClient(api_key="test", client=http).generate("new scene", "", model="seedream-v5-lite", reference_image=png())
            assert result.model == "seedream-v5-lite-edit"
            assert result.metadata["reference_image_used"] is True
    asyncio.run(run())
    assert len(calls) == 1 and calls[0][0].endswith("/image/multi-edit")
    assert calls[0][1]["enhance_prompt"] is False


def test_worker_passes_reference_and_keeps_actual_execution_model(account, monkeypatch):
    import app.services.image_generation_service as worker
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    from app.services.telegram_service import TelegramDeliveryResult
    db, user = account
    profile, source, current = reference_fixture(db, user)
    calls = []
    class Client:
        supports_reference_images = True
        async def generate(self, *args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(image_bytes=png("red"), mime_type="image/png", request_id="edit-request",
                model="seedream-v5-lite-edit", width=1024, height=1280, latency_seconds=.1,
                response_type="reference_edit", metadata={"reference_image_used": True, "seed_used": -1})
    class Telegram:
        async def send_photo_bytes(self, *args, **kwargs):
            return TelegramDeliveryResult(message_id=200, file_id="second-file")
        async def send_message(self, *args, **kwargs):
            return 201
    async def qa(*args, **kwargs):
        return GeneratedImageQAResult(True, 1, 1, False, False, False, False, False, False, "high", [], "test")
    async def archive(*args, **kwargs):
        return False
    monkeypatch.setattr(worker.GeneratedMediaArchiveService, "archive_image", archive)
    result = asyncio.run(worker.process_job(db, current, image_client=Client(), telegram_service=Telegram(), generated_image_qa_evaluator=qa))
    assert result.status == "sent", (result.error_code, result.error_message)
    assert calls[0]["reference_image"] == png()
    assert result.metadata_json["reference_source_job_id"] == source.id
    assert result.metadata_json["final_generation_model"] == "seedream-v5-lite-edit"


def test_google_speech_uses_unstored_request_and_reports_usage(monkeypatch):
    real_client = httpx.AsyncClient
    payloads = []
    def handle(request):
        import json
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "steps": [{"type": "model_output",
            "content": [{"type": "audio", "mime_type": "audio/ogg", "data": base64.b64encode(b"ogg-bytes").decode()}]}],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 500}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    settings = Settings(gemini_api_key="test-key", tts_provider="google")
    result = asyncio.run(google_speech("سلام", settings=settings, voice="Iapetus"))
    assert isinstance(result, bytes) and result == b"ogg-bytes"
    assert (result.input_tokens, result.output_tokens) == (10, 500)
    assert payloads[0]["store"] is False and payloads[0]["stream"] is False
    assert payloads[0]["generation_config"]["speech_config"] == [{"voice": "Iapetus"}]
    assert payloads[0]["input"][0]["content"][0]["text"] == "سلام"


def test_google_missing_key_never_starts_an_api_request(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("unexpected API request"))
    with pytest.raises(TTSFailure, match="google_key_missing"):
        asyncio.run(google_speech("سلام", settings=Settings(gemini_api_key=""), voice="Iapetus"))


def test_google_audio_is_settled_from_real_tokens_and_unknown_is_refunded(account):
    db, user = account
    db.add(Wallet(user_id=user.id, balance_coins=1000)); db.flush()
    charge, _ = reserve_google_speech(db, user, "سلام", "message-1")
    audio = SpeechBytes(b"audio", provider="google", model=charge.model, input_tokens=10, output_tokens=500)
    event = settle_google_speech(db, charge, audio)
    assert charge.status == "settled" and event.cost_usd == Decimal(".003005")
    charge2, _ = reserve_google_speech(db, user, "سلام", "message-2")
    event2 = settle_google_speech(db, charge2, b"no-usage")
    assert charge2.status == "refunded" and event2.cost_usd is None


def test_google_promotion_expiry_is_in_the_pricing_snapshot(monkeypatch):
    import app.services.provider_pricing_registry as registry
    class January(date):
        @classmethod
        def today(cls): return cls(2027, 1, 1)
    monkeypatch.setattr(registry, "date", January)
    rate = get_price("google", "gemini-3.8-flash-lite-tts", "tts_output")
    assert rate.standard_rate_usd == 12 and rate.effective_date == "2027-01-01"


def test_unknown_cost_migration_preserves_rows_and_refuses_lossy_downgrade():
    migration = importlib.import_module("migrations.versions.0046_usage_cost_unknown")
    engine = create_engine("sqlite:///:memory:")
    table = Table("ai_usage_events", MetaData(), Column("id", Integer, primary_key=True),
                  Column("cost_usd", Numeric, nullable=False), Column("cost_toman", Numeric, nullable=False))
    table.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, cost_usd=Decimal(".25"), cost_toman=15000))
        old_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            assert all(c["nullable"] for c in inspect(connection).get_columns("ai_usage_events") if c["name"].startswith("cost_"))
            connection.execute(table.insert().values(id=2, cost_usd=None, cost_toman=None))
            assert connection.scalar(select(table.c.cost_usd).where(table.c.id == 1)) == Decimal(".25")
            with pytest.raises(RuntimeError, match="Reconcile"):
                migration.downgrade()
        finally:
            migration.op = old_op


def test_internal_image_typeerror_does_not_repeat_a_possibly_paid_request():
    from app.llm.image_client import generate_once
    calls = []
    class Client:
        async def generate(self, prompt, negative, **options):
            calls.append(options)
            raise TypeError("internal decode failed")
    with pytest.raises(TypeError):
        asyncio.run(generate_once(Client(), "scene", "", model="seedream-v5-lite"))
    assert len(calls) == 1


def test_reference_cannot_be_dropped_for_an_incompatible_adapter():
    from app.llm.image_client import generate_once, ImageValidationError
    class Client:
        async def generate(self, prompt, negative, *, seed):
            pytest.fail("must reject before spending on an unreferenced image")
    with pytest.raises(ImageValidationError, match="reference_not_supported"):
        asyncio.run(generate_once(Client(), "scene", "", seed=1, reference_image=png()))
