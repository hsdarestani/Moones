from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
router = replace_once(
    router,
    '''    if any(ref in t for ref in ["قبلی", "همونو", "همون رو", "همون عکس"]) and any(v in t for v in ["دوباره بفرست", "باز بفرست", "بفرست"]):
        return SemanticImageAction.RESEND_EXACT
    # Compatibility fallback only. Production still calls the semantic model for
''',
    '''    if any(ref in t for ref in ["قبلی", "همونو", "همون رو", "همون عکس"]) and any(v in t for v in ["دوباره بفرست", "باز بفرست", "بفرست"]):
        return SemanticImageAction.RESEND_EXACT
    previous_refs = ["قبلی", "همینو", "همین رو", "همونو", "همون رو", "همون عکس", "این عکس"]
    delivery_verbs = ["بده", "بدی", "بساز", "بگیر", "بگیری", "درست کن"]
    visual_change_markers = [
        " تو ", "توی ", " در ", " با ", " بدون ", "روی ", "کنار ", "داخل ",
        "کافه", "خونه", "خانه", "خیابون", "خیابان", "ماشین", "بیرون", "پارک",
        "نور", "پس زمینه", "بک گراند", "قدی", "سلفی", "لباس", "حالت", "فضا",
        "صبح", "ظهر", "عصر", "شب",
    ]
    if (
        any(ref in t for ref in previous_refs)
        and any(verb in t for verb in delivery_verbs)
        and any(marker in f" {t} " for marker in visual_change_markers)
    ):
        return SemanticImageAction.REFINE_PREVIOUS
    # Compatibility fallback only. Production still calls the semantic model for
''',
    "contextual previous-image refinement",
)
router_path.write_text(router)


qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
qa = replace_once(
    qa,
    '''    if bool(vr.get('explicit_nudity_requested') and vr.get('anatomy_qa_required')):
        aqa=metadata.get('adult_anatomy_qa') or {}
        ok=bool(vr.get('anatomical_profile') not in (None,'','unspecified') and aqa.get('passed') is True and aqa.get('consensus_passed') is True and len(aqa.get('qa_passes') or []) >= 2 and aqa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest() and aqa.get('anatomy_visible_enough_to_assess') is True and aqa.get('anatomy_consistent_with_profile') is True and aqa.get('contradictory_sex_characteristics') is False and aqa.get('malformed_anatomy') is False and aqa.get('implausible_anatomy') is False and aqa.get('duplicated_anatomy_parts') is False and aqa.get('missing_expected_parts_when_visible') is False and aqa.get('ambiguous_anatomy') is False and aqa.get('confidence') in {'medium','high'})
        if not ok: return False
    contract=vr.get('photo_contract') or {}
''',
    '''    adult_strict=bool(vr.get('explicit_nudity_requested') and vr.get('anatomy_qa_required'))
    if adult_strict:
        aqa=metadata.get('adult_anatomy_qa') or {}
        ok=bool(vr.get('anatomical_profile') not in (None,'','unspecified') and aqa.get('passed') is True and aqa.get('consensus_passed') is True and len(aqa.get('qa_passes') or []) >= 2 and aqa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest() and aqa.get('anatomy_visible_enough_to_assess') is True and aqa.get('anatomy_consistent_with_profile') is True and aqa.get('contradictory_sex_characteristics') is False and aqa.get('malformed_anatomy') is False and aqa.get('implausible_anatomy') is False and aqa.get('duplicated_anatomy_parts') is False and aqa.get('missing_expected_parts_when_visible') is False and aqa.get('ambiguous_anatomy') is False and aqa.get('confidence') in {'medium','high'})
        if not ok: return False
    # A normal image that was explicitly accepted in bounded degraded mode already
    # passed provider/error-screen checks. Do not contradict that decision at delivery
    # merely because the Vision QA provider was unavailable. Adult/anatomy QA remains
    # fail-closed above.
    if metadata.get('qa_degraded_provider_unavailable') is True and not adult_strict:
        return True
    contract=vr.get('photo_contract') or {}
''',
    "degraded QA delivery contract",
)
qa_path.write_text(qa)


contract_path = Path("app/services/partner_photo_contract.py")
contract = contract_path.read_text()
contract = replace_once(
    contract,
    '''        lines.append("Identity continuity is mandatory: this must be the same recurring fictional partner, never a new generic person.")
''',
    '''        lines.append("Identity continuity is mandatory: preserve the same exact recurring fictional partner's facial structure, eye shape, nose, mouth, skin tone, hairline, hair texture, apparent age and body build; never substitute a new generic person.")
''',
    "stronger identity continuity prompt",
)
contract = replace_once(
    contract,
    '''    if status == "queued":
        return "آره یادمه؛ یه لحظه بذار عکسش خوب دربیاد 🤍"
    if status in {"processing", "generating"}:
        return "هنوز دارم درستش می‌کنم؛ این یکی رو نمی‌خوام سرسری بفرستم 🤍"
''',
    '''    if status == "queued":
        return "درخواست عکست ثبت شده و هنوز توی صف ساخته‌شدنه؛ به محض آماده شدن خود عکس رو می‌فرستم 🤍"
    if status in {"processing", "generating"}:
        return "عکست هنوز در حال ساخته‌شدنه؛ به محض آماده شدن خود عکس رو می‌فرستم 🤍"
''',
    "precise image status copy",
)
contract_path.write_text(contract)


docker_path = Path("Dockerfile")
docker = docker_path.read_text()
docker = replace_once(
    docker,
    'CMD ["uvicorn", "app.ops_main:app", "--host", "0.0.0.0", "--port", "8000"]\n',
    'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n',
    "restore production entrypoint",
)
docker_path.write_text(docker)

for temporary in (
    "conftest.py",
    "app/ops_image_job_startup_diagnostic.py",
    "app/api/ops_image_job_diagnostic.py",
    "app/ops_main.py",
):
    path = Path(temporary)
    if path.exists():
        path.unlink()


test_path = Path("tests/test_refinement_qa_delivery_contract.py")
test_path.write_text('''import hashlib
from types import SimpleNamespace


def test_contextual_previous_image_request_routes_to_refinement():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        canonical_explicit_image_action,
    )

    assert canonical_explicit_image_action("همین قبلی رو تو کافه بده") == SemanticImageAction.REFINE_PREVIOUS
    assert canonical_explicit_image_action("همونو با نور طبیعی بده") == SemanticImageAction.REFINE_PREVIOUS
    assert canonical_explicit_image_action("این عکس رو قدی بده") == SemanticImageAction.REFINE_PREVIOUS


def test_previous_image_chat_and_exact_resend_stay_distinct():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        canonical_explicit_image_action,
    )

    assert canonical_explicit_image_action("عکس قبلی خوب نبود چرا") is None
    assert canonical_explicit_image_action("عکس قبلی رو دوباره بفرست") == SemanticImageAction.RESEND_EXACT


def test_refinement_route_uses_latest_delivered_source_job():
    from app.api.telegram import _semantic_decision_to_legacy_route
    from app.services.semantic_image_intent_router import SemanticImageAction, SemanticImageDecision

    decision = SemanticImageDecision(
        action=SemanticImageAction.REFINE_PREVIOUS,
        media_delivery_requested=True,
        confidence=1.0,
        reason_code="deterministic_explicit_action",
        needs_clarification=False,
    )
    route = _semantic_decision_to_legacy_route(decision, SimpleNamespace(id=131))
    assert route.route == "semantic_refine_previous"
    assert route.source_image_job_id == 131
    assert route.contextual_followup is True


def _qa_metadata(image_bytes: bytes, *, degraded: bool, adult: bool = False) -> dict:
    checksum = hashlib.sha256(image_bytes).hexdigest()
    metadata = {
        "generated_image_qa": {
            "passed": True,
            "artifact_checksum": checksum,
            "reason_codes": [],
        },
        "qa_degraded_provider_unavailable": degraded,
        "qa_requested_framing": "full_body",
        "visual_requirements": {
            "full_body_visible": True,
            "framing_requirement": "full_body",
            "explicit_nudity_requested": adult,
            "anatomy_qa_required": adult,
            "photo_contract": {
                "camera_mode": "casual_selfie",
                "identity_consistency_required": True,
                "identity_visibility_scope": "full",
                "natural_capture_required": True,
                "face_visible": True,
            },
        },
    }
    if adult:
        metadata["visual_requirements"]["anatomical_profile"] = "female"
    return metadata


def test_normal_full_body_qa_outage_stays_deliverable_after_degraded_acceptance():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"normal-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=True), image) is True


def test_strict_contract_without_degraded_acceptance_remains_fail_closed():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"normal-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=False), image) is False


def test_adult_anatomy_never_uses_degraded_delivery_bypass():
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa

    image = b"adult-generated-image"
    assert metadata_has_valid_generated_image_qa(_qa_metadata(image, degraded=True, adult=True), image) is False


def test_status_copy_reports_real_job_state():
    from app.services.partner_photo_contract import image_status_text

    assert "ثبت شده" in image_status_text("queued")
    assert "در حال ساخته" in image_status_text("generating")
    assert "خود عکس" in image_status_text("processing")
''')

print("patch_refinement_qa_delivery_identity: ok")
