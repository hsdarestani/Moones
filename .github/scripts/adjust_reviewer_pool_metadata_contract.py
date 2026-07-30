from pathlib import Path

service = Path("app/services/generated_image_qa_service.py")
text = service.read_text(encoding="utf-8")
old = '''    failure.consensus_passed=False
    failure.qa_passes=[
        {'model':result.model,'passed':result.passed,'confidence':result.confidence,'reason_codes':list(result.reason_codes or [])}
        for result in successful
    ]
    failure.reviewer_failures=[
'''
new = '''    failure.consensus_passed=False
    # Preserve the historical contract: qa_passes contains only a complete,
    # accepted consensus. Keep insufficient successful votes separately for
    # diagnostics without making downstream delivery checks ambiguous.
    failure.qa_passes=[]
    failure.partial_qa_passes=[
        {'model':result.model,'passed':result.passed,'confidence':result.confidence,'reason_codes':list(result.reason_codes or [])}
        for result in successful
    ]
    failure.reviewer_failures=[
'''
if old not in text:
    raise SystemExit("incomplete consensus metadata block not found")
service.write_text(text.replace(old, new, 1), encoding="utf-8")

test = Path("tests/test_independent_vision_reviewer_pool.py")
text = test.read_text(encoding="utf-8")
old = '''        assert "anatomy_qa_consensus_incomplete" in result.reason_codes
        assert len(result.qa_passes) == 1
        assert calls == [
'''
new = '''        assert "anatomy_qa_consensus_incomplete" in result.reason_codes
        assert result.qa_passes == []
        assert [item["model"] for item in result.partial_qa_passes] == [
            "qwen3-vl-235b-a22b"
        ]
        assert calls == [
'''
if old not in text:
    raise SystemExit("pool incomplete-consensus test block not found")
test.write_text(text.replace(old, new, 1), encoding="utf-8")
