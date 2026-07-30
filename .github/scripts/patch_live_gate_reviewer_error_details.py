from pathlib import Path

conftest_path = Path("conftest.py")
text = conftest_path.read_text(encoding="utf-8")
old = '''            "reviewer_failures": list(
                getattr(anatomy, "reviewer_failures", []) or []
            ),
'''
new = '''            "reviewer_failures": list(
                getattr(anatomy, "reviewer_failures", []) or []
            ),
            "reviewer_error_details": list(
                getattr(anatomy, "reviewer_error_details", []) or []
            ),
'''
if old not in text:
    if '"reviewer_error_details"' not in text:
        raise SystemExit("live gate reviewer failure block not found")
else:
    conftest_path.write_text(text.replace(old, new, 1), encoding="utf-8")

path = Path("tests/test_live_gate_uses_production_reviewer_pool.py")
test_text = path.read_text(encoding="utf-8")
assertion = '    assert \'"reviewer_error_details"\' in source\n'
if assertion not in test_text:
    if not test_text.endswith("\n"):
        test_text += "\n"
    test_text += assertion
    path.write_text(test_text, encoding="utf-8")
