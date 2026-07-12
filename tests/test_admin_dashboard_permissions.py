from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import AppSetting, UserAddon, PaymentReceipt, UsageCharge, Wallet, WalletTransaction, User, AiUsageEvent, ImageGenerationJob, GeneratedVoiceOutput, ProactiveMessage
from app.services.admin_metrics_service import AdminMetricsService


def db():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[AppSetting.__table__, UserAddon.__table__, PaymentReceipt.__table__, UsageCharge.__table__, Wallet.__table__, WalletTransaction.__table__, User.__table__, AiUsageEvent.__table__, ImageGenerationJob.__table__, GeneratedVoiceOutput.__table__, ProactiveMessage.__table__]); return sessionmaker(bind=e)()

def test_viewer_cannot_see_financial_values_and_finance_can():
    svc=AdminMetricsService(db()); r=svc.build_range('today','UTC')
    assert svc.dashboard(r,'viewer')['financial'] is None
    assert svc.dashboard(r,'finance')['financial'] is not None
