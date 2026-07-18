from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.admin_security import SESSION_COOKIE, hash_password, hash_token
from app.db.base import Base
from app.models.addon import AddonProduct, UserAddon
from app.models.admin_security import AdminAuditEvent, AdminSession, AdminUser
from app.models.settings import AppSetting
from app.models.user import User
from app.services.addon_service import INTIMACY_MAX_UNLOCK


ADDON_KEYS = [
    INTIMACY_MAX_UNLOCK,
    "image_generation_unlock",
    "adult_image_generation_unlock",
    "voice_message_unlock",
]


@pytest.fixture()
def admin_addons_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    tables = [
        AdminUser.__table__,
        AdminSession.__table__,
        AdminAuditEvent.__table__,
        User.__table__,
        AddonProduct.__table__,
        UserAddon.__table__,
        AppSetting.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)

    import app.db.session as db_session
    import app.main as main_module

    monkeypatch.setattr(db_session, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)

    token = "admin-addon-session-token"
    csrf = hash_token("addon-csrf")
    with TestingSessionLocal() as db:
        admin = AdminUser(
            username="addon-admin",
            password_hash=hash_password("very-secure-password"),
            role="finance",
            is_active=True,
        )
        db.add(admin)
        db.flush()
        db.add(AdminSession(
            admin_user_id=admin.id,
            token_hash=hash_token(token),
            csrf_token_hash=csrf,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
        ))
        for index, key in enumerate(ADDON_KEYS):
            db.add(AddonProduct(
                key=key,
                title=f"Addon {index}",
                price_toman=1000 + index,
                sort_order=index,
                metadata_json={"preserved": key, "nested": {"index": index}},
            ))
        db.add(AppSetting(key="addon_intimacy_max_price_toman", value="100000", value_type="integer"))
        db.commit()

    client = TestClient(main_module.app)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        yield client, TestingSessionLocal, csrf
    finally:
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def _metadata_payload(csrf, **overrides):
    payload = {
        "csrf_token": csrf,
        "upsell_enabled": "on",
        "requires_adult": "on",
        "trigger_keywords": "اول\nsecond\n\n third ",
        "negative_keywords": "نه\nno",
        "upsell_title": "عنوان فارسی",
        "upsell_text": "متن فارسی برای پیشنهاد",
        "cta_text": "فعال‌سازی",
        "cooldown_hours": "12",
        "max_suggestions_per_7d": "3",
        "management_deeplink": "https://t.me/moones_bot?start=addons",
    }
    payload.update(overrides)
    return payload


def _metadata(session_factory, addon_key):
    with session_factory() as db:
        return db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key)).metadata_json


def test_metadata_save_preserves_existing_values_and_persists_after_fresh_session(admin_addons_client):
    client, session_factory, csrf = admin_addons_client
    response = client.post(
        "/admin/addons/image_generation_unlock/metadata",
        data=_metadata_payload(csrf),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/addons?saved=image_generation_unlock"
    meta = _metadata(session_factory, "image_generation_unlock")
    assert meta["preserved"] == "image_generation_unlock"
    assert meta["nested"] == {"index": 1}
    assert meta["upsell_title"] == "عنوان فارسی"
    assert meta["upsell_text"] == "متن فارسی برای پیشنهاد"
    assert meta["cta_text"] == "فعال‌سازی"


def test_checkbox_false_values_persist(admin_addons_client):
    client, session_factory, csrf = admin_addons_client
    payload = _metadata_payload(csrf)
    payload.pop("upsell_enabled")
    payload.pop("requires_adult")

    response = client.post("/admin/addons/image_generation_unlock/metadata", data=payload, follow_redirects=False)

    assert response.status_code == 303
    meta = _metadata(session_factory, "image_generation_unlock")
    assert meta["upsell_enabled"] is False
    assert meta["requires_adult"] is False


def test_multiline_trigger_keywords_persist_as_list(admin_addons_client):
    client, session_factory, csrf = admin_addons_client
    response = client.post("/admin/addons/image_generation_unlock/metadata", data=_metadata_payload(csrf), follow_redirects=False)

    assert response.status_code == 303
    meta = _metadata(session_factory, "image_generation_unlock")
    assert meta["trigger_keywords"] == ["اول", "second", "third"]


def test_all_four_addon_rows_save_independently(admin_addons_client):
    client, session_factory, csrf = admin_addons_client
    for index, key in enumerate(ADDON_KEYS):
        response = client.post(
            f"/admin/addons/{key}/metadata",
            data=_metadata_payload(csrf, upsell_title=f"عنوان {index}", trigger_keywords=f"kw-{index}"),
            follow_redirects=False,
        )
        assert response.status_code == 303

    with session_factory() as db:
        rows = db.scalars(select(AddonProduct).order_by(AddonProduct.sort_order)).all()
        assert [row.metadata_json["upsell_title"] for row in rows] == ["عنوان 0", "عنوان 1", "عنوان 2", "عنوان 3"]
        assert [row.metadata_json["trigger_keywords"] for row in rows] == [["kw-0"], ["kw-1"], ["kw-2"], ["kw-3"]]
        assert [row.metadata_json["preserved"] for row in rows] == ADDON_KEYS


def test_addon_metadata_success_message_visible(admin_addons_client):
    client, _, _ = admin_addons_client
    response = client.get("/admin/addons?saved=image_generation_unlock")

    assert response.status_code == 200
    assert "متادیتای افزودنی ذخیره شد." in response.text


def test_metadata_save_requires_csrf(admin_addons_client):
    client, session_factory, _ = admin_addons_client
    before = _metadata(session_factory, "image_generation_unlock")

    response = client.post(
        "/admin/addons/image_generation_unlock/metadata",
        data=_metadata_payload("bad-token", upsell_title="نباید ذخیره شود"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _metadata(session_factory, "image_generation_unlock") == before


def test_non_intimacy_price_update_does_not_update_intimacy_setting(admin_addons_client):
    client, session_factory, csrf = admin_addons_client

    response = client.post(
        "/admin/addons/image_generation_unlock/price",
        data={"csrf_token": csrf, "price_toman": "5555"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as db:
        assert db.scalar(select(AddonProduct.price_toman).where(AddonProduct.key == "image_generation_unlock")) == 5555
        assert db.scalar(select(AppSetting.value).where(AppSetting.key == "addon_intimacy_max_price_toman")) == "100000"


def test_intimacy_price_update_updates_intimacy_setting(admin_addons_client):
    client, session_factory, csrf = admin_addons_client

    response = client.post(
        f"/admin/addons/{INTIMACY_MAX_UNLOCK}/price",
        data={"csrf_token": csrf, "price_toman": "7777"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with session_factory() as db:
        assert db.scalar(select(AppSetting.value).where(AppSetting.key == "addon_intimacy_max_price_toman")) == "7777"
