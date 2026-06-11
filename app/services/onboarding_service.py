import json
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User

GENDER_OPTIONS = {"female": "Female", "male": "Male", "neutral": "Neutral"}
AGE_OPTIONS = {"18-20": "18–20", "21-25": "21–25", "26-30": "26–30", "30+": "30+"}
PERSONALITY_OPTIONS = {
    "calm_caring": "Calm & Caring",
    "playful_funny": "Playful & Funny",
    "deep_reflective": "Deep & Reflective",
    "romantic_emotional": "Romantic & Emotional",
}
INTEREST_OPTIONS = {
    "music": "Music",
    "movies": "Movies",
    "travel": "Travel",
    "deep_talks": "Deep Talks",
    "humor": "Humor",
    "life_advice": "Life Advice",
}


@dataclass
class BotReply:
    text: str
    reply_markup: dict | None = None


class OnboardingService:
    def get_or_create_user(self, db: Session, telegram_id: int, display_name: str | None, locale: str | None = None) -> User:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            user.display_name = display_name or user.display_name
            user.locale = locale or user.locale
            return user
        user = User(telegram_id=telegram_id, display_name=display_name, locale=locale)
        db.add(user)
        db.flush()
        return user

    def start(self, user: User) -> BotReply:
        user.onboarding_step = "gender"
        return BotReply("Let’s create your Mones partner 💙", self._keyboard("gender", GENDER_OPTIONS))

    def handle_text(self, user: User, text: str) -> BotReply | None:
        if text == "/start" or user.onboarding_step in ("not_started", ""):
            return self.start(user)
        if user.onboarding_step != "name":
            return None
        name = text.strip()
        if not 2 <= len(name) <= 20:
            return BotReply("اسم پارتنرت باید بین ۲ تا ۲۰ کاراکتر باشه. یه اسم قشنگ برام بفرست 💙")
        user.partner_name = name
        user.onboarding_step = "age"
        return BotReply("سن پارتنرت رو انتخاب کن:", self._keyboard("age", AGE_OPTIONS))

    def handle_callback(self, user: User, data: str) -> BotReply:
        if not data.startswith("onboarding:"):
            return BotReply("یکم گیج شدم؛ لطفاً /start رو بزن تا از اول بسازیم 💙")
        _, action, value = (data.split(":", 2) + [""])[:3]
        if action == "gender" and value in GENDER_OPTIONS:
            user.partner_gender = GENDER_OPTIONS[value]
            user.onboarding_step = "name"
            return BotReply("اسم پارتنرت چی باشه؟ یه اسم ۲ تا ۲۰ کاراکتری بفرست.")
        if action == "age" and value in AGE_OPTIONS:
            user.partner_age_range = AGE_OPTIONS[value]
            user.onboarding_step = "personality"
            return BotReply("شخصیتش بیشتر چه حال‌وهوایی داشته باشه؟", self._keyboard("personality", PERSONALITY_OPTIONS))
        if action == "personality" and value in PERSONALITY_OPTIONS:
            user.partner_personality_type = PERSONALITY_OPTIONS[value]
            user.onboarding_step = "interests"
            return BotReply("علایقش رو انتخاب کن؛ می‌تونی چندتا رو بزنی و بعد تأیید کنی.", self._interests_keyboard(user))
        if action == "interest" and value in INTEREST_OPTIONS:
            selected = self._selected_interests(user)
            if value in selected:
                selected.remove(value)
            else:
                selected.append(value)
            user.partner_interests = json.dumps(selected)
            return BotReply("علایقش رو انتخاب کن؛ می‌تونی چندتا رو بزنی و بعد تأیید کنی.", self._interests_keyboard(user))
        if action == "interests_done":
            selected = self._selected_interests(user)
            if not selected:
                return BotReply("حداقل یک علاقه انتخاب کن تا شخصیتش طبیعی‌تر بشه 💙", self._interests_keyboard(user))
            user.onboarding_step = "complete"
            return BotReply(self.summary(user))
        return BotReply("این گزینه معتبر نیست؛ لطفاً دوباره انتخاب کن 💙")

    def summary(self, user: User) -> str:
        interests = ", ".join(INTEREST_OPTIONS[item] for item in self._selected_interests(user) if item in INTEREST_OPTIONS)
        return (
            "Your Mones partner is ready 💙\n\n"
            f"Name: {user.partner_name}\n"
            f"Gender: {user.partner_gender}\n"
            f"Age: {user.partner_age_range}\n"
            f"Personality: {user.partner_personality_type}\n"
            f"Interests: {interests}\n\n"
            "حالا می‌تونی باهاش حرف بزنی؛ همین‌جا برام بنویس."
        )

    def partner_profile(self, user: User) -> dict[str, object]:
        return {
            "gender": user.partner_gender,
            "name": user.partner_name,
            "age_range": user.partner_age_range,
            "personality_type": user.partner_personality_type,
            "interests": [INTEREST_OPTIONS[item] for item in self._selected_interests(user) if item in INTEREST_OPTIONS],
        }

    def _selected_interests(self, user: User) -> list[str]:
        if not user.partner_interests:
            return []
        try:
            data = json.loads(user.partner_interests)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _keyboard(self, action: str, options: dict[str, str]) -> dict:
        return {"inline_keyboard": [[{"text": label, "callback_data": f"onboarding:{action}:{key}"}] for key, label in options.items()]}

    def _interests_keyboard(self, user: User) -> dict:
        selected = set(self._selected_interests(user))
        rows = []
        for key, label in INTEREST_OPTIONS.items():
            prefix = "✅ " if key in selected else ""
            rows.append([{"text": f"{prefix}{label}", "callback_data": f"onboarding:interest:{key}"}])
        rows.append([{"text": "Done", "callback_data": "onboarding:interests_done:"}])
        return {"inline_keyboard": rows}
