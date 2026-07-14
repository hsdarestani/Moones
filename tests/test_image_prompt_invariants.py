from types import SimpleNamespace
import pytest
from app.services.image_prompt_engine import build_image_prompt, validate_prompt_invariants
from tests.test_image_prompt_engine import db, user, _enable_adult


def test_topless_request_has_single_frame_share_and_consistent_plan():
    s=db(); u=user(s); _enable_adult(s, u)
    res=build_image_prompt(s,user=u,user_request='یه عکس بده ممه هات توش معلوم باشن تو رخت خواب')
    assert res.adult_visual_intent == 'topless'
    assert res.adult_nudity_level == 'topless'
    assert '45%–70%' in res.prompt and '25%–45%' not in res.prompt
    assert res.resolved_plan.composition_plan.subject_frame_share == '45%–70%'
    assert res.resolved_plan.prompt == res.prompt
    assert not validate_prompt_invariants(res.resolved_plan, res.prompt, res.negative_prompt)


@pytest.mark.parametrize('req_text,env,surface', [
    ('روی مبل', 'home', 'sofa'), ('تو اتاق خواب', 'home', None), ('جلوی آینه', 'home', None),
    ('تو حمام', 'home', None), ('داخل ماشین', 'car', 'car_seat'), ('تو کافه', 'cafe', 'chair'),
    ('تو پارک', 'park', None), ('تو هتل', 'travel', None), ('تو باشگاه', 'gym', None), ('کنار ساحل', 'beach', None),
])
def test_general_scene_ontology(req_text, env, surface):
    s=db(); u=user(s)
    res=build_image_prompt(s,user=u,user_request='عکس ' + req_text, time_context=SimpleNamespace(local_hour=12))
    assert res.resolved_plan.visual_scene_state.environment_type == env
    assert res.resolved_plan.visual_scene_state.support_surface == surface


def test_prior_liked_cafe_does_not_reinsert_location_into_bedroom():
    s=db(); u=user(s)
    res=build_image_prompt(s,user=u,user_request='عکس تو اتاق خواب', time_context=SimpleNamespace(local_hour=12))
    assert 'cafe' not in res.prompt.lower()
