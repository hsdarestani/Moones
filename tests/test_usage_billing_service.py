from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, WalletTransaction, UsageCharge
from app.models.settings import AppSetting
from app.services.coin_pricing_service import CoinPricingService, BillingSettingsError
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins

@pytest.fixture
def db():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, UsageCharge.__table__, AppSetting.__table__]); S=sessionmaker(bind=e); s=S(); yield s; s.close()
def user(db, bal=100):
    u=User(telegram_id=123); db.add(u); db.flush(); db.add(Wallet(user_id=u.id,balance_coins=bal,total_added_coins=bal)); db.flush(); return u

def test_decimal_quote_formula_margin_100(db):
    q=CoinPricingService().quote_usd(db, Decimal('1'))
    assert q.provider_cost_toman == Decimal('60000') and q.charged_coins == 1200

def test_margin_zero_and_one_coin_min(db):
    db.add(AppSetting(key='billing.profit_margin_percent', value='0', value_type='decimal')); db.flush()
    assert CoinPricingService().quote_usd(db, Decimal('0.000001')).charged_coins == 1

def test_invalid_settings_rejected(db):
    s=CoinPricingService()
    with pytest.raises(BillingSettingsError): s.validate(Decimal('0'), Decimal('10'))
    with pytest.raises(BillingSettingsError): s.validate(Decimal('60000'), Decimal('1001'))

def test_successful_reserve_settle_and_partial_refund(db):
    u=user(db, 50); svc=UsageBillingService(); qp=CoinPricingService()
    reserve_q=qp.quote_usd(db, Decimal('0.01')) # 12 coins
    ch=svc.reserve(db,user=u,idempotency_key='upd:1:chat',feature='chat',provider='venice',model='qwen-3-6-plus',quote=reserve_q,correlation_id='c1')
    assert u.wallet.balance_coins == 38
    actual=qp.quote_usd(db, Decimal('0.005')) # 6 coins
    svc.settle(db, charge=ch, actual_quote=actual)
    assert ch.status == 'settled' and ch.charged_coins == 6 and ch.refunded_coins == 6 and u.wallet.balance_coins == 44

def test_full_refund_and_duplicate_idempotency(db):
    u=user(db, 20); svc=UsageBillingService(); q=CoinPricingService().quote_usd(db, Decimal('0.01'))
    ch1=svc.reserve(db,user=u,idempotency_key='same',feature='stt',provider='venice',model='openai/whisper-large-v3',quote=q)
    ch2=svc.reserve(db,user=u,idempotency_key='same',feature='stt',provider='venice',model='openai/whisper-large-v3',quote=q)
    assert ch1.id == ch2.id and u.wallet.balance_coins == 8
    svc.refund(db, charge=ch1, error='provider failed'); svc.refund(db, charge=ch1, error='provider failed')
    assert u.wallet.balance_coins == 20 and ch1.status == 'refunded'

def test_insufficient_prevents_call(db):
    u=user(db, 1); q=CoinPricingService().quote_usd(db, Decimal('1'))
    with pytest.raises(InsufficientCoins): UsageBillingService().reserve(db,user=u,idempotency_key='x',feature='chat',provider='venice',model='qwen-3-6-plus',quote=q)
