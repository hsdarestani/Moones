from app.services.image_prompt_engine import is_explicit_image_request
from app.llm.image_client import venice_image_payload

def test_explicit_persian_requests_and_payload_safe_mode_false():
    for text in ['عکس بساز','یه عکس درست کن','عکست رو بفرست','عکس توی کافه','تصویر بساز']:
        assert is_explicit_image_request(text)
    assert not is_explicit_image_request('فقط درباره عکس حرف زدیم')
    assert venice_image_payload('p','n')['safe_mode'] is False


def test_natural_persian_image_requests_enqueue_intent():
    positives = [
        "یه عکس برام بفرست",
        "یه عکس از خودت بده",
        "میشه یه عکس بفرستی",
        "عکس خودتو بفرست",
        "از خودت عکس بفرست",
        "یه تصویر از خودت بساز",
    ]
    for text in positives:
        assert is_explicit_image_request(text), text


def test_photo_discussion_without_request_does_not_enqueue_intent():
    negatives = [
        "دیروز درباره عکس حرف زدیم",
        "عکس‌ها چیزهای جالبی هستن",
        "اگه کسی عکس داشته باشه بهتره؟",
        "من عکس فرستادن رو دوست ندارم",
    ]
    for text in negatives:
        assert not is_explicit_image_request(text), text
