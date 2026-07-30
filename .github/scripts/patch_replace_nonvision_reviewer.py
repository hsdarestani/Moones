from pathlib import Path


replacements = {
    "app/core/config.py": [
        ('vision_fallback_model: str = "mistral-31-24b"', 'vision_fallback_model: str = "z-ai-glm-5v-turbo"'),
        ('"mistral-31-24b,"', '"z-ai-glm-5v-turbo,"'),
    ],
    "app/services/generated_image_qa_service.py": [
        ('_REQUIRED_VISION_SECONDARY_MODEL = "mistral-31-24b"', '_REQUIRED_VISION_SECONDARY_MODEL = "z-ai-glm-5v-turbo"'),
        ('independent Mistral reviewer', 'independent GLM 5V reviewer'),
        ('Mistral is always second', 'GLM 5V is always second'),
    ],
    "tests/test_enforced_reviewer_order.py": [
        ('MISTRAL = "mistral-31-24b"', 'SECONDARY = "z-ai-glm-5v-turbo"'),
        ('cannot_remove_mistral', 'cannot_remove_vision_secondary'),
        ('MISTRAL,', 'SECONDARY,'),
        ('MISTRAL\n', 'SECONDARY\n'),
    ],
    "tests/test_independent_vision_reviewer_pool.py": [
        ('mistral-31-24b', 'z-ai-glm-5v-turbo'),
        ('uses_mistral', 'uses_glm_5v'),
        ('transient_mistral', 'transient_glm_5v'),
        ('mistral transient outage', 'glm 5v transient outage'),
    ],
}

for filename, pairs in replacements.items():
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in pairs:
        text = text.replace(old, new)
    if text == original:
        raise SystemExit(f"no replacements made in {filename}")
    path.write_text(text, encoding="utf-8")

order_test = Path("tests/test_enforced_reviewer_order.py")
text = order_test.read_text(encoding="utf-8")
extra = '''\n\ndef test_stale_nonvision_mistral_env_cannot_enter_bounded_runtime_pool():\n    settings = SimpleNamespace(\n        vision_model=PRIMARY,\n        vision_reviewer_models=f"{PRIMARY},mistral-31-24b,{EMERGENCY}",\n        vision_fallback_model="mistral-31-24b",\n    )\n\n    assert _configured_vision_reviewer_models(settings, max_models=3) == [\n        PRIMARY,\n        SECONDARY,\n        EMERGENCY,\n    ]\n'''
if "test_stale_nonvision_mistral_env_cannot_enter_bounded_runtime_pool" not in text:
    text += extra
order_test.write_text(text, encoding="utf-8")
