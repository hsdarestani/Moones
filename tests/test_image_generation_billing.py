from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from app.db.base import Base
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction
from app.models.addon import AddonProduct, UserAddon
from app.models.billing import UsageCharge
from app.models.usage import AiUsageEvent
from app.models.settings import AppSetting
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.services.addon_service import IMAGE_GENERATION_UNLOCK
from app.services.image_generation_service import enqueue_image_request, ImageGenerationDenied

def session():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AddonProduct.__table__, UserAddon.__table__, UsageCharge.__table__, AiUsageEvent.__table__, AppSetting.__table__, PartnerVisualProfile.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__, ImageGenerationFeedback.__table__])
    return sessionmaker(bind=e)()

def test_user_without_addon_cannot_enqueue():
    s=session(); u=User(telegram_id=1, partner_age_range='24'); s.add(u); s.commit()
    with pytest.raises(ImageGenerationDenied): enqueue_image_request(s,user=u,chat_id=1,source_telegram_message_id=1,user_request='عکس بساز')

def test_duplicate_telegram_update_one_job_and_reservation():
    s=session(); u=User(telegram_id=1, partner_age_range='24'); s.add(u); s.flush(); s.add(Wallet(user_id=u.id,balance_coins=10000)); s.add(UserAddon(user_id=u.id,addon_key=IMAGE_GENERATION_UNLOCK,status='active')); s.commit()
    a=enqueue_image_request(s,user=u,chat_id=1,source_telegram_message_id=99,user_request='عکس بساز')
    b=enqueue_image_request(s,user=u,chat_id=1,source_telegram_message_id=99,user_request='عکس بساز')
    assert a.id == b.id and s.query(ImageGenerationJob).count() == 1 and s.query(UsageCharge).count() == 1
