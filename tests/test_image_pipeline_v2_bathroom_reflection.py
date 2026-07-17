from app.services import image_pipeline_v2 as v2


def _bathroom_prompt(*, explicit_mirror=False):
    text = (
        "یه عکس بده لخت باشی تو حموم "
        "هیکلت خوب باشه و ورزشکاری"
    )

    intent = v2.parse_image_intent(
        v2.normalize_request_v2(text)
    )

    intent.scene.scene_key = "bathroom"
    intent.scene.support_surface = "standing"
    intent.scene.source_spans = [(0, 1)]

    intent.pose.pose = "standing"
    intent.pose.source_spans = [(0, 1)]

    if explicit_mirror:
        intent.scene.spatial_relations = [
            v2.SpatialRelation(
                relation="near",
                object="mirror",
                source_span=(0, 1),
            )
        ]

    merged = v2.merge_image_intent(intent)

    plan = v2.construct_resolved_plan(
        intent,
        merged,
        v2.SafetyDecision(
            decision=v2.PolicyDecision.ALLOW,
        ),
        v2.ReadOnlyProfileAdapter(
            user_id=1,
            fictional_age=30,
            partner_name="Mahnaz",
        ),
        message_id=991014,
        user_request=text,
    )

    assert v2.validate_plan_invariants(plan) == []

    compiled = v2.compile_image_prompt(plan)

    assert (
        v2.validate_compiled_prompt(
            plan,
            compiled,
        )
        == []
    )

    return plan, compiled


def _negative_terms(compiled):
    return {
        term.strip().casefold()
        for term
        in compiled.negative_prompt.split(",")
        if term.strip()
    }


def test_bathroom_does_not_require_visible_mirror():
    plan, compiled = _bathroom_prompt()

    required = {
        str(obj).casefold()
        for obj in plan.required_objects.value
    }

    negatives = _negative_terms(compiled)
    positive = compiled.positive_prompt.casefold()

    assert "mirror" not in required
    assert "shower" in required
    assert "bathroom fixtures" in required
    assert "tiled walls" in required

    assert "including mirror" not in positive
    assert "visible mirror" in negatives
    assert "mirror reflection" in negatives
    assert "reflected person" in negatives
    assert "reflected duplicate" in negatives


def test_explicit_mirror_request_is_preserved():
    _, compiled = _bathroom_prompt(
        explicit_mirror=True,
    )

    negatives = _negative_terms(compiled)
    positive = compiled.positive_prompt.casefold()

    assert (
        "requested visual objects: mirror"
        in positive
    )

    assert "visible mirror" not in negatives
    assert "mirror reflection" not in negatives
    assert "reflected person" not in negatives


def test_bathroom_prompt_keeps_single_subject_contract():
    _, compiled = _bathroom_prompt()

    positive = compiled.positive_prompt.casefold()
    negatives = _negative_terms(compiled)

    assert (
        "solitary fictional adult woman "
        "photographed alone"
        in positive
    )

    assert "duplicate subject" in negatives
    assert "multiple panels" in negatives
    assert "collage" in negatives
