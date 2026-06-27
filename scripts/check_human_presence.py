import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.human_presence_engine import HumanPresencePlan, HumanPresenceEngine
from app.services.partner_autonomy_policy import is_autonomy_question, violates_autonomy_policy, safe_autonomous_fallback
assert is_autonomy_question('چیکارا کردی؟')
bad,_=violates_autonomy_policy('هیچی خاص، فقط منتظرت بودم')
assert bad
assert not violates_autonomy_policy(safe_autonomous_fallback(type('U',(),{'id':1})(), None, 'چخبر'))[0]
assert HumanPresencePlan().delivery_shape == 'single'
assert HumanPresenceEngine().afterthought_text(HumanPresencePlan(), 'x')
assert HumanPresenceEngine().interjection_text(HumanPresencePlan(), 'x')
print('human presence checks passed')
