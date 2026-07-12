import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction
from app.models.admin_security import AdminUser, AdminAuditEvent
from app.models.admin_coin_campaign import AdminCoinCampaign, AdminCoinCampaignRecipient
TABLES = [User.__table__, Wallet.__table__, WalletTransaction.__table__, AdminUser.__table__, AdminAuditEvent.__table__, AdminCoinCampaign.__table__, AdminCoinCampaignRecipient.__table__]

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session._session_factory = SessionLocal
    try:
        yield session
    finally:
        session.close()

import uuid
from sqlalchemy import select
from app.api.admin import _campaign_key, _preview_payload
from app.models.admin_coin_campaign import AdminCoinCampaign, AdminCoinCampaignRecipient
from app.models.wallet import Wallet
from app.models.user import User


def test_campaign_key_is_uuid_based_and_unique():
    one = _campaign_key("same")
    two = _campaign_key("same")
    assert one != two
    assert uuid.UUID(one)
    assert uuid.UUID(two)


def test_preview_does_not_mutate_wallets(db_session):
    db_session.add(User(telegram_id=101, display_name="A")); db_session.commit()
    before = db_session.scalar(select(Wallet).where(Wallet.user_id == 1))
    payload = _preview_payload(db_session, 5, "gift", "reason")
    assert payload["target_count"] >= 1
    assert before is None
    assert db_session.scalar(select(Wallet).where(Wallet.user_id == 1)) is None


def test_recipient_rows_are_unique(db_session):
    u = User(telegram_id=102); db_session.add(u); db_session.flush()
    c = AdminCoinCampaign(campaign_key=_campaign_key(), title="t", admin_note="n", amount_coins=1)
    db_session.add(c); db_session.flush()
    db_session.add(AdminCoinCampaignRecipient(campaign_id=c.id, user_id=u.id)); db_session.commit()
    db_session.add(AdminCoinCampaignRecipient(campaign_id=c.id, user_id=u.id))
    try:
        db_session.commit()
    except Exception:
        db_session.rollback()
        assert True
    else:
        assert False
