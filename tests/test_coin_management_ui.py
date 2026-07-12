from app.services.provider_pricing_registry import list_prices, REGISTRY_VERSION

def test_registry_read_only_and_contains_krea():
    prices=list_prices(); assert REGISTRY_VERSION and any(p.model_id=='krea-2-turbo' and p.feature=='image_1k' for p in prices)

def test_no_plan_words_in_new_main_menu_contract():
    labels=['💬 رفتن به چت','👤 پارتنر من','🪙 موجودی و هزینه‌ها','➕ افزودن سکه','🧩 افزودنی‌ها','⚙️ تنظیمات','🧠 وضعیت رابطه','پشتیبانی']
    assert all('پلن' not in x and 'اشتراک' not in x for x in labels)
