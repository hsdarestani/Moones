from app.engine.emotion_engine import Emotion, detect_emotion
from app.engine.relationship_engine import update_state
from app.models.relationship import Relationship, RelationshipStage


def test_detect_emotion_supports_persian_lonely_keyword() -> None:
    assert detect_emotion("امشب خیلی تنها هستم") == Emotion.LONELY


def test_relationship_state_progresses_from_baseline() -> None:
    state = Relationship(user_id=1)
    updated = update_state(state, message_count=5, emotion=Emotion.EXCITED, last_seen_at=None)

    assert updated.intimacy > 0.05
    assert updated.trust > 0.05
    assert updated.stage in {stage.value for stage in RelationshipStage}

from app.engine.relationship_engine import update_simple_chat_relationship
from app.services.bot_menu_service import BotMenuService


def test_affectionate_simple_chat_message_increases_intimacy_and_trust() -> None:
    state = Relationship(user_id=2, intimacy=0.1, trust=0.1, attachment=0.1, attraction=0.1)
    updated = update_simple_chat_relationship(state, "عزیزم دوستت دارم مرسی", "منم همینطور", "affectionate")

    assert updated.intimacy > 0.1
    assert updated.trust > 0.1


def test_rude_simple_chat_message_updates_volatility_without_crash() -> None:
    state = Relationship(user_id=3, intimacy=0.1, trust=0.1, attachment=0.1, attraction=0.1, volatility=0.1)
    updated = update_simple_chat_relationship(state, "خفه شو مزخرفه", "باشه", "cold")

    assert updated.volatility > 0.1
    assert updated.stage in {stage.value for stage in RelationshipStage}


def test_none_relationship_fields_are_normalized_to_zero() -> None:
    state = Relationship(user_id=4)
    state.intimacy = None
    state.trust = None
    state.attachment = None
    state.attraction = None
    state.dependency = None
    state.volatility = None

    updated = update_simple_chat_relationship(state, "سلام", "سلام", "warm")

    assert updated.intimacy >= 0
    assert updated.trust >= 0
    assert updated.attachment >= 0
    assert updated.attraction >= 0
    assert updated.volatility >= 0


def test_relationship_menu_text_handles_none_values() -> None:
    class UserStub:
        id = 5
        relationship_state = Relationship(user_id=5)

    UserStub.relationship_state.intimacy = None
    UserStub.relationship_state.trust = None
    UserStub.relationship_state.attachment = None
    UserStub.relationship_state.attraction = None
    UserStub.relationship_state.stage = None

    text = BotMenuService().relationship_text(UserStub())

    assert "وضعیت رابطه" in text
    assert "صمیمیت: 0٪" in text
