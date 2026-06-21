from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_patch11_user_detail_is_insight_first_and_raw_collapsed():
    html = read("app/templates/admin/user_detail.html")
    assert "خلاصه وضعیت" in html
    assert "وضعیت رابطه" in html
    assert "داده خام کاربر" in html
    assert html.index("خلاصه وضعیت") < html.index("داده خام کاربر")
    assert "<details class=\"admin-card admin-raw-panel\"" in html
    assert "copy-raw" in html


def test_patch11_mobile_utilities_and_breakpoints_exist():
    css = read("app/static/admin.css")
    for token in [
        ".admin-page",
        ".admin-grid",
        ".admin-card",
        ".admin-kpi-grid",
        ".admin-mobile-stack",
        ".admin-table-wrap",
        ".admin-insight-card",
        ".admin-raw-panel",
        ".admin-action-row",
        ".admin-badge",
        ".admin-tabs",
        "overflow-x:hidden",
        "min-height:44px",
    ]:
        assert token in css
    assert "@media(max-width:640px)" in css
    assert "@media(max-width:768px)" in css
    assert "@media(max-width:1024px)" in css


def test_patch11_lists_use_mobile_cards_and_tables_are_wrapped():
    users = read("app/templates/admin/users.html")
    receipts = read("app/templates/admin/receipts.html")
    assert "mobile-card-list" in users
    assert "filter-drawer" in users
    assert "admin-table-wrap desktop-table" in users
    assert "mobile-card-list" in receipts
    assert "admin-table-wrap desktop-table" in receipts
    assert "admin-raw-panel" in receipts


def test_patch11_base_is_rtl_and_static_assets_present():
    base = read("app/templates/admin/base.html")
    assert '<html lang="fa" dir="rtl">' in base
    assert "/static/admin.css" in base
    assert "/static/admin.js" in base
