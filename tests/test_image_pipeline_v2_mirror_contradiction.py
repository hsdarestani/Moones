from app.services import image_pipeline_v2 as v2


def _bathroom_prompt():
    intent = v2.parse_image_intent(
        v2.normalize_request_v2(
            "یه عکس لخت بده"
        )
    )

    intent.scene.scene_key = "bathroom"
    intent.scene.support_surface = "standing"
    intent.scene.source_spans = [(0, 1)]

    intent.pose.pose = "standing"
    intent.pose.source_spans = [(0, 1)]

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
        message_id=991001,
        user_request=(
            "یه عکس لخت تو حموم بده "
            "هیکلت ورزشکاری باشه"
        ),
    )

    assert v2.validate_plan_invariants(plan) == []

    compiled = v2.compile_image_prompt(plan)

    return plan, compiled


def test_bathroom_mirror_does_not_conflict_with_mirror_clone():
    plan, compiled = _bathroom_prompt()

    assert "mirror" in plan.required_objects.value
    assert "mirror" in compiled.positive_prompt
    assert "mirror clone" in compiled.negative_prompt

    errors = v2.validate_compiled_prompt(
        plan,
        compiled,
    )

    assert (
        str(v2.InvariantCode.PROMPT_CONTRADICTION)
        not in errors
    )


def test_exact_required_object_in_negative_prompt_is_conflict():
    plan, compiled = _bathroom_prompt()

    conflicting = v2.CompiledImagePrompt(
        positive_prompt=compiled.positive_prompt,
        negative_prompt=(
            compiled.negative_prompt
            + ", mirror"
        ),
        provider_parameters=compiled.provider_parameters,
        sections=compiled.sections,
    )

    errors = v2.validate_compiled_prompt(
        plan,
        conflicting,
    )

    assert (
        str(v2.InvariantCode.PROMPT_CONTRADICTION)
        in errors
    )
