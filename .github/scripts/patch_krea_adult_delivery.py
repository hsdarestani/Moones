from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:220]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/core/config.py",
    '''    image_generation_model: str = "seedream-v5-lite"\n    image_generation_fallback_model: str = "venice-sd35"\n    image_generation_emergency_models: str = "z-image-turbo"\n    image_generation_adult_model: str = "lustify-sdxl"\n    image_generation_adult_fallback_model: str = "lustify-v8"\n''',
    '''    # Krea is the preferred high-compliance image model. Runtime discovery still\n    # skips it safely if Venice temporarily removes it.\n    image_generation_preferred_model: str = "krea-2-turbo"\n    image_generation_model: str = "krea-2-turbo"\n    image_generation_fallback_model: str = "seedream-v5-lite"\n    image_generation_emergency_models: str = "venice-sd35,z-image-turbo"\n    image_generation_adult_preferred_model: str = "krea-2-turbo"\n    image_generation_adult_model: str = "krea-2-turbo"\n    image_generation_adult_fallback_model: str = "lustify-sdxl"\n    image_generation_adult_emergency_models: str = "lustify-v8"\n    image_generation_adult_max_generation_attempts: int = 4\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    '''def _variation_requested(text: str, meta: dict | None = None) -> bool:\n    m=meta or {}\n    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or m.get('route_action') in {'variation','refinement','refine_previous'})\n\n\n''',
    '''def _variation_requested(text: str, meta: dict | None = None) -> bool:\n    m=meta or {}\n    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or m.get('route_action') in {'variation','refinement','refine_previous'})\n\n\ndef _split_configured_models(value: object) -> list[str]:\n    return [part.strip() for part in str(value or '').split(',') if part.strip()]\n\n\ndef build_generation_model_plan(settings, primary_model: str, *, adult_generation: bool) -> list[str]:\n    if adult_generation:\n        candidates = [\n            getattr(settings, 'image_generation_adult_preferred_model', None),\n            primary_model,\n            getattr(settings, 'image_generation_adult_model', None),\n            getattr(settings, 'image_generation_adult_fallback_model', None),\n            *_split_configured_models(getattr(settings, 'image_generation_adult_emergency_models', '')),\n        ]\n    else:\n        candidates = [\n            getattr(settings, 'image_generation_preferred_model', None),\n            primary_model,\n            getattr(settings, 'image_generation_model', None),\n            getattr(settings, 'image_generation_fallback_model', None),\n            *_split_configured_models(getattr(settings, 'image_generation_emergency_models', '')),\n        ]\n    plan: list[str] = []\n    for candidate in candidates:\n        model = str(candidate or '').strip()\n        if model and model not in plan:\n            plan.append(model)\n    return plan\n\n\ndef build_generation_attempt_plan(model_plan: list[str], *, adult_generation: bool, max_attempts: int) -> list[tuple[str, int]]:\n    attempts: list[tuple[str, int]] = []\n    for index, model in enumerate(model_plan):\n        attempts.append((model, 0))\n        # Adult full-body requests get one same-model corrective retry before\n        # falling back. This is the missing retry that previously refunded after\n        # the first cropped Krea/Lustify candidate.\n        if adult_generation and index == 0:\n            attempts.append((model, 1))\n    return attempts[: max(1, int(max_attempts))]\n\n\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    '''    runtime_settings=get_settings()\n    configured_default_model=(getattr(runtime_settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()\n    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=configured_default_model, adult_model=getattr(runtime_settings, 'image_generation_adult_model', None))\n''',
    '''    runtime_settings=get_settings()\n    configured_default_model=(getattr(runtime_settings, 'image_generation_preferred_model', None) or getattr(runtime_settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()\n    configured_adult_model=(getattr(runtime_settings, 'image_generation_adult_preferred_model', None) or getattr(runtime_settings, 'image_generation_adult_model', None) or configured_default_model).strip()\n    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=configured_default_model, adult_model=configured_adult_model)\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    '''            configured_current_model = (getattr(settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()\n            if adult_generation:\n                fallback_model = (getattr(settings, 'image_generation_adult_fallback_model', '') or '').strip()\n                candidate_models = [primary_model, fallback_model]\n            else:\n                fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()\n                emergency_models = [part.strip() for part in str(getattr(settings, 'image_generation_emergency_models', '') or '').split(',') if part.strip()]\n                candidate_models = [primary_model, configured_current_model, fallback_model, *emergency_models]\n            configured_model_plan = []\n            for candidate_model in candidate_models:\n                if candidate_model and candidate_model not in configured_model_plan:\n                    configured_model_plan.append(candidate_model)\n            model_plan = list(configured_model_plan)\n''',
    '''            fallback_model = ((getattr(settings, 'image_generation_adult_fallback_model', '') if adult_generation else getattr(settings, 'image_generation_fallback_model', '')) or '').strip()\n            configured_model_plan = build_generation_model_plan(settings, primary_model, adult_generation=adult_generation)\n            model_plan = list(configured_model_plan)\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    '''            deferred_generation_models = model_plan[2:]\n            model_plan = model_plan[:2]\n            if not model_plan:\n                raise ImageValidationError('no_configured_image_model_available')\n            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'deferred_generation_models':deferred_generation_models,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}\n''',
    '''            max_model_count = 3 if adult_generation else 2\n            deferred_generation_models = model_plan[max_model_count:]\n            model_plan = model_plan[:max_model_count]\n            if not model_plan:\n                raise ImageValidationError('no_configured_image_model_available')\n            max_generation_attempts = int(getattr(settings, 'image_generation_adult_max_generation_attempts', 4) or 4) if adult_generation else len(model_plan)\n            attempt_plan = build_generation_attempt_plan(model_plan, adult_generation=adult_generation, max_attempts=max_generation_attempts)\n            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'effective_generation_attempt_plan':[{'model':model,'correction_round':round_index} for model,round_index in attempt_plan],'deferred_generation_models':deferred_generation_models,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    '''            for attempt_index, attempt_model in enumerate(model_plan):\n                attempt_seed, norm_applied = normalize_venice_seed(job.seed, salt=f'job:{job.id}:{attempt_model}')\n''',
    '''            for attempt_index, (attempt_model, correction_round) in enumerate(attempt_plan):\n                # A corrective repeat is meaningful only after the same model\n                # produced a real image that QA rejected. Provider errors and\n                # moderation cards move directly to the next available model.\n                if correction_round and (not rejected_quality or rejected_quality[-1].get('model') != attempt_model):\n                    continue\n                seed_source = job.seed if attempt_index == 0 else deterministic_provider_seed(\n                    job.seed, job.id, attempt_model, correction_round, attempt_index,\n                    ','.join(rejected_quality[-1].get('reason_codes') or []) if rejected_quality else 'provider-fallback',\n                )\n                attempt_seed, norm_applied = normalize_venice_seed(seed_source, salt=f'job:{job.id}:{attempt_model}:{correction_round}:{attempt_index}')\n''',
)

replace_once(
    "app/services/image_generation_service.py",
    "                        'model': attempt_model,\n                        'seed': attempt_seed,\n",
    "                        'model': attempt_model,\n                        'attempt_index': attempt_index,\n                        'correction_round': correction_round,\n                        'seed': attempt_seed,\n",
)

replace_once(
    "app/services/image_generation_service.py",
    "logger.warning('IMAGE_PROVIDER_MODEL_FAILED job_id=%s user_id=%s model=%s error_type=%s error_code=%s has_next_model=%s', job.id, job.user_id, attempt_model, type(provider_exc).__name__, getattr(provider_exc, 'code', 'image_error'), attempt_index + 1 < len(model_plan))",
    "logger.warning('IMAGE_PROVIDER_MODEL_FAILED job_id=%s user_id=%s model=%s error_type=%s error_code=%s has_next_attempt=%s', job.id, job.user_id, attempt_model, type(provider_exc).__name__, getattr(provider_exc, 'code', 'image_error'), attempt_index + 1 < len(attempt_plan))",
)
replace_once(
    "app/services/image_generation_service.py",
    "                    if attempt_index + 1 < len(model_plan):\n                        continue\n",
    "                    if attempt_index + 1 < len(attempt_plan):\n                        continue\n",
)

replace_once(
    "app/services/image_generation_service.py",
    "attempt={'provider': job.provider, 'model': attempt_model, 'provider_request_id': res.request_id,",
    "attempt={'provider': job.provider, 'model': attempt_model, 'attempt_index':attempt_index, 'correction_round':correction_round, 'provider_request_id': res.request_id,",
)

replace_once(
    "app/services/image_generation_service.py",
    "                    if attempt_index + 1 < len(model_plan):\n                        logger.info('IMAGE_SINGLE_SUBJECT_RETRY job_id=%s user_id=%s chat_id=%s generation_model=%s next_generation_model=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, attempt_model, model_plan[attempt_index+1], qa.reason_codes, response_checksum[:12])\n",
    "                    if attempt_index + 1 < len(attempt_plan):\n                        logger.info('IMAGE_SINGLE_SUBJECT_RETRY job_id=%s user_id=%s chat_id=%s generation_model=%s correction_round=%s next_generation_model=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, attempt_model, correction_round, attempt_plan[attempt_index+1][0], qa.reason_codes, response_checksum[:12])\n",
)

replace_once(
    "app/services/generated_image_qa_service.py",
    "        lines.append('Correct the framing exactly: full body visible; full figure head-to-feet; camera farther away; no close-up; no crop.')\n",
    "        lines.append('Correct the framing exactly: portrait 4:5 full-body mirror composition; entire head-to-feet figure inside the frame; visible headroom above the hair and visible floor below both feet; both feet fully visible; subject no more than about 70 percent of frame height; camera farther away; no close-up and no crop.')\n",
)

Path("tests/test_krea_adult_generation_plan.py").write_text(
    '''from types import SimpleNamespace\n\nfrom app.core.config import Settings\nfrom app.services.generated_image_qa_service import corrective_prompt_for_reasons\nfrom app.services.image_generation_guardrails import select_generation_model\nfrom app.services.image_generation_service import (\n    build_generation_attempt_plan,\n    build_generation_model_plan,\n)\nfrom app.services.image_pipeline_v2 import ContentClassification\n\n\ndef test_krea_is_default_for_normal_and_adult_generation():\n    settings = Settings()\n    assert settings.image_generation_preferred_model == "krea-2-turbo"\n    assert settings.image_generation_model == "krea-2-turbo"\n    assert settings.image_generation_adult_preferred_model == "krea-2-turbo"\n    assert settings.image_generation_adult_model == "krea-2-turbo"\n\n\ndef test_adult_model_selection_uses_krea_not_lustify():\n    selected = select_generation_model(\n        content_classification=ContentClassification.FULL_NUDITY,\n        default_model="krea-2-turbo",\n        adult_model="krea-2-turbo",\n    )\n    assert selected == "krea-2-turbo"\n\n\ndef test_adult_model_plan_keeps_krea_then_both_lustify_fallbacks():\n    settings = SimpleNamespace(\n        image_generation_adult_preferred_model="krea-2-turbo",\n        image_generation_adult_model="krea-2-turbo",\n        image_generation_adult_fallback_model="lustify-sdxl",\n        image_generation_adult_emergency_models="lustify-v8",\n    )\n    assert build_generation_model_plan(\n        settings, "krea-2-turbo", adult_generation=True\n    ) == ["krea-2-turbo", "lustify-sdxl", "lustify-v8"]\n\n\ndef test_adult_attempt_plan_retries_krea_before_fallback():\n    assert build_generation_attempt_plan(\n        ["krea-2-turbo", "lustify-sdxl", "lustify-v8"],\n        adult_generation=True,\n        max_attempts=4,\n    ) == [\n        ("krea-2-turbo", 0),\n        ("krea-2-turbo", 1),\n        ("lustify-sdxl", 0),\n        ("lustify-v8", 0),\n    ]\n\n\ndef test_full_body_correction_is_composition_specific():\n    correction = corrective_prompt_for_reasons(\n        ["framing_mismatch", "missing_feet", "cropped_body"],\n        photo_contract={"camera_mode": "mirror_selfie"},\n    ).lower()\n    assert "head-to-feet" in correction\n    assert "headroom" in correction\n    assert "floor below both feet" in correction\n    assert "70 percent" in correction\n    assert "mirror" in correction\n''',
    encoding="utf-8",
)
