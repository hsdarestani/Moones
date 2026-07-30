from pathlib import Path


def test_live_gate_reports_actual_production_reviewer_pool_metadata():
    source = Path("conftest.py").read_text(encoding="utf-8")

    assert "diagnose_anatomy_reviewers" not in source
    assert "analyze_image_bytes_with_venice" not in source
    assert "_configured_vision_reviewer_models" in source
    assert '"resolved_reviewer_models"' in source
    assert '"qa_passes"' in source
    assert '"partial_qa_passes"' in source
    assert '"reviewer_failures"' in source
