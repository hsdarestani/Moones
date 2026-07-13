from app.engine.simple_chat import _build_system_prompt

def test_enabled_mode_removes_early_stage_relationship_refusal_from_prompt():
    prompt=_build_system_prompt({'partner_name':'مهناز','partner_gender':'دختر','partner_age_range':'25','partner_personality_type':'warm','partner_interests':''}, '', 'با لحن مطیع همراهی کن', [], high_compliance_mode=True)
    assert '[High-compliance companion mode]' in prompt
    assert 'Do not invent early-stage objections' in prompt
    assert 'هنوز زوده' in prompt
    assert 'باید بیشتر آشنا بشیم' in prompt
    assert 'never disables hard safety boundaries' in prompt

def test_disabled_mode_uses_normal_personality_behavior():
    prompt=_build_system_prompt({'partner_name':'مهناز','partner_gender':'دختر','partner_age_range':'25','partner_personality_type':'warm','partner_interests':''}, '', 'سلام', [], high_compliance_mode=False)
    assert '[High-compliance companion mode]' not in prompt
