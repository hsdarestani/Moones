import asyncio
from types import SimpleNamespace

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base
from app.services import partner_image_quality_policy as quality


def _qa(*codes, confidence="high"):
    return SimpleNamespace(
        passed=not bool(codes),
        reason_codes=list(codes),
        confidence=confidence,
        requested_scene_visible=True,
    )


def _rooftop_requirements():
    return {
        "partner_visible": True,
        "environment_visibility_required": True,
        "visibility_targets": {"environment_visible": True},
        "must_satisfy": {
            "required_scene_elements": [
                "rooftop at night",
                "city lights in the background",
            ]
        },
        "photo_contract": {
            "partner_visible": True,
            "identity_consistency_required": True,
            "scene_context_summary": "rooftop at night; elevated building roof; city lights",
        },
    }


def test_partner_face_quality_is_added_without_replacing_identity():
    compiled = SimpleNamespace(
        positive_prompt="same recurring fictional woman; preserve identity anchor",
        negative_prompt="blurry, deformed",
    )
    plan = SimpleNamespace(
        identity={"identity_fingerprint": "stable-fingerprint"},
        visual_requirements=SimpleNamespace(
            partner_visible=True,
            object_only=False,
            pet_only=False,
        ),
    )

    result = quality.apply_partner_face_quality(compiled, plan)

    assert "naturally beautiful" in result.positive_prompt
    assert "photogenic" in result.positive_prompt
    assert "preserve the exact recurring fictional partner identity" in result.positive_prompt
    assert "beauty-filter" in result.positive_prompt
    assert "doll face" in result.negative_prompt
    assert "same recurring fictional woman" in result.positive_prompt


def test_partner_face_quality_does_not_leak_into_object_only_generation():
    compiled = SimpleNamespace(
        positive_prompt="a ceramic vase on a table",
        negative_prompt="blurry",
    )
    plan = SimpleNamespace(
        identity={"identity_fingerprint": "stable-fingerprint"},
        visual_requirements=SimpleNamespace(
            partner_visible=False,
            object_only=True,
            pet_only=False,
        ),
    )

    result = quality.apply_partner_face_quality(compiled, plan)

    assert result.positive_prompt == "a ceramic vase on a table"
    assert result.negative_prompt == "blurry"


def test_strict_scene_guard_rejects_street_false_positive_for_rooftop():
    async def reviewer(image_bytes, *, prompt, model):
        assert image_bytes == b"street-image"
        assert "street, pavement, sidewalk" in prompt
        return {
            "scene_matches_request": False,
            "detected_scene": "street/sidewalk at ground level",
            "required_scene_evidence": ["city lights"],
            "contradictory_scene_evidence": ["sidewalk", "road", "streetlights"],
            "confidence": "high",
        }

    result = asyncio.run(
        quality.enforce_strict_partner_scene_guard(
            b"street-image",
            _qa(),
            visual_requirements=_rooftop_requirements(),
            analyzer=reviewer,
        )
    )

    assert result.passed is False
    assert result.requested_scene_visible is False
    assert "requested_scene_not_visible" in result.reason_codes
    assert "wrong_scene" in result.reason_codes
    assert result.strict_scene_guard_passed is False
    assert result.strict_scene_guard_payload["detected_scene"] == "street/sidewalk at ground level"


def test_strict_scene_guard_accepts_rooftop_only_with_visible_structural_evidence():
    async def reviewer(image_bytes, *, prompt, model):
        return {
            "scene_matches_request": True,
            "detected_scene": "elevated building rooftop at night",
            "required_scene_evidence": [
                "roof surface",
                "parapet edge",
                "elevated city skyline",
            ],
            "contradictory_scene_evidence": [],
            "confidence": "high",
        }

    result = asyncio.run(
        quality.enforce_strict_partner_scene_guard(
            b"rooftop-image",
            _qa(),
            visual_requirements=_rooftop_requirements(),
            analyzer=reviewer,
        )
    )

    assert result.passed is True
    assert result.reason_codes == []
    assert result.strict_scene_guard_passed is True


def test_strict_scene_guard_is_fail_closed_when_independent_reviewer_is_uncertain():
    async def reviewer(image_bytes, *, prompt, model):
        return {
            "scene_matches_request": True,
            "detected_scene": "urban night scene",
            "required_scene_evidence": [],
            "contradictory_scene_evidence": [],
            "confidence": "low",
        }

    result = asyncio.run(
        quality.enforce_strict_partner_scene_guard(
            b"ambiguous-image",
            _qa(),
            visual_requirements=_rooftop_requirements(),
            analyzer=reviewer,
        )
    )

    assert result.passed is False
    assert "requested_scene_not_visible" in result.reason_codes
    assert "wrong_scene" in result.reason_codes


def test_runtime_applies_strict_scene_guard_after_normal_partner_qa(monkeypatch):
    captured = {}
    job = SimpleNamespace(
        metadata_json={
            "expected_subject_count": 1,
            "identity_descriptor": {"face": "stable fictional face"},
            "visual_requirements": _rooftop_requirements(),
        }
    )

    async def ordinary_reviewer(*args, **kwargs):
        return _qa()

    async def strict_reviewer(image_bytes, *, prompt, model):
        return {
            "scene_matches_request": False,
            "detected_scene": "street/sidewalk at ground level",
            "required_scene_evidence": ["city lights"],
            "contradictory_scene_evidence": ["sidewalk", "road"],
            "confidence": "high",
        }

    async def fake_core_process_job(
        db,
        received_job,
        *,
        image_client=None,
        telegram_service=None,
        generated_image_qa_evaluator=None,
    ):
        captured["qa"] = await generated_image_qa_evaluator(b"candidate")
        return "done"

    monkeypatch.setattr(base, "partner_identity_generation_required", lambda metadata: True)
    monkeypatch.setattr(base, "process_job", fake_core_process_job)

    outcome = asyncio.run(
        runtime.process_job(
            None,
            job,
            generated_image_qa_evaluator=ordinary_reviewer,
            strict_scene_guard_evaluator=strict_reviewer,
        )
    )

    assert outcome == "done"
    assert captured["qa"].passed is False
    assert "wrong_scene" in captured["qa"].reason_codes
