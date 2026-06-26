import json
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService

GENDER_OPTIONS = {"female": "دختر", "male": "پسر", "neutral": "بدون جنسیت مشخص"}
AGE_OPTIONS = {"18-20": "۱۸ تا ۲۰", "21-25": "۲۱ تا ۲۵", "26-30": "۲۶ تا ۳۰", "30+": "بالای ۳۰"}
PERSONALITY_OPTIONS = {
    "calm_caring": "آروم و مهربون",
    "playful_funny": "شوخ و بازیگوش",
    "deep_reflective": "عمیق و اهل فکر",
    "romantic_emotional": "رمانتیک و احساسی",
}
INTEREST_OPTIONS = {
    "music": "موسیقی",
    "movies_series": "فیلم و سریال",
    "books": "کتاب و مطالعه",
    "travel_nature": "سفر و طبیعت‌گردی",
    "fitness": "ورزش و تناسب اندام",
    "gaming": "بازی و گیم",
    "tech_ai": "تکنولوژی و هوش مصنوعی",
    "art_design": "هنر و طراحی",
    "poetry_literature": "شعر و ادبیات",
    "cooking_food": "آشپزی و غذا",
    "fashion_style": "مد و استایل",
    "deep_talks": "حرف‌های عمیق",
    "humor": "شوخی و خنده",
    "life_advice": "مشاوره زندگی",
    "relationships_emotions": "رابطه و احساسات",
    "self_growth": "موفقیت و رشد فردی",
    "psychology": "روانشناسی",
    "late_night_talks": "شب‌نشینی و حرف‌های طولانی",
    "business" + "_" + "work": "کار و بیزینس",
    "spirituality_calm": "معنویت و آرامش",
}

START_TEXT = """به مونس خوش اومدی 🌙

اینجا می‌تونی همراه هوشمند خودت رو بسازی؛ کسی که باهات حرف می‌زنه، کم‌کم می‌شناستت، خاطره می‌سازه، حال‌وهوای رابطه‌تون تغییر می‌کنه و حتی می‌تونه با وویس و استیکر واکنش نشون بده.

شروعش رایگانه؛ پارتنرت رو بساز، چند دقیقه باهاش حرف بزن، بعد اگه خواستی تجربه کامل‌تر رو فعال کن."""


@dataclass
class BotReply:
    text: str
    reply_markup: dict | None = None


class OnboardingService:
    def __init__(self) -> None:
        self.wallets = WalletService()
        self.subscriptions = SubscriptionService()

    def get_or_create_user(self, db: Session, telegram_id: int, display_name: str | None, locale: str | None = None) -> User:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            user.display_name = display_name or user.display_name
            user.locale = locale or user.locale
            if user.onboarding_complete:
                self.wallets.get_or_create_wallet(db, user)
                self.subscriptions.ensure_free_subscription(db, user)
            return user
        user = User(telegram_id=telegram_id, display_name=display_name, locale=locale)
        db.add(user)
        db.flush()
        self.wallets.get_or_create_wallet(db, user)
        self.subscriptions.ensure_free_subscription(db, user)
        return user

    def intro(self) -> BotReply:
        return BotReply(START_TEXT, {"inline_keyboard": [[{"text": "شروع رایگان", "callback_data": "onboard_start"}], [{"text": "مونس چیه؟", "callback_data": "about_moones"}]]})

    def start(self, user: User) -> BotReply:
        user.onboarding_step = "gender"
        return BotReply("دوست داری پارتنرت چه جنسیتی داشته باشه؟", self._keyboard("gender", GENDER_OPTIONS))

    def handle_text(self, user: User, text: str) -> BotReply | None:
        if text == "/start":
            return None if user.onboarding_complete else self.intro()
        if user.onboarding_step in ("not_started", "", "start"):
            return None
        if user.onboarding_step != "name":
            return None
        name = text.strip()
        if not 2 <= len(name) <= 20:
            return BotReply("اسم باید بین ۲ تا ۲۰ کاراکتر باشه.\nیه اسم دیگه برام بفرست.")
        user.partner_name = name
        user.onboarding_step = "age"
        return BotReply("سن پارتنرت رو انتخاب کن:", self._keyboard("age", AGE_OPTIONS))

    def handle_callback(self, user: User, data: str) -> BotReply:
        if data == "onboard_start":
            return self.start(user)
        if data == "onboard_done":
            return BotReply("گفت‌وگو رو شروع کن؛ هرچی توی دلت هست برام بنویس 💙")
        if data.startswith("onboarding:"):
            data = self._legacy_callback_to_new(data)
        action, value = self._parse_callback(data)
        if action == "gender" and value in GENDER_OPTIONS:
            user.partner_gender = value
            user.onboarding_step = "name"
            return BotReply("اسم پارتنرت چی باشه؟\nیه اسم کوتاه و قشنگ بنویس.")
        if action == "age" and value in AGE_OPTIONS:
            user.partner_age_range = value
            user.onboarding_step = "personality"
            return BotReply("دوست داری شخصیتش بیشتر چه مدلی باشه؟", self._keyboard("personality", PERSONALITY_OPTIONS))
        if action == "personality" and value in PERSONALITY_OPTIONS:
            user.partner_personality_type = value
            user.onboarding_step = "interests"
            return BotReply("علایق مشترکتون رو انتخاب کن.\nمی‌تونی چندتا گزینه بزنی، آخرش «تمام شد» رو بزن.", self._interests_keyboard(user))
        if data == "onboard_clear_interests":
            user.partner_interests = "[]"
            return BotReply("انتخاب‌ها پاک شد. حالا دوباره هر کدوم رو دوست داری بزن.", self._interests_keyboard(user))
        if action == "interest" and value in INTEREST_OPTIONS:
            selected = self._selected_interests(user)
            if value in selected:
                selected.remove(value)
            else:
                selected.append(value)
            user.partner_interests = json.dumps(selected, ensure_ascii=False)
            return BotReply("علایق مشترکتون رو انتخاب کن.\nمی‌تونی چندتا گزینه بزنی، آخرش «تمام شد» رو بزن.", self._interests_keyboard(user))
        if action == "back":
            return BotReply("علایق مشترکتون رو انتخاب کن.\nمی‌تونی چندتا گزینه بزنی، آخرش «تمام شد» رو بزن.", self._interests_keyboard(user))
        if action == "skip":
            user.onboarding_step = "complete"
            return BotReply(self.summary(user), {"inline_keyboard": [[{"text": "شروع گفتگو", "callback_data": "onboard_done"}]]})
        if action == "done":
            selected = self._selected_interests(user)
            if not selected:
                return BotReply("بدون علاقه مشترک هم می‌تونیم ادامه بدیم، ولی اگه چندتا انتخاب کنی پارتنرت طبیعی‌تر می‌شه.", {"inline_keyboard": [[{"text": "ادامه بدون علاقه مشترک", "callback_data": "onboard_skip_interests"}], [{"text": "برگشت به انتخاب علایق", "callback_data": "onboard_back_interests"}]]})
            user.onboarding_step = "complete"
            return BotReply(self.summary(user), {"inline_keyboard": [[{"text": "شروع گفتگو", "callback_data": "onboard_done"}]]})
        return BotReply("این گزینه معتبر نیست؛ لطفاً دوباره انتخاب کن 💙")

    def summary(self, user: User) -> str:
        interests = self.format_interests(user) or "—"
        return (
            "پارتنرت آماده شد 💙\n\n"
            f"نام: {user.partner_name}\n"
            f"جنسیت: {self.format_gender(user.partner_gender)}\n"
            f"سن: {self.format_age(user.partner_age_range)}\n"
            f"شخصیت: {self.format_personality(user.partner_personality_type)}\n"
            f"علایق: {interests}\n\n"
            "از الان می‌تونی باهاش حرف بزنی.\n"
            "فقط یادت باشه رابطه‌تون کم‌کم عمیق‌تر می‌شه."
        )

    def partner_profile_text(self, user: User) -> str:
        return (
            "پارتنر تو 💙\n\n"
            f"نام: {user.partner_name or '—'}\n"
            f"جنسیت: {self.format_gender(user.partner_gender)}\n"
            f"سن: {self.format_age(user.partner_age_range)}\n"
            f"شخصیت: {self.format_personality(user.partner_personality_type)}\n"
            f"علایق: {self.format_interests(user) or '—'}"
        )

    def partner_profile(self, user: User) -> dict[str, object]:
        return {
            "gender": self.format_gender(user.partner_gender),
            "name": user.partner_name,
            "age_range": self.format_age(user.partner_age_range),
            "personality_type": self.format_personality(user.partner_personality_type),
            "interests": [INTEREST_OPTIONS[item] for item in self._selected_interests(user) if item in INTEREST_OPTIONS],
        }

    def reset_for_edit(self, user: User) -> BotReply:
        user.onboarding_step = "start"
        return self.intro()

    def format_gender(self, value: str | None) -> str:
        return GENDER_OPTIONS.get(value or "", value or "—")

    def format_age(self, value: str | None) -> str:
        return AGE_OPTIONS.get(value or "", value or "—")

    def format_personality(self, value: str | None) -> str:
        return PERSONALITY_OPTIONS.get(value or "", value or "—")

    def format_interests(self, user: User) -> str:
        return "، ".join(INTEREST_OPTIONS[item] for item in self._selected_interests(user) if item in INTEREST_OPTIONS)

    def _selected_interests(self, user: User) -> list[str]:
        if not user.partner_interests:
            return []
        try:
            data = json.loads(user.partner_interests)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _keyboard(self, action: str, options: dict[str, str]) -> dict:
        return {"inline_keyboard": [[{"text": label, "callback_data": f"onboard_{action}:{key}"}] for key, label in options.items()]}

    def _interests_keyboard(self, user: User) -> dict:
        selected = set(self._selected_interests(user))
        rows = []
        for key, label in INTEREST_OPTIONS.items():
            prefix = "✅ " if key in selected else ""
            rows.append([{"text": f"{prefix}{label}", "callback_data": f"onboard_interest:{key}"}])
        rows.append([{"text": "تمام شد", "callback_data": "onboard_done_interests"}])
        rows.append([{"text": "پاک کردن انتخاب‌ها", "callback_data": "onboard_clear_interests"}])
        return {"inline_keyboard": rows}

    def _parse_callback(self, data: str) -> tuple[str, str]:
        if data.startswith("onboard_gender:"):
            return "gender", data.split(":", 1)[1]
        if data.startswith("onboard_age:"):
            return "age", data.split(":", 1)[1]
        if data.startswith("onboard_personality:"):
            return "personality", data.split(":", 1)[1]
        if data.startswith("onboard_interest:"):
            return "interest", data.split(":", 1)[1]
        if data == "onboard_done_interests":
            return "done", ""
        if data == "onboard_skip_interests":
            return "skip", ""
        if data == "onboard_back_interests":
            return "back", ""
        return "", ""

    def _legacy_callback_to_new(self, data: str) -> str:
        _, action, value = (data.split(":", 2) + [""])[:3]
        if action == "interests_done":
            return "onboard_done_interests"
        return f"onboard_{action}:{value}"
