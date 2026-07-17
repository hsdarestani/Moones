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

import pytest
from types import SimpleNamespace
from app.llm.image_client import VENICE_SEED_MIN, VENICE_SEED_MAX, venice_image_payload
from app.services.image_generation_service import deterministic_provider_seed, process_job


def test_venice_seed_normalization_range_and_no_minus_one():
    for raw in [1425953685, 1824167606, 1543286203, -1, 0, 1, 999999999]:
        payload=venice_image_payload('p','n',seed=raw)
        assert VENICE_SEED_MIN <= payload['seed'] <= VENICE_SEED_MAX
        assert payload['seed'] != -1
    assert VENICE_SEED_MIN <= deterministic_provider_seed('job22', 1425953685) <= VENICE_SEED_MAX


def test_job_seed_equals_metadata_seed_used_and_variations_differ():
    s=session(); u=User(telegram_id=7); s.add(u); s.flush()
    j1=ImageGenerationJob(idempotency_key='k1', correlation_id='c1', user_id=u.id, chat_id=1, seed=deterministic_provider_seed('same', 1), metadata_json={'seed_used':deterministic_provider_seed('same', 1)})
    j2=ImageGenerationJob(idempotency_key='k2', correlation_id='c2', user_id=u.id, chat_id=1, seed=deterministic_provider_seed('same', 2), metadata_json={'seed_used':deterministic_provider_seed('same', 2)})
    s.add_all([j1,j2]); s.commit()
    assert j1.seed == j1.metadata_json['seed_used']
    assert j2.seed == j2.metadata_json['seed_used']
    assert j1.seed != j2.seed


class _Client:
    def __init__(self): self.calls=[]
    async def generate(self, prompt, negative_prompt, *, width, height, seed):
        self.calls.append(seed)
        data = b'old-bytes' if len(self.calls)==1 else b'new-bytes'
        return SimpleNamespace(image_bytes=data, mime_type='image/png', request_id='r', width=width, height=height, latency_seconds=0.01, response_type='binary')

class _Telegram:
    async def send_photo_bytes(self, *args, **kwargs): return 123


def test_identical_checksum_triggers_one_controlled_variation_retry(monkeypatch):
    import asyncio
    async def _run():
        import app.services.image_generation_service as svc

        qa_calls = []

        async def fake_qa(*args, **kwargs):
            qa_calls.append(True)
            return {
                'person_count': 1,
                'single_continuous_frame': True,
                'has_panel_layout': False,
                'has_duplicate_or_reflection': False,
                'passed': True,
            }

        async def fake_archive(self, db, job):
            job.archive_status = 'disabled'
            return False

        monkeypatch.setattr(
            svc,
            'assess_generated_image_conformance',
            fake_qa,
        )
        monkeypatch.setattr(
            svc.GeneratedMediaArchiveService,
            'archive_image',
            fake_archive,
        )
        s=session(); u=User(telegram_id=9); s.add(u); s.flush()
        old=ImageGenerationJob(idempotency_key='old', correlation_id='old', user_id=u.id, chat_id=1, status='sent', sent_at=datetime.utcnow())
        s.add(old); s.flush(); s.add(ImageGenerationArtifact(job_id=old.id,mime_type='image/png',checksum=__import__('hashlib').sha256(b'old-bytes').hexdigest(),byte_size=9,image_bytes=b'old-bytes'))
        job=ImageGenerationJob(idempotency_key='new', correlation_id='new', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123, metadata_json={'route_type':'image_followup'})
        s.add(job); s.commit()
        client=_Client()
        await process_job(s, job, image_client=client, telegram_service=_Telegram())
        assert len(client.calls) == 2
        assert len(qa_calls) == 2
        assert client.calls[0] != client.calls[1]
        assert job.metadata_json['duplicate_retry_applied'] is True
        assert job.seed == job.metadata_json['seed_used']
    asyncio.run(_run())
