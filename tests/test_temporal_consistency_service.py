from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.temporal_consistency_service import detect_temporal_claim, validate_temporal_response, validate_claim_against_context, deterministic_temporal_repair


def ctx(hour, minute=0, daypart=None):
    return SimpleNamespace(local_hour=hour, local_now=datetime(2026,7,12,hour,minute,tzinfo=timezone.utc), daypart=daypart or ({0:'night',3:'late_night',8:'morning',12:'noon',16:'afternoon',20:'evening',23:'night'}.get(hour,'night')))


def test_persian_claim_detection_and_historical_filters():
    assert detect_temporal_claim('صبح بخیر').claimed_daypart == 'morning'
    assert detect_temporal_claim('سر ظهره الان').claimed_daypart == 'noon'
    assert detect_temporal_claim('فردا صبح می‌بینمت').claimed_daypart is None
    assert detect_temporal_claim('گفتی صبح بخیر').claimed_daypart is None
    assert detect_temporal_claim('الان ۳ صبحه').exact_hour_claim == 3


def test_conflicts_and_compatible_claims():
    night = ctx(0,35,'night')
    assert validate_claim_against_context(detect_temporal_claim('صبح بخیر'), night).violated
    assert validate_claim_against_context(detect_temporal_claim('سر ظهره الان'), night).violated
    assert not validate_claim_against_context(detect_temporal_claim('شب بخیر'), night).violated
    assert not validate_claim_against_context(detect_temporal_claim('ظهر بخیر'), ctx(12,30,'noon')).violated


def test_assistant_response_validation_and_repair():
    night = ctx(0,35,'night')
    assert validate_temporal_response('سلام صبح بخیر', night).violated
    fixed = deterministic_temporal_repair('سلام صبح بخیر', night)
    assert fixed and not validate_temporal_response(fixed, night).violated
    noon = ctx(12,30,'noon')
    assert validate_temporal_response('صبح زوده', noon).violated
    assert not validate_temporal_response('هنوز نصف‌شبه، خوابت نمی‌بره؟', night).violated
