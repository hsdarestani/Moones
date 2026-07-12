from __future__ import annotations
from dataclasses import dataclass
from app.llm.image_client import DEFAULT_IMAGE_MODEL, image_resolution_tier
from app.services.coin_pricing_service import CoinPricingService
from app.services.coin_formatting_service import format_coin_toman_pair
from app.services.provider_pricing_registry import get_price

@dataclass(frozen=True)
class PricingEstimate:
    key: str
    label: str
    coins: int
    display: str
    note: str = "تخمینی است؛ هزینه واقعی به اندازه ورودی/خروجی بستگی دارد."

class PricingTransparencyService:
    def __init__(self, pricing: CoinPricingService | None = None):
        self.pricing = pricing or CoinPricingService()
    def estimates(self, db):
        items=[]
        def add(key,label,quote): items.append(PricingEstimate(key,label,quote.charged_coins,format_coin_toman_pair(quote.charged_coins)))
        add('chat_short','چت متنی کوتاه', self.pricing.quote_tokens(db, model='qwen-3-6-plus', input_tokens=250, output_tokens=250))
        add('stt_30s','تبدیل گفتار ۳۰ ثانیه', self.pricing.quote_unit(db, model='openai/whisper-large-v3', feature='stt', quantity=30))
        add('stt_60s','تبدیل گفتار ۶۰ ثانیه', self.pricing.quote_unit(db, model='openai/whisper-large-v3', feature='stt', quantity=60))
        add('vision_input','ورودی تصویر/بینایی', self.pricing.quote_tokens(db, model='qwen3-vl-235b-a22b', feature='vision', input_tokens=1000, output_tokens=250))
        add('tts_100','وویس ۱۰۰ نویسه فارسی', self.pricing.quote_unit(db, model='tts-gemini-3-1-flash', feature='tts', quantity=100))
        add('tts_300','وویس ۳۰۰ نویسه فارسی', self.pricing.quote_unit(db, model='tts-gemini-3-1-flash', feature='tts', quantity=300))
        for w,h,key in [(1024,1024,'image_1k'),(2048,2048,'image_2k')]:
            price=get_price('venice', DEFAULT_IMAGE_MODEL, image_resolution_tier(w,h))
            add(key, f'ساخت تصویر {w//1024}K', self.pricing.quote_usd(db, price.standard_rate_usd, {'feature':'image_generation','model':DEFAULT_IMAGE_MODEL,'resolution':f'{w}x{h}'}))
        return items
    def image_bundle_estimate(self, db):
        from app.services.image_generation_service import image_generation_quote
        q=image_generation_quote(db)
        return PricingEstimate('image_bundle','پرامپت + ساخت تصویر',q.charged_coins,format_coin_toman_pair(q.charged_coins))
