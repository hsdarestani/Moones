from pathlib import Path


def test_live_gate_uses_krea_same_seed_then_seedream_only():
    source = Path("conftest.py").read_text(encoding="utf-8")
    assert '("krea-2-turbo", 0)' in source
    assert '("krea-2-turbo", 1)' in source
    assert '("seedream-v5-lite", 0)' in source
    assert 'stable_krea_seed if model == "krea-2-turbo"' in source
    assert "lustify-sdxl" not in source
    assert "lustify-v8" not in source


def test_live_gate_anchors_krea_to_profile_identity_seed():
    source = Path("conftest.py").read_text(encoding="utf-8")
    assert 'plan.seed_strategy["identity_seed"]' in source
    assert 'plan.seed_strategy["final_provider_seed"]' not in source
