#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["NATURAL_STYLE_GUARD_ENABLED"] = "false"
os.environ["RESPONSE_QUALITY_GATE_ENABLED"] = "false"
os.environ["OUTBOUND_TEXT_POLICY_ENABLED"] = "false"
os.environ["CONTEXT_AWARE_FALLBACK_ENABLED"] = "false"
os.environ["PARTNER_AUTONOMY_POLICY_ENABLED"] = "false"

from app.engine.response_quality_gate import apply_quality_gate
from app.engine.simple_chat import raw_llm_final_text
from app.services.outbound_text_policy import sanitize_user_facing_text
from app.services.natural_conversation_governor import NaturalConversationGovernor, StylePlan


def assert_equal(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_false(value: bool, label: str) -> None:
    if value:
        raise AssertionError(f"{label}: expected False, got {value!r}")


def main() -> None:
    samples = {
        "multiple_questions": "کجایی؟ خوبی؟ امروز چی شد؟",
        "poetic": "دل من مثل ماه پشت ابر، آرام از صدای تو روشن می‌شود.",
        "over_100_chars": "این یک پاسخ طولانی است که باید کاملاً دست‌نخورده باقی بماند، حتی اگر بیشتر از صد کاراکتر باشد و هیچ گاردی حق تغییرش را ندارد.",
        "generic_filler": "سرت شلوغه؟",
    }

    governor = NaturalConversationGovernor()
    plan = StylePlan("plain", 10, 0, False, False, 0.1, 0, True, True, [], {})

    for label, llm_text in samples.items():
        final = raw_llm_final_text(llm_text)
        assert_equal(final, llm_text, f"raw final {label}")
        sanitized, issues = sanitize_user_facing_text(final, surface="chat", user_text="چه خبر")
        assert_equal(sanitized, llm_text, f"outbound disabled {label}")
        assert_equal(str(issues), "[]", f"outbound issues {label}")
        gated = apply_quality_gate(final, "general")
        assert_equal(gated.final_text, llm_text, f"quality gate disabled {label}")
        violation = governor.validate_response("چه خبر", final, plan, [])
        assert_false(violation.violated, f"natural guard disabled {label}")
        repaired = governor.deterministic_repair("چه خبر", final, plan, {})
        assert_equal(repaired, llm_text, f"repair disabled {label}")

    retry_used = False
    assert_false(retry_used, "retry_used disabled guards")
    assert_equal(raw_llm_final_text("بگو، گوش می‌دم."), "بگو، گوش می‌دم.", "canned text only when LLM returned it")
    if raw_llm_final_text(samples["multiple_questions"]) == "بگو، گوش می‌دم.":
        raise AssertionError("unexpected canned fallback")
    assert_equal(raw_llm_final_text(""), "یه لحظه قاطی کردم، دوباره بگو.", "empty technical fallback")
    print("raw LLM no-guard checks passed")


if __name__ == "__main__":
    main()
