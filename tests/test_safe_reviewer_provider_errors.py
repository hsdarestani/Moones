from app.services.generated_image_qa_service import (
    GeneratedImageQAResult,
    _safe_reviewer_provider_error,
    merge_adult_anatomy_reviewer_pool,
)


def _transient(model: str) -> GeneratedImageQAResult:
    return GeneratedImageQAResult(
        False, None, None, False, False, False, False, False, False,
        "low", ["anatomy_qa_provider_failure"], model,
    )


def test_safe_provider_error_extracts_status_and_bounds_detail():
    error = RuntimeError("vision_http_400: response_format is not supported by this model")
    metadata = _safe_reviewer_provider_error(error)

    assert metadata["error_type"] == "RuntimeError"
    assert metadata["status"] == 400
    assert metadata["error_detail"] == (
        "vision_http_400: response_format is not supported by this model"
    )


def test_safe_provider_error_redacts_base64_and_bounds_text():
    error = RuntimeError("vision_http_400:data:image/png;base64," + "A" * 1000)
    metadata = _safe_reviewer_provider_error(error)

    assert "AAAA" not in metadata["error_detail"]
    assert "base64 redacted" in metadata["error_detail"]
    assert len(metadata["error_detail"]) <= 240


def test_pool_keeps_legacy_failure_contract_and_separate_error_details():
    qwen = GeneratedImageQAResult(
        True, None, None, False, False, False, False, False, False,
        "high", [], "qwen3-vl-235b-a22b",
        anatomy_visible_enough_to_assess=True,
        anatomy_consistent_with_profile=True,
        contradictory_sex_characteristics=False,
        malformed_anatomy=False,
        implausible_anatomy=False,
        duplicated_anatomy_parts=False,
        missing_expected_parts_when_visible=False,
        ambiguous_anatomy=False,
    )
    mistral = _transient("mistral-31-24b")
    mistral.provider_error_type = "RuntimeError"
    mistral.provider_status = 404
    mistral.provider_error_detail = "vision_http_404:model unavailable"

    merged = merge_adult_anatomy_reviewer_pool(
        [qwen, mistral], required_reviewers=2
    )

    assert merged.passed is False
    assert merged.reviewer_failures == [{
        "model": "mistral-31-24b",
        "reason_codes": ["anatomy_qa_provider_failure"],
    }]
    assert merged.reviewer_error_details == [{
        "model": "mistral-31-24b",
        "error_type": "RuntimeError",
        "status": 404,
        "error_detail": "vision_http_404:model unavailable",
    }]
