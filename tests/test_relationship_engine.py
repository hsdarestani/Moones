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
