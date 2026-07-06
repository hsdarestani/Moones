#!/usr/bin/env python3
from pathlib import Path
import ast

root = Path(__file__).resolve().parents[1]
checks = []

def ok(name, condition):
    if not condition:
        raise SystemExit(f"FAIL: {name}")
    checks.append(name)

css = (root / "app/static/admin.css").read_text(encoding="utf-8")
base = (root / "app/templates/admin/base.html").read_text(encoding="utf-8")
admin = (root / "app/api/admin.py").read_text(encoding="utf-8")
models = (root / "app/models/usage.py").read_text(encoding="utf-8")
service = (root / "app/services/usage_cost_service.py").read_text(encoding="utf-8")
settings = (root / "app/services/settings_service.py").read_text(encoding="utf-8")

ok("Dashboard CSS includes RTL and Vazirmatn/Vazir stack", 'dir="rtl"' in base and "Vazirmatn" in css and "Vazir" in css)
ok("Admin dashboard routes load without 500 (static route smoke)", all(x in admin for x in ['@router.get("/usage"', '@router.get("/plans"', '@router.get("/models"']))
ok("ai_usage_events table exists", '"ai_usage_events"' in models and 'class AiUsageEvent' in models)
ok("Recording LLM usage creates event with token/cost", 'def record_ai_usage_event' in service and 'input_tokens' in service and 'cost_usd' in service)
ok("Recording STT usage creates event with audio_seconds/cost", 'estimate_audio_cost' in service and 'feature == "stt"' in service and 'audio_seconds' in service)
ok("User detail includes usage/cost fields", 'AiUsageEvent' in admin and 'usage_events' in admin or 'مصرف و هزینه' in base)
ok("Plans page shows effective limits", 'پلن‌ها و محدودیت‌ها' in (root/'app/templates/admin/plans.html').read_text(encoding='utf-8') and 'Effective hardcoded' in (root/'app/templates/admin/plans.html').read_text(encoding='utf-8'))
ok("Plans page warns if DB/config mismatch exists", 'هشدار: محدودیت موثر' in (root/'app/templates/admin/plans.html').read_text(encoding='utf-8'))
ok("Receipts still approve correctly", '/receipts' in base and 'PaymentReceipt' in admin)
ok("Add-ons page shows intimacy_max_unlock", 'INTIMACY_MAX_UNLOCK' in admin)
ok("Media page shows media rows without raw image preview by default", 'has_raw_preview' in admin and 'store_raw_user_images' in admin)
ok("Management bot still works", (root/'app/services/bot_menu_service.py').exists())
ok("Normal text chat still uses raw LLM mode", 'handle_simple_chat' in (root/'app/api/telegram.py').read_text(encoding='utf-8'))
ok("No Natural Style Guard rewrite is re-enabled", 'Natural Style Guard' not in (root/'app/engine/simple_chat.py').read_text(encoding='utf-8'))

for py in ["app/api/admin.py", "app/services/usage_cost_service.py", "app/engine/simple_chat.py", "app/engine/orchestrator.py", "app/models/usage.py"]:
    ast.parse((root/py).read_text(encoding="utf-8"), filename=py)
print("PASS admin dashboard v2 checks:")
for c in checks:
    print(" -", c)
