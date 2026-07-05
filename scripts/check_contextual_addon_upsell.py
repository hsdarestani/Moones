import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401 - register models
from app.models.addon import AddonProduct, UserAddon, AddonUpsellEvent
from app.models.user import User
from app.services.addon_service import INTIMACY_MAX_UNLOCK, seed_default_addon
from app.services.addon_upsell_service import detect_addon_opportunity
from app.engine.simple_chat import raw_llm_final_text

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine, tables=[User.__table__, AddonProduct.__table__, UserAddon.__table__, AddonUpsellEvent.__table__])
Session = sessionmaker(bind=engine)
db = Session()


def make_user(tid: int, age: str = "بالای ۳۰") -> User:
    user = User(telegram_id=tid, display_name=f"u{tid}", onboarding_step="complete", partner_age_range=age)
    db.add(user)
    db.flush()
    return user

product = seed_default_addon(db)
db.commit()
assert product.key == INTIMACY_MAX_UNLOCK, "intimacy_max_unlock exists"
assert product.metadata_json and product.metadata_json.get("upsell_enabled") is True, "upsell metadata exists"
keys = [p.key for p in db.scalars(select(AddonProduct)).all()]
assert keys == [INTIMACY_MAX_UNLOCK], "test database only seeds the current add-on"

user = make_user(1001)
s = detect_addon_opportunity(db, user=user, user_text="چرا هنوز زوده؟ میخوام صمیمی‌تر باشی", assistant_text="بذار بیشتر آشنا شیم")
assert s and s.addon_key == INTIMACY_MAX_UNLOCK, "eligible user gets intimacy suggestion"

owner = make_user(1002)
db.add(UserAddon(user_id=owner.id, addon_key=INTIMACY_MAX_UNLOCK, status="active"))
db.flush()
assert detect_addon_opportunity(db, user=owner, user_text="میخوام صمیمی‌تر باشی") is None, "owner gets no suggestion"

minor = make_user(1003, "زیر ۱۸")
assert detect_addon_opportunity(db, user=minor, user_text="میخوام صمیمی‌تر باشی") is None, "underage gets no adult suggestion"

safe = make_user(1004)
assert detect_addon_opportunity(db, user=safe, user_text="زیر ۱۸ و اجبار و صمیمی‌تر") is None, "hard forbidden phrase gets no upsell"

product.metadata_json = {}
db.flush()
assert detect_addon_opportunity(db, user=make_user(1005), user_text="صمیمی‌تر") is None, "no upsell metadata returns None"

assert raw_llm_final_text("  همین متن خام  ") == "همین متن خام", "normal raw LLM finalization remains unchanged"
telegram_source = Path("app/api/telegram.py").read_text(encoding="utf-8")
assert "چطور بخرم" in telegram_source and "قابلیتاش بیشتر" in telegram_source and "UPGRADE_INTENT_ROUTED_TO_MANAGEMENT_BOT" in telegram_source, "direct upgrade routing remains"
assert "FREE_PHOTO_UPGRADE_MESSAGE" in telegram_source and "FREE_VOICE_UPGRADE_MESSAGE" in telegram_source and "@moonesaibot" in telegram_source, "media upgrade routing still points to management bot"

print("contextual addon upsell checks passed")
