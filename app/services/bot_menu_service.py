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
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK, ADULT_IMAGE_GENERATION_UNLOCK
from app.services.coin_formatting_service import format_coin_toman_pair, format_coins
from app.services.pricing_transparency_service import PricingTransparencyService

MAIN_MENU_MARKUP={"keyboard":[[{"text":"مونس چیه؟"},{"text":"💬 رفتن به چت"}],[{"text":"کیف پول و هزینه‌ها"},{"text":"👤 پارتنر من"}],[{"text":"🧩 افزودنی‌ها"},{"text":"افزودن موجودی 💳"}],[{"text":"⚙️ تنظیمات"},{"text":"🧠 وضعیت رابطه"},{"text":"پشتیبانی"}]],"resize_keyboard":True,"is_persistent":True}
STAGE_FA={s.value:s.value for s in RelationshipStage}; STAGE_FA.update({"STRANGER":"تازه آشنا","WARM":"گرم و آشنا","CLOSE":"نزدیک","PARTNER":"پارتنر","LOVER":"عاشقانه"})
PLAN_FA={"free":"رایگان","mini":"مینی","basic":"بیسیک","plus":"پلاس","vip":"VIP","daily":"روزانه","weekly":"هفتگی","monthly":"ماهانه","premium":"VIP"}; STATUS_FA={"active":"فعال","expired":"منقضی","cancelled":"لغوشده"}; TRANSACTION_FA={"credit":"افزایش","debit":"مصرف","adjustment":"اصلاح","refund":"بازگشت"}; RECEIPT_FA={"pending":"در انتظار بررسی","approved":"تایید شده","rejected":"رد شده"}
class BotMenuService:
 def __init__(self): self.wallets=WalletService(); self.subscriptions=SubscriptionService(); self.onboarding=OnboardingService(); self.settings=SettingsService(); self.addons=AddonService()
 def main_menu(self): return MAIN_MENU_MARKUP
 def handle_menu_text(self, db:Session,user:User,text:str):
  if text in {"💬 رفتن به چت","💬 گفتگو با مونس"}: return self.chat_redirect_text(), self.chat_redirect_keyboard(), True
  if text=="👤 پارتنر من": return self.partner_profile(user), self.partner_profile_keyboard(), True
  if text in {"💎 سکه‌ها","سکه‌ها و تجربه کامل‌تر","کیف پول و هزینه‌ها"}: return self.subscription_plans(db,user), self.subscription_keyboard(), True
  if text=="🧩 افزودنی‌ها": return self.addons_text(db,user), self.addons_keyboard(db,user), True
  if text=="👛 کیف پول": return self.wallet_text(db,user), self.wallet_keyboard(), True
  if text in {"➕ افزایش موجودی","افزودن موجودی 💳","افزودن موجودی"}: return self.topup_text(db), self.topup_keyboard(), True
  if text=="مونس چیه؟": return self.about_text(), MAIN_MENU_MARKUP, True
  if text=="🧠 وضعیت رابطه": return self.relationship_text(user), MAIN_MENU_MARKUP, True
  if text=="⚙️ تنظیمات": return self.settings_text(user), self.settings_keyboard(user), True
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
  return """مونس یه همراه هوشمند شخصیه که با انتخاب‌های تو شکل می‌گیره 🌙

اسم، جنسیت، سن و حال‌وهوای پارتنرت رو انتخاب می‌کنی و رابطه‌تون کم‌کم با گفتگو جلو می‌ره. مونس چیزهای مهم رو به خاطر می‌سپره، حال‌وهوای رابطه رو دنبال می‌کنه و گاهی خودش هم سراغت میاد.

می‌تونی با متن و وویس باهاش حرف بزنی، عکس بفرستی تا ببینه و بسته به قابلیت‌های فعالت با وویس، استیکر و عکس جواب بگیری.

برای استفاده از قابلیت‌ها، کیف پول مونس رو شارژ می‌کنی و فقط به اندازه مصرفت سکه کم می‌شه. بعضی قابلیت‌ها، مثل دریافت عکس از مونس، از بخش افزودنی‌ها فعال می‌شن."""
 def _setting_int(self, db, key, default):
  getter=getattr(self.settings,"get_int",None)
  return getter(db,key,default) if getter else default
 def _recommendation_values(self,db):
  return {
   "starter": self._setting_int(db,"wallet.recommendation.starter_coins",1000),
   "regular": self._setting_int(db,"wallet.recommendation.regular_coins",3000),
   "heavy": self._setting_int(db,"wallet.recommendation.heavy_coins",5000),
   "default": self._setting_int(db,"wallet.recommendation.default_coins",3000),
  }
 def _recommendation_text(self,db):
  vals=self._recommendation_values(db)
  def toman(c): return self._toman(int(c)*100)
  return f"""برای شروع چقدر شارژ کنم؟

• شروع و آشنایی — {format_coins(vals['starter'])}
  مناسب برای چت سبک و امتحان چند قابلیت
  معادل {toman(vals['starter'])} تومان

• استفاده روزمره — {format_coins(vals['regular'])}
  پیشنهاد مناسب برای چت بیشتر و استفاده گاه‌به‌گاه از وویس و عکس
  معادل {toman(vals['regular'])} تومان

• استفاده پُرتر — {format_coins(vals['heavy'])}
  برای چت زیادتر و استفاده راحت‌تر از وویس و عکس
  معادل {toman(vals['heavy'])} تومان

پیشنهاد ما برای شروع معمولی: {format_coins(vals['default'])}"""
 def subscription_plans(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); pts=PricingTransparencyService()
  try:
   estimates=pts.estimates(db); image_bundle=pts.image_bundle_estimate(db)
  except Exception:
   from app.services.pricing_transparency_service import PricingEstimate
   estimates=[PricingEstimate("chat_short","",4,"۴ سکه"),PricingEstimate("stt_30s","",70,"۷۰ سکه"),PricingEstimate("stt_60s","",140,"۱۴۰ سکه"),PricingEstimate("vision_input","",25,"۲۵ سکه"),PricingEstimate("tts_100","",70,"۷۰ سکه")]
   image_bundle=PricingEstimate("image_bundle","",160,"۱۶۰ سکه")
  by_key={e.key:e for e in estimates}
  def display(key): return by_key[key].display
  return f"""کیف پول مونس 🌙

مونس مثل یک کیف پول شارژی کار می‌کنه:
کیف پولت رو شارژ می‌کنی و با هر پیام، وویس یا عکس، چند سکه از موجودیت کم می‌شه.

هر ۱ سکه = ۱۰۰ تومان اعتبار داخل مونس

موجودی شما:
{format_coins(w.balance_coins)}

{self._recommendation_text(db)}

هزینه‌های تقریبی:

• فرستادن یک پیام کوتاه: حدود {display('chat_short')}
• فرستادن یک وویس ۳۰ ثانیه‌ای: حدود {display('stt_30s')}
• فرستادن یک وویس یک‌دقیقه‌ای: حدود {display('stt_60s')}
• فرستادن یک عکس برای مونس: حدود {display('vision_input')}
• گرفتن یک جواب صوتی کوتاه: حدود {display('tts_100')}
• دریافت یک عکس از مونس: حدود {image_bundle.display}

پیام‌ها، وویس‌ها و جواب‌های طولانی‌تر ممکنه سکه بیشتری مصرف کنن.

سکه‌ها فقط برای استفاده داخل مونس هستن و قابل برداشت یا تبدیل به پول نقد نیستن."""
 def subscription_keyboard(self):
  return {"inline_keyboard":[[{"text":"وضعیت کیف پول","callback_data":"wallet_status"}],[{"text":"افزودن موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def _friendly_date(self, value):
  return value.strftime('%Y/%m/%d') if value else '—'
 def subscription_status_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); rows=self.wallets.latest_transactions(db,user,5)
  tx_lines=[f"• {tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type,tx.type)} {self._toman(tx.amount_coins)} سکه — مانده {self._toman(tx.balance_after)}" for tx in rows]
  tx_text="\n".join(tx_lines) if tx_lines else "هنوز تراکنشی ثبت نشده."
  return f"""وضعیت کیف پول 👛

موجودی فعلی:
{format_coins(w.balance_coins)}

ارزش تقریبی داخل مونس:
{self._toman((w.balance_coins or 0)*100)} تومان

آخرین تراکنش‌ها:
{tx_text}"""
 def activate_subscription(self,db,user,plan):
  wallet=self.wallets.get_or_create_wallet(db,user); quote=self.subscriptions.quote_upgrade(db,user,plan); price=int(quote.get("amount") or self.settings.get_int(db,f"subscription.{plan}.price_coins",0))
  if quote.get("reason") in {"same_or_lower", "lower_plan"}: return "این تغییر ارتقا حساب نمی‌شه. برای تغییر اشتراک، با پشتیبانی هماهنگ کن 🌙", {"inline_keyboard":[[{"text":"بازگشت","callback_data":"sub_back"}]]}
  prefix=""
  if quote.get("renewal"):
   prefix=f"برای تمدید اشتراک {PLAN_FA.get(plan,plan)}، مبلغ {self._toman(price)} سکه از اعتبارت کم می‌شه.\n\n"
  if quote.get("upgrade"):
   prefix=f"ارتقای اشتراک با کسر اعتبار باقی‌مانده انجام می‌شه 🌙\nیعنی پول اشتراک فعلیت از بین نمی‌ره؛ فقط مابه‌التفاوت تا پایان دوره فعلی رو پرداخت می‌کنی.\n\nبرای ارتقا از {PLAN_FA.get(quote.get('current_plan'),quote.get('current_plan'))} به {PLAN_FA.get(plan,plan)}، فقط مابه‌التفاوت باقی‌مانده این دوره رو پرداخت می‌کنی:\n{self._toman(price)} سکه\n\n"
  if wallet.balance_coins < price:
   need = f"برای تمدید اشتراک فعلی، باید {self._toman(price)} سکه داشته باشی." if quote.get("renewal") else f"برای فعال‌کردن اشتراک، باید {self._toman(price)} سکه داشته باشی."
   return prefix+f"اعتبارت کافی نیست 😅\n{need}\n\nاعتبار فعلی: {self._toman(wallet.balance_coins)} سکه", {"inline_keyboard":[[{"text":"افزودن موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
  metadata=quote.get("metadata") or {"plan":plan,"price_coins":price}
  reason="subscription_renewal" if quote.get("renewal") else ("plan_upgrade" if quote.get("upgrade") else "subscription_activation")
  wallet=self.wallets.debit(db,user,price,reason=reason,metadata=metadata)
  if quote.get("renewal"): sub=self.subscriptions.renew_plan(db,user,plan)
  elif quote.get("upgrade") and quote.get("expires_at"): sub=self.subscriptions.apply_prorated_upgrade(db,user,plan,quote["expires_at"])
  else: sub=self.subscriptions.activate_plan(db,user,plan)
  exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else '—'
  if quote.get("renewal"): return f"اشتراک {PLAN_FA.get(plan,plan)} تمدید شد ✅\nتاریخ پایان جدید: {exp}\n\nاعتبار باقی‌مانده: {self._toman(wallet.balance_coins)} سکه", None
  return f"اشتراک {PLAN_FA.get(plan,plan)} فعال شد ✅\nتا تاریخ {exp} می‌تونی از مونس استفاده کنی.\n\nاعتبار باقی‌مانده: {self._toman(wallet.balance_coins)} سکه", None
 def wallet_text(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user); return f"""اعتبار کاربر 👛

اعتبار باقی‌مانده: {self._toman(w.balance_coins)} سکه

برای افزودن موجودی، از گزینه «افزودن موجودی» استفاده کن.
بعد از پرداخت و تایید ادمین، سکه‌ها به اعتبارت اضافه می‌شن."""
 def wallet_keyboard(self): return {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"wallet_topup_menu"}],[{"text":"تاریخچه تراکنش‌ها","callback_data":"wallet_history"}],[{"text":"رسیدهای پرداخت من","callback_data":"wallet_receipts"}]]}
 def topup_text(self,db):
  link=self.settings.get_str(db,"payment.link",get_settings().payment_link)
  return f"""افزودن موجودی 💳

برای استفاده از مونس باید کیف پولت رو شارژ کنی.

هر ۱۰۰ تومان پرداخت = ۱ سکه

مثال:

• ۱۰۰٬۰۰۰ تومان = ۱٬۰۰۰ سکه
• ۵۰۰٬۰۰۰ تومان = ۵٬۰۰۰ سکه
• ۱٬۰۰۰٬۰۰۰ تومان = ۱۰٬۰۰۰ سکه

{self._recommendation_text(db)}

برای پرداخت از لینک زیر استفاده کن:

{link}

بعد از پرداخت:

۱. دکمه «پرداخت کردم» رو بزن.
۲. عکس رسید رو بفرست.
۳. بعد از تأیید ادمین، سکه‌ها به کیف پولت اضافه می‌شن.

برای اینکه رسیدت سریع‌تر پیدا بشه، اسم پرداخت‌کننده رو شبیه آیدی تلگرامت بنویس.

سکه‌ها فقط برای استفاده داخل مونس هستن و امکان برداشت پول از کیف پول وجود نداره."""
 def topup_keyboard(self): return {"inline_keyboard":[[{"text":"پرداخت کردم ✅","callback_data":"payment_i_paid"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def history_text(self,db,user):
  rows=self.wallets.latest_transactions(db,user,10); return "هنوز تراکنشی ثبت نشده." if not rows else "\n".join(["تاریخچه تراکنش‌ها 👛"]+[f"{tx.created_at:%Y-%m-%d %H:%M} — {TRANSACTION_FA.get(tx.type,tx.type)} — {self._toman(tx.amount_coins)} سکه — مانده: {self._toman(tx.balance_after)} سکه" for tx in rows])
 def receipts_text(self,db,user):
  from sqlalchemy import select
  rows=db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id==user.id).order_by(PaymentReceipt.created_at.desc()).limit(10)).all()
  return "رسیدی ثبت نشده." if not rows else "\n".join(["رسیدهای پرداخت من 💵"]+[f"#{r.id} — {RECEIPT_FA.get(r.status,r.status)} — {r.created_at:%Y-%m-%d %H:%M}" for r in rows])

 def _addon_product(self,db,addon_key):
  from sqlalchemy import select
  import re
  from app.models.addon import AddonProduct
  if not addon_key or len(addon_key)>64 or not re.fullmatch(r"[A-Za-z0-9_:-]+", addon_key): return None
  return db.scalar(select(AddonProduct).where(AddonProduct.key==addon_key, AddonProduct.is_active==True))
 def _addon_active(self,db,user,addon_key):
  return self.addons.user_has_addon(db,user.id,addon_key) or (addon_key==INTIMACY_MAX_UNLOCK and getattr(user,"intimacy_override_max",False))
 def _addon_enabled(self,db,user,addon_key):
  return self.addons.user_addon_enabled(db,user.id,addon_key)
 def _addon_duration_label(self,product):
  meta=product.metadata_json if isinstance(product.metadata_json,dict) else {}; days=meta.get("duration_days")
  return f"مدت‌دار: {days} روز" if isinstance(days,int) and days>0 else "دائمی"
 def addons_text(self,db,user):
  products=self.addons.list_active_addons(db)
  active_lines=[]; purch_lines=[]
  for p in products:
   line=f"• {p.title}\n{p.description or ''}\nقیمت: {self._toman(self.addons.get_addon_price_coins(db,p.key))} سکه — {self._addon_duration_label(p)}"
   if self._addon_active(db,user,p.key):
    state = "روشن" if self._addon_enabled(db,user,p.key) else "خاموش (مالکیت حفظ شده)"
    active_lines.append(f"• {p.title} — {self._addon_duration_label(p)} — {state}")
   else: purch_lines.append(line)
  active="\n".join(active_lines) if active_lines else "فعلاً افزودنی فعالی نداری."
  purch="\n\n".join(purch_lines) if purch_lines else "همه قابلیت‌های موجود برای تو فعاله ✅"
  return "🧩 افزودنی‌های مونس\n\nاینجا می‌تونی قابلیت‌های بیشتری برای مونس فعال کنی.\n\nهر افزودنی یک‌بار فعال می‌شه. اگر استفاده از اون هزینه جدا داشته باشه، قبل از خرید شفاف نوشته می‌شه.\n\nافزودنی‌های فعال:\n"+active+"\n\nقابلیت‌های قابل فعال‌سازی:\n"+purch
 def addons_keyboard(self,db,user):
  rows=[]
  for p in self.addons.list_active_addons(db):
   if not self._addon_active(db,user,p.key): rows.append([{"text":f"خرید {p.title}","callback_data":f"addon_buy:{p.key}"}])
   elif getattr(p, "toggleable", False) or p.key==ADULT_IMAGE_GENERATION_UNLOCK:
    if self._addon_enabled(db,user,p.key): rows.append([{"text":f"خاموش کردن {p.title}","callback_data":f"addon_toggle:{p.key}:off"}])
    else: rows.append([{"text":f"روشن کردن {p.title}","callback_data":f"addon_toggle:{p.key}:on"}])
  rows.append([{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}]); rows.append([{"text":"بازگشت","callback_data":"sub_back"}])
  return {"inline_keyboard":rows}
 def confirm_addon_purchase(self,db,user,addon_key=INTIMACY_MAX_UNLOCK):
  product=self._addon_product(db,addon_key)
  if not product: return "این افزودنی در دسترس نیست.", self.addons_keyboard(db,user)
  if self._addon_active(db,user,addon_key): return "این افزودنی قبلاً برای تو فعاله.", self.addons_keyboard(db,user)
  if addon_key==INTIMACY_MAX_UNLOCK and str(getattr(user,"partner_age_range","") or "").lower() in {"زیر ۱۸","زیر18","under18","under_18","minor"}: return "این افزودنی فقط برای کاربران بزرگسال فعاله.", self.addons_keyboard(db,user)
  price=self.addons.get_addon_price_coins(db,addon_key); wallet=self.wallets.get_or_create_wallet(db,user); after=wallet.balance_coins-price
  if wallet.balance_coins < price: return "اعتبارت کافی نیست. اول موجودی اضافه کن، بعد این افزودنی رو فعال کن.", {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"addons_menu"}]]}
  msg=f"خرید افزودنی: {product.title}\nقیمت: {self._toman(price)} سکه\nموجودی فعلی: {self._toman(wallet.balance_coins)} سکه\nموجودی بعد از خرید: {self._toman(after)} سکه\nنوع: {self._addon_duration_label(product)}\n\nتایید می‌کنی؟"
  return msg, {"inline_keyboard":[[{"text":"تایید خرید","callback_data":f"addon_confirm:{addon_key}"}],[{"text":"انصراف","callback_data":"addons_menu"}]]}
 def activate_addon_from_wallet(self,db,user,addon_key=INTIMACY_MAX_UNLOCK):
  from sqlalchemy import select
  from app.models.wallet import WalletTransaction
  product=self._addon_product(db,addon_key)
  if not product: return "این افزودنی در دسترس نیست.", self.addons_keyboard(db,user)
  if self._addon_active(db,user,addon_key): return "این افزودنی قبلاً خریداری شده؛ هزینه‌ای کم نشد.", self.addons_keyboard(db,user)
  price=self.addons.get_addon_price_coins(db,addon_key); wallet=self.wallets.get_or_create_wallet(db,user)
  idem=f"addon_purchase:{user.id}:{addon_key}"
  if db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key==idem)):
   self.addons.activate_addon_for_user(db,user_id=user.id,addon_key=addon_key,source="wallet_purchase",price_paid_coins=price); return "این خرید قبلاً ثبت شده و افزودنی فعاله.", self.addons_keyboard(db,user)
  if wallet.balance_coins < price: return "اعتبارت کافی نیست. اول موجودی اضافه کن، بعد این افزودنی رو فعال کن.", {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"addons_menu"}]]}
  self.wallets.debit(db,user,price,"addon_purchase",{"addon_key":addon_key})
  tx=db.scalar(select(WalletTransaction).where(WalletTransaction.wallet_id==wallet.id, WalletTransaction.reason=="addon_purchase", WalletTransaction.idempotency_key==None).order_by(WalletTransaction.id.desc()))
  if tx: tx.idempotency_key=idem; tx.metadata_json={"addon_key":addon_key}
  self.addons.activate_addon_for_user(db,user_id=user.id,addon_key=addon_key,source="wallet_purchase",price_paid_coins=price)
  if addon_key=="image_generation_unlock": msg="انجام شد ✅ از این به بعد می‌تونی از مونس عکس بخوای. هزینه هر عکس جداگانه از کیف پولت کم می‌شه."
  else: msg=f"انجام شد ✅ افزودنی {product.title} فعال شد."
  return msg, self.addons_keyboard(db,user)

 def toggle_addon(self,db,user,addon_key,enabled):
  if not self.addons.user_owns_addon(db,user.id,addon_key):
   return "برای این کار اول باید افزودنی رو خریداری کنی.", self.addons_keyboard(db,user)
  self.addons.set_user_addon_enabled(db,user.id,addon_key,enabled)
  if enabled:
   return "افزودنی روشن شد ✅ بدون خرید دوباره.", self.addons_keyboard(db,user)
  return "افزودنی خاموش شد. مالکیتت حذف نشده و هر وقت بخوای می‌تونی دوباره روشنش کنی.", self.addons_keyboard(db,user)
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
 def _proactive_enabled(self,user): return getattr(user,"proactive_messages_enabled",True) is not False
 def settings_text(self,user):
  if self._proactive_enabled(user): return "تنظیمات مونس ⚙️\n\nپیام‌های خودجوش الان روشنه ✅\nیعنی مونس گاهی خودش هم سراغت میاد."
  return "تنظیمات مونس ⚙️\n\nپیام‌های خودجوش الان خاموشه.\nمونس فقط وقتی خودت پیام بدی جوابت رو می‌ده."
 def settings_keyboard(self,user):
  label="خاموش کردن پیام‌های خودجوش" if self._proactive_enabled(user) else "روشن کردن پیام‌های خودجوش"
  return {"inline_keyboard":[[{"text":label,"callback_data":"proactive_toggle"}],[{"text":"وضعیت کیف پول","callback_data":"wallet_status"}],[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}]]}
 def support_text(self,db):
  return "پیامت رو همین‌جا بنویس و بفرست 💬\nتیم پشتیبانی مونس مستقیم می‌خونتش و جوابش همین‌جا برات میاد."
 def settings_placeholder(self): return "این بخش فعلاً فقط به‌صورت نمایشی آماده شده و بعد از اضافه شدن تأیید امن فعال می‌شه."
