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
    '''    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز", "عکس", "ببینم", "ببینمت"}\n    if word_set & chat_markers:\n        return None\n    if word_set & refine_markers:\n        return "refine_previous"\n    if word_set & generate_markers:\n        return "generate_new"\n    # The question already offered only new/edit. Any other short affirmative reply\n    # defaults to a new photo instead of reopening the same clarification loop.\n    return "generate_new" if len(words) <= 4 else None\n''',
    '''    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز", "عکس", "ببینم", "ببینمت"}\n    affirmative_markers = {"باشه", "باش", "آره", "اره", "بله", "اوکی", "حتما", "حتماً"}\n    if word_set & chat_markers:\n        return None\n    if word_set & refine_markers:\n        return "refine_previous"\n    if word_set & generate_markers:\n        return "generate_new"\n    if word_set & affirmative_markers and len(words) <= 4:\n        return "generate_new"\n    return None\n''',
    "safe short clarification defaults",
)
router = replace_once(
    router,
    '''    explicitly_editing_previous = any(marker in normalized for marker in previous_markers) and any(marker in normalized for marker in edit_markers)\n    if explicitly_editing_previous:\n        return decision\n''',
    '''    references_previous_image = any(marker in normalized for marker in previous_markers)\n    explicitly_editing_previous = references_previous_image and any(marker in normalized for marker in edit_markers)\n    if references_previous_image or explicitly_editing_previous:\n        return decision\n''',
    "preserve all previous-image references",
)
router_path.write_text(router)

test_path = Path("tests/test_remove_new_vs_edit_clarification.py")
test = test_path.read_text()
test = replace_once(
    test,
    '''    assert default_pending_clarification_action("باشه بگیر") == "generate_new"\n''',
    '''    assert default_pending_clarification_action("باشه بگیر") == "generate_new"\n    assert default_pending_clarification_action("باشه") == "generate_new"\n    assert default_pending_clarification_action("خوبی") is None\n''',
    "safe short reply tests",
)
test = replace_once(
    test,
    '''    resolved = enforce_new_photo_default("همون عکس قبلی رو تغییر بده", None, decision)\n    assert resolved.action == SemanticImageAction.CLARIFY\n''',
    '''    resolved = enforce_new_photo_default("همون عکس قبلی رو تغییر بده", None, decision)\n    assert resolved.action == SemanticImageAction.CLARIFY\n    second = SemanticImageDecision(\n        action=SemanticImageAction.CLARIFY,\n        media_delivery_requested=False,\n        confidence=.7,\n        reason_code="image_source_ambiguous",\n        needs_clarification=True,\n    )\n    assert enforce_new_photo_default("عکس قبلی خوب نبود چرا", None, second).action == SemanticImageAction.CLARIFY\n''',
    "previous image reference test",
)
test_path.write_text(test)
print("patch_clarification_defaults_final: ok")
