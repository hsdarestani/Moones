from pathlib import Path


def test_live_gate_uses_krea_same_seed_then_seedream_only():
    source = Path("conftest.py").read_text(encoding="utf-8")
    assert '("krea-2-turbo", 0)' in source
    assert '("krea-2-turbo", 1)' in source
    assert '("seedream-v5-lite", 0)' in source
    assert 'stable_krea_seed if model == "krea-2-turbo"' in source
    assert "lustify-sdxl" not in source
    assert "lustify-v8" not in source
