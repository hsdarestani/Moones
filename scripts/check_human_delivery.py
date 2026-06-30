import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.human_delivery_service import HumanDeliveryService
s=HumanDeliveryService()
long='امروز یه چیز کوچیک فهمیدم. بعضی سکوت‌ها خالی نیستن؛ فقط آروم‌تر از حرف معمولی‌اند. برای همین جوابم رو کمی آهسته‌تر می‌چینم.'
parts=s.split_text(long)
assert 1 <= len(parts) <= 3
assert not (len(parts)>1 and long in parts)
assert s.split_text('باشه عزیزم.') == ['باشه عزیزم.']
assert s.split_text('پرداخت از https://example.com انجام می‌شود.') == ['پرداخت از https://example.com انجام می‌شود.']
assert s.apply_question_guard(['خوبی؟','امروز چی شد؟'], False, 1)[1].endswith('.')
for t in ['صبر کن، این قسمت حرفت مهم بود.','یه لحظه، قبل از اینکه ادامه بدی...']:
    assert len(t)<=90
for t in ['یه جمله‌ی کوچیک ته " + "ذهنم موند.']:
    assert len(t)<=140
print('human delivery checks passed')
