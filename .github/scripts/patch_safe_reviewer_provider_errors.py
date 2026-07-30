from pathlib import Path


service_path = Path("app/services/generated_image_qa_service.py")
text = service_path.read_text(encoding="utf-8")
anchor = '''def merge_adult_anatomy_reviewer_pool(\n    results: list[GeneratedImageQAResult],\n    *,\n    required_reviewers: int = 2,\n) -> GeneratedImageQAResult:\n'''
helper = '''def _safe_reviewer_provider_error(exc: Exception) -> dict:\n    detail = " ".join(str(exc or "").split())\n    lowered = detail.lower()\n    if "base64," in lowered:\n        detail = detail[: lowered.index("base64,")] + "[base64 redacted]"\n    detail = detail[:240]\n    status = None\n    if detail.startswith("vision_http_"):\n        status_token = detail[len("vision_http_"):].split(":", 1)[0]\n        if status_token.isdigit():\n            status = int(status_token)\n    return {\n        "error_type": type(exc).__name__,\n        "status": status,\n        "error_detail": detail,\n    }\n\n\ndef _reviewer_error_metadata(result: GeneratedImageQAResult) -> dict | None:\n    metadata = {"model": result.model}\n    for key in ("error_type", "status", "error_detail"):\n        value = getattr(result, f"provider_{key}", None)\n        if value not in (None, ""):\n            metadata[key] = value\n    return metadata if len(metadata) > 1 else None\n\n\n'''
if anchor not in text:
    raise SystemExit("reviewer pool anchor not found")
text = text.replace(anchor, helper + anchor, 1)

blocks = [
    ('''        failure.reviewer_failures=[\n            {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n            for result in transient\n        ]\n        return failure\n''', '''        failure.reviewer_failures=[\n            {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n            for result in transient\n        ]\n        failure.reviewer_error_details=[\n            metadata for result in transient\n            if (metadata := _reviewer_error_metadata(result)) is not None\n        ]\n        return failure\n'''),
    ('''        merged.reviewer_failures=[\n            {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n            for result in transient\n        ]\n        return merged\n''', '''        merged.reviewer_failures=[\n            {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n            for result in transient\n        ]\n        merged.reviewer_error_details=[\n            metadata for result in transient\n            if (metadata := _reviewer_error_metadata(result)) is not None\n        ]\n        return merged\n'''),
    ('''    failure.reviewer_failures=[\n        {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n        for result in transient\n    ]\n    return failure\n''', '''    failure.reviewer_failures=[\n        {'model':result.model,'reason_codes':list(result.reason_codes or [])}\n        for result in transient\n    ]\n    failure.reviewer_error_details=[\n        metadata for result in transient\n        if (metadata := _reviewer_error_metadata(result)) is not None\n    ]\n    return failure\n'''),
]
for old, new in blocks:
    if old not in text:
        raise SystemExit(f"reviewer failure block not found: {old[:80]!r}")
    text = text.replace(old, new, 1)

old_except = """                reviewer_result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],model)\n                break\n"""
new_except = """                reviewer_result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],model)\n                safe_error=_safe_reviewer_provider_error(exc)\n                reviewer_result.provider_error_type=safe_error['error_type']\n                reviewer_result.provider_status=safe_error['status']\n                reviewer_result.provider_error_detail=safe_error['error_detail']\n                break\n"""
if old_except not in text:
    raise SystemExit("final provider exception block not found")
text = text.replace(old_except, new_except, 1)
service_path.write_text(text, encoding="utf-8")

conftest_path = Path("conftest.py")
conftest = conftest_path.read_text(encoding="utf-8")
old_gate = '''            "reviewer_failures": list(\n                getattr(anatomy, "reviewer_failures", []) or []\n            ),\n'''
new_gate = '''            "reviewer_failures": list(\n                getattr(anatomy, "reviewer_failures", []) or []\n            ),\n            "reviewer_error_details": list(\n                getattr(anatomy, "reviewer_error_details", []) or []\n            ),\n'''
if old_gate not in conftest:
    raise SystemExit("live gate reviewer failure metadata block not found")
conftest_path.write_text(conftest.replace(old_gate, new_gate, 1), encoding="utf-8")

Path("tests/test_safe_reviewer_provider_errors.py").write_text(
    '''from app.services.generated_image_qa_service import (\n    GeneratedImageQAResult,\n    _safe_reviewer_provider_error,\n    merge_adult_anatomy_reviewer_pool,\n)\n\n\ndef _transient(model: str) -> GeneratedImageQAResult:\n    return GeneratedImageQAResult(\n        False, None, None, False, False, False, False, False, False,\n        "low", ["anatomy_qa_provider_failure"], model,\n    )\n\n\ndef test_safe_provider_error_extracts_status_and_bounds_detail():\n    error = RuntimeError("vision_http_400: response_format is not supported by this model")\n    metadata = _safe_reviewer_provider_error(error)\n\n    assert metadata["error_type"] == "RuntimeError"\n    assert metadata["status"] == 400\n    assert metadata["error_detail"] == (\n        "vision_http_400: response_format is not supported by this model"\n    )\n\n\ndef test_safe_provider_error_redacts_base64_and_bounds_text():\n    error = RuntimeError("vision_http_400:data:image/png;base64," + "A" * 1000)\n    metadata = _safe_reviewer_provider_error(error)\n\n    assert "AAAA" not in metadata["error_detail"]\n    assert "base64 redacted" in metadata["error_detail"]\n    assert len(metadata["error_detail"]) <= 240\n\n\ndef test_pool_keeps_legacy_failure_contract_and_separate_error_details():\n    qwen = GeneratedImageQAResult(\n        True, None, None, False, False, False, False, False, False,\n        "high", [], "qwen3-vl-235b-a22b",\n        anatomy_visible_enough_to_assess=True,\n        anatomy_consistent_with_profile=True,\n        contradictory_sex_characteristics=False,\n        malformed_anatomy=False,\n        implausible_anatomy=False,\n        duplicated_anatomy_parts=False,\n        missing_expected_parts_when_visible=False,\n        ambiguous_anatomy=False,\n    )\n    mistral = _transient("mistral-31-24b")\n    mistral.provider_error_type = "RuntimeError"\n    mistral.provider_status = 404\n    mistral.provider_error_detail = "vision_http_404:model unavailable"\n\n    merged = merge_adult_anatomy_reviewer_pool(\n        [qwen, mistral], required_reviewers=2\n    )\n\n    assert merged.passed is False\n    assert merged.reviewer_failures == [{\n        "model": "mistral-31-24b",\n        "reason_codes": ["anatomy_qa_provider_failure"],\n    }]\n    assert merged.reviewer_error_details == [{\n        "model": "mistral-31-24b",\n        "error_type": "RuntimeError",\n        "status": 404,\n        "error_detail": "vision_http_404:model unavailable",\n    }]\n''',
    encoding="utf-8",
)

gate_test_path = Path("tests/test_live_gate_uses_production_reviewer_pool.py")
gate_test = gate_test_path.read_text(encoding="utf-8")
if 'assert \'"reviewer_error_details"\' in source' not in gate_test:
    gate_test += '\n    assert \'"reviewer_error_details"\' in source\n'
gate_test_path.write_text(gate_test, encoding="utf-8")
