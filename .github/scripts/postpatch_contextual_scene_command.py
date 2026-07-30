from pathlib import Path

path=Path('app/services/image_generation_service.py')
text=path.read_text()
old='''        if not prior.is_image_request or not (scene_key or location):
            continue
'''
new='''        normalized=" ".join(text.replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())
        contextual_image_command=bool(("قبلی" in normalized or "همین" in normalized or "همون" in normalized) and any(term in normalized for term in ("بده", "بدی", "بفرست", "بفرس", "بساز", "درست کن")))
        if not (prior.is_image_request or contextual_image_command) or not (scene_key or location):
            continue
'''
if text.count(old) != 1:
    raise RuntimeError(f'contextual scene condition count={text.count(old)}')
path.write_text(text.replace(old,new,1))
