from pathlib import Path


SERVICE = Path("app/services/generated_image_qa_service.py")
text = SERVICE.read_text(encoding="utf-8")
old = '''def _configured_vision_reviewer_models(settings, *, max_models: int | None = None) -> list[str]:
    candidates=[getattr(settings, 'vision_model', None)]
    candidates.extend(
        part.strip()
        for part in str(getattr(settings, 'vision_reviewer_models', '') or '').split(',')
        if part.strip()
    )
    candidates.append(getattr(settings, 'vision_fallback_model', None))
    models=[]
    for candidate in candidates:
        model=str(candidate or '').strip()
        if model and model not in models:
            models.append(model)
    if max_models is not None:
        models=models[:max(1, int(max_models))]
    return models
'''
new = '''_DEFAULT_VISION_PRIMARY_MODEL = "qwen3-vl-235b-a22b"
_REQUIRED_VISION_SECONDARY_MODEL = "mistral-31-24b"
_EMERGENCY_VISION_REVIEWER_MODEL = "e2ee-qwen3-vl-30b-a3b-p"


def _configured_vision_reviewer_models(settings, *, max_models: int | None = None) -> list[str]:
    """Return a stable independent-reviewer order despite stale deployment env.

    Older production environments may still set ``VISION_REVIEWER_MODELS`` and
    ``VISION_FALLBACK_MODEL`` to the legacy Qwen/E2EE pair.  Those values must
    not remove the independent Mistral reviewer introduced by the strict adult
    QA pool.  The configured primary remains first, Mistral is always second,
    and E2EE remains the bounded emergency third reviewer.  Additional
    configured models are appended after those required roles.
    """
    configured_primary=str(getattr(settings, 'vision_model', None) or '').strip()
    if not configured_primary or configured_primary in {
        _REQUIRED_VISION_SECONDARY_MODEL,
        _EMERGENCY_VISION_REVIEWER_MODEL,
    }:
        configured_primary=_DEFAULT_VISION_PRIMARY_MODEL

    configured_candidates=[
        part.strip()
        for part in str(getattr(settings, 'vision_reviewer_models', '') or '').split(',')
        if part.strip()
    ]
    configured_candidates.append(getattr(settings, 'vision_fallback_model', None))

    candidates=[
        configured_primary,
        _REQUIRED_VISION_SECONDARY_MODEL,
        _EMERGENCY_VISION_REVIEWER_MODEL,
        *configured_candidates,
    ]
    models=[]
    for candidate in candidates:
        model=str(candidate or '').strip()
        if model and model not in models:
            models.append(model)
    if max_models is not None:
        models=models[:max(1, int(max_models))]
    return models
'''
if old not in text:
    raise SystemExit("reviewer helper snippet not found")
SERVICE.write_text(text.replace(old, new, 1), encoding="utf-8")

Path("tests/test_enforced_reviewer_order.py").write_text(
    '''from types import SimpleNamespace\n\nfrom app.services.generated_image_qa_service import _configured_vision_reviewer_models\n\n\nPRIMARY = "qwen3-vl-235b-a22b"\nMISTRAL = "mistral-31-24b"\nEMERGENCY = "e2ee-qwen3-vl-30b-a3b-p"\n\n\ndef test_stale_legacy_env_cannot_remove_mistral_from_second_position():\n    settings = SimpleNamespace(\n        vision_model=PRIMARY,\n        vision_reviewer_models=f"{PRIMARY},{EMERGENCY}",\n        vision_fallback_model=EMERGENCY,\n    )\n\n    assert _configured_vision_reviewer_models(settings, max_models=3) == [\n        PRIMARY,\n        MISTRAL,\n        EMERGENCY,\n    ]\n\n\ndef test_stale_fallback_only_env_still_uses_independent_secondary():\n    settings = SimpleNamespace(\n        vision_model=PRIMARY,\n        vision_reviewer_models="",\n        vision_fallback_model=EMERGENCY,\n    )\n\n    assert _configured_vision_reviewer_models(settings, max_models=2) == [\n        PRIMARY,\n        MISTRAL,\n    ]\n\n\ndef test_legacy_reviewer_cannot_be_promoted_to_primary_role():\n    settings = SimpleNamespace(\n        vision_model=EMERGENCY,\n        vision_reviewer_models=f"{EMERGENCY},{PRIMARY}",\n        vision_fallback_model=EMERGENCY,\n    )\n\n    assert _configured_vision_reviewer_models(settings, max_models=3) == [\n        PRIMARY,\n        MISTRAL,\n        EMERGENCY,\n    ]\n\n\ndef test_additional_configured_reviewers_are_appended_after_required_roles():\n    settings = SimpleNamespace(\n        vision_model="custom-primary",\n        vision_reviewer_models="custom-primary,custom-fourth",\n        vision_fallback_model="custom-fifth",\n    )\n\n    assert _configured_vision_reviewer_models(settings) == [\n        "custom-primary",\n        MISTRAL,\n        EMERGENCY,\n        "custom-fourth",\n        "custom-fifth",\n    ]\n''',
    encoding="utf-8",
)
