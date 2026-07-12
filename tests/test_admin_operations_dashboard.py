from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, UsageCharge, PaymentReceipt, AppSetting, ImageGenerationJob, GeneratedVoiceOutput, ProactiveMessage, AiUsageEvent
from app.services.admin_metrics_service import AdminMetricsService


def test_stuck_reserved_and_image_archive_alerts_use_settings():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, UsageCharge.__table__, PaymentReceipt.__table__, AppSetting.__table__, ImageGenerationJob.__table__, GeneratedVoiceOutput.__table__, ProactiveMessage.__table__, AiUsageEvent.__table__]); db=sessionmaker(bind=e)(); now=datetime.utcnow(); u=User(telegram_id=1); db.add(u); db.flush(); w=Wallet(user_id=u.id); db.add(w); db.flush()
    db.add(AppSetting(key='admin.alert.archive_failure_count',value='1',value_type='integer'))
    db.add(UsageCharge(idempotency_key='r',user_id=u.id,wallet_id=w.id,feature='chat',provider='p',model='m',status='reserved',created_at=now-timedelta(hours=2)))
    db.add(ImageGenerationJob(idempotency_key='i',correlation_id='i',user_id=u.id,chat_id=1,status='queued',archive_status='failed',created_at=now-timedelta(hours=1)))
    db.commit(); svc=AdminMetricsService(db); ops=svc.operations_summary(svc.build_range('today','UTC'))
    assert ops['billing']['stuck_reserved']==1
    assert ops['image_jobs']['queued']==1 and ops['image_jobs']['failed_archive_deliveries']==1
    assert any(a['severity']=='warning' for a in svc.alerts(ops))
