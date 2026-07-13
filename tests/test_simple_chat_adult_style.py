from app.engine.simple_chat import _build_system_prompt

def test_high_compliance_adult_style_preserves_safety_boundaries():
    prompt=_build_system_prompt({'partner_name':'مهناز','partner_gender':'دختر','partner_age_range':'25','partner_personality_type':'warm','partner_interests':''}, '', 'باهام صمیمی‌تر حرف بزن', [], adult_context=True, high_compliance_mode=True)
    assert 'Adult consensual intimacy is allowed' in prompt
    assert 'never disables hard safety boundaries' in prompt
    assert 'minors/underage' in prompt
