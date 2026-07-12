from decimal import Decimal, ROUND_CEILING
from app.services.wallet_service import grant_signup_welcome_credit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, WalletTransaction
from app.models.settings import AppSetting

def cc(v): return int((Decimal(v)/Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
def test_legacy_590000_converts_to_5900(): assert cc(590000) == 5900
def test_legacy_ceiling_preserves_value(): assert cc(590001) == 5901

def test_welcome_200_once_and_units():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AppSetting.__table__]); db=sessionmaker(bind=e)()
    u=User(telegram_id=9); db.add(u); db.flush(); db.add(Wallet(user_id=u.id,balance_coins=0)); db.flush()
    grant_signup_welcome_credit(db,u); grant_signup_welcome_credit(db,u)
    assert u.wallet.balance_coins == 200
    tx=db.query(WalletTransaction).filter_by(reason='signup_welcome_credit').all()
    assert len(tx)==1 and tx[0].unit == 'coin'


def test_0030_preserves_active_paid_subscriptions_idempotently():
    import importlib
    from datetime import datetime, timedelta
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, text

    e = create_engine('sqlite:///:memory:')
    md = MetaData()
    Table('wallets', md, Column('id', Integer, primary_key=True), Column('user_id', Integer), Column('balance_coins', Integer, default=0), Column('total_added_coins', Integer, default=0), Column('total_spent_coins', Integer, default=0), Column('currency_version', Integer, default=2))
    Table('wallet_transactions', md, Column('id', Integer, primary_key=True), Column('user_id', Integer), Column('wallet_id', Integer), Column('type', String), Column('amount_coins', Integer), Column('balance_after', Integer), Column('reason', String), Column('created_at', DateTime))
    Table('users', md, Column('id', Integer, primary_key=True), Column('welcome_coins_granted_at', DateTime), Column('welcome_coins_amount', Integer))
    Table('addon_products', md, Column('id', Integer, primary_key=True), Column('price_toman', Integer, default=0))
    Table('user_addons', md, Column('id', Integer, primary_key=True))
    Table('payment_receipts', md, Column('id', Integer, primary_key=True))
    Table('ai_usage_events', md, Column('id', Integer, primary_key=True))
    Table('subscriptions', md, Column('id', Integer, primary_key=True), Column('user_id', Integer), Column('plan', String), Column('status', String), Column('expires_at', DateTime))
    md.create_all(e)

    expires = datetime.utcnow() + timedelta(days=7)
    with e.begin() as conn:
        conn.execute(text("INSERT INTO users (id) VALUES (1), (2), (3)"))
        conn.execute(text("INSERT INTO wallets (id,user_id,balance_coins,total_added_coins,total_spent_coins,currency_version) VALUES (1,1,0,0,0,2),(2,2,0,0,0,2),(3,3,0,0,0,2)"))
        conn.execute(text("INSERT INTO subscriptions (id,user_id,plan,status,expires_at) VALUES (1,1,'vip','active',:e),(2,2,'plus','trialing',:e),(3,3,'free','active',:e)"), {'e': expires})
        ctx = MigrationContext.configure(conn)
        mig = importlib.import_module('migrations.versions.0030_coin_usage_billing')
        old_op = mig.op
        mig.op = Operations(ctx)
        try:
            mig.upgrade(); mig.upgrade()
        finally:
            mig.op = old_op
        rows = conn.execute(text("SELECT subscription_id,user_id,plan,status,expires_at,preservation_policy,converted_subscription_value FROM legacy_subscription_preservations ORDER BY subscription_id")).mappings().all()
        statuses = conn.execute(text("SELECT id,status,expires_at FROM subscriptions ORDER BY id")).mappings().all()

    assert [(r.subscription_id, r.user_id, r.plan, r.status, r.preservation_policy, r.converted_subscription_value) for r in rows] == [(1, 1, 'vip', 'active', 'preserve_until_expiry', 0), (2, 2, 'plus', 'trialing', 'preserve_until_expiry', 0)]
    assert len(rows) == 2
    assert statuses[0].status == 'active' and str(statuses[0].expires_at) == str(expires)
    assert statuses[1].status == 'trialing' and str(statuses[1].expires_at) == str(expires)
