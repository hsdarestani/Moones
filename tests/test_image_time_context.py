from types import SimpleNamespace


def test_tehran_evening_context_shape():
    ctx = SimpleNamespace(local_hour=20, timezone='Asia/Tehran')
    assert ctx.local_hour == 20 and ctx.timezone == 'Asia/Tehran'
