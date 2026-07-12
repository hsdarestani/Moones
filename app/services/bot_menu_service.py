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

تو اسم، جنسیت و حال‌وهوای پارتنرت رو انتخاب می‌کنی و رابطه‌تون با حرف‌زدن جلو می‌ره. مونس می‌تونه مهربون، بازیگوش، جدی، صمیمی یا حتی کمی لوس و قهری بشه؛ چیزهایی ازت یادش بمونه، باهات خاطره بسازه و بسته به موجودی کیف پولت با متن، وویس و استیکر زنده‌تر واکنش نشون بده."""
 def subscription_plans(self,db,user):
  w=self.wallets.get_or_create_wallet(db,user)
  if not self.settings.get_bool(db,"subscriptions.new_sales_enabled",False):
   sub=self.subscriptions.get_active_subscription(db,user)
   legacy=(f"اشتراک قدیمی {PLAN_FA.get(sub.plan,sub.plan)} شما تا {self._friendly_date(sub.expires_at)} فعال می‌مونه." if sub and sub.expires_at else "")
   pts=PricingTransparencyService()
   by_key={e.key:e for e in pts.estimates(db)}
   image_bundle=pts.image_bundle_estimate(db)
   def display(key): return by_key[key].display
   legacy_block=f"\n{legacy}\n" if legacy else ""
   return f"""کیف پول مونس 🌙

مونس مثل یک کیف پول شارژی کار می‌کنه:
کیف پولت رو شارژ می‌کنی و با هر پیام، وویس یا عکس، چند سکه از موجودیت کم می‌شه.

هر ۱ سکه = ۱۰۰ تومان اعتبار داخل مونس

موجودی شما:
{format_coins(w.balance_coins)}

هزینه‌های تقریبی:

• فرستادن یک پیام کوتاه به مونس: حدود {display('chat_short')}
• فرستادن یک وویس ۳۰ ثانیه‌ای به مونس: حدود {display('stt_30s')}
• فرستادن یک وویس یک‌دقیقه‌ای به مونس: حدود {display('stt_60s')}
• فرستادن یک عکس برای مونس: حدود {display('vision_input')}
• گرفتن یک جواب صوتی کوتاه از مونس: حدود {display('tts_100')}
• ساخت یک عکس توسط مونس: حدود {image_bundle.display}

پیام‌ها، وویس‌ها و جواب‌های طولانی‌تر ممکنه سکه بیشتری مصرف کنن.
{legacy_block}
سکه‌ها فقط برای استفاده داخل مونس هستن و قابل برداشت یا تبدیل به پول نقد نیستن."""
  rows=[]
  for code in ["mini","basic","plus","vip"]:
   price=self.settings.get_int(db,f"subscription.{code}.price_coins", self.subscriptions.plan_config_by_code(db,code).price_coins if hasattr(self.subscriptions,'plan_config_by_code') else 0)
   rows.append(f"• {PLAN_FA.get(code,code)} — {format_coin_toman_pair(price)}: عضویت تجربه؛ مصرف ارائه‌دهنده جداگانه با سکه محاسبه می‌شود.")
  return "عضویت‌های تجربه مونس 🌙\n\nنرخ ثابت: ۱ سکه = ۱۰۰ تومان\n"+"\n".join(rows)+f"\n\nموجودی شما: {format_coins(w.balance_coins)}"
 def subscription_keyboard(self):
  return {"inline_keyboard":[[{"text":"وضعیت کیف پول","callback_data":"sub_status"}],[{"text":"افزودن موجودی","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"sub_back"}]]}
 def _friendly_date(self, value):
  return value.strftime('%Y/%m/%d') if value else '—'
 def subscription_status_text(self,db,user):
  sub=self.subscriptions.get_active_subscription(db,user) or self.subscriptions.ensure_free_subscription(db,user); usage=self.subscriptions.get_or_create_today_usage(db,user); cfg=self.subscriptions.plan_config(db,user); exp=sub.expires_at.strftime('%Y-%m-%d %H:%M') if sub.expires_at else 'ندارد'; rem='—'
  if sub.expires_at:
   delta=sub.expires_at-datetime.utcnow(); rem=f"{max(0,delta.days)} روز و {max(0,delta.seconds//3600)} ساعت"
  plan_line = "گفت‌وگوی نامحدود ویژه" if sub.plan in {"vip", "premium"} else ("گفت‌وگوی نامحدود منصفانه" if sub.plan in {"plus", "monthly"} else "ظرفیت روزانه فعال")
  return f"""وضعیت اشتراک 💎

اشتراک: {PLAN_FA.get(sub.plan,sub.plan)}
وضعیت: {STATUS_FA.get(sub.status,sub.status)}
تاریخ پایان: {exp}
زمان باقی‌مانده: {rem}
وضعیت اشتراک: {plan_line}

برای وویس و استیکر، مونس بسته به حال‌وهوای گفتگو و اشتراک فعال، طبیعی و بدون نمایش عدد واکنش نشون می‌ده."""
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
 def _addon_duration_label(self,product):
  meta=product.metadata_json if isinstance(product.metadata_json,dict) else {}; days=meta.get("duration_days")
  return f"مدت‌دار: {days} روز" if isinstance(days,int) and days>0 else "دائمی"
 def addons_text(self,db,user):
  products=self.addons.list_active_addons(db)
  from sqlalchemy import select
  from app.models.addon import UserAddon, AddonProduct
  rows=db.execute(select(UserAddon,AddonProduct).join(AddonProduct, UserAddon.addon_key==AddonProduct.key).where(UserAddon.user_id==user.id, UserAddon.status=="active").order_by(AddonProduct.sort_order)).all()
  active_lines=[]
  for ua,prod in rows:
   expiry=f" — تا {ua.expires_at:%Y-%m-%d}" if ua.expires_at else " — دائمی"
   active_lines.append(f"• {prod.title}{expiry}")
  active="\n".join(active_lines) if active_lines else "فعلاً افزودنی فعالی نداری."
  blocks=[]
  for p in products:
   state="فعال ✅" if self._addon_active(db,user,p.key) else "غیرفعال"
   blocks.append(f"• {p.title}\n{p.description or ''}\nقیمت: {self._toman(self.addons.get_addon_price_coins(db,p.key))} سکه\nوضعیت: {state} — {self._addon_duration_label(p)}")
  return "🧩 افزودنی‌ها\n\nاینجا می‌تونی قابلیت‌های جداگانه بخری، بدون اینکه پلنت تغییر کنه.\n\nافزودنی‌های فعال:\n"+active+"\n\nافزودنی‌های قابل خرید:\n\n"+"\n\n".join(blocks)
 def addons_keyboard(self,db,user):
  rows=[]
  for p in self.addons.list_active_addons(db):
   if not self._addon_active(db,user,p.key): rows.append([{"text":f"خرید {p.title}","callback_data":f"addon_buy:{p.key}"}])
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
  if self._addon_active(db,user,addon_key): return "این افزودنی قبلاً برای تو فعاله؛ هزینه‌ای کم نشد.", self.addons_keyboard(db,user)
  price=self.addons.get_addon_price_coins(db,addon_key); wallet=self.wallets.get_or_create_wallet(db,user)
  idem=f"addon_purchase:{user.id}:{addon_key}"
  if db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key==idem)):
   self.addons.activate_addon_for_user(db,user_id=user.id,addon_key=addon_key,source="wallet_purchase",price_paid_coins=price); return "این خرید قبلاً ثبت شده و افزودنی فعاله.", self.addons_keyboard(db,user)
  if wallet.balance_coins < price: return "اعتبارت کافی نیست. اول موجودی اضافه کن، بعد این افزودنی رو فعال کن.", {"inline_keyboard":[[{"text":"افزودن موجودی 💳","callback_data":"sub_go_topup"}],[{"text":"بازگشت","callback_data":"addons_menu"}]]}
  self.wallets.debit(db,user,price,"addon_purchase",{"addon_key":addon_key})
  tx=db.scalar(select(WalletTransaction).where(WalletTransaction.wallet_id==wallet.id, WalletTransaction.reason=="addon_purchase", WalletTransaction.idempotency_key==None).order_by(WalletTransaction.id.desc()))
  if tx: tx.idempotency_key=idem; tx.metadata_json={"addon_key":addon_key}
  self.addons.activate_addon_for_user(db,user_id=user.id,addon_key=addon_key,source="wallet_purchase",price_paid_coins=price)
  if addon_key=="image_generation_unlock": msg="انجام شد ✅ درخواست تصویر مونس برای تو باز شد. هر تصویر هزینه مصرف جداگانه دارد و این خرید تصویر رایگان نمی‌دهد. حالا می‌تونی به ربات چت برگردی و درخواست تصویر بدی."
  else: msg=f"انجام شد ✅ افزودنی {product.title} فعال شد."
  return msg, self.addons_keyboard(db,user)
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
 def settings_keyboard(self): return {"inline_keyboard":[[{"text":"پیام‌های خودجوش: روشن باشه","callback_data":"proactive_on"}],[{"text":"پیام‌های خودجوش: خاموش باشه","callback_data":"proactive_off"}],[{"text":"وضعیت کیف پول","callback_data":"sub_status"}],[{"text":"ویرایش پارتنر","callback_data":"partner_edit_prompt"}]]}
 def support_text(self,db):
  return "پیامت رو همین‌جا بنویس و بفرست 💬\nتیم پشتیبانی مونس مستقیم می‌خونتش و جوابش همین‌جا برات میاد."
 def settings_placeholder(self): return "این بخش فعلاً فقط به‌صورت نمایشی آماده شده و بعد از اضافه شدن تأیید امن فعال می‌شه."
