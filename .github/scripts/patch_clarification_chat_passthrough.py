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
    '''    if word_set & chat_markers:\n        return "chat"\n    if word_set & refine_markers:\n''',
    '''    if word_set & chat_markers:\n        return None\n    if word_set & refine_markers:\n''',
    "unrelated pending clarification passthrough",
)
router_path.write_text(router)

test_path = Path("tests/test_remove_new_vs_edit_clarification.py")
test = test_path.read_text()
test = replace_once(
    test,
    '''    assert default_pending_clarification_action("نه بیخیال") == "chat"\n''',
    '''    assert default_pending_clarification_action("نه بیخیال") is None\n    assert default_pending_clarification_action("وا مگه نگفتی کافه ای برگشتی خونه") is None\n''',
    "passthrough test expectation",
)
test_path.write_text(test)
print("patch_clarification_chat_passthrough: ok")
