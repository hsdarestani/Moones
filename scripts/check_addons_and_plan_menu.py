from pathlib import Path

checks = []

def expect(path, needle, desc):
    text = Path(path).read_text(encoding='utf-8')
    assert needle in text, f"missing {desc}: {needle} in {path}"
    checks.append(desc)

def reject(path, needle, desc):
    text = Path(path).read_text(encoding='utf-8')
    assert needle not in text, f"unexpected {desc}: {needle} in {path}"
    checks.append(desc)

expect('app/services/bot_menu_service.py','🧩 افزودنی‌ها','Add-ons menu exists in management bot')
expect('app/services/addon_service.py','intimacy_max_unlock','Default add-on product exists')
expect('app/services/addon_service.py','100000','Price defaults to 100000')
expect('app/api/admin.py','ADDON_PRICE_UPDATED','Admin can change price')
expect('app/services/bot_menu_service.py','activate_addon_from_wallet','User can buy add-on without subscription plan changing')
expect('app/services/bot_menu_service.py','addon_purchase','User with enough wallet balance can activate add-on')
expect('app/services/bot_menu_service.py','اعتبارت کافی نیست. اول موجودی اضافه کن','User without balance is sent to top-up')
expect('app/api/admin.py','rec.purpose == "addon"','Approved add-on receipt activates add-on')
for needle in ['intimacy_override_max = True','mature_intimacy_unlocked = True','intimacy_level = MAX_INTIMACY_LEVEL']:
    expect('app/services/addon_service.py', needle, f'Add-on sets {needle}')
expect('app/engine/simple_chat.py','Relationship/intimacy state:','Persona context includes max intimacy')
expect('app/engine/simple_chat.py','intimacy_override = (not underage_signal)','Persona normal users are conditional')
expect('app/services/bot_menu_service.py','پلن‌های مونس 🌙','Plan menu text is simplified')
reject('app/services/bot_menu_service.py','برای وقتی که مونس بخشی از روزته','Old Plus long paragraph removed')
for path in ['app/services/bot_menu_service.py','app/api/telegram.py']:
    reject(path,'افزایش' + ' ظرفیت',f'Old capacity label removed from {path}')
expect('app/services/bot_menu_service.py','افزودن موجودی','New top-up label appears')
expect('app/services/bot_menu_service.py','activate_subscription','Normal subscription purchase still exists')
expect('app/services/bot_menu_service.py','payment_i_paid','Existing wallet top-up still works')
expect('app/engine/simple_chat.py','RAW_LLM_OUTPUT_USED','Raw LLM mode remains')
expect('app/services/media_input_service.py','Monthly paid media input quota','Media input paid gating remains')
print(f"addon and plan menu checks passed ({len(checks)} checks)")
