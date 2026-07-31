from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing snippet: {label}")
    return text.replace(old, new, 1)


p = Path("app/services/image_generation_service.py")
s = p.read_text(encoding="utf-8")
old = '''def build_generation_attempt_plan(model_plan: list[str], *, adult_generation: bool, max_attempts: int) -> list[tuple[str, int]]:
    attempts: list[tuple[str, int]] = []
    for model in model_plan:
        attempts.append((model, 0))
        # Each allowed adult generator gets at most one bounded QA-driven
        # corrective retry. Krea keeps its identity seed; Seedream receives a new
        # deterministic seed so an invented prop/collage is not reproduced. No
        # third model may enter the route.
        if adult_generation and model in ADULT_ALLOWED_GENERATION_MODELS:
            attempts.append((model, 1))
    return attempts[: max(1, int(max_attempts))]
'''
new = '''def partner_identity_generation_required(metadata: dict | None) -> bool:
    """Whether this generation visibly represents the recurring partner.

    This is semantic and scene-agnostic: no location/activity names are involved.
    Object-only, pet-only, scene-only and zero-human requests must not inherit a
    person identity seed.
    """
    meta=dict(metadata or {})
    vr=dict(meta.get('visual_requirements') or {})
    contract=dict(vr.get('photo_contract') or meta.get('photo_contract') or {})
    try:
        expected=int(meta.get('expected_subject_count', contract.get('expected_human_subject_count', 1)))
    except (TypeError, ValueError):
        expected=1
    partner_visible=vr.get('partner_visible', contract.get('partner_visible', True)) is not False
    primary=str(contract.get('primary_subject') or meta.get('primary_subject_role') or 'partner').strip().lower()
    object_only=bool(contract.get('object_only') or contract.get('pet_only'))
    return bool(expected == 1 and partner_visible and not object_only and primary in {'partner','person','self','moones_partner'})


def build_generation_attempt_plan(model_plan: list[str], *, adult_generation: bool, max_attempts: int, identity_locked_generation: bool=False) -> list[tuple[str, int]]:
    attempts: list[tuple[str, int]] = []
    for model in model_plan:
        attempts.append((model, 0))
        # Adult: both allowed generators may receive one bounded QA correction.
        # Any ordinary recurring-partner photo: Krea alone gets one same-seed
        # correction before fallback. This is independent of scene or activity.
        if adult_generation and model in ADULT_ALLOWED_GENERATION_MODELS:
            attempts.append((model, 1))
        elif identity_locked_generation and model == ADULT_PRIMARY_GENERATION_MODEL:
            attempts.append((model, 1))
    return attempts[: max(1, int(max_attempts))]
'''
s = replace_once(s, old, new, "attempt plan and identity predicate")
old = '''            max_generation_attempts = int(getattr(settings, 'image_generation_adult_max_generation_attempts', 4) or 4) if adult_generation else len(model_plan)
            attempt_plan = build_generation_attempt_plan(model_plan, adult_generation=adult_generation, max_attempts=max_generation_attempts)
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'effective_generation_attempt_plan':[{'model':model,'correction_round':round_index} for model,round_index in attempt_plan],'deferred_generation_models':deferred_generation_models,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}
'''
new = '''            identity_locked_generation=partner_identity_generation_required(meta)
            if adult_generation:
                max_generation_attempts=int(getattr(settings, 'image_generation_adult_max_generation_attempts', 4) or 4)
            elif identity_locked_generation:
                # Krea base + same-seed correction + one fallback. Corrections are
                # conditional, so successful first attempts do not add cost.
                max_generation_attempts=min(3, max(1, len(model_plan) + 1))
            else:
                max_generation_attempts=len(model_plan)
            attempt_plan = build_generation_attempt_plan(
                model_plan,
                adult_generation=adult_generation,
                identity_locked_generation=identity_locked_generation,
                max_attempts=max_generation_attempts,
            )
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'effective_generation_attempt_plan':[{'model':model,'correction_round':round_index} for model,round_index in attempt_plan],'identity_locked_generation':identity_locked_generation,'deferred_generation_models':deferred_generation_models,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}
'''
s = replace_once(s, old, new, "worker attempt plan")
old = '''            if adult_generation and ADULT_PRIMARY_GENERATION_MODEL in model_plan:
                # Use the profile-level identity seed, not the per-request scene
                # seed. This keeps Krea in one identity family across separate
                # scenes and messages while prompt fields control composition.
'''
new = '''            if identity_locked_generation and ADULT_PRIMARY_GENERATION_MODEL in model_plan:
                # Every visible recurring-partner Krea image uses the profile-level
                # identity seed, not the per-request scene seed. Scene/activity/
                # styling remain prompt variables and may be completely arbitrary.
'''
s = replace_once(s, old, new, "stable krea all partner images")
old = '''                if adult_generation and attempt_model == ADULT_PRIMARY_GENERATION_MODEL:
                    # Keep the exact numeric Krea seed across its base and
                    # composition-correction attempts. Only framing instructions
                    # may change; identity seed/family must not drift.
'''
new = '''                if identity_locked_generation and attempt_model == ADULT_PRIMARY_GENERATION_MODEL:
                    # Keep the exact numeric Krea identity seed across ordinary,
                    # adult and correction attempts. Context may change; identity
                    # seed/family must not drift.
'''
s = replace_once(s, old, new, "attempt stable krea seed")
p.write_text(s, encoding="utf-8")


p = Path("app/llm/image_client.py")
s = p.read_text(encoding="utf-8")
old = '''    if "identity lock" in lower or "stored fingerprint" in lower or "same recognizable person" in lower:
        essentials.append("Preserve the same stored fictional adult identity: face shape, eyes, eyebrows, hair and hairline, skin tone, age appearance, body build, and distinguishing details.")
'''
new = '''    if "identity lock" in lower or "stored fingerprint" in lower or "same recognizable person" in lower:
        essentials.append("Preserve the same canonical fictional identity: core face geometry, eye shape and spacing, eyebrows, nose geometry, jaw/chin structure, stable distinguishing features, core hair color/texture, skin tone, and body-build family.")
    age_match=re.search(r"fictional age\\s+(\\d{2,3})", prompt, re.I)
    if age_match:
        essentials.append(f"Mutable profile overlay: render this same canonical person at fictional age {age_match.group(1)}; age appearance may change but the canonical facial identity must not be redesigned or replaced.")
'''
s = replace_once(s, old, new, "provider compact identity")
old = '''        "recurring fictional partner",
        "identity lock",
        "scene:",
'''
new = '''        "recurring fictional partner",
        "identity lock",
        "canonical identity lock",
        "mutable profile overlay",
        "scene:",
'''
s = replace_once(s, old, new, "provider priority markers")
p.write_text(s, encoding="utf-8")


p = Path("app/services/partner_photo_contract.py")
s = p.read_text(encoding="utf-8")
old = '''    if contract.get("identity_consistency_required"):
        lines.append("Identity continuity is mandatory: preserve the same exact recurring fictional partner's facial structure, eye shape, nose, mouth, skin tone, hairline, hair texture, apparent age and body build; never substitute a new generic person.")
'''
new = '''    if contract.get("identity_consistency_required"):
        lines.append("Identity continuity is mandatory: preserve the same exact recurring fictional partner's canonical facial structure, eye shape and spacing, nose geometry, mouth, jaw/chin structure, skin tone, stable hair characteristics and body-build family; never substitute a new generic person. Apply the current profile age as a mutable appearance overlay rather than treating an age edit as a new identity.")
'''
s = replace_once(s, old, new, "photo contract identity wording")
p.write_text(s, encoding="utf-8")


p = Path("conftest.py")
s = p.read_text(encoding="utf-8")
old = '''    user = SimpleNamespace(partner_gender="دختر")
    profile = SimpleNamespace(
'''
new = '''    user = SimpleNamespace(partner_gender="دختر", partner_name="مونس", partner_age_range="30+")
    profile = SimpleNamespace(
'''
s = replace_once(s, old, new, "live smoke user age")
old = '''        fictional_age=25,
'''
new = '''        fictional_age=30,
'''
s = replace_once(s, old, new, "live smoke initial age")
old = '''    profile = ensure_visual_profile_v2(DummyDB(), user, profile)
    intent = parse_image_intent(normalize_request_v2(request))
'''
new = '''    profile = ensure_visual_profile_v2(DummyDB(), user, profile)
    identity_fp_before=(profile.profile_json or {}).get("identity_anchor_fingerprint")
    identity_seed_before=profile.base_seed
    user.partner_age_range="18-20"
    profile = ensure_visual_profile_v2(DummyDB(), user, profile)
    identity_fp_after=(profile.profile_json or {}).get("identity_anchor_fingerprint")
    if not identity_fp_before or identity_fp_after != identity_fp_before or profile.base_seed != identity_seed_before or profile.fictional_age != 18:
        raise RuntimeError(
            "LIVE_IDENTITY_EDIT_CONTRACT_FAILED "
            f"fingerprint_stable={identity_fp_after == identity_fp_before} seed_stable={profile.base_seed == identity_seed_before} age={profile.fictional_age}"
        )
    print(
        "LIVE_IDENTITY_EDIT_CONTRACT_OK "
        f"fingerprint_prefix={identity_fp_after[:12]} seed={profile.base_seed} age={profile.fictional_age}",
        flush=True,
    )
    intent = parse_image_intent(normalize_request_v2(request))
'''
s = replace_once(s, old, new, "live identity edit contract")
p.write_text(s, encoding="utf-8")
