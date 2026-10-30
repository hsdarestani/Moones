"""Fast offline checks: real prompt renderer, paired scoring, provider-budget safety."""
import asyncio
from decimal import Decimal

import pytest

from app.llm.client import LLMResult
from scripts.evaluate_memory_answers import (
    CATEGORIES, grade, make_cases, parse_chat_model_rates,
    bill, preflight, evaluate_live, summarize,
)


def test_render_36_paired_real_simple_chat_prompts():
    cases = make_cases()
    assert len(cases) == 36
    assert len({case.id for case in cases}) == 36
    assert all(sum(case.family == name for case in cases) == 6 for name in CATEGORIES)
    assert all("[Core role/persona instruction]" in c.legacy_prompt for c in cases)
    assert all("[Core role/persona instruction]" in c.v2_prompt for c in cases)
    assert all(c.query in c.legacy_prompt and c.query in c.v2_prompt for c in cases)
    assert all("مونا" in c.legacy_prompt and "مونا" in c.v2_prompt for c in cases)
    assert preflight(cases)["status"] == "preflight_only_no_model_invoked"
    assert preflight(cases)["paired_calls_if_complete"] == 72


def test_scoring_cannot_confuse_found_fact_with_response_quality():
    cases = make_cases()
    direct = next(c for c in cases if c.family == "direct")
    missing = next(c for c in cases if c.family == "unknown")
    assert grade(direct, "یادت هست، بیدمشک بود")["expected_fact_present"]
    assert grade(direct, "فکر کنم چیز دیگه ای بود")["expected_fact_present"] is False
    assert grade(missing, "نه، همچین رمزی بهم نگفتی")["unsupported_confident_answer"] is False
    assert grade(missing, "رمزش ۱۲۳۴ بود")["unsupported_confident_answer"] is True
    assert grade(direct, "system prompt: بیدمشک")["persona_format_ok"] is False


def test_chat_price_catalog_and_actual_tokens():
    model = "qwen-3-6-plus"
    rates = parse_chat_model_rates({
        "data": [{"id": model, "model_spec": {"pricing": {
            "input": {"usd": "0.63"}, "output": {"usd": "3.75"}
        }}}]
    }, model)
    assert rates == (Decimal("0.63"), Decimal("3.75"))
    assert parse_chat_model_rates({"data": []}, model) is None
    assert bill(LLMResult(text="خوبه", input_tokens=1000, output_tokens=100), rates) == Decimal("0.001005")
    assert bill(LLMResult(text="خوبه", input_tokens=None, output_tokens=100), rates) is None


class StubClient:
    api_key = "fake-no-network"
    model = "qwen-3-6-plus"
    def __init__(self):
        self.calls = []
    async def complete_result(self, messages, *, model, parameters, timeout):
        prompt = messages[0]["content"]
        self.calls.append(prompt)
        return LLMResult(text="اسمش بیدمشک بود", model=model, input_tokens=700,
                         output_tokens=18, raw_usage={"prompt_tokens":700, "completion_tokens":18})


def test_live_harness_uses_identical_model_and_price_guard_without_network():
    client = StubClient()
    cases = make_cases()
    rates = (Decimal("0.63"), Decimal("3.75"))
    result = asyncio.run(evaluate_live(
        cases, max_calls=2, max_usd=Decimal("0.15"),
        runner=client, rates=rates,
    ))
    assert result["calls"] == 2
    assert result["paired_cases"] == 1
    assert len(client.calls) == 2
    assert result["scores"]["legacy"]["provider_input_tokens"] == 700
    assert result["scores"]["v2"]["provider_input_tokens"] == 700
    assert len(result["paired_results"][0]["answers"]) == 2
    assert result["paired_results"][0]["answers"][0]["heuristics"]["expected_fact_present"]


def test_live_harness_does_not_call_provider_when_budget_too_small():
    client = StubClient()
    result = asyncio.run(evaluate_live(
        make_cases(), max_calls=4, max_usd=Decimal("0.00000001"),
        runner=client, rates=(Decimal("0.63"), Decimal("3.75")),
    ))
    assert result["calls"] == 0
    assert client.calls == []


def test_live_requires_key_even_if_test_rates_provided():
    client = StubClient()
    client.api_key = ""
    with pytest.raises(RuntimeError, match="Venice key"):
        asyncio.run(evaluate_live(
            make_cases(), max_calls=2, max_usd=Decimal("1"),
            runner=client, rates=(Decimal("0.63"), Decimal("3.75")),
        ))

def test_blinded_review_csv_has_no_variant_names(tmp_path):
    from scripts.evaluate_memory_answers import write_blind_review_csv
    row = {
        "case_id": "direct-01", "family": "direct", "expected": "بیدمشک",
        "answers": [
            {"arm": "legacy", "text": "نمیدونم"},
            {"arm": "v2", "text": "بیدمشک بود"},
        ],
    }
    out = tmp_path / "review.csv"
    write_blind_review_csv([row], str(out))
    text = out.read_text(encoding="utf-8-sig")
    assert "answer_A" in text and "answer_B" in text
    assert "میدونم" in text and "بیدمشک" in text
    assert "legacy" not in text and ",v2," not in text
