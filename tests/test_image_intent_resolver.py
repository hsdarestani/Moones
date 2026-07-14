from app.services.image_prompt_engine import resolve_adult_visual_intent, normalize_request


def test_persian_normalization_splits_suffix_and_digits():
    nr = normalize_request('یه عکس بده ممه‌هات ۱۲ توش معلوم باشن')
    assert 'ممه' in nr.tokens and 'هات' in nr.tokens
    assert '12' in nr.normalized


def test_topless_feature_window_examples():
    positives = [
        'یه عکس بده ممه هات توش معلوم باشن',
        'یه عکس بده ممه‌هات توش معلوم باشن',
        'تو عکس سینه هات واضح معلوم باشه',
        'ممه هات یکم پیدا باشن',
        'میشه بالا تنت لخت باشه',
        'بدون سوتین یه عکس بده',
    ]
    for text in positives:
        intent = resolve_adult_visual_intent(text)
        assert intent.intent_type == 'topless'
        assert intent.nudity_level == 'topless'
        assert 'breasts_visible' in intent.body_emphasis and 'upper_body' in intent.body_emphasis
        assert 'topless' in (intent.requested_clothing_state or '')


def test_negated_and_non_visual_body_terms_are_not_topless():
    negatives = ['سینه هات معلوم نباشن', 'درد سینه دارم', 'سینه چیست', 'راجع به ممه حرف بزن', 'لباس روی سینه ات خوبه']
    for text in negatives:
        assert resolve_adult_visual_intent(text).intent_type != 'topless'


def test_wardrobe_levels_do_not_escalate_or_downgrade():
    cases = {
        'لباس زیر': 'lingerie',
        'لباس جذاب': 'suggestive',
        'نیمه لخت': 'topless',
        'تاپلس': 'topless',
        'کاملاً لخت': 'full_nudity',
        'بدون لباس': 'full_nudity',
    }
    for text, expected in cases.items():
        assert resolve_adult_visual_intent(text).intent_type == expected
