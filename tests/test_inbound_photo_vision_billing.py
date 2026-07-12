import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import User, Wallet, WalletTransaction, UsageCharge, AppSetting, MediaMessage, Message, DailyUsage, Subscription
from app.services.coin_pricing_service import CoinPricingService
from app.core.config import get_settings
from app.api import telegram


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Wallet.__table__, WalletTransaction.__table__, UsageCharge.__table__,
        AppSetting.__table__, MediaMessage.__table__, Message.__table__, DailyUsage.__table__,
        Subscription.__table__,
    ])
    return sessionmaker(bind=engine)()


def _user(db, balance=100):
    u = User(telegram_id=4242, display_name="Test", onboarding_step="complete")
    db.add(u); db.flush()
    db.add(Wallet(user_id=u.id, balance_coins=balance, total_added_coins=balance, total_spent_coins=0))
    db.flush()
    return u


def _settings(**overrides):
    real = get_settings()
    data = {
        "image_input_enabled": True,
        "max_image_bytes": real.max_image_bytes,
        "support_media_forward_enabled": False,
        "support_media_chat_id": "",
        "store_raw_user_images": False,
        "store_image_summary": True,
        "store_telegram_file_id": False,
        "vision_model": real.vision_model,
        "stt_model": real.stt_model,
        "billing_usd_to_toman": real.billing_usd_to_toman,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeTelegramService:
    token = "fake-token"

    def __init__(self):
        self.sent_texts = []
        self.actions = []

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))

    async def get_file_path(self, file_id):
        return "photos/file.jpg"

    async def download_file(self, file_path, destination):
        import os
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "wb") as fh:
            fh.write(b"image")
        return 5

    async def send_text(self, chat_id, text, reply_markup=None, reply_to_message_id=None, allow_sending_without_reply=None):
        self.sent_texts.append(text)
        return len(self.sent_texts)


def _message(message_id=77):
    return telegram.TelegramMessage.model_validate({
        "message_id": message_id,
        "from": {"id": 999, "first_name": "Ali", "username": "ali"},
        "chat": {"id": 555},
        "caption": "این چیه؟",
        "photo": [{"file_id": "file-1", "file_unique_id": "unique-1", "file_size": 100, "width": 640, "height": 480}],
    })


def _charges(db):
    return db.scalars(select(UsageCharge)).all()


def _txs(db):
    return db.scalars(select(WalletTransaction)).all()


def test_default_vision_model_can_be_quoted_without_keyerror():
    db = _db()
    quote = CoinPricingService().quote_tokens(db, model=get_settings().vision_model, feature="vision", input_tokens=1200, output_tokens=700)
    assert quote.charged_coins > 0
    assert quote.pricing_snapshot["input"]["feature"] == "vision_input"
    assert quote.pricing_snapshot["output"]["feature"] == "vision_output"


def test_stt_billing_still_uses_unit_pricing():
    db = _db(); u = _user(db)
    charge, quote = telegram._reserve_media_charge(db, u, feature="stt", model=get_settings().stt_model, quantity=30, key_suffix="voice-1")
    assert charge.feature == "stt"
    assert quote.pricing_snapshot["price"]["feature"] == "stt"
    assert quote.pricing_snapshot["quantity"] == "30"


def test_inbound_photo_reaches_vision_and_settles_charge(monkeypatch):
    db = _db(); u = _user(db); svc = FakeTelegramService(); calls = []
    monkeypatch.setattr(telegram, "get_settings", lambda: _settings())

    async def fake_analyze(path, *, user_caption=None, model=None):
        calls.append((path, user_caption, model))
        return {"model": model, "confidence": 0.9, "summary": "ok"}

    async def fake_chat(*args, **kwargs):
        return "دیدمش."

    monkeypatch.setattr(telegram, "analyze_image_with_venice", fake_analyze)
    monkeypatch.setattr(telegram, "handle_simple_chat", fake_chat)

    asyncio.run(telegram._handle_inbound_photo(db, _message(), u, svc, 555, _message().from_user))

    assert calls and calls[0][2] == get_settings().vision_model
    charge = _charges(db)[0]
    assert charge.feature == "vision" and charge.status == "settled" and charge.charged_coins > 0
    assert len([t for t in _txs(db) if t.type == "debit"]) == 1


def test_failed_vision_request_is_refunded_and_response_acknowledges_receipt(monkeypatch):
    db = _db(); u = _user(db); svc = FakeTelegramService()
    monkeypatch.setattr(telegram, "get_settings", lambda: _settings())

    async def fail_analyze(*args, **kwargs):
        raise RuntimeError("venice unavailable")

    monkeypatch.setattr(telegram, "analyze_image_with_venice", fail_analyze)

    asyncio.run(telegram._handle_inbound_photo(db, _message(), u, svc, 555, _message().from_user))

    charge = _charges(db)[0]
    assert charge.status == "refunded" and charge.refunded_coins == charge.reserved_coins
    assert u.wallet.balance_coins == 100
    assert any("عکستو دریافت کردم" in text for text in svc.sent_texts)
    assert all("دوباره بفرست" not in text for text in svc.sent_texts)


def test_reprocessing_same_telegram_message_does_not_double_charge(monkeypatch):
    db = _db(); u = _user(db); svc = FakeTelegramService(); calls = []
    monkeypatch.setattr(telegram, "get_settings", lambda: _settings())

    async def fake_analyze(*args, **kwargs):
        calls.append(1)
        return {"model": get_settings().vision_model, "confidence": 1}

    async def fake_chat(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(telegram, "analyze_image_with_venice", fake_analyze)
    monkeypatch.setattr(telegram, "handle_simple_chat", fake_chat)

    asyncio.run(telegram._handle_inbound_photo(db, _message(88), u, svc, 555, _message(88).from_user))
    after_first = u.wallet.balance_coins
    asyncio.run(telegram._handle_inbound_photo(db, _message(88), u, svc, 555, _message(88).from_user))

    assert len(_charges(db)) == 1
    assert u.wallet.balance_coins == after_first
    assert _charges(db)[0].status == "settled"


def test_support_forwarding_is_independent_of_vision_failure(monkeypatch):
    db = _db(); u = _user(db); svc = FakeTelegramService(); forwards = []
    monkeypatch.setattr(telegram, "get_settings", lambda: _settings(support_media_forward_enabled=True, support_media_chat_id="-100"))

    async def fake_forward(**kwargs):
        forwards.append(kwargs)
        return {"ok": True, "message_id": 321}

    async def fail_analyze(*args, **kwargs):
        raise RuntimeError("vision failed")

    monkeypatch.setattr(telegram, "forward_photo_to_support", fake_forward)
    monkeypatch.setattr(telegram, "analyze_image_with_venice", fail_analyze)

    asyncio.run(telegram._handle_inbound_photo(db, _message(99), u, svc, 555, _message(99).from_user))

    media = db.scalar(select(MediaMessage).where(MediaMessage.telegram_message_id == 99))
    assert forwards and media.support_forward_status == "sent" and media.support_message_id == 321
    assert media.processing_status == "failed"
