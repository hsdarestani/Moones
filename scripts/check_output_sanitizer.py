import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.output_sanitizer import sanitize_output
bad='امروز یه حس کوچیک با همون حال‌وهوای ["business_work"] و intent=memory_callback'
out=sanitize_output(bad,1).text
assert '["business_work"]' not in out and 'business_work' not in out and 'intent' not in out, out
assert 'کار و مسیر حرفه‌ای' in out or 'حرفه‌ای' in out, out
print('ok', out)
