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

from sqlalchemy import select
from app.api.admin import _campaign_key, _execute_campaign_recipients, _recalculate_campaign_counts
from app.models.admin_coin_campaign import AdminCoinCampaign, AdminCoinCampaignRecipient
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction


def test_each_user_credited_once_and_second_execute_no_double_credit(db_session):
    users = [User(telegram_id=201), User(telegram_id=202)]
    db_session.add_all(users); db_session.flush()
    c = AdminCoinCampaign(campaign_key=_campaign_key(), title="t", admin_note="n", amount_coins=7, status="running")
    db_session.add(c); db_session.flush()
    for u in users: db_session.add(AdminCoinCampaignRecipient(campaign_id=c.id, user_id=u.id))
    db_session.commit()
    _execute_campaign_recipients(c.id, limit=10, session_factory=db_session._session_factory)
    _execute_campaign_recipients(c.id, limit=10, session_factory=db_session._session_factory)
    txs = db_session.scalars(select(WalletTransaction).where(WalletTransaction.reason == "admin_bulk_gift")).all()
    assert len(txs) == 2
    assert sum(w.balance_coins for w in db_session.scalars(select(Wallet)).all()) == 14


def test_users_without_wallets_are_supported_and_counters_match(db_session):
    u = User(telegram_id=203); db_session.add(u); db_session.flush()
    c = AdminCoinCampaign(campaign_key=_campaign_key(), title="t", admin_note="n", amount_coins=3, status="running")
    db_session.add(c); db_session.flush(); db_session.add(AdminCoinCampaignRecipient(campaign_id=c.id, user_id=u.id)); db_session.commit()
    _execute_campaign_recipients(c.id, limit=10, session_factory=db_session._session_factory)
    c = db_session.get(AdminCoinCampaign, c.id); _recalculate_campaign_counts(db_session, c)
    assert c.credited_count == 1
    assert c.total_credited_coins == 3
    assert db_session.scalar(select(Wallet).where(Wallet.user_id == u.id)).balance_coins == 3
