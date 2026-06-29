import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.style_audit import detect_style_issues

bad='منتظرت بودم و دلم پیش تو بود با ["business_work"] و intent=romantic_note'
issues=detect_style_issues(bad)
types={i.issue_type for i in issues}
assert 'passive_waiting_object' in types and ('json_list_leak' in types or 'snake_case_leak' in types or 'internal_label_leak' in types), types
bad2='من داشتم یه پلی لیست جدید می‌چیدم که ریتمش دقیقاً مثل تپش قلب لحظه‌های آرامشه...'
types2={i.issue_type for i in detect_style_issues(bad2)}
assert 'unrequested_poetic_style' in types2, types2
bad3='خیلی شاعرانه بود اذیت میشم؛ ولی قلبم در سکوت مشترک تو می‌تپه'
types3={i.issue_type for i in detect_style_issues(bad3)}
assert 'ignores_user_style_correction' in types3, types3
print('ok', sorted(types | types2 | types3))
