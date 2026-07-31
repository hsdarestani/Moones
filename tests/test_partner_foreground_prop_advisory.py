import asyncio
from types import SimpleNamespace

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base


def _qa(*codes, confidence="high"):
    return SimpleNamespace(
        passed=not bool(codes),
        reason_codes=list(codes),
        confidence=confidence,
        unrequested_foreground_object_labels=["book"],
    )


def test_foreground_prop_only_is_advisory_for_normal_partner_photo():
    result = runtime._accept_foreground_prop_only_as_advisory(
        _qa("unrequested_foreground_object"),
        visual_requirements={
            "partner_visible": True,
            "explicit_nudity_requested": False,
            "anatomy_qa_required": False,
        },
    )

    assert result.passed is True
    assert result.reason_codes == []
    assert result.raw_provider_reason_codes == ["unrequested_foreground_object"]
    assert result.qa_advisory_foreground_object is True


def test_foreground_prop_does_not_hide_real_fulfillment_failure():
    result = runtime._accept_foreground_prop_only_as_advisory(
        _qa("unrequested_foreground_object", "framing_mismatch"),
        visual_requirements={"partner_visible": True},
    )

    assert result.passed is False
    assert set(result.reason_codes) == {
        "unrequested_foreground_object",
        "framing_mismatch",
    }
    assert not hasattr(result, "qa_advisory_foreground_object")


def test_foreground_prop_does_not_hide_camera_phone_failure():
    result = runtime._accept_foreground_prop_only_as_advisory(
        _qa("unrequested_foreground_object", "visible_phone_in_non_mirror_selfie"),
        visual_requirements={"partner_visible": True},
    )

    assert result.passed is False
    assert "visible_phone_in_non_mirror_selfie" in result.reason_codes


def test_foreground_prop_remains_fail_closed_for_adult_anatomy_checked_photo():
    result = runtime._accept_foreground_prop_only_as_advisory(
        _qa("unrequested_foreground_object"),
        visual_requirements={
            "partner_visible": True,
            "explicit_nudity_requested": True,
            "anatomy_qa_required": True,
        },
    )

    assert result.passed is False
    assert result.reason_codes == ["unrequested_foreground_object"]


def test_low_confidence_foreground_prop_result_is_not_relaxed():
    result = runtime._accept_foreground_prop_only_as_advisory(
        _qa("unrequested_foreground_object", confidence="low"),
        visual_requirements={"partner_visible": True},
    )

    assert result.passed is False
    assert result.reason_codes == ["unrequested_foreground_object"]


def test_runtime_process_job_wraps_real_partner_qa_before_core_worker(monkeypatch):
    captured = {}
    job = SimpleNamespace(
        image_action="refinement",
        metadata_json={
            "route_action": "refinement",
            "expected_subject_count": 1,
            "identity_descriptor": {"face": "stable fictional face"},
            "visual_requirements": {
                "partner_visible": True,
                "explicit_nudity_requested": False,
                "anatomy_qa_required": False,
                "photo_contract": {
                    "partner_visible": True,
                    "identity_consistency_required": True,
                },
            },
        }
    )

    async def reviewer(*args, **kwargs):
        return _qa("unrequested_foreground_object")

    async def fake_core_process_job(
        db,
        received_job,
        *,
        image_client=None,
        telegram_service=None,
        generated_image_qa_evaluator=None,
    ):
        result = await generated_image_qa_evaluator(b"image")
        captured["qa"] = result
        captured["job"] = received_job
        return "sent"

    monkeypatch.setattr(base, "partner_identity_generation_required", lambda metadata: True)
    monkeypatch.setattr(base, "process_job", fake_core_process_job)

    outcome = asyncio.run(
        runtime.process_job(
            None,
            job,
            generated_image_qa_evaluator=reviewer,
        )
    )

    assert outcome == "sent"
    assert captured["job"] is job
    assert captured["qa"].passed is True
    assert captured["qa"].reason_codes == []
    assert captured["qa"].qa_advisory_foreground_object is True
