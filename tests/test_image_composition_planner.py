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
