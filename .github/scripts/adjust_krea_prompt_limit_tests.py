from pathlib import Path

path = Path("tests/test_krea_provider_prompt_limit.py")
text = path.read_text(encoding="utf-8")

old = '''def test_exact_krea_request_is_bounded_below_live_5000_character_limit():
    plan, compiled = _compiled_exact_request()
    assert len(compiled.positive_prompt) > 5000

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", compiled.positive_prompt, compiled.negative_prompt
    )

    assert len(prompt) <= 4800
    assert len(prompt) < 5000
    assert diagnostics["provider_prompt_compacted"] is True
    assert diagnostics["provider_prompt_limit"] == 4800
    assert diagnostics["original_prompt_chars"] > diagnostics["provider_prompt_chars"]
    lower = prompt.lower()
    assert "exactly one fictional adult" in lower
    assert "visibly fully nude" in lower
    assert "head to feet" in lower or "head-to-feet" in lower
    assert "mirror selfie" in lower
    assert "same stored fictional adult identity" in lower
    assert "female adult anatomy" in lower
    assert negative == compiled.negative_prompt
'''
new = '''def test_exact_minimal_krea_request_is_unchanged_when_already_below_limit():
    _, compiled = _compiled_exact_request()
    assert len(compiled.positive_prompt) < 4800

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", compiled.positive_prompt, compiled.negative_prompt
    )

    assert prompt == compiled.positive_prompt
    assert negative == compiled.negative_prompt
    assert diagnostics["provider_prompt_compacted"] is False
    assert diagnostics["provider_prompt_limit"] == 4800


def test_context_heavy_krea_request_is_bounded_below_live_5000_character_limit():
    _, compiled = _compiled_exact_request()
    overflow_context = (
        " Additional low-priority world-memory detail about ordinary room atmosphere, "
        "decor, routine continuity, and noncritical background texture."
    ) * 24
    long_prompt = compiled.positive_prompt + overflow_context
    assert len(long_prompt) > 5000

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", long_prompt, compiled.negative_prompt
    )

    assert len(prompt) <= 4800
    assert len(prompt) < 5000
    assert diagnostics["provider_prompt_compacted"] is True
    assert diagnostics["provider_prompt_limit"] == 4800
    assert diagnostics["original_prompt_chars"] > diagnostics["provider_prompt_chars"]
    lower = prompt.lower()
    assert "exactly one fictional adult" in lower
    assert "visibly fully nude" in lower
    assert "head to feet" in lower or "head-to-feet" in lower
    assert "mirror selfie" in lower
    assert "same stored fictional adult identity" in lower
    assert "female adult anatomy" in lower
    assert negative == compiled.negative_prompt
'''
if old not in text:
    raise SystemExit("exact-request test block not found")
text = text.replace(old, new, 1)

old = '''    combined = compiled.positive_prompt + correction
    prompt, _, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", combined, compiled.negative_prompt
    )
'''
new = '''    overflow_context = (
        " Additional low-priority conversational memory and ordinary background detail."
    ) * 30
    combined = compiled.positive_prompt + overflow_context + correction
    assert len(combined) > 5000
    prompt, _, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", combined, compiled.negative_prompt
    )
'''
if old not in text:
    raise SystemExit("corrective test block not found")
text = text.replace(old, new, 1)

old = '''        _, compiled = _compiled_exact_request()
        response = await client.generate(
            compiled.positive_prompt,
            compiled.negative_prompt,
'''
new = '''        _, compiled = _compiled_exact_request()
        long_prompt = compiled.positive_prompt + (
            " Additional low-priority world-memory context and ordinary background detail."
            * 30
        )
        assert len(long_prompt) > 5000
        response = await client.generate(
            long_prompt,
            compiled.negative_prompt,
'''
if old not in text:
    raise SystemExit("client payload test block not found")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
