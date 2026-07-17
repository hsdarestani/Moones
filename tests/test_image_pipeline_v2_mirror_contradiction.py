from app.services import image_pipeline_v2 as v2


def _plan_requiring_mirror(
    negative_prompt: str,
):
    plan = v2.ResolvedImagePlan(
        required_objects=v2.ResolvedField(
            ["mirror"]
        )
    )

    compiled = v2.CompiledImagePrompt(
        positive_prompt=(
            "Create a realistic image of a solitary "
            "fictional adult woman photographed alone "
            "near a mirror."
        ),
        negative_prompt=negative_prompt,
        provider_parameters={},
        sections={},
    )

    return plan, compiled


def test_mirror_does_not_conflict_with_mirror_clone():
    plan, compiled = _plan_requiring_mirror(
        (
            "duplicate subject, split screen, collage, "
            "multiple panels, mirror clone"
        )
    )

    errors = v2.validate_compiled_prompt(
        plan,
        compiled,
    )

    assert (
        str(v2.InvariantCode.PROMPT_CONTRADICTION)
        not in errors
    )


def test_exact_required_object_in_negative_prompt_is_conflict():
    plan, compiled = _plan_requiring_mirror(
        (
            "duplicate subject, split screen, collage, "
            "multiple panels, mirror clone, mirror"
        )
    )

    errors = v2.validate_compiled_prompt(
        plan,
        compiled,
    )

    assert (
        str(v2.InvariantCode.PROMPT_CONTRADICTION)
        in errors
    )
