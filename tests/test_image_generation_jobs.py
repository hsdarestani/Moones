from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction
from app.models.addon import AddonProduct, UserAddon
from app.models.billing import UsageCharge
from app.models.usage import AiUsageEvent
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.services.image_generation_service import claim_next_job, cleanup_stale_artifacts

def session():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AddonProduct.__table__, UserAddon.__table__, UsageCharge.__table__, AiUsageEvent.__table__, PartnerVisualProfile.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__, ImageGenerationFeedback.__table__])
    return sessionmaker(bind=e)()

def test_claim_sets_lock_and_expired_lock_recovery():
    s=session(); u=User(telegram_id=1); s.add(u); s.flush()
    j=ImageGenerationJob(idempotency_key='k', correlation_id='c', user_id=u.id, chat_id=1, scheduled_at=datetime.utcnow())
    s.add(j); s.commit()
    claimed=claim_next_job(s); assert claimed.id == j.id and claimed.status == 'processing' and claimed.lock_expires_at
    assert claim_next_job(s) is None
    claimed.status='queued'; claimed.lock_expires_at=datetime.utcnow()-timedelta(seconds=1); s.commit()
    assert claim_next_job(s).id == j.id

def test_stale_artifact_cleanup_clears_bytes():
    s=session(); u=User(telegram_id=1); s.add(u); s.flush(); j=ImageGenerationJob(idempotency_key='k', correlation_id='c', user_id=u.id, chat_id=1); s.add(j); s.flush()
    a=ImageGenerationArtifact(job_id=j.id,mime_type='image/png',checksum='x',byte_size=3,image_bytes=b'abc',created_at=datetime.utcnow()-timedelta(hours=7)); s.add(a); s.commit()
    assert cleanup_stale_artifacts(s, older_than_hours=6) == 1
    assert s.get(ImageGenerationArtifact, a.id).image_bytes is None
