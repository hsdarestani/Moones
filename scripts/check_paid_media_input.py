#!/usr/bin/env python3
from pathlib import Path

checks = []
def expect(path, needle, label):
    text = Path(path).read_text(encoding='utf-8')
    ok = needle in text
    checks.append((ok, label))
    if not ok:
        print(f"FAIL: {label}: missing {needle!r} in {path}")

expect('app/services/media_input_service.py', 'FREE_PHOTO_MESSAGE', 'free photo gate message')
expect('app/services/media_input_service.py', 'FREE_VOICE_MESSAGE', 'free voice gate message')
expect('app/services/media_input_service.py', 'monthly_image_inputs_used', 'image quota usage counter')
expect('app/services/media_input_service.py', 'monthly_voice_inputs_used', 'voice quota usage counter')
expect('app/services/media_input_service.py', 'generate_media_ref', 'media ref generator')
expect('app/services/media_input_service.py', 'store_telegram_file_id', 'file_id persistence gate')
expect('app/api/telegram.py', 'PHOTO_SUPPORT_FORWARD_STARTED', 'support forward before vision logging')
expect('app/api/telegram.py', 'forward_photo_to_support', 'photo support forwarding call')
expect('app/api/telegram.py', 'VISION_ANALYSIS_STARTED', 'vision starts after support forward')
expect('app/api/telegram.py', 'transcribe_audio_with_venice', 'paid voice STT call')
expect('app/api/telegram.py', 'save_user_message=False', 'vision/STT persona path does not duplicate direct model output')
expect('app/api/telegram.py', 'os.remove(tmp)', 'temp media deletion')
expect('app/services/support_media_service.py', '/sendPhoto', 'support sendPhoto uses file_id')
expect('app/services/support_media_service.py', '/copyMessage', 'support copyMessage fallback')
expect('app/llm/vision_client.py', 'return JSON only', 'vision structured prompt')
expect('app/llm/stt_client.py', '/audio/transcriptions', 'Venice STT endpoint')
expect('app/models/media.py', 'stored_path', 'raw storage nullable metadata')
expect('app/models/media.py', 'summary_json', 'summary metadata persisted')
expect('migrations/versions/0024_paid_media_inputs.py', 'media_messages', 'media migration table')
expect('app/engine/simple_chat.py', 'RAW_LLM_OUTPUT_USED', 'normal chat raw LLM mode remains')
expect('app/engine/simple_chat.py', '_env_enabled("NATURAL_STYLE_GUARD_ENABLED", False)', 'Natural Style Guard remains disabled by default')

# Ordering check: support forwarding must be attempted before vision starts in photo handler.
telegram = Path('app/api/telegram.py').read_text(encoding='utf-8')
if not (telegram.index('PHOTO_SUPPORT_FORWARD_STARTED') < telegram.index('VISION_ANALYSIS_STARTED')):
    print('FAIL: support forwarding does not precede vision')
    checks.append((False, 'support before vision'))
else:
    checks.append((True, 'support before vision'))

failed = [label for ok, label in checks if not ok]
if failed:
    raise SystemExit(1)
print(f"paid media input checks passed ({len(checks)} checks)")
