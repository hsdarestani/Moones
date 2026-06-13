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
STAGE_FA={s.value:s.value for s in RelationshipStage}; STAGE_FA.update({"STRANGER":"تازه آشنا","FAMILIAR":"آشنا","FRIEND":"دوست نزدیک","ROMANTIC":"رابطه رمانتیک","PARTNER":"پارتنر"})
PLAN_FA={"free":"رایگان","daily":"روزانه","weekly":"هفتگی","monthly":"ماهانه"}; STATUS_FA={"active":"فعال","expired":"منقضی","cancelled":"لغوشده"}; TRANSACTION_FA={"credit":"افزایش","debit":"مصرف","adjustment":"اصلاح","refund":"بازگشت"}; RECEIPT_FA={"pending":"در انتظار بررسی","approved":"تایید شده","rejected":"رد شده"}
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
  if text=="پشتیبانی": return self.support_text(db), MAIN_MENU_MARKUP, True
  return "",None,False
 def chat_redirect_text(self):
  u=get_settings().telegram_chat_bot_username or "MonesChatBot"; u=u if u.startswith('@') else '@'+u
  return f"برای صحبت با پارتنرت، وارد ربات چت مونس شو 💙\n\n{u}"
 def chat_redirect_keyboard(self):
  u=(get_settings().telegram_chat_bot_username or "MonesChatBot").lstrip('@')
  return {"inline_keyboard":[[{"text":"ورود به چت","url":f"https://t.me/{u}"}]]}
 def subscription_plans(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); g=lambda k,d:self.settings.get_int(db,k,d)
  return f"""اشتراک‌های مونس 💎

برای فعال‌سازی اشتراک، اول کیف پولت رو شارژ کن.
بعد از موجودی کیف پول، می‌تونی یکی از پلن‌ها رو فعال کنی.

رایگان:
{g('limits.free.daily_messages',30)} پیام روزانه

روزانه:
قیمت: {g('subscription.daily.price_coins',100)} سکه
مدت: ۱ روز
حداکثر استفاده منصفانه: {g('limits.daily.daily_messages',500)} پیام در روز

هفتگی:
قیمت: {g('subscription.weekly.price_coins',500)} سکه
مدت: ۷ روز
حداکثر استفاده منصفانه: {g('limits.weekly.daily_messages',500)} پیام در روز

ماهانه:
قیمت: {g('subscription.monthly.price_coins',1500)} سکه
مدت: ۳۰ روز
حداکثر استفاده منصفانه: {g('limits.monthly.daily_messages',500)} پیام در روز

موجودی کیف پول تو: {w.balance_coins} سکه"""
 def subscription_keyboard(self): return {"inline_keyboard":[[{"text":"فعال‌سازی روزانه","callback_data":"sub_activate_daily"}],[{"text":"فعال‌سازی هفتگی","callback_data":"sub_activate_weekly"}],[{"text":"فعال‌سازی ماهانه","callback_data":"sub_activate_monthly"}],[{"text":"وضعیت اشتراک من","callback_data":"sub_status"}],[{"text":"افزایش موجودی","callback_data":"sub_go_topup"}]]}
 def subscription_status_text(self,db,user):
  sub=self.subscriptions.get_active_subscription(db,user) or self.subscriptions.ensure_free_subscription(db,user); usage=self.subscriptions.get_or_create_today_usage(db,user); limit=self.subscriptions.daily_limit(db,user); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else 'ندارد'; rem='—'
  if sub.expires_at:
   delta=sub.expires_at-datetime.utcnow(); rem=f"{max(0,delta.days)} روز و {max(0,delta.seconds//3600)} ساعت"
  return f"""وضعیت اشتراک من 💎

پلن: {PLAN_FA.get(sub.plan,sub.plan)}
وضعیت: {STATUS_FA.get(sub.status,sub.status)}
تاریخ پایان: {exp}
زمان باقی‌مانده: {rem}
مصرف امروز: {usage.messages_used} از {limit} پیام"""
 def activate_subscription(self,db,user,plan):
  price=self.settings.get_int(db,f"subscription.{plan}.price_coins",0); wallet=self.wallets.get_or_create_wallet(db,user)
  if wallet.balance_coins < price: return f"موجودی کیف پولت کافی نیست 😅\nبرای فعال‌سازی این اشتراک، باید {price} سکه داشته باشی.\n\nموجودی فعلی: {wallet.balance_coins} سکه", {"inline_keyboard":[[{"text":"افزایش موجودی","callback_data":"sub_go_topup"}],[{"text":"برگشت به اشتراک‌ها","callback_data":"sub_back"}]]}
  wallet=self.wallets.debit(db,user,price,reason="subscription_activation",metadata={"plan":plan,"price_coins":price}); sub=self.subscriptions.activate_plan(db,user,plan); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else '—'
  return f"اشتراک {PLAN_FA.get(plan,plan)} فعال شد ✅\nتا تاریخ {exp} می‌تونی از مونس استفاده کنی.\n\nموجودی جدید کیف پول: {wallet.balance_coins} سکه", None
 def wallet_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); return f"""کیف پولت 👛

موجودی فعلی: {w.balance_coins} سکه

برای افزایش موجودی، از گزینه «افزایش موجودی» استفاده کن.
بعد از پرداخت و تایید ادمین، سکه‌ها به کیف پولت اضافه می‌شن."""
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
  rows=self.wallets.latest_transactions(db,user,10); return "هنوز تراکنشی ثبت نشده." if not rows else "\n".join(["تاریخچه تراکنش‌ها 👛"]+[f"{tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type,tx.type)} — {tx.amount_coins} سکه — مانده: {tx.balance_after}" for tx in rows])
 def receipts_text(self,db,user):
  from sqlalchemy import select
  rows=db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id==user.id).order_by(PaymentReceipt.created_at.desc()).limit(10)).all()
  return "رسیدی ثبت نشده." if not rows else "\n".join(["رسیدهای پرداخت من 💵"]+[f"#{r.id} — {RECEIPT_FA.get(r.status,r.status)} — {r.created_at:%Y-%m-%d %H:%M}" for r in rows])
 def partner_profile(self,user): return self.onboarding.partner_profile_text(user)
 def partner_profile_keyboard(self): return {"inline_keyboard":[[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}],[{"text":"رفتن به چت","callback_data":"go_chat"}]]}
 def partner_edit_prompt_keyboard(self): return {"inline_keyboard":[[{"text":"بله، دوباره بساز","callback_data":"partner_edit_confirm"}],[{"text":"نه، منصرف شدم","callback_data":"partner_edit_cancel"}]]}
 def relationship_text(self,user):
  st=ensure_relationship(user.id,user.relationship_state); pct=lambda v:round(max(0,min(1,v))*100)
  return f"مرحله رابطه: {STAGE_FA.get(st.stage,st.stage)}\nصمیمیت: {pct(st.intimacy)}٪\nاعتماد: {pct(st.trust)}٪\nوابستگی عاطفی: {pct(st.attachment)}٪\nکشش: {pct(st.attraction)}٪"
 def settings_text(self): return "تنظیمات مونس ⚙️\n\nیکی از گزینه‌ها رو انتخاب کن:"
 def settings_keyboard(self): return {"inline_keyboard":[[{"text":"وضعیت اشتراک","callback_data":"sub_status"}],[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}],[{"text":"ریست حافظه","callback_data":"settings_reset_memory"}],[{"text":"حذف داده‌های من","callback_data":"settings_delete_data"}]]}
 def support_text(self,db):
  u=self.settings.get_str(db,"support.username",get_settings().support_username) or "YOUR_SUPPORT_USERNAME"; u=u if u.startswith('@') else '@'+u
  return f"برای پشتیبانی یا گزارش مشکل، به ادمین پیام بده:\n\n{u}"
 def settings_placeholder(self): return "این بخش فعلاً فقط به‌صورت نمایشی آماده شده و بعد از اضافه شدن تأیید امن فعال می‌شه."
