from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text()
old = '''            if adult_generation:
                fallback_model = (getattr(settings, 'image_generation_adult_fallback_model', '') or '').strip()
                emergency_models = []
            else:
                fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()
                emergency_models = [part.strip() for part in str(getattr(settings, 'image_generation_emergency_models', '') or '').split(',') if part.strip()]
            configured_model_plan = []
            for candidate_model in [primary_model, fallback_model, *emergency_models]:
                if candidate_model and candidate_model not in configured_model_plan:
                    configured_model_plan.append(candidate_model)
            model_plan = list(configured_model_plan)
'''
new = '''            configured_current_model = (getattr(settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()
            if adult_generation:
                fallback_model = (getattr(settings, 'image_generation_adult_fallback_model', '') or '').strip()
                candidate_models = [primary_model, fallback_model]
            else:
                fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()
                emergency_models = [part.strip() for part in str(getattr(settings, 'image_generation_emergency_models', '') or '').split(',') if part.strip()]
                candidate_models = [primary_model, configured_current_model, fallback_model, *emergency_models]
            configured_model_plan = []
            for candidate_model in candidate_models:
                if candidate_model and candidate_model not in configured_model_plan:
                    configured_model_plan.append(candidate_model)
            model_plan = list(configured_model_plan)
'''
service = replace_once(service, old, new, "current primary in provider plan")
old_filter = '''            if available_models is not None:
                skipped_unavailable_models = [model for model in model_plan if model not in available_models]
                model_plan = [model for model in model_plan if model in available_models]
                if skipped_unavailable_models:
                    logger.warning('IMAGE_PROVIDER_MODELS_SKIPPED_UNAVAILABLE job_id=%s models=%s', job.id, skipped_unavailable_models)
            if not model_plan:
                raise ImageValidationError('no_configured_image_model_available')
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}
'''
new_filter = '''            if available_models is not None:
                skipped_unavailable_models = [model for model in model_plan if model not in available_models]
                model_plan = [model for model in model_plan if model in available_models]
                if skipped_unavailable_models:
                    logger.warning('IMAGE_PROVIDER_MODELS_SKIPPED_UNAVAILABLE job_id=%s models=%s', job.id, skipped_unavailable_models)
            deferred_generation_models = model_plan[2:]
            model_plan = model_plan[:2]
            if not model_plan:
                raise ImageValidationError('no_configured_image_model_available')
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'deferred_generation_models':deferred_generation_models,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}
'''
service = replace_once(service, old_filter, new_filter, "limit provider attempts")
service_path.write_text(service)


test_path = Path("tests/test_image_provider_failover.py")
test = test_path.read_text()
test = replace_once(
    test,
    '        assert result.metadata_json["skipped_unavailable_generation_models"] == ["krea-2-turbo", "venice-sd35"]\n',
    '        assert set(result.metadata_json["skipped_unavailable_generation_models"]) == {"krea-2-turbo", "venice-sd35", "z-image-turbo"}\n',
    "discovery skipped models expectation",
)
test_path.write_text(test)
print("patch_provider_plan_limit: ok")
