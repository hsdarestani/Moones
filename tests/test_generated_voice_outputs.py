from app.models.image_generation import GeneratedVoiceOutput


def test_generated_voice_model_tracks_message_and_feedback_fields():
    cols = GeneratedVoiceOutput.__table__.columns.keys()
    assert 'user_telegram_message_id' in cols
    assert 'archive_telegram_message_id' in cols
    assert 'feedback' in cols
