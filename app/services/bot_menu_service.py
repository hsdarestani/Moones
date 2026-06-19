from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.engine.relationship_engine import ensure_relationship
from app.models.relationship import RelationshipStage
from app.models.user import User
from app.models.payment import PaymentReceipt
from app.services.onboarding_service import OnboardingService
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService
from app.services.settings_service import SettingsService

MAIN_MENU_MARKUP={"keyboard":[[{"text":"👤 پارتنر من"},{"text":"💬 رفتن به چت"}],[{"text":"💎 اشتراک‌ها"},{"text":"👛 کیف پول"}],[{"text":"➕ افزایش موجودی"},{"text":"🧠 وضعیت رابطه"}],[{"text":"⚙️ تنظیمات"},{"text":"پشتیبانی"}]],"resize_keyboard":True,"is_persistent":True}
STAGE_FA={s.value:s.value for s in RelationshipStage}; STAGE_FA.update({"STRANGER":"تازه آشنا","WARM":"گرم و آشنا","CLOSE":"نزدیک","PARTNER":"پارتنر","LOVER":"عاشقانه"})
PLAN_FA={"free":"رایگان","mini":"مینی","basic":"بیسیک","plus":"پلاس","vip":"VIP","daily":"روزانه","weekly":"هفتگی","monthly":"ماهانه","premium":"VIP"}; STATUS_FA={"active":"فعال","expired":"منقضی","cancelled":"لغوشده"}; TRANSACTION_FA={"credit":"افزایش","debit":"مصرف","adjustment":"اصلاح","refund":"بازگشت"}; RECEIPT_FA={"pending":"در انتظار بررسی","approved":"تایید شده","rejected":"رد شده"}
class BotMenuService:
 def __init__(self): self.wallets=WalletService(); self.subscriptions=SubscriptionService(); self.onboarding=OnboardingService(); self.settings=SettingsService()
 def main_menu(self): return MAIN_MENU_MARKUP
 def handle_menu_text(self, db:Session,user:User,text:str):
  if text in {"💬 رفتن به چت","💬 گفتگو با مونس"}: return self.chat_redirect_text(), self.chat_redirect_keyboard(), True
  if text=="👤 پارتنر من": return self.partner_profile(user), self.partner_profile_keyboard(), True
  if text=="💎 اشتراک‌ها": return self.subscription_plans(db,user), self.subscription_keyboard(), True
  if text=="👛 کیف پول": return self.wallet_text(db,user), self.wallet_keyboard(), True
  if text=="➕ افزایش موجودی": return self.topup_text(db), self.topup_keyboard(), True
  if text=="🧠 وضعیت رابطه": return self.relationship_text(user), MAIN_MENU_MARKUP, True
  if text=="⚙️ تنظیمات": return self.settings_text(), self.settings_keyboard(), True
  if text=="پشتیبانی": user.admin_state="awaiting_support_message"; return self.support_text(db), MAIN_MENU_MARKUP, True
  return "",None,False
 def chat_redirect_text(self):
  u=get_settings().telegram_chat_bot_username or "mooneschatbot"; u=u if u.startswith('@') else '@'+u
  return f"برای صحبت با پارتنرت، وارد ربات چت مونس شو 💙\n\n{u}"
 def chat_redirect_keyboard(self):
  u=(get_settings().telegram_chat_bot_username or "mooneschatbot").lstrip('@')
  return {"inline_keyboard":[[{"text":"ورود به چت","url":f"https://t.me/{u}"}]]}
 def _toman(self, value):
  return f"{int(value or 0):,}"
 def subscription_plans(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user)
  return f"""اشتراک‌های مونس 💎

مونس رو با سطحی انتخاب کن که به سبک حرف زدنت میاد؛ از چت روزمره تا همراهی عمیق‌تر، ویس، استیکر و ظرفیت بیشتر برای گفتگو.

🟢 رایگان
برای شروع و آشنا شدن با مونس
• ظرفیت روزانه محدود
• بدون ویس
• ۳ استیکر در روز

🌱 مینی — ۵۹۰,۰۰۰ تومان / ماه
برای استفاده سبک ولی جدی‌تر
• ظرفیت روزانه بیشتر از رایگان
• ۱ ویس در روز
• ۸ استیکر در روز

💙 بیسیک — ۹۹۰,۰۰۰ تومان / ماه
برای چت روزانه و همراهی منظم
• ظرفیت گفتگوی بیشتر
• ۲ ویس در روز
• ۱۵ استیکر در روز

💜 پلاس — ۲,۲۹۰,۰۰۰ تومان / ماه
برای رابطه گرم‌تر، فعال‌تر و طبیعی‌تر
• ظرفیت گفتگوی زیاد
• ۸ ویس در روز
• ۳۰ استیکر در روز

👑 VIP — ۴,۹۰۰,۰۰۰ تومان / ماه
بالاترین سطح همراهی مونس
• بیشترین ظرفیت گفتگو
• ۲۰ ویس در روز
• ۶۰ استیکر در روز
• مناسب استفاده سنگین و تجربه کامل‌تر

اعتبار کیف پولت: {self._toman(w.balance_coins)} تومان"""
 def subscription_keyboard(self): return {"inline_keyboard":[[{"text":"خرید مینی","callback_data":"sub_activate_mini"}],[{"text":"خرید بیسیک","callback_data":"sub_activate_basic"}],[{"text":"خرید پلاس","callback_data":"sub_activate_plus"}],[{"text":"خرید VIP","callback_data":"sub_activate_vip"}],[{"text":"وضعیت اشتراک من","callback_data":"sub_status"}],[{"text":"افزایش موجودی","callback_data":"sub_go_topup"}]]}
 def subscription_status_text(self,db,user):
  sub=self.subscriptions.get_active_subscription(db,user) or self.subscriptions.ensure_free_subscription(db,user); usage=self.subscriptions.get_or_create_today_usage(db,user); cfg=self.subscriptions.plan_config(db,user); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else 'ندارد'; rem='—'
  if sub.expires_at:
   delta=sub.expires_at-datetime.utcnow(); rem=f"{max(0,delta.days)} روز و {max(0,delta.seconds//3600)} ساعت"
  used=self.subscriptions.total_tokens_used(usage); percent=min(100, int((used/max(1,cfg.daily_token_limit))*100)); bar='█'*(percent//10)+'░'*(10-percent//10)
  return f"""وضعیت اشتراک من 💎

پلن: {PLAN_FA.get(sub.plan,sub.plan)}
وضعیت: {STATUS_FA.get(sub.status,sub.status)}
تاریخ پایان: {exp}
زمان باقی‌مانده: {rem}
ظرفیت روزانه: {bar} {percent}%
ویس امروز: {usage.daily_voice_sent} از {cfg.daily_voice_limit}
استیکر امروز: {usage.daily_stickers_sent} از {cfg.daily_sticker_limit}"""
 def activate_subscription(self,db,user,plan):
  price=self.settings.get_int(db,f"subscription.{plan}.price_coins",0); wallet=self.wallets.get_or_create_wallet(db,user)
  if wallet.balance_coins < price: return f"موجودی کیف پولت کافی نیست 😅\nبرای فعال‌سازی این اشتراک، باید {self._toman(price)} تومان داشته باشی.\n\nموجودی فعلی: {self._toman(wallet.balance_coins)} تومان", {"inline_keyboard":[[{"text":"افزایش موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
  wallet=self.wallets.debit(db,user,price,reason="subscription_activation",metadata={"plan":plan,"price_coins":price}); sub=self.subscriptions.activate_plan(db,user,plan); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else '—'
  return f"اشتراک {PLAN_FA.get(plan,plan)} فعال شد ✅\nتا تاریخ {exp} می‌تونی از مونس استفاده کنی.\n\nموجودی جدید کیف پول: {self._toman(wallet.balance_coins)} تومان", None
 def wallet_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); return f"""کیف پولت 👛

موجودی فعلی: {self._toman(w.balance_coins)} تومان

برای افزایش موجودی، از گزینه «افزایش موجودی» استفاده کن.
بعد از پرداخت و تایید ادمین، تومان‌ها به کیف پولت اضافه می‌شن."""
 def wallet_keyboard(self): return {"inline_keyboard":[[{"text":"➕ افزایش موجودی","callback_data":"wallet_topup_menu"}],[{"text":"تاریخچه تراکنش‌ها","callback_data":"wallet_history"}],[{"text":"رسیدهای پرداخت من","callback_data":"wallet_receipts"}]]}
 def topup_text(self,db):
  link=self.settings.get_str(db,"payment.link",get_settings().payment_link)
  return f"""برای افزایش موجودی، مبلغ مدنظرت رو از طریق لینک زیر واریز کن:

{link}

❇️ در صفحه باز شده، پایین صفحه مبلغ رو به تومان وارد کن.
بعد پرداخت رو بزن و بعد از اتمام پرداخت، اسکرین‌شات رسید رو همینجا ارسال کن.

====================

‼️ اسم حمایت‌کننده رو شبیه آیدی تلگرام خودت بذار تا رسیدت زودتر تایید بشه.

‼️ امکان برداشت وجه از کیف پول وجود نداره.
‼️ مسئولیت واریز اشتباه با خود کاربره.

بعد از پرداخت، دکمه «پرداخت کردم» رو بزن و تصویر رسید رو ارسال کن.

بعد از تایید ادمین، کیف پولت شارژ می‌شه 💵"""
 def topup_keyboard(self): return {"inline_keyboard":[[{"text":"پرداخت کردم","callback_data":"payment_i_paid"}]]}
 def history_text(self,db,user):
  rows=self.wallets.latest_transactions(db,user,10); return "هنوز تراکنشی ثبت نشده." if not rows else "\n".join(["تاریخچه تراکنش‌ها 👛"]+[f"{tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type,tx.type)} — {self._toman(tx.amount_coins)} تومان — مانده: {self._toman(tx.balance_after)} تومان" for tx in rows])
 def receipts_text(self,db,user):
  from sqlalchemy import select
  rows=db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id==user.id).order_by(PaymentReceipt.created_at.desc()).limit(10)).all()
  return "رسیدی ثبت نشده." if not rows else "\n".join(["رسیدهای پرداخت من 💵"]+[f"#{r.id} — {RECEIPT_FA.get(r.status,r.status)} — {r.created_at:%Y-%m-%d %H:%M}" for r in rows])
 def partner_profile(self,user): return self.onboarding.partner_profile_text(user)
 def partner_profile_keyboard(self): return {"inline_keyboard":[[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}],[{"text":"رفتن به چت","callback_data":"go_chat"}]]}
 def partner_edit_prompt_keyboard(self): return {"inline_keyboard":[[{"text":"بله، دوباره بساز","callback_data":"partner_edit_confirm"}],[{"text":"نه، منصرف شدم","callback_data":"partner_edit_cancel"}]]}
 def relationship_text(self,user):
  st=ensure_relationship(user.id,user.relationship_state)
  def pct(v):
   try: v = 0 if v is None else float(v)
   except Exception: v = 0
   return round(max(0, min(1, v)) * 100)
  stage = st.stage or "STRANGER"
  return f"وضعیت رابطه شما با مونس 🧠\n\nمرحله رابطه: {STAGE_FA.get(stage,stage)}\nصمیمیت: {pct(st.intimacy)}٪\nاعتماد: {pct(st.trust)}٪\nوابستگی عاطفی: {pct(st.attachment)}٪\nکشش: {pct(st.attraction)}٪"
 def settings_text(self): return "تنظیمات مونس ⚙️\n\nپیام‌های خودجوش مونس یعنی گاهی خودش سراغت بیاد و منتظر پیام تو نمونه."
 def settings_keyboard(self): return {"inline_keyboard":[[{"text":"پیام‌های خودجوش: روشن باشه","callback_data":"proactive_on"}],[{"text":"پیام‌های خودجوش: خاموش باشه","callback_data":"proactive_off"}],[{"text":"وضعیت اشتراک","callback_data":"sub_status"}],[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}]]}
 def support_text(self,db):
  return "پیامت رو همین‌جا بنویس و بفرست 💬\nتیم پشتیبانی مونس مستقیم می‌خونتش و جوابش همین‌جا برات میاد."
 def settings_placeholder(self): return "این بخش فعلاً فقط به‌صورت نمایشی آماده شده و بعد از اضافه شدن تأیید امن فعال می‌شه."
