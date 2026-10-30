"""Compact persona canary tests: opt-in gating, preservation, source and safety."""
from __future__ import annotations

from app.engine.simple_chat import _build_system_prompt
from scripts.benchmark_compact_persona import measure
from scripts.evaluate_memory_answers import PERSONA, make_cases


def test_flag_off_is_exact_legacy_and_shadow_changes_nothing(monkeypatch):
    monkeypatch.delenv("MONES_COMPACT_PERSONA_ENABLED", raising=False)
    monkeypatch.delenv("MONES_COMPACT_PERSONA_SHADOW", raising=False)
    baseline = _build_system_prompt(PERSONA, "user: سلام", "سلام خوبی؟", ["اسمم سارا است"])
    assert "[Core role/persona instruction]" in baseline
    monkeypatch.setenv("MONES_COMPACT_PERSONA_SHADOW", "true")
    shadow = _build_system_prompt(PERSONA, "user: سلام", "سلام خوبی؟", ["اسمم سارا است"])
    assert shadow == baseline
    monkeypatch.setenv("MONES_COMPACT_PERSONA_ENABLED", "true")
    compact = _build_system_prompt(PERSONA, "user: سلام", "سلام خوبی؟", ["اسمم سارا است"])
    assert compact != baseline
    assert len(compact) < len(baseline)
    assert "اسمم سارا است" in compact


def test_compact_keeps_all_required_dynamic_signals(monkeypatch):
    monkeypatch.setenv("MONES_COMPACT_PERSONA_ENABLED", "1")
    cases = [
        ("secret", "شماره مورد علاقه کاربر ۱۲۳ نیست"),
        ("historical", "[historical until 2026-10-01] اسم سگ من: فندق"),
    ]
    text = _build_system_prompt(
        PERSONA,
        "user: دیروز بهت گفتم\npartner: جدی؟",
        "اسم سگ قبلیم چی بود؟",
        [k[1] for k in cases],
        adult_context=True, high_compliance_mode=True, intimacy_override=True,
        mood_recovery=True, style_contract="NO EXTRA QUESTION CONTRACT",
        style_lessons=["CUSTOM TONE LESSON"],
        media_continuity_context="USER_MEDIA_EVENT_224",
        temporal_correction_block="ACTUAL_LOCAL_TIME_OVERRIDE",
        delayed_context={"user_message_local_time": "12:00", "reply_local_time": "12:08", "delay_seconds": 480},
        current_routine_slot={"slot_name": "afternoon", "activity": "reading"},
        routine_continuity_detail="SITTING_AT_CAFE",
    )
    for value in (
        "شماره مورد علاقه کاربر ۱۲۳ نیست", "اسم سگ من: فندق",
        "NO EXTRA QUESTION CONTRACT", "CUSTOM TONE LESSON",
        "USER_MEDIA_EVENT_224", "ACTUAL_LOCAL_TIME_OVERRIDE",
        "SITTING_AT_CAFE", "12:08", "مونا",
        "اسم سگ قبلیم چی بود؟",
    ):
        assert value in text
    assert "consent" in text
    assert "minors" in text
    assert "UNTRUSTED QUOTED DATA" in text
    assert "Purchased cooperative style" in text
    assert "Mature relationship" in text


def test_compact_36_fixtures_preserve_retrieval_and_reduce_full_prompt(monkeypatch):
    monkeypatch.delenv("MONES_COMPACT_PERSONA_ENABLED", raising=False)
    monkeypatch.delenv("MONES_COMPACT_PERSONA_SHADOW", raising=False)
    report = measure()
    assert report["cases"] == 36
    assert report["source_facts_present_in_both"]
    assert report["compact_vs_v2_percent_fewer_chars"] >= 15
    assert all(r["v2_compact_chars"] < r["v2_standard_chars"] for r in report["details"])
    # The benchmark must restore environment after its comparison.
    assert "MONES_COMPACT_PERSONA_ENABLED" not in __import__("os").environ


def test_compact_staging_keeps_original_questions_and_selected_source_text(monkeypatch):
    monkeypatch.setenv("MONES_COMPACT_PERSONA_ENABLED", "true")
    cases = make_cases()
    assert len(cases) == 36
    for case in cases:
        assert case.query in case.v2_prompt
        assert "[Recent dialogue" in case.v2_prompt
        assert "مونا" in case.v2_prompt
        assert "Final Persian message" not in case.v2_prompt  # compact phrasing differs

def test_persona_only_experiment_uses_same_selected_v2_memory(monkeypatch):
    from scripts.evaluate_memory_answers import make_experiment_cases
    monkeypatch.delenv("MONES_COMPACT_PERSONA_ENABLED", raising=False)
    pairs = make_experiment_cases("persona")
    assert len(pairs) == 36
    assert all("مونا" in p.v2_prompt and "مونا" in p.legacy_prompt for p in pairs)
    assert all(p.legacy_prompt != p.v2_prompt for p in pairs)
    assert all(len(p.v2_prompt) < len(p.legacy_prompt) for p in pairs)
    assert all((p.expected in p.legacy_prompt) == (p.expected in p.v2_prompt)
               for p in pairs if p.expected is not None)
    assert "MONES_COMPACT_PERSONA_ENABLED" not in __import__("os").environ
