from app.services.image_prompt_engine import VisualSceneState, plan_composition


def test_sofa_reclining_uses_landscape_environmental_framing():
    plan=plan_composition(VisualSceneState(scene='home interior with a clearly visible sofa', pose='reclining comfortably'))
    assert plan.orientation == 'landscape'
    assert (plan.width, plan.height) == (1280, 1024)
    assert 'sofa/bed' in plan.environment_visibility
    assert 'not sitting upright' in plan.pose_constraints


def test_explicit_selfie_retains_portrait_dimensions():
    plan=plan_composition(VisualSceneState(camera_request='selfie'))
    assert plan.orientation == 'portrait'
    assert (plan.width, plan.height) == (1024, 1280)


def test_default_composition_is_environmental_not_waist_up():
    plan=plan_composition(VisualSceneState())
    assert plan.composition_key == 'environmental candid'
    assert '25%–45%' in plan.subject_scale
    assert 'waist-up' not in plan.subject_scale and 'half-body' not in plan.subject_scale


def test_cafe_scene_uses_readable_environmental_framing():
    plan=plan_composition(VisualSceneState(environment_type='cafe', activity='drinking coffee'))
    assert plan.orientation == 'landscape'
    assert 'environmental' in plan.shot_type
    assert '25%–45%' in plan.subject_scale
    assert 'readable' in plan.environment_visibility



def test_scene_plan_has_final_framing_metadata_defaults():
    plan=plan_composition(VisualSceneState(environment_type='restaurant', activity='sitting'))
    assert plan.requested_close_framing is False
    assert plan.subject_frame_share == '25%–45%'
    assert plan.camera_distance == 'camera positioned a few steps away'
