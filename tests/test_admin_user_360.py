def test_user_overview_template_has_tabs_and_no_message_dump():
    html = open('app/templates/admin/user_detail.html').read()
    assert '/conversation' in open('app/templates/admin/user_tabs.html').read()
    assert '{% for m in messages %}' not in html
    assert 'Raw prompts and large JSON are intentionally not shown' in html
