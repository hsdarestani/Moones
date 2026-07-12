def test_role_aware_redaction_and_cost_hiding_are_present():
    src = open('app/api/admin.py').read()
    assert 'admin.role not in {"finance"}' in src
    assert 'admin.role not in {"support","viewer"}' in src
    assert 'Conversation export denied' in src
