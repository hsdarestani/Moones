from app.services.partner_photo_contract import build_partner_photo_contract
from app.services.semantic_image_intent_router import VisualIntent
from app.services import image_pipeline_v2 as v2


def test_generic_partner_photo_does_not_infer_selfie():
    contract = build_partner_photo_contract(
        VisualIntent(
            primary_subject="partner",
            partner_visible=True,
            camera_mode=None,
            camera_explicit_current_request=False,
        )
    )
    assert contract["camera_mode"] == "casual_phone_photo"
    assert contract["camera_explicit_current_request"] is False


def test_unspecified_full_body_partner_photo_uses_timer_not_mirror_selfie():
    contract = build_partner_photo_contract(
        VisualIntent(
            primary_subject="partner",
            partner_visible=True,
            framing="full_body",
            framing_explicit_current_request=True,
            camera_mode=None,
            camera_explicit_current_request=False,
        )
    )
    assert contract["camera_mode"] == "tripod_timer"
    assert contract["framing"] == "full_body"


def test_explicit_selfie_is_preserved():
    contract = build_partner_photo_contract(
        VisualIntent(
            primary_subject="partner",
            partner_visible=True,
            framing="full_body",
            camera_mode="casual_selfie",
            camera_explicit_current_request=True,
        )
    )
    assert contract["camera_mode"] == "casual_selfie"
    assert contract["camera_explicit_current_request"] is True


def test_explicit_mirror_selfie_is_preserved():
    contract = build_partner_photo_contract(
        VisualIntent(
            primary_subject="partner",
            partner_visible=True,
            framing="full_body",
            camera_mode="mirror_selfie",
            camera_explicit_current_request=True,
        )
    )
    assert contract["camera_mode"] == "mirror_selfie"


def test_v2_fallback_generic_partner_photo_does_not_default_to_selfie():
    intent = v2.parse_image_intent(
        v2.normalize_request_v2("یه عکس از خودت بده", user_id=1, chat_id=1, source_message_id=1)
    )
    intent.photo_contract = {"primary_subject": "partner", "partner_visible": True}
    intent.composition.camera = None
    intent.composition.framing = None
    vr = v2.resolve_visual_requirements(intent, user_request="یه عکس از خودت بده")
    assert vr.camera_mode == "casual_phone_photo"


def test_v2_fallback_full_body_without_explicit_camera_uses_timer():
    intent = v2.parse_image_intent(
        v2.normalize_request_v2("یه عکس تمام‌قد از خودت بده", user_id=1, chat_id=1, source_message_id=2)
    )
    intent.photo_contract = {
        "primary_subject": "partner",
        "partner_visible": True,
        "camera_explicit_current_request": False,
        "framing": "full_body",
    }
    intent.composition.camera = None
    intent.composition.framing = "full_body"
    vr = v2.resolve_visual_requirements(intent, user_request="یه عکس تمام‌قد از خودت بده")
    assert vr.camera_mode == "tripod_timer"
    assert vr.framing_requirement == "full_body"
