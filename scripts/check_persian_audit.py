import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.style_audit import detect_style_issues
bad='منتظرت بودم و دلم پیش تو بود با ["business_work"] و intent=romantic_note'
issues=detect_style_issues(bad)
types={i.issue_type for i in issues}
assert 'needy_waiting' in types and ('json_list_leak' in types or 'snake_case_leak' in types), types
print('ok', sorted(types))
