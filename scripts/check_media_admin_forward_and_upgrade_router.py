#!/usr/bin/env python3
from pathlib import Path

checks=[]
def expect(path, needle, label):
    text=Path(path).read_text(encoding='utf-8')
    ok=needle in text
    checks.append((ok,label))
    if not ok:
        print(f"FAIL: {label}: missing {needle!r} in {path}")

def expect_order(path, first, second, label):
    text=Path(path).read_text(encoding='utf-8')
    ok=first in text and second in text and text.index(first) < text.index(second)
    checks.append((ok,label))
    if not ok:
        print(f"FAIL: {label}: expected {first!r} before {second!r}")

expect('app/core/config.py','admin_media_forward_enabled: bool = True','admin forwarding env')
expect('app/core/config.py','admin_media_review_chat_id: str = ""','admin review chat env')
expect('app/core/config.py','management_bot_username: str = "moonesaibot"','management username env')
expect('app/core/config.py','management_bot_url: str = "https://t.me/moonesaibot"','management url env')
expect('.env.example','ADMIN_MEDIA_FORWARD_ENABLED=true','env example admin forward')
expect('.env.example','MANAGEMENT_BOT_URL=https://t.me/moonesaibot','env example management url')

expect('app/api/telegram.py','def is_upgrade_or_feature_unlock_intent','upgrade intent function')
expect('app/api/telegram.py','UPGRADE_INTENT_ROUTED_TO_MANAGEMENT_BOT','upgrade route log')
expect('app/api/telegram.py','is_upgrade_or_feature_unlock_intent(text)','upgrade route before LLM')
expect('app/api/telegram.py','UPGRADE_INTENT_MESSAGE','upgrade direct response')
expect('app/api/telegram.py','_management_keyboard()','upgrade inline management keyboard')
expect('app/api/telegram.py','چطور باز کنم','persian unlock phrase')
expect('app/api/telegram.py','قابلیتاش بیشتر','persian feature phrase')
expect_order('app/api/telegram.py','is_upgrade_or_feature_unlock_intent(text)','handle_simple_chat(db,user,text','upgrade route before raw LLM call')

expect('app/api/telegram.py','FREE_PHOTO_RECEIVED','free photo receipt log')
expect('app/api/telegram.py','FREE_PHOTO_FORWARDED_TO_ADMIN','free photo forwarded log')
expect('app/api/telegram.py','FREE_PHOTO_FORWARD_FAILED','free photo failure log')
expect('app/api/telegram.py','FREE_PHOTO_ADMIN_FORWARD_SKIPPED_NO_CHAT_ID','missing admin chat skip log')
expect('app/api/telegram.py','PHOTO_INPUT_BLOCKED_FREE_PLAN','photo free blocked log')
expect('app/api/telegram.py','svc.send_photo(admin_chat_id, file_id, caption)','admin sendPhoto uses file_id')
expect('app/api/telegram.py','svc.copy_message(admin_chat_id, msg.chat.id, msg.message_id, caption)','admin copyMessage fallback')
expect('app/api/telegram.py','svc.forward_message(admin_chat_id, msg.chat.id, msg.message_id)','admin forwardMessage fallback')
expect_order('app/api/telegram.py','if not allowed:','VISION_ANALYSIS_STARTED','free media gate precedes vision')
expect('app/api/telegram.py','return {"ok": True}\n    photo=(msg.photo or [])[-1]','blocked photo returns before paid flow')

expect('app/api/telegram.py','VOICE_INPUT_BLOCKED_FREE_PLAN','voice free blocked log')
expect('app/api/telegram.py','voice_input_blocked_free_plan','voice admin reason')
expect('app/api/telegram.py','return {"ok": True}\n    if duration and duration > settings.max_voice_seconds','blocked voice returns before STT')

expect('app/services/media_input_service.py','@moonesaibot','blocked messages name management bot')
expect('app/services/media_input_service.py','سهمیه دیدن عکس این ماهت تموم شده','photo quota message')
expect('app/services/media_input_service.py','سهمیه وویس این ماهت تموم شده','voice quota message')
expect('app/engine/simple_chat.py','RAW_LLM_OUTPUT_USED','normal raw LLM path intact')
expect('app/engine/simple_chat.py','_env_enabled("NATURAL_STYLE_GUARD_ENABLED", False)','natural guard disabled by default')
expect('app/api/telegram.py','media_inputs.record_media_usage(db,user,"photo")','paid photo quota usage remains')
expect('app/api/telegram.py','media_inputs.record_media_usage(db,user,"voice")','paid voice quota usage remains')

failed=[label for ok,label in checks if not ok]
if failed:
    raise SystemExit(1)
print(f"media admin forward and upgrade router checks passed ({len(checks)} checks)")
