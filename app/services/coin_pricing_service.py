from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from sqlalchemy.orm import Session
from app.services.settings_service import SettingsService
from app.services.provider_pricing_registry import get_price, REGISTRY_VERSION
from app.core.config import get_settings
import logging
logger=logging.getLogger(__name__)

TOMAN_PER_COIN = 100
MAX_PROFIT_MARGIN_PERCENT = Decimal("1000")

class BillingSettingsError(ValueError): pass

@dataclass(frozen=True)
class CoinQuote:
    provider_cost_usd: Decimal
    provider_cost_toman: Decimal
    sell_price_toman: Decimal
    charged_coins: int
    exchange_rate_toman: Decimal
    profit_margin_percent: Decimal
    toman_per_coin: int
    pricing_snapshot: dict

class CoinPricingService:
    def __init__(self, settings: SettingsService | None = None): self.settings = settings or SettingsService()
    def billing_settings(self, db: Session) -> tuple[Decimal, Decimal]:
        env_rate = Decimal(str(get_settings().billing_usd_to_toman))
        ex = Decimal(str(self.settings.get(db,"billing.usd_to_toman",str(env_rate))))
        if ex != env_rate: logger.warning("BILLING_RATE_MISMATCH db_usd_to_toman=%s env_usd_to_toman=%s", ex, env_rate)
        margin = Decimal(str(self.settings.get(db,"billing.profit_margin_percent","100")))
        self.validate(ex, margin); return ex, margin
    def validate(self, exchange_rate: Decimal, margin: Decimal) -> None:
        if exchange_rate <= 0: raise BillingSettingsError("نرخ دلار باید عددی مثبت باشد.")
        if margin < 0 or margin > MAX_PROFIT_MARGIN_PERCENT: raise BillingSettingsError("درصد سود باید بین ۰ تا ۱۰۰۰ باشد.")
    def quote_usd(self, db: Session, provider_cost_usd: Decimal, snapshot: dict | None=None) -> CoinQuote:
        ex, margin = self.billing_settings(db)
        cost_toman = provider_cost_usd * ex
        sell = cost_toman * (Decimal("1") + margin / Decimal("100"))
        coins = int((sell / Decimal(TOMAN_PER_COIN)).to_integral_value(rounding=ROUND_CEILING))
        if provider_cost_usd > 0 and coins < 1: coins = 1
        return CoinQuote(provider_cost_usd, cost_toman, sell, coins, ex, margin, TOMAN_PER_COIN, snapshot or {})
    def quote_tokens(self, db: Session, *, provider="venice", model: str, feature="chat", input_tokens=0, output_tokens=0, long_context=False) -> CoinQuote:
        prefix = "vision" if feature == "vision" else "chat"
        inp = get_price(provider, model, f"{prefix}_input"); out = get_price(provider, model, f"{prefix}_output")
        unit_in = inp.long_context_rate_usd if long_context and inp.long_context_rate_usd else inp.standard_rate_usd
        unit_out = out.long_context_rate_usd if long_context and out.long_context_rate_usd else out.standard_rate_usd
        usd = Decimal(int(input_tokens or 0))*unit_in/Decimal(1_000_000)+Decimal(int(output_tokens or 0))*unit_out/Decimal(1_000_000)
        return self.quote_usd(db, usd, {"registry_version":REGISTRY_VERSION,"input":inp.snapshot(),"output":out.snapshot(),"input_tokens":input_tokens,"output_tokens":output_tokens})
    def quote_unit(self, db: Session, *, provider="venice", model: str, feature: str, quantity=0) -> CoinQuote:
        p = get_price(provider, model, feature); usd = Decimal(str(quantity or 0))*p.standard_rate_usd
        return self.quote_usd(db, usd, {"registry_version":REGISTRY_VERSION,"price":p.snapshot(),"quantity":str(quantity)})
