from app.llm.image_client import venice_image_payload


def test_safe_mode_always_false():
    assert venice_image_payload('x','y')['safe_mode'] is False
