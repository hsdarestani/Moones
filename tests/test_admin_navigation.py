from pathlib import Path


def test_navigation_is_role_aware_and_persian():
    html = Path("app/templates/admin/base.html").read_text()
    for label in ["نمای کلی", "کاربران", "مالی", "محتوا و رسانه", "عملیات", "تنظیمات", "امنیت و ادمین‌ها"]:
        assert label in html
    assert "active" in html
    assert "Generated Media" not in html


def test_rtl_templates_have_routes():
    html = Path("app/templates/admin/base.html").read_text()
    assert 'dir="rtl"' in html
    assert "/admin/settings" in html
    assert "/admin/audit" in html
