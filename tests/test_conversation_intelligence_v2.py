from app.engine.context_aware_fallback import context_aware_fallback
from app.engine.fast_response_engine import fast_response
from app.engine.profile_answer_handler import profile_answer
from app.engine.safety_handler import safety_response
from app.engine.situation_detector import detect_situation

PROFILE = {"name": "آذر", "age_range": "۲۵ تا ۳۰", "gender": "دختر", "personality_type": "calm"}


def _reply(message, previous=None):
    situation = detect_situation(message, previous or [])
    return situation, fast_response(message, situation, PROFILE)


def test_a_greeting_fast_no_fallback_shape():
    situation, response = _reply("سلام چطوری")
    assert situation["intent"] == "greeting"
    assert response


def test_b_bad_nistam_casual_no_fallback():
    situation, response = _reply("بد نیستم")
    assert situation["intent"] == "casual_checkin"
    assert "امروزت" in response


def test_c_che_khabar_casual_no_fallback():
    situation, response = _reply("چه خبر")
    assert situation["intent"] == "casual_checkin"
    assert "خبر" in response


def test_d_cinema_life_update_asks_movie():
    situation, response = _reply("امروز رفتم سینما")
    assert situation["intent"] == "casual_life_update"
    assert "چی دیدی" in response


def test_e_financial_context_resets_for_cinema():
    situation = detect_situation("امروز رفتم سینما", ["چک برگشتی داشتم"])
    assert situation["intent"] == "casual_life_update"
    assert situation["context_should_reset"] is True


def test_f_financial_context_preserved_for_continuation():
    situation = detect_situation("تبعاتش سخت‌تره", ["چک برگشتی داشتم"])
    assert situation["intent"] == "financial_stress"
    assert situation["context_should_reset"] is False


def test_g_self_harm_safety_not_financial():
    situation = detect_situation("میخوام خودمو بکشم از دست تو")
    response = safety_response("میخوام خودمو بکشم از دست تو", "حسین")
    assert situation["intent"] == "self_harm_signal"
    assert "قصد آسیب" in response
    assert "پول" not in response and "چک" not in response


def test_h_comfort_fast_response_no_old_banned_text():
    situation, response = _reply("میشه بهم دلداری بدی؟")
    assert situation["intent"] == "ask_comfort"
    assert "من اینجام" in response or "آروم" in response
    assert "ذهنم قفل کرد" not in response


def test_i_profile_name_no_llm():
    assert profile_answer("ask_partner_name", PROFILE) == "من آذرم :)"


def test_j_no_repeated_fallback_twice_in_last_five():
    situation = {"intent": "unknown"}
    first = context_aware_fallback(situation, "نامفهوم", [], PROFILE, [])
    second = context_aware_fallback(situation, "نامفهوم", [], PROFILE, [first])
    assert second != first
