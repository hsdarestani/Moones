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
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK

MAIN_MENU_MARKUP={"keyboard":[[{"text":"مونس چیه؟"},{"text":"💬 رفتن به چت"}],[{"text":"پلن‌ها و تجربه کامل‌تر"},{"text":"👤 پارتنر من"}],[{"text":"🧩 افزودنی‌ها"},{"text":"افزودن موجودی 💳"}],[{"text":"⚙️ تنظیمات"},{"text":"🧠 وضعیت رابطه"},{"text":"پشتیبانی"}]],"resize_keyboard":True,"is_persistent":True}
STAGE_FA={s.value:s.value for s in RelationshipStage}; STAGE_FA.update({"STRANGER":"تازه آشنا","WARM":"گرم و آشنا","CLOSE":"نزدیک","PARTNER":"پارتنر","LOVER":"عاشقانه"})
PLAN_FA={"free":"رایگان","mini":"مینی","basic":"بیسیک","plus":"پلاس","vip":"VIP","daily":"روزانه","weekly":"هفتگی","monthly":"ماهانه","premium":"VIP"}; STATUS_FA={"active":"فعال","expired":"منقضی","cancelled":"لغوشده"}; TRANSACTION_FA={"credit":"افزایش","debit":"مصرف","adjustment":"اصلاح","refund":"بازگشت"}; RECEIPT_FA={"pending":"در انتظار بررسی","approved":"تایید شده","rejected":"رد شده"}
class BotMenuService:
 def __init__(self): self.wallets=WalletService(); self.subscriptions=SubscriptionService(); self.onboarding=OnboardingService(); self.settings=SettingsService(); self.addons=AddonService()
 def main_menu(self): return MAIN_MENU_MARKUP
 def handle_menu_text(self, db:Session,user:User,text:str):
  if text in {"💬 رفتن به چت","💬 گفتگو با مونس"}: return self.chat_redirect_text(), self.chat_redirect_keyboard(), True
  if text=="👤 پارتنر من": return self.partner_profile(user), self.partner_profile_keyboard(), True
  if text in {"💎 اشتراک‌ها","پلن‌ها و تجربه کامل‌تر"}: return self.subscription_plans(db,user), self.subscription_keyboard(), True
  if text=="🧩 افزودنی‌ها": return self.addons_text(db,user), self.addons_keyboard(db,user), True
  if text=="👛 کیف پول": return self.wallet_text(db,user), self.wallet_keyboard(), True
  if text in {"➕ افزایش موجودی","افزودن موجودی 💳","افزودن موجودی"}: return self.topup_text(db), self.topup_keyboard(), True
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
  return f"""پلن‌های مونس 🌙

شروع رایگانه؛ می‌تونی پارتنرت رو بسازی و چند پیام رایگان بگیری.

🟢 رایگان
حدود ۱۵ پیام در روز
برای تست و آشنا شدن با مونس.

🌱 مینی — ۵۹۰٬۰۰۰ تومان / ماه
حدود ۵۰ پیام در روز
برای چت سبک روزانه.

💙 بیسیک — ۹۹۰٬۰۰۰ تومان / ماه
حدود ۱۰۰ پیام در روز
برای استفاده روزانه و گفت‌وگوی طولانی‌تر.

💜 پلاس — ۲٬۲۹۰٬۰۰۰ تومان / ماه
نامحدود منصفانه
برای تجربه کامل‌تر، رابطه صمیمی‌تر، و استفاده جدی‌تر.

👑 VIP — ۴٬۹۰۰٬۰۰۰ تومان / ماه
نامحدود ویژه
بالاترین سطح تجربه، آزادی بیشتر، اولویت بهتر و حس زنده‌تر.

ظرفیت‌ها تقریبی‌اند و به طول پیام‌ها بستگی دارن.

اعتبار شما: {self._toman(w.balance_coins)} تومان"""
 def subscription_keyboard(self): return {"inline_keyboard":[[{"text":"خرید این پلن — مینی","callback_data":"sub_activate_mini"}],[{"text":"خرید این پلن — بیسیک","callback_data":"sub_activate_basic"}],[{"text":"خرید این پلن — پلاس","callback_data":"sub_activate_plus"}],[{"text":"خرید این پلن — VIP","callback_data":"sub_activate_vip"}],[{"text":"وضعیت اشتراکت","callback_data":"sub_status"}],[{"text":"افزودن موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def subscription_status_text(self,db,user):
  sub=self.subscriptions.get_active_subscription(db,user) or self.subscriptions.ensure_free_subscription(db,user); usage=self.subscriptions.get_or_create_today_usage(db,user); cfg=self.subscriptions.plan_config(db,user); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else 'ندارد'; rem='—'
  if sub.expires_at:
   delta=sub.expires_at-datetime.utcnow(); rem=f"{max(0,delta.days)} روز و {max(0,delta.seconds//3600)} ساعت"
  plan_line = "گفت‌وگوی نامحدود ویژه" if sub.plan in {"vip", "premium"} else ("گفت‌وگوی نامحدود منصفانه" if sub.plan in {"plus", "monthly"} else "ظرفیت روزانه فعال")
  return f"""وضعیت اشتراکت 💎

پلن: {PLAN_FA.get(sub.plan,sub.plan)}
وضعیت: {STATUS_FA.get(sub.status,sub.status)}
تاریخ پایان: {exp}
زمان باقی‌مانده: {rem}
ظرفیت گفت‌وگو: {plan_line}

برای وویس و استیکر، مونس بسته به حال‌وهوای گفتگو و پلن فعال، طبیعی و بدون نمایش عدد واکنش نشون می‌ده."""
 def activate_subscription(self,db,user,plan):
  wallet=self.wallets.get_or_create_wallet(db,user); quote=self.subscriptions.quote_upgrade(db,user,plan); price=int(quote.get("amount") or self.settings.get_int(db,f"subscription.{plan}.price_coins",0))
  if quote.get("reason") in {"same_or_lower", "lower_plan"}: return "این تغییر ارتقا حساب نمی‌شه. برای تغییر پلن، با پشتیبانی هماهنگ کن 🌙", {"inline_keyboard":[[{"text":"بازگشت","callback_data":"sub_back"}]]}
  prefix=""
  if quote.get("renewal"):
   prefix=f"برای تمدید پلن {PLAN_FA.get(plan,plan)}، مبلغ {self._toman(price)} تومان از اعتبارت کم می‌شه.\n\n"
  if quote.get("upgrade"):
   prefix=f"ارتقای پلن با کسر اعتبار باقی‌مانده انجام می‌شه 🌙\nیعنی پول پلن فعلیت از بین نمی‌ره؛ فقط مابه‌التفاوت تا پایان دوره فعلی رو پرداخت می‌کنی.\n\nبرای ارتقا از {PLAN_FA.get(quote.get('current_plan'),quote.get('current_plan'))} به {PLAN_FA.get(plan,plan)}، فقط مابه‌التفاوت باقی‌مانده این دوره رو پرداخت می‌کنی:\n{self._toman(price)} تومان\n\n"
  if wallet.balance_coins < price:
   need = f"برای تمدید پلن فعلی، باید {self._toman(price)} تومان داشته باشی." if quote.get("renewal") else f"برای فعال کردن این پلن، باید {self._toman(price)} تومان داشته باشی."
   return prefix+f"اعتبارت کافی نیست 😅\n{need}\n\nاعتبار فعلی: {self._toman(wallet.balance_coins)} تومان", {"inline_keyboard":[[{"text":"افزودن موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
  metadata=quote.get("metadata") or {"plan":plan,"price_coins":price}
  reason="subscription_renewal" if quote.get("renewal") else ("plan_upgrade" if quote.get("upgrade") else "subscription_activation")
  wallet=self.wallets.debit(db,user,price,reason=reason,metadata=metadata)
  if quote.get("renewal"): sub=self.subscriptions.renew_plan(db,user,plan)
  elif quote.get("upgrade") and quote.get("expires_at"): sub=self.subscriptions.apply_prorated_upgrade(db,user,plan,quote["expires_at"])
  else: sub=self.subscriptions.activate_plan(db,user,plan)
  exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else '—'
  if quote.get("renewal"): return f"پلن {PLAN_FA.get(plan,plan)} تمدید شد ✅\nتاریخ پایان جدید: {exp}\n\nاعتبار باقی‌مانده: {self._toman(wallet.balance_coins)} تومان", None
  return f"پلن {PLAN_FA.get(plan,plan)} فعال شد ✅\nتا تاریخ {exp} می‌تونی از مونس استفاده کنی.\n\nاعتبار باقی‌مانده: {self._toman(wallet.balance_coins)} تومان", None
 def wallet_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); return f"""اعتبار کاربر 👛

اعتبار باقی‌مانده: {self._toman(w.balance_coins)} تومان

برای افزودن موجودی، از گزینه «افزودن موجودی» استفاده کن.
بعد از پرداخت و تایید ادمین، تومان‌ها به اعتبارت اضافه می‌شن."""
 def wallet_keyboard(self): return {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"wallet_topup_menu"}],[{"text":"تاریخچه تراکنش‌ها","callback_data":"wallet_history"}],[{"text":"رسیدهای پرداخت من","callback_data":"wallet_receipts"}]]}
 def topup_text(self,db):
  link=self.settings.get_str(db,"payment.link",get_settings().payment_link)
  return f"""افزودن موجودی 💳

برای شارژ اعتبار، مبلغ مدنظرت رو از لینک زیر پرداخت کن:

{link}

بعد از پرداخت:
1. دکمه «پرداخت کردم» رو بزن.
2. تصویر رسید رو همینجا ارسال کن.
3. بعد از تایید ادمین، اعتبارت شارژ می‌شه.

نکته مهم:
اسم حمایت‌کننده رو شبیه آیدی تلگرام خودت بذار تا رسیدت سریع‌تر تایید بشه.

برداشت وجه از کیف پول امکان‌پذیر نیست.
مسئولیت واریز اشتباه با خود کاربره."""
 def topup_keyboard(self): return {"inline_keyboard":[[{"text":"پرداخت کردم ✅","callback_data":"payment_i_paid"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def history_text(self,db,user):
  rows=self.wallets.latest_transactions(db,user,10); return "هنوز تراکنشی ثبت نشده." if not rows else "\n".join(["تاریخچه تراکنش‌ها 👛"]+[f"{tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type,tx.type)} — {self._toman(tx.amount_coins)} تومان — مانده: {self._toman(tx.balance_after)} تومان" for tx in rows])
 def receipts_text(self,db,user):
  from sqlalchemy import select
  rows=db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id==user.id).order_by(PaymentReceipt.created_at.desc()).limit(10)).all()
  return "رسیدی ثبت نشده." if not rows else "\n".join(["رسیدهای پرداخت من 💵"]+[f"#{r.id} — {RECEIPT_FA.get(r.status,r.status)} — {r.created_at:%Y-%m-%d %H:%M}" for r in rows])

 def addons_text(self,db,user):
  self.addons.list_active_addons(db); price=self.addons.get_addon_price_toman(db,INTIMACY_MAX_UNLOCK)
  active="افزایش صمیمیت رابطه 🔥" if self.addons.user_has_addon(db,user.id,INTIMACY_MAX_UNLOCK) or getattr(user,"intimacy_override_max",False) else "فعلاً افزودنی فعالی نداری."
  return f"""🧩 افزودنی‌ها

اینجا می‌تونی قابلیت‌های جداگانه بخری، بدون اینکه پلنت تغییر کنه.

افزودنی‌های فعال:
{active}

افزودنی‌های قابل خرید:

🔥 افزایش صمیمیت رابطه
قیمت: {self._toman(price)} تومان

صمیمیت مونس با تو را به بالاترین سطح می‌رساند.
پلنت تغییر نمی‌کند و فقط سطح رابطه بازتر و نزدیک‌تر می‌شود."""
 def addons_keyboard(self,db,user):
  return {"inline_keyboard":[[{"text":"خرید افزایش صمیمیت 🔥","callback_data":"addon_buy_intimacy_max"}],[{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def confirm_addon_purchase(self,db,user):
  if self.addons.user_has_addon(db,user.id,INTIMACY_MAX_UNLOCK) or getattr(user,"intimacy_override_max",False): return "این افزودنی قبلاً برای تو فعاله.", self.addons_keyboard(db,user)
  if str(getattr(user,"partner_age_range","") or "").lower() in {"زیر ۱۸","زیر18","under18","under_18","minor"}: return "این افزودنی فقط برای کاربران بزرگسال فعاله.", self.addons_keyboard(db,user)
  price=self.addons.get_addon_price_toman(db,INTIMACY_MAX_UNLOCK); wallet=self.wallets.get_or_create_wallet(db,user)
  if wallet.balance_coins < price: return "اعتبارت کافی نیست. اول موجودی اضافه کن، بعد این افزودنی رو فعال کن.", {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"addons_menu"}]]}
  return f"از اعتبارت {self._toman(price)} تومان کم بشه و افزودنی فعال بشه؟", {"inline_keyboard":[[{"text":"تایید خرید","callback_data":"addon_confirm_intimacy_max"}],[{"text":"انصراف","callback_data":"addons_menu"}]]}
 def activate_addon_from_wallet(self,db,user):
  if self.addons.user_has_addon(db,user.id,INTIMACY_MAX_UNLOCK) or getattr(user,"intimacy_override_max",False): return "این افزودنی قبلاً برای تو فعاله.", self.addons_keyboard(db,user)
  if str(getattr(user,"partner_age_range","") or "").lower() in {"زیر ۱۸","زیر18","under18","under_18","minor"}: return "این افزودنی فقط برای کاربران بزرگسال فعاله.", self.addons_keyboard(db,user)
  price=self.addons.get_addon_price_toman(db,INTIMACY_MAX_UNLOCK); self.wallets.debit(db,user,price,"addon_purchase",{"addon_key":INTIMACY_MAX_UNLOCK}); self.addons.activate_addon_for_user(db,user_id=user.id,addon_key=INTIMACY_MAX_UNLOCK,source="wallet_purchase",price_paid_toman=price)
  return "انجام شد 🔥 سطح صمیمیت رابطه‌ات با مونس به بالاترین درجه رسید.", self.addons_keyboard(db,user)
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
