def test_users_route_uses_offset_limit_not_fixed_300():
    src = open('app/api/admin.py').read()
    assert 'page_size = min(max(page_size, 1), 100)' in src
    assert '.offset((page-1)*page_size).limit(page_size)' in src
    assert '/users/export.csv' in src
