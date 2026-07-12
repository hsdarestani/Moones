from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, UsageCharge, AiUsageEvent, AppSetting
from app.services.admin_metrics_service import AdminMetricsService


def test_linked_events_not_double_counted_in_usage_breakdown():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, UsageCharge.__table__, AiUsageEvent.__table__, AppSetting.__table__]); db=sessionmaker(bind=e)(); now=datetime.utcnow(); u=User(telegram_id=1); db.add(u); db.flush(); w=Wallet(user_id=u.id,balance_coins=100); db.add(w); db.flush()
    ev=AiUsageEvent(user_id=u.id,feature='chat',provider='p',model='m',status='success',charged_coins=99,created_at=now); db.add(ev); db.flush()
    db.add(UsageCharge(idempotency_key='c',user_id=u.id,wallet_id=w.id,usage_event_id=ev.id,feature='chat',provider='p',model='m',status='settled',charged_coins=10,actual_cost_usd=1,created_at=now)); db.commit()
    rows=AdminMetricsService(db).usage_breakdown(AdminMetricsService(db).build_range('today','UTC'))
    assert rows[0]['requests']==1 and rows[0]['charged_coins']==10


def test_date_boundaries_respect_timezone():
    db=sessionmaker(bind=create_engine('sqlite:///:memory:'))(); svc=AdminMetricsService(db); r=svc.build_range('custom','Asia/Tehran','2026-01-02','2026-01-02')
    assert r.start_utc.hour == 20 and r.end_utc.hour == 20
