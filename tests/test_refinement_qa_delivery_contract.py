import hashlib
from types import SimpleNamespace


def test_contextual_previous_image_request_routes_to_refinement():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        canonical_explicit_image_action,
    )

    assert canonical_explicit_image_action("همین قبلی رو تو کافه بده") == SemanticImageAction.REFINE_PREVIOUS
    assert canonical_explicit_image_action("همونو با نور طبیعی بده") == SemanticImageAction.REFINE_PREVIOUS
    assert canonical_explicit_image_action("این عکس رو قدی بده") == SemanticImageAction.REFINE_PREVIOUS


def test_previous_image_chat_and_exact_resend_stay_distinct():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        canonical_explicit_image_action,
    )

    assert canonical_explicit_image_action("عکس قبلی خوب نبود چرا") is None
    assert canonical_explicit_image_action("عکس قبلی رو دوباره بفرست") == SemanticImageAction.RESEND_EXACT


def test_refinement_route_uses_latest_delivered_source_job():
    from app.api.telegram import _semantic_decision_to_legacy_route
    from app.services.semantic_image_intent_router import SemanticImageAction, SemanticImageDecision

    decision = SemanticImageDecision(
        action=SemanticImageAction.REFINE_PREVIOUS,
        media_delivery_requested=True,
        confidence=1.0,
        reason_code="deterministic_explicit_action",
        needs_clarification=False,
    )
    route = _semantic_decision_to_legacy_route(decision, SimpleNamespace(id=131))
    assert route.route == "semantic_refine_previous"
    assert route.source_image_job_id == 131
    assert route.contextual_followup is True


def _qa_metadata(image_bytes: bytes, *, degraded: bool, adult: bool = False) -> dict:
    checksum = hashlib.sha256(image_bytes).hexdigest()
    metadata = {
        "generated_image_qa": {
            "passed": True,
            "artifact_checksum": checksum,
            "reason_codes": [],
        },
        "qa_degraded_provider_unavailable": degraded,
        "qa_requested_framing": "full_body",
        "visual_requirements": {
            "full_body_visible": True,
            "framing_requirement": "full_body",
            "explicit_nudity_requested": adult,
            "anatomy_qa_required": adult,
            "photo_contract": {
                "camera_mode": "casual_selfie",
                "identity_consistency_required": True,
                "identity_visibility_scope": "full",
                "natural_capture_required": True,
                "face_visible": True,
            },
        },
    }
    if adult:
        metadata["visual_requirements"]["anatomical_profile"] = "female"
    return metadata


def test_normal_full_body_qa_outage_stays_deliverable_after_degraded_acceptance():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"normal-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=True), image) is True


def test_strict_contract_without_degraded_acceptance_remains_fail_closed():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"normal-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=False), image) is False


def test_adult_anatomy_never_uses_degraded_delivery_bypass():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"adult-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=True, adult=True), image) is False


def test_status_copy_reports_real_job_state():
    from app.services.partner_photo_contract import image_status_text

    assert "ثبت شده" in image_status_text("queued")
    assert "در حال ساخته" in image_status_text("generating")
    assert "خود عکس" in image_status_text("processing")
