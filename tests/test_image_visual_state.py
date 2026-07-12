from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.memory import MemoryItem
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationFeedback
from app.services.image_prompt_engine import build_image_prompt, resolve_visual_scene_state
from app.llm.image_client import venice_image_payload, image_resolution_tier


def db():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Message.__table__, MemoryItem.__table__, PartnerVisualProfile.__table__, ImageGenerationJob.__table__, ImageGenerationFeedback.__table__])
    return sessionmaker(bind=e)()

def user(s):
    u=User(telegram_id=10, display_name='u', onboarding_step='complete', partner_name='Solmaz', partner_age_range='30', partner_gender='female')
    s.add(u); s.commit(); return u


def test_assistant_physical_state_available_to_later_generic_request():
    s=db(); u=user(s)
    recent=[SimpleNamespace(role='assistant', id=1, created_at=datetime.utcnow(), content='تو خونه‌م، روی مبل لم دادم و دارم برای خواب آماده می‌شم.')]
    res=build_image_prompt(s,user=u,user_request='یه عکس بده ببینمت',recent_conversation=recent,time_context=SimpleNamespace(local_hour=23, daypart='night'))
    assert 'sofa' in res.prompt and ('reclining' in res.prompt or 'lying back' in res.prompt)
    assert 'relaxed natural pose' not in res.prompt
    assert res.width == 1280 and res.height == 1024 and res.orientation == 'landscape'


def test_latest_explicit_pose_wins_and_colloquial_variants():
    recent=[SimpleNamespace(role='assistant', id=1, created_at=datetime.utcnow(), content='روی صندلی نشستم'), SimpleNamespace(role='assistant', id=2, created_at=datetime.utcnow(), content='الان لم دادم رو مبل')]
    st=resolve_visual_scene_state('یه عکس بده', recent)
    assert st.pose and 'reclining' in st.pose
    assert 'sofa' in (st.scene or '')
    assert 'reclining' in resolve_visual_scene_state('ولو شدم روی مبل', []).pose


def test_refinement_critiques_add_constraints_and_negative_terms():
    s=db(); u=user(s)
    res=build_image_prompt(s,user=u,user_request='این یکی زشته، لم ندادی و مبل هم معلوم نیست و نوشته داره. یه عکس بهتر بده',recent_conversation=[],time_context=SimpleNamespace(local_hour=23, daypart='night'))
    assert 'flattering believable lighting' in res.prompt
    assert 'sofa must be clearly visible' in res.prompt
    assert 'not sitting upright' in res.prompt or 'exclude sitting upright' in res.prompt
    for term in ['uncanny face','malformed hands','floating body','warped furniture','close-up headshot','inconsistent identity','Persian writing']:
        assert term in res.negative_prompt


def test_provider_payload_uses_selected_dimensions_and_same_tier():
    payload=venice_image_payload('p','n',width=1280,height=1024)
    assert payload['width'] == 1280 and payload['height'] == 1024
    assert image_resolution_tier(1024,1280) == image_resolution_tier(1280,1024) == 'image_1k'
