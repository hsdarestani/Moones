from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.memory import MemoryItem
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationFeedback
from app.services.image_prompt_engine import build_image_prompt, resolve_visual_scene_state, ensure_visual_profile
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


def test_persian_street_ice_cream_recent_assistant_scene_not_home_selfie():
    s=db(); u=user(s)
    recent=[SimpleNamespace(role='assistant', id=7, created_at=datetime.utcnow(), content='الان توی خیابون دارم بستنی می‌خورم')]
    res=build_image_prompt(s,user=u,user_request='عکس بفرست',recent_conversation=recent,time_context=SimpleNamespace(local_hour=17, daypart='evening'))
    p=res.prompt.lower()
    assert 'tehran street' in p or 'street' in p
    assert 'ice cream' in p and ('holding' in p or 'eating' in p)
    assert 'environment clearly visible' in p or 'visible urban environment' in p
    assert 'home interior' not in p and 'sofa' not in p and 'bedroom' not in p
    assert any(k in res.camera for k in ['three-quarter','full-body','environmental'])


def test_distinct_trait_profiles_and_grooming_by_gender():
    s=db()
    u1=User(telegram_id=101, display_name='a', onboarding_step='complete', partner_name='A', partner_age_range='28', partner_gender='female')
    u2=User(telegram_id=102, display_name='b', onboarding_step='complete', partner_name='B', partner_age_range='28', partner_gender='male')
    u3=User(telegram_id=103, display_name='c', onboarding_step='complete', partner_name='C', partner_age_range='28', partner_gender='neutral')
    s.add_all([u1,u2,u3]); s.commit()
    p1=ensure_visual_profile(s,u1); p2=ensure_visual_profile(s,u2); p3=ensure_visual_profile(s,u3)
    assert p1.face_description != p2.face_description
    assert 'natural makeup' in p1.distinguishing_details
    assert 'groomed hair' in p2.distinguishing_details
    assert 'gender-neutral' in p3.distinguishing_details
