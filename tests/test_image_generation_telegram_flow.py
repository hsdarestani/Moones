from app.services.image_prompt_engine import is_explicit_image_request
from app.llm.image_client import venice_image_payload

def test_explicit_persian_requests_and_payload_safe_mode_false():
    for text in ['عکس بساز','یه عکس درست کن','عکست رو بفرست','عکس توی کافه','تصویر بساز']:
        assert is_explicit_image_request(text)
    assert not is_explicit_image_request('فقط درباره عکس حرف زدیم')
    assert venice_image_payload('p','n')['safe_mode'] is False
