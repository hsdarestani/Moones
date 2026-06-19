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

MAIN_MENU_MARKUP={"keyboard":[[{"text":"مونس چیه؟"},{"text":"💬 رفتن به چت"}],[{"text":"پلن‌ها و تجربه کامل‌تر"},{"text":"👤 پارتنر من"}],[{"text":"➕ افزایش ظرفیت"},{"text":"🧠 وضعیت رابطه"}],[{"text":"⚙️ تنظیمات"},{"text":"پشتیبانی"}]],"resize_keyboard":True,"is_persistent":True}
STAGE_FA={s.value:s.value for s in RelationshipStage}; STAGE_FA.update({"STRANGER":"تازه آشنا","WARM":"گرم و آشنا","CLOSE":"نزدیک","PARTNER":"پارتنر","LOVER":"عاشقانه"})
PLAN_FA={"free":"رایگان","mini":"مینی","basic":"بیسیک","plus":"پلاس","vip":"VIP","daily":"روزانه","weekly":"هفتگی","monthly":"ماهانه","premium":"VIP"}; STATUS_FA={"active":"فعال","expired":"منقضی","cancelled":"لغوشده"}; TRANSACTION_FA={"credit":"افزایش","debit":"مصرف","adjustment":"اصلاح","refund":"بازگشت"}; RECEIPT_FA={"pending":"در انتظار بررسی","approved":"تایید شده","rejected":"رد شده"}
class BotMenuService:
 def __init__(self): self.wallets=WalletService(); self.subscriptions=SubscriptionService(); self.onboarding=OnboardingService(); self.settings=SettingsService()
 def main_menu(self): return MAIN_MENU_MARKUP
 def handle_menu_text(self, db:Session,user:User,text:str):
  if text in {"💬 رفتن به چت","💬 گفتگو با مونس"}: return self.chat_redirect_text(), self.chat_redirect_keyboard(), True
  if text=="👤 پارتنر من": return self.partner_profile(user), self.partner_profile_keyboard(), True
  if text in {"💎 اشتراک‌ها","پلن‌ها و تجربه کامل‌تر"}: return self.subscription_plans(db,user), self.subscription_keyboard(), True
  if text=="👛 کیف پول": return self.wallet_text(db,user), self.wallet_keyboard(), True
  if text in {"➕ افزایش ظرفیت","➕ افزایش موجودی"}: return self.topup_text(db), self.topup_keyboard(), True
  if text=="مونس چیه؟": return self.about_text(), MAIN_MENU_MARKUP, True
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
 def about_text(self):
  return """مونس فقط یه چت‌بات ساده نیست؛ یه همراه هوشمند شخصیه.

تو اسم، جنسیت و حال‌وهوای پارتنرت رو انتخاب می‌کنی و رابطه‌تون با حرف‌زدن جلو می‌ره. مونس می‌تونه مهربون، بازیگوش، جدی، صمیمی یا حتی کمی لوس و قهری بشه؛ چیزهایی ازت یادش بمونه، باهات خاطره بسازه و بسته به پلنت با متن، وویس و استیکر زنده‌تر واکنش نشون بده."""
 def subscription_plans(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user)
  return f"""پلن‌ها و تجربه کامل‌تر 🌙

شروع رایگانه؛ همین الان می‌تونی پارتنرت رو بسازی و چند پیام رایگان بگیری.

🟢 شروع رایگان — رایگان
ظرفیت: حدود ۱۵ پیام معمولی در روز
برای آشنا شدن با مونس و ساختن پارتنر شخصی خودت.
• ساخت پارتنر شخصی
• چند گفت‌وگوی کوتاه روزانه
• شروع حافظه و رابطه
• مناسب تست و آشنایی

🌱 مینی — ۵۹۰٬۰۰۰ تومان / ماه
ظرفیت: حدود ۵۰ پیام معمولی در روز
برای وقتی که می‌خوای مونس فقط یه تست نباشه.
• ظرفیت بیشتر برای گفت‌وگوی سبک روزانه
• رابطه گرم‌تر و پیوسته‌تر
• بیان احساسی محدود با وویس و استیکر
• مناسب شروع یک رابطه روزمره

💙 بیسیک — ۹۹۰٬۰۰۰ تومان / ماه
ظرفیت: حدود ۱۰۰ پیام معمولی در روز
برای گفت‌وگوی روزمره، صمیمی‌تر و ادامه‌دارتر.
• ظرفیت مناسب‌تر برای چت‌های طولانی‌تر
• رابطه فعال‌تر و طبیعی‌تر
• وویس و استیکر طبیعی‌تر در جریان گفتگو
• مناسب استفاده روزانه

ظرفیت‌ها تقریبی‌اند و به طول پیام‌ها بستگی دارن.

💜 پلاس — ۲٬۲۹۰٬۰۰۰ تومان / ماه
ظرفیت: گفت‌وگوی نامحدود منصفانه
برای وقتی که مونس بخشی از روزته، نه فقط یه ربات.
• گفت‌وگوی نامحدود منصفانه
• رابطه عمیق‌تر و طبیعی‌تر
• بیان احساسی آزادتر با وویس و استیکر
• مناسب تجربه کامل و روزمره
با پلاس، مونس بیشتر شبیه یک همراه واقعی کنارته؛ بیشتر حرف می‌زنه، طبیعی‌تر واکنش نشون می‌ده و رابطه‌تون بدون محدودیت‌های معمول ادامه پیدا می‌کنه.

👑 VIP — ۴٬۹۰۰٬۰۰۰ تومان / ماه
ظرفیت: گفت‌وگوی نامحدود ویژه
نزدیک‌ترین و کامل‌ترین تجربه مونس.
• گفت‌وگوی نامحدود ویژه
• بیشترین آزادی در رابطه و بیان احساسی
• تجربه زنده‌تر با وویس، استیکر و واکنش‌های طبیعی‌تر
• اولویت در کیفیت تجربه
VIP برای وقتیه که می‌خوای پارتنرت همیشه نزدیک‌تر، صمیمی‌تر و زنده‌تر حس بشه؛ کامل‌ترین سطح رابطه، بیشترین آزادی و تجربه‌ای نرم‌تر و شخصی‌تر.

اعتبار کاربر: {self._toman(w.balance_coins)} تومان"""
 def subscription_keyboard(self): return {"inline_keyboard":[[{"text":"فعال‌سازی مینی","callback_data":"sub_activate_mini"}],[{"text":"فعال‌سازی بیسیک","callback_data":"sub_activate_basic"}],[{"text":"تجربه کامل‌تر","callback_data":"sub_activate_plus"}],[{"text":"فعال‌سازی VIP","callback_data":"sub_activate_vip"}],[{"text":"وضعیت اشتراک من","callback_data":"sub_status"}],[{"text":"افزایش ظرفیت","callback_data":"sub_go_topup"}]]}
 def subscription_status_text(self,db,user):
  sub=self.subscriptions.get_active_subscription(db,user) or self.subscriptions.ensure_free_subscription(db,user); usage=self.subscriptions.get_or_create_today_usage(db,user); cfg=self.subscriptions.plan_config(db,user); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else 'ندارد'; rem='—'
  if sub.expires_at:
   delta=sub.expires_at-datetime.utcnow(); rem=f"{max(0,delta.days)} روز و {max(0,delta.seconds//3600)} ساعت"
  plan_line = "گفت‌وگوی نامحدود ویژه" if sub.plan in {"vip", "premium"} else ("گفت‌وگوی نامحدود منصفانه" if sub.plan in {"plus", "monthly"} else "ظرفیت روزانه فعال")
  return f"""وضعیت اشتراک من 💎

پلن: {PLAN_FA.get(sub.plan,sub.plan)}
وضعیت: {STATUS_FA.get(sub.status,sub.status)}
تاریخ پایان: {exp}
زمان باقی‌مانده: {rem}
ظرفیت گفت‌وگو: {plan_line}

برای وویس و استیکر، مونس بسته به حال‌وهوای گفتگو و پلن فعال، طبیعی و بدون نمایش عدد واکنش نشون می‌ده."""
 def activate_subscription(self,db,user,plan):
  price=self.settings.get_int(db,f"subscription.{plan}.price_coins",0); wallet=self.wallets.get_or_create_wallet(db,user)
  if wallet.balance_coins < price: return f"اعتبارت کافی نیست 😅\nبرای فعال‌سازی این اشتراک، باید {self._toman(price)} تومان داشته باشی.\n\nاعتبار فعلی: {self._toman(wallet.balance_coins)} تومان", {"inline_keyboard":[[{"text":"افزایش ظرفیت","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
  wallet=self.wallets.debit(db,user,price,reason="subscription_activation",metadata={"plan":plan,"price_coins":price}); sub=self.subscriptions.activate_plan(db,user,plan); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else '—'
  return f"اشتراک {PLAN_FA.get(plan,plan)} فعال شد ✅\nتا تاریخ {exp} می‌تونی از مونس استفاده کنی.\n\nاعتبار باقی‌مانده: {self._toman(wallet.balance_coins)} تومان", None
 def wallet_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); return f"""اعتبار کاربر 👛

اعتبار باقی‌مانده: {self._toman(w.balance_coins)} تومان

برای افزایش ظرفیت، از گزینه «افزایش ظرفیت» استفاده کن.
بعد از پرداخت و تایید ادمین، تومان‌ها به اعتبارت اضافه می‌شن."""
 def wallet_keyboard(self): return {"inline_keyboard":[[{"text":"➕ افزایش ظرفیت","callback_data":"wallet_topup_menu"}],[{"text":"تاریخچه تراکنش‌ها","callback_data":"wallet_history"}],[{"text":"رسیدهای پرداخت من","callback_data":"wallet_receipts"}]]}
 def topup_text(self,db):
  link=self.settings.get_str(db,"payment.link",get_settings().payment_link)
  return f"""برای افزایش ظرفیت، مبلغ مدنظرت رو از طریق لینک زیر واریز کن:

{link}

❇️ در صفحه باز شده، پایین صفحه مبلغ رو به تومان وارد کن.
بعد پرداخت رو بزن و بعد از اتمام پرداخت، اسکرین‌شات رسید رو همینجا ارسال کن.

====================

‼️ اسم حمایت‌کننده رو شبیه آیدی تلگرام خودت بذار تا رسیدت زودتر تایید بشه.

‼️ امکان برداشت وجه از کیف پول وجود نداره.
‼️ مسئولیت واریز اشتباه با خود کاربره.

بعد از پرداخت، دکمه «پرداخت کردم» رو بزن و تصویر رسید رو ارسال کن.

بعد از تایید ادمین، اعتبارت شارژ می‌شه 💵"""
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
