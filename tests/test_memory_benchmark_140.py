"""Phase 6 benchmark shape and guardrails: no target metric invented."""
from scripts.benchmark_memory_140 import CASE_FAMILIES, evaluate, markdown_summary


def test_benchmark_140_scenarios_and_case_diversity():
    result = evaluate()
    assert result["case_count"] == 140
    assert len(result["cases"]) == 140
    assert len({case["case_id"] for case in result["cases"]}) == 140
    assert set(result["families"]) == set(CASE_FAMILIES)
    assert all(result["families"][f]["cases"] == 20 for f in CASE_FAMILIES)


def test_benchmark_source_integrity_no_cross_user_leak():
    result = evaluate()
    for case in result["cases"]:
        assert case["full_history_chars"] > case["legacy_context_chars"]
        assert case["hybrid_context_chars"] < case["full_history_chars"]
        assert case["lexical_context_chars"] < case["full_history_chars"]
    summary = markdown_summary(result)
    assert "oracle" in summary.lower()
    assert "NOT a model-generated response quality test" in summary
    assert "not tokens" in summary.lower()

def test_no_unrelated_or_cross_user_evidence_recalled():
    """Regression guard: one generic word cannot invent a past memory."""
    result = evaluate()
    assert result["families"]["no_evidence"]["false_source_pct"] == 0.0
    assert result["families"]["cross_user"]["false_source_pct"] == 0.0
    assert result["families"]["direct_recall"]["lexical_expected_fact_pct"] >= 80.0
    assert result["families"]["emotional_continuity"]["lexical_expected_fact_pct"] >= 80.0
