from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.relationship_engine import ensure_relationship
from app.models.relationship import RelationshipStage
from app.models.user import User
from app.services.onboarding_service import OnboardingService
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService

MAIN_MENU_MARKUP = {
    "keyboard": [
        [{"text": "💬 گفتگو با مونس"}, {"text": "👤 پارتنر من"}],
        [{"text": "💎 اشتراک‌ها"}, {"text": "👛 کیف پول"}],
        [{"text": "➕ افزایش موجودی"}, {"text": "🧠 وضعیت رابطه"}],
        [{"text": "⚙️ تنظیمات"}, {"text": "پشتیبانی"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

STAGE_FA = {
    RelationshipStage.STRANGER.value: "تازه آشنا",
    RelationshipStage.FAMILIAR.value: "آشنا",
    RelationshipStage.FRIEND.value: "دوست نزدیک",
    RelationshipStage.ROMANTIC.value: "رابطه رمانتیک",
    RelationshipStage.PARTNER.value: "پارتنر",
}
PLAN_FA = {"free": "رایگان", "daily": "روزانه", "weekly": "هفتگی", "monthly": "ماهانه", "premium": "پریمیوم"}
STATUS_FA = {"active": "فعال", "expired": "منقضی", "cancelled": "لغوشده"}
TRANSACTION_FA = {"credit": "افزایش", "debit": "مصرف", "adjustment": "اصلاح", "refund": "بازگشت"}


class BotMenuService:
    def __init__(self) -> None:
        self.wallets = WalletService()
        self.subscriptions = SubscriptionService()
        self.onboarding = OnboardingService()

    def main_menu(self) -> dict:
        return MAIN_MENU_MARKUP

    def handle_menu_text(self, db: Session, user: User, text: str) -> tuple[str, dict | None, bool]:
        if text == "💬 گفتگو با مونس":
            return "من آماده‌ام 💙\nهرچی دوست داری برام بنویس.", MAIN_MENU_MARKUP, True
        if text == "👤 پارتنر من":
            return self.partner_profile(user), self.partner_profile_keyboard(), True
        if text == "💎 اشتراک‌ها":
            return self.subscription_plans(), self.subscription_keyboard(), True
        if text == "👛 کیف پول":
            return self.wallet_text(db, user), self.wallet_keyboard(), True
        if text == "➕ افزایش موجودی":
            return self.topup_text(), self.topup_keyboard(), True
        if text == "🧠 وضعیت رابطه":
            return self.relationship_text(user), MAIN_MENU_MARKUP, True
        if text == "⚙️ تنظیمات":
            return self.settings_text(), self.settings_keyboard(), True
        if text == "پشتیبانی":
            return self.support_text(), MAIN_MENU_MARKUP, True
        return "", None, False

    def subscription_plans(self) -> str:
        return """پلن‌های مونس 💎

رایگان:
۳۰ پیام روزانه، حافظه محدود

روزانه:
دسترسی نامحدود معمولی برای امروز
مناسب تست و استفاده کوتاه

هفتگی:
برای وقتی که می‌خوای چند روز مونس کنارته

ماهانه:
رابطه عمیق‌تر، حافظه بهتر، حالت رمانتیک

پریمیوم:
بالاترین کیفیت، حافظه کامل، اولویت پاسخ‌دهی"""

    def subscription_keyboard(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "خرید اشتراک روزانه", "callback_data": "sub_buy_daily"}],
            [{"text": "خرید اشتراک هفتگی", "callback_data": "sub_buy_weekly"}],
            [{"text": "خرید اشتراک ماهانه", "callback_data": "sub_buy_monthly"}],
            [{"text": "خرید پریمیوم", "callback_data": "sub_buy_premium"}],
            [{"text": "وضعیت اشتراک من", "callback_data": "sub_status"}],
        ]}

    def subscription_status_text(self, db: Session, user: User) -> str:
        sub = self.subscriptions.get_active_subscription(db, user) or self.subscriptions.ensure_free_subscription(db, user)
        usage = self.subscriptions.get_or_create_today_usage(db, user)
        expires = sub.expires_at.strftime("%Y-%m-%d %H:%M") if sub.expires_at else "ندارد"
        limit = self.subscriptions.daily_limit(db, user)
        return f"""وضعیت اشتراک من 💎

پلن: {PLAN_FA.get(sub.plan, sub.plan)}
وضعیت: {STATUS_FA.get(sub.status, sub.status)}
تاریخ پایان: {expires}
مصرف امروز: {usage.messages_used} از {limit} پیام"""

    def wallet_text(self, db: Session, user: User) -> str:
        wallet = self.wallets.get_or_create_wallet(db, user)
        return f"""کیف پولت 👛

موجودی فعلی: {wallet.balance_coins} سکه
کل سکه‌های اضافه‌شده: {wallet.total_added_coins}
کل سکه‌های مصرف‌شده: {wallet.total_spent_coins}"""

    def wallet_keyboard(self) -> dict:
        return {"inline_keyboard": [[{"text": "افزایش موجودی", "callback_data": "wallet_topup_menu"}], [{"text": "تاریخچه تراکنش‌ها", "callback_data": "wallet_history"}]]}

    def topup_text(self) -> str:
        if not get_settings().enable_test_wallet_topup:
            return "افزایش موجودی به‌زودی فعال می‌شه."
        return """افزایش موجودی هنوز به درگاه پرداخت وصل نشده.
برای تست، می‌تونی یکی از گزینه‌های زیر رو انتخاب کنی."""

    def topup_keyboard(self) -> dict | None:
        if not get_settings().enable_test_wallet_topup:
            return MAIN_MENU_MARKUP
        return {"inline_keyboard": [
            [{"text": "تست: افزودن ۱۰۰ سکه", "callback_data": "wallet_topup_100"}],
            [{"text": "تست: افزودن ۵۰۰ سکه", "callback_data": "wallet_topup_500"}],
            [{"text": "تست: افزودن ۱۰۰۰ سکه", "callback_data": "wallet_topup_1000"}],
        ]}

    def history_text(self, db: Session, user: User) -> str:
        rows = self.wallets.latest_transactions(db, user, 10)
        if not rows:
            return "هنوز تراکنشی ثبت نشده."
        lines = ["تاریخچه تراکنش‌ها 👛"]
        for tx in rows:
            lines.append(f"{tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type, tx.type)} — {tx.amount_coins} سکه — مانده: {tx.balance_after}")
        return "\n".join(lines)

    def partner_profile(self, user: User) -> str:
        return self.onboarding.partner_profile_text(user)

    def partner_profile_keyboard(self) -> dict:
        return {"inline_keyboard": [[{"text": "ویرایش پارتنر", "callback_data": "partner_edit_prompt"}], [{"text": "شروع گفت‌وگو", "callback_data": "onboard_done"}]]}

    def partner_edit_prompt_keyboard(self) -> dict:
        return {"inline_keyboard": [[{"text": "بله، دوباره بساز", "callback_data": "partner_edit_confirm"}], [{"text": "نه، منصرف شدم", "callback_data": "partner_edit_cancel"}]]}

    def relationship_text(self, user: User) -> str:
        state = ensure_relationship(user.id, user.relationship_state)
        pct = lambda value: round(max(0, min(1, value)) * 100)
        return f"""وضعیت رابطه شما 💙

مرحله رابطه: {STAGE_FA.get(state.stage, state.stage)}
صمیمیت: {pct(state.intimacy)}٪
اعتماد: {pct(state.trust)}٪
وابستگی عاطفی: {pct(state.attachment)}٪
کشش: {pct(state.attraction)}٪

هرچی بیشتر و عمیق‌تر حرف بزنین، رابطه‌تون طبیعی‌تر رشد می‌کنه."""

    def settings_text(self) -> str:
        return """تنظیمات مونس ⚙️

یکی از گزینه‌ها رو انتخاب کن:"""

    def settings_keyboard(self) -> dict:
        return {"inline_keyboard": [
            [{"text": "وضعیت اشتراک", "callback_data": "sub_status"}],
            [{"text": "ویرایش پارتنر", "callback_data": "partner_edit_prompt"}],
            [{"text": "ریست حافظه", "callback_data": "settings_reset_memory"}],
            [{"text": "حذف داده‌های من", "callback_data": "settings_delete_data"}],
        ]}

    def support_text(self) -> str:
        username = get_settings().support_username or "YOUR_SUPPORT_USERNAME"
        if not username.startswith("@"):
            username = f"@{username}"
        return f"""برای پشتیبانی یا گزارش مشکل، به ادمین پیام بده:

{username}"""

    def payment_placeholder(self) -> str:
        return """پرداخت آنلاین هنوز فعال نشده.
فعلاً این بخش برای تست آماده شده و به‌زودی امکان خرید واقعی اضافه می‌شه."""

    def settings_placeholder(self) -> str:
        return "این بخش فعلاً فقط به‌صورت نمایشی آماده شده و بعد از اضافه شدن تأیید امن فعال می‌شه."
