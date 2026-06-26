import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date
from types import SimpleNamespace
from app.services.partner_life_service import PartnerLifeService
u=SimpleNamespace(id=1, partner_name='مونس', partner_personality_type='صمیمی')
data=PartnerLifeService().deterministic_event(u, date(2026,6,26))
assert data['content'] and 'منتظرت' not in data['content'] and data['event_type']
print('ok', data['event_type'])
