from pathlib import Path

qa_path=Path('app/services/generated_image_qa_service.py')
qa=qa_path.read_text(encoding='utf-8')
old="    single_frame=None if payload.get('single_frame_image') is None else _bool(payload.get('single_frame_image'))\n"
new="    # Vision responses are validated for the new fields before reaching this\n    # evaluator. Direct legacy callers and historical unit tests may omit them;\n    # treat omission as the pre-feature single-frame default without weakening\n    # the fail-closed Vision path. An explicitly false/null field still fails.\n    single_frame=True if 'single_frame_image' not in payload else _bool(payload.get('single_frame_image'))\n"
if old not in qa:
    raise SystemExit('missing legacy single-frame anchor')
qa_path.write_text(qa.replace(old,new,1), encoding='utf-8')

test_path=Path('tests/test_split_panel_collage_rejection.py')
test=test_path.read_text(encoding='utf-8')
append=r'''


def test_legacy_direct_payload_without_new_vision_fields_defaults_to_single_frame():
    payload={
        "person_count": 2,
        "face_count": 2,
        "intended_subject_count": 2,
        "second_person_visible": True,
        "unexpected_additional_person_visible": False,
        "background_extra_person_visible": False,
        "duplicate_subject_visible": False,
        "reflected_extra_person_visible": False,
        "interaction_detected": "kiss",
        "interaction_matches_request": True,
        "confidence": "high",
    }
    result=evaluate_generated_image_composition_payload(
        payload,
        expected_subject_count=2,
        expected_interaction="kiss",
    )
    assert result.passed is True
    assert result.single_frame_image is True
    assert "collage_or_split_panel" not in result.reason_codes
'''
if 'test_legacy_direct_payload_without_new_vision_fields_defaults_to_single_frame' not in test:
    test_path.write_text(test + append, encoding='utf-8')
