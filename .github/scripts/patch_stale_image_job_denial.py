from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/services/image_generation_service.py",
    "class ImageGenerationDenied(Exception): pass\n",
    "class ImageGenerationDenied(Exception): pass\n\n\n# Terminal failures must never block later image requests.\nACTIVE_ENQUEUE_JOB_STATUSES = ('queued', 'processing', 'generating', 'sending')\n",
)

replace_once(
    "app/services/image_generation_service.py",
    "ImageGenerationJob.status.in_(['queued','processing','generating','sending','delivery_failed'])",
    "ImageGenerationJob.status.in_(ACTIVE_ENQUEUE_JOB_STATUSES)",
)

replace_once(
    "app/api/telegram.py",
    '        "anatomy_profile_missing": "پروفایل بدنی پارتنرت برای تصویر کاملاً برهنه هنوز کامل نشده؛ از ربات مدیریت مشخصات پارتنر رو بررسی کن و دوباره امتحان کن.",\n',
    '        "anatomy_profile_missing": "پروفایل بدنی پارتنرت برای تصویر کاملاً برهنه هنوز کامل نشده؛ از ربات مدیریت مشخصات پارتنر رو بررسی کن و دوباره امتحان کن.",\n        "active_image_job_exists": "عکس قبلی هنوز در حال آماده‌شدنه؛ چند لحظه صبر کن تا همون درخواست تموم بشه.",\n        "adult_partner_age_not_eligible": "سن پارتنر داستانی برای تصویر بزرگسال باید حداقل ۱۸ سال باشه.",\n',
)

replace_once(
    "app/api/telegram.py",
    "    except ImageGenerationDenied as exc:\n        reason = str(exc)\n",
    "    except ImageGenerationDenied as exc:\n        reason = str(exc)\n        logger.warning(\"IMAGE_REQUEST_DENIED user_id=%s chat_id=%s reason=%s\", user.id, chat_id, reason[:300])\n",
)

replace_once(
    "app/api/telegram.py",
    "        else:\n            await _send_user_text(telegram_service, chat_id, \"این بار نتونستم عکس رو درست آماده کنم؛ همون چیزی که می‌خوای رو دوباره بگو تا از نو بگیرمش.\", user_id=user.id, surface=\"chat\", user_text=user_text)\n",
    "        elif reason.startswith(('plan_invariant_failed:', 'prompt_invariant_failed:')):\n            await _send_user_text(telegram_service, chat_id, \"درخواست قبل از ساخت تصویر به‌خاطر یک ناسازگاری داخلی متوقف شد و سکه‌ای کم نشد؛ گزارشش ثبت شد.\", user_id=user.id, surface=\"chat\", user_text=user_text)\n        else:\n            await _send_user_text(telegram_service, chat_id, \"این بار درخواست عکس قبل از ساخت متوقف شد و سکه‌ای کم نشد؛ گزارش فنی‌اش ثبت شد.\", user_id=user.id, surface=\"chat\", user_text=user_text)\n",
)

p = Path("tests/test_nude_anatomy_profile_migration.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    "    construct_resolved_plan,\n",
    "    compile_image_prompt,\n    construct_resolved_plan,\n",
    1,
)
text = text.replace(
    "    normalize_request_v2,\n    parse_image_intent,\n",
    "    normalize_request_v2,\n    parse_image_intent,\n    validate_compiled_prompt,\n    validate_plan_invariants,\n",
    1,
)
text = text.replace(
    "    assert plan.visual_requirements.anatomy_qa_required is True\n",
    "    assert plan.visual_requirements.anatomy_qa_required is True\n    assert validate_plan_invariants(plan, source_job=None, user_id=1, chat_id=1) == []\n    compiled = compile_image_prompt(plan)\n    assert validate_compiled_prompt(plan, compiled) == []\n",
    1,
)
text += '''\n\ndef test_terminal_delivery_failure_is_not_an_active_enqueue_status():\n    from app.services.image_generation_service import ACTIVE_ENQUEUE_JOB_STATUSES\n\n    assert "delivery_failed" not in ACTIVE_ENQUEUE_JOB_STATUSES\n    assert "failed" not in ACTIVE_ENQUEUE_JOB_STATUSES\n    assert set(ACTIVE_ENQUEUE_JOB_STATUSES) == {"queued", "processing", "generating", "sending"}\n'''
p.write_text(text, encoding="utf-8")
