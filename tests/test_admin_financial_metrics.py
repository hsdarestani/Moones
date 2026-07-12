from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, WalletTransaction, PaymentReceipt, UsageCharge, AppSetting, UserAddon
from app.services.admin_metrics_service import AdminMetricsService


def session():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, PaymentReceipt.__table__, UsageCharge.__table__, AppSetting.__table__, UserAddon.__table__])
    return sessionmaker(bind=e)()


def test_customer_payments_separated_from_gifts_and_refunds_not_revenue():
    db=session(); now=datetime.utcnow(); u=User(telegram_id=1); db.add(u); db.flush(); w=Wallet(user_id=u.id,balance_coins=25); db.add(w); db.flush()
    db.add(PaymentReceipt(user_id=u.id, telegram_file_id='f', telegram_file_type='image', amount_toman=100000, approved_coins=1000, status='approved', reviewed_at=now))
    db.add(WalletTransaction(user_id=u.id,wallet_id=w.id,type='credit',amount_coins=50,balance_after=50,reason='admin_gift',created_at=now))
    db.add(WalletTransaction(user_id=u.id,wallet_id=w.id,type='credit',amount_coins=5,balance_after=55,reason='usage_refund',created_at=now))
    db.add(UsageCharge(idempotency_key='c',user_id=u.id,wallet_id=w.id,feature='chat',provider='p',model='m',status='settled',charged_coins=20,refunded_coins=5,actual_cost_usd=2,exchange_rate_toman=60000,toman_per_coin=100,created_at=now))
    db.commit(); svc=AdminMetricsService(db); r=svc.build_range('today','UTC'); f=svc.financial_summary(r)
    assert f['approved_topup_amount_toman']==100000
    assert f['gift_promotional_coins_credited']==50
    assert f['refund_coins_credited']==5
    assert f['net_usage_coins']==15
    assert f['provider_cost_toman']==120000
    assert f['current_total_wallet_balance']==25
