from datetime import datetime
from types import SimpleNamespace


def test_natural_fresh_reply_resolves_to_generate_new():
    from app.services.semantic_image_intent_router import _clarification_action_from_reply
    assert _clarification_action_from_reply("بگیر تازه ببینم") == "generate_new"
    assert _clarification_action_from_reply("جدید بده بابا کشتی") == "generate_new"
    assert _clarification_action_from_reply("تازه تازه") == "generate_new"


def test_pending_unknown_short_affirmative_defaults_to_new():
    from app.services.semantic_image_intent_router import default_pending_clarification_action
    assert default_pending_clarification_action("باشه بگیر") == "generate_new"
    assert default_pending_clarification_action("همون قبلی رو تغییر بده") == "refine_previous"
    assert default_pending_clarification_action("نه بیخیال") == "chat"


def test_clear_image_complaint_never_reopens_new_vs_edit_question():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction, SemanticImageDecision, enforce_new_photo_default,
    )
    decision = SemanticImageDecision(
        action=SemanticImageAction.CLARIFY,
        media_delivery_requested=False,
        confidence=.7,
        reason_code="image_action_ambiguous",
        needs_clarification=True,
    )
    resolved = enforce_new_photo_default("عکس ندادی اعصابم گریه", None, decision)
    assert resolved.action == SemanticImageAction.GENERATE_NEW
    assert resolved.media_delivery_requested is True
    assert resolved.needs_clarification is False


def test_explicit_previous_edit_is_not_forced_to_new():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction, SemanticImageDecision, enforce_new_photo_default,
    )
    decision = SemanticImageDecision(
        action=SemanticImageAction.CLARIFY,
        media_delivery_requested=False,
        confidence=.7,
        reason_code="image_source_ambiguous",
        needs_clarification=True,
    )
    resolved = enforce_new_photo_default("همون عکس قبلی رو تغییر بده", None, decision)
    assert resolved.action == SemanticImageAction.CLARIFY


def test_pending_resolution_uses_original_request_and_resolves_once():
    from app.services.semantic_image_intent_router import resolve_pending_image_clarification
    source = SimpleNamespace(id=10, content="عکس ندادی اعصابم گریه", telegram_message_id=100)
    clarification = SimpleNamespace(
        id=11,
        created_at=datetime.utcnow(),
        metadata_json={
            "kind": "pending_image_clarification",
            "status": "pending",
            "source_user_telegram_message_id": 100,
        },
    )
    class Rows:
        def all(self):
            return [clarification]
    class DB:
        def scalars(self, statement):
            return Rows()
        def scalar(self, statement):
            return source
    result = resolve_pending_image_clarification(DB(), user_id=1, text="بگیر تازه ببینم")
    assert result.action == "generate_new"
    assert result.effective_request_text == source.content
