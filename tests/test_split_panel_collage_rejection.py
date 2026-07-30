from io import BytesIO

from PIL import Image, ImageDraw

from app.services.generated_image_qa_service import (
    QA_PROMPT,
    _qa_payload_missing_required_fields,
    corrective_prompt_for_reasons,
    detect_split_panel_collage,
    evaluate_generated_image_composition_payload,
)


def _png(image: Image.Image) -> bytes:
    output=BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _panel_image() -> Image.Image:
    half=Image.new("RGB", (256, 640), (205, 198, 186))
    draw=ImageDraw.Draw(half)
    draw.rectangle((16, 0, 42, 640), fill=(238, 233, 222))
    draw.rectangle((215, 0, 255, 640), fill=(181, 169, 151))
    draw.ellipse((88, 55, 168, 145), fill=(98, 71, 56))
    draw.rectangle((93, 140, 163, 390), fill=(151, 112, 91))
    draw.rectangle((99, 385, 125, 585), fill=(131, 96, 78))
    draw.rectangle((136, 385, 162, 585), fill=(131, 96, 78))
    canvas=Image.new("RGB", (518, 640), (245, 245, 245))
    canvas.paste(half, (0,0))
    right=half.copy()
    right_draw=ImageDraw.Draw(right)
    right_draw.ellipse((168, 180, 220, 225), fill=(240, 240, 235))
    canvas.paste(right, (262,0))
    return canvas


def _continuous_image() -> Image.Image:
    image=Image.new("RGB", (518, 640))
    draw=ImageDraw.Draw(image)
    for y in range(640):
        draw.line((0,y,517,y), fill=(40+(y%90), 90+(y%70), 130+(y%50)))
    draw.ellipse((56,70,220,300), fill=(220,170,130))
    draw.rectangle((290,120,490,580), fill=(72,105,145))
    return image


def _passing_payload() -> dict:
    return {
        "person_count": 1,
        "face_count": 1,
        "intended_subject_count": 1,
        "unexpected_additional_person_visible": False,
        "background_extra_person_visible": False,
        "duplicate_subject_visible": False,
        "single_frame_image": True,
        "collage_or_split_panel_detected": False,
        "repeated_subject_panel_detected": False,
        "unrequested_foreground_object_visible": False,
        "unrequested_foreground_object_labels": [],
        "reflection_visible": True,
        "reflection_matches_primary_subject": True,
        "reflected_distinct_person_visible": False,
        "selfie_detected": False,
        "mirror_selfie_detected": True,
        "confidence": "high",
        "framing": "full_body",
        "framing_matches_request": True,
        "head_inside_frame": True,
        "feet_inside_frame": True,
        "body_not_cropped": True,
        "requested_nudity_visible": True,
        "requested_scene_visible": True,
        "identity_consistency_reasonable": True,
        "camera_mode_matches_request": True,
        "camera_source_geometry_consistent": True,
        "third_person_viewpoint_detected": False,
        "natural_capture_plausible": True,
        "looks_like_id_photo": False,
        "reason_codes": [],
    }


def _requirements() -> dict:
    return {
        "explicit_nudity_requested": True,
        "full_body_visible": True,
        "framing_requirement": "full_body",
        "environment_visibility_required": True,
        "photo_contract": {
            "camera_mode": "mirror_selfie",
            "natural_capture_required": True,
            "identity_consistency_required": True,
            "identity_visibility_scope": "full",
        },
    }


def test_deterministic_detector_rejects_near_duplicate_side_by_side_panels():
    diagnostics=detect_split_panel_collage(_png(_panel_image()))
    assert diagnostics["detected"] is True
    assert diagnostics["panel_correlation"] >= 0.76
    assert diagnostics["panel_mad"] <= 0.14
    assert diagnostics["seam_ratio"] >= 3.2


def test_deterministic_detector_allows_single_continuous_asymmetric_photo():
    diagnostics=detect_split_panel_collage(_png(_continuous_image()))
    assert diagnostics["detected"] is False


def test_payload_rejects_collage_even_when_same_subject_mirror_reflection_claimed():
    payload=_passing_payload()
    payload.update({
        "single_frame_image": False,
        "collage_or_split_panel_detected": True,
        "repeated_subject_panel_detected": True,
    })
    result=evaluate_generated_image_composition_payload(
        payload,
        expected_subject_count=1,
        selfie_allowed=True,
        mirror_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "collage_or_split_panel" in result.reason_codes
    assert "multiple_frames" in result.reason_codes
    assert "repeated_subject_panel" in result.reason_codes


def test_payload_rejects_unrequested_foreground_prop():
    payload=_passing_payload()
    payload.update({
        "unrequested_foreground_object_visible": True,
        "unrequested_foreground_object_labels": ["mug"],
    })
    result=evaluate_generated_image_composition_payload(
        payload,
        expected_subject_count=1,
        selfie_allowed=True,
        mirror_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "unrequested_foreground_object" in result.reason_codes
    assert result.unrequested_foreground_object_labels == ["mug"]


def test_collage_fields_are_required_from_every_vision_reviewer():
    payload=_passing_payload()
    payload.pop("single_frame_image")
    payload.pop("collage_or_split_panel_detected")
    missing=_qa_payload_missing_required_fields(payload, _requirements())
    assert "single_frame_image" in missing
    assert "collage_or_split_panel_detected" in missing


def test_prompts_explicitly_distinguish_coherent_mirror_from_split_panel():
    assert "one continuous photographic frame" in QA_PROMPT
    assert "not a second full-frame panel" in QA_PROMPT
    correction=corrective_prompt_for_reasons(
        ["collage_or_split_panel", "unrequested_foreground_object"],
        identity_requirements={"face": "stable fictional face"},
        photo_contract={"camera_mode": "mirror_selfie"},
    ).lower()
    assert "exactly one continuous photograph" in correction
    assert "no collage" in correction
    assert "remove every conspicuous unrequested" in correction
    assert "cup" in correction
