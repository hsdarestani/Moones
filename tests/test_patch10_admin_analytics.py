from datetime import datetime, timedelta
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["database_url"] = "sqlite:///:memory:"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.admin import _analytics_overview, _partner_analytics, _plan_distribution, _range, _revenue_by_plan
from app.db.base import Base
from app.models import AnalyticsEvent, DailyUsage, Message, PaymentReceipt, ProactiveMessage, Relationship, Subscription, SupportMessage, User, Wallet


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_patch10_range_has_daily_labels():
    start, end, labels = _range("7d")
    assert end > start
    assert len(labels) == 7


def test_patch10_empty_analytics_helpers_do_not_crash(db_session):
    overview = _analytics_overview(db_session, "30d")
    assert overview["kpis"]
    assert _plan_distribution(db_session)["free"] == 0
    assert _partner_analytics(db_session)["depth"]["intimacy"] == 0


def test_patch10_revenue_and_plan_distribution(db_session):
    user = User(telegram_id=9001, display_name="Patch 10", created_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
    db_session.add(user); db_session.flush()
    db_session.add(Subscription(user_id=user.id, plan="plus", status="active"))
    db_session.add(PaymentReceipt(user_id=user.id, telegram_file_id="file", telegram_file_type="photo", amount_toman=199000, status="approved", metadata_json={"target_plan": "plus"}))
    db_session.add(Relationship(user_id=user.id, stage="PARTNER", intimacy=.5, trust=.6, attachment=.7, attraction=.8))
    db_session.commit()
    start = datetime.utcnow() - timedelta(days=1); end = datetime.utcnow() + timedelta(days=1)
    assert _revenue_by_plan(db_session, start, end)["plus"] == 199000
    assert _plan_distribution(db_session)["plus"] == 1
    assert _partner_analytics(db_session)["relationship_stage"]["PARTNER"] == 1


def test_patch10_admin_static_and_rtl_markers_exist():
    base = open("app/templates/admin/base.html", encoding="utf-8").read()
    css = open("app/static/admin.css", encoding="utf-8").read()
    js = open("app/static/admin.js", encoding="utf-8").read()
    assert 'dir="rtl"' in base
    assert "/static/admin.css" in base
    assert "ApexCharts" in js
    assert "@media" in css
