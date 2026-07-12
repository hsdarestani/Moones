from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.admin_security import has_permission
from app.models.addon import UserAddon
from app.models.billing import UsageCharge
from app.models.image_generation import ImageGenerationJob
from app.models.payment import PaymentReceipt
from app.models.proactive import ProactiveMessage
from app.models.settings import AppSetting
from app.models.usage import AiUsageEvent
from app.models.wallet import Wallet, WalletTransaction
try:
    from app.models.generated_voice import GeneratedVoiceOutput
except Exception:  # pragma: no cover
    from app.models.image_generation import GeneratedVoiceOutput


@dataclass(frozen=True)
class MetricsRange:
    key: str
    timezone: str
    start_utc: datetime
    end_utc: datetime
    previous_start_utc: datetime
    previous_end_utc: datetime


class AdminMetricsService:
    """Read-only SQL aggregate service for admin KPIs.

    KPI definitions intentionally separate paid credits, gifts, welcome credits,
    refunds, and usage charges. Wallet credits are not revenue unless backed by
    approved top-up receipts. Usage rows are counted from UsageCharge first;
    unlinked AiUsageEvent rows are added only when no UsageCharge exists to avoid
    double-counting.
    """

    DEFAULT_TZ = "Asia/Tehran"

    def __init__(self, db: Session):
        self.db = db

    def build_range(self, range_key: str = "last_30_days", tz_name: str | None = None, custom_start: str | None = None, custom_end: str | None = None) -> MetricsRange:
        tz_name = tz_name or self.DEFAULT_TZ
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        today = now.date()
        key = range_key or "last_30_days"
        if key in {"today", "1d"}:
            start_d, end_d, key = today, today + timedelta(days=1), "today"
        elif key == "yesterday":
            start_d, end_d = today - timedelta(days=1), today
        elif key in {"7d", "last_7_days"}:
            start_d, end_d, key = today - timedelta(days=6), today + timedelta(days=1), "last_7_days"
        elif key in {"30d", "last_30_days"}:
            start_d, end_d, key = today - timedelta(days=29), today + timedelta(days=1), "last_30_days"
        elif key == "custom":
            start_d = date.fromisoformat(custom_start or today.isoformat())
            end_d = date.fromisoformat(custom_end or today.isoformat()) + timedelta(days=1)
        else:
            start_d, end_d, key = today - timedelta(days=29), today + timedelta(days=1), "last_30_days"
        start_local = datetime.combine(start_d, time.min, tzinfo=tz)
        end_local = datetime.combine(end_d, time.min, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
        delta = end_utc - start_utc
        return MetricsRange(key, tz_name, start_utc, end_utc, start_utc - delta, start_utc)

    def _setting_int(self, key: str, default: int) -> int:
        row = self.db.scalar(select(AppSetting.value).where(AppSetting.key == key))
        try: return int(row) if row is not None else default
        except (TypeError, ValueError): return default

    def financial_summary(self, r: MetricsRange) -> dict:
        pr = self.db.execute(select(
            func.coalesce(func.sum(PaymentReceipt.amount_toman), 0),
            func.coalesce(func.sum(PaymentReceipt.approved_coins), 0),
        ).where(PaymentReceipt.status == "approved", PaymentReceipt.reviewed_at >= r.start_utc, PaymentReceipt.reviewed_at < r.end_utc)).one()
        tx = self.db.execute(select(
            func.coalesce(func.sum(case((WalletTransaction.reason.in_(["admin_gift", "admin_bulk_gift", "gift", "promo"]), WalletTransaction.amount_coins), else_=0)), 0),
            func.coalesce(func.sum(case((WalletTransaction.reason.in_(["welcome", "welcome_credit", "onboarding"]), WalletTransaction.amount_coins), else_=0)), 0),
            func.coalesce(func.sum(case((WalletTransaction.reason.in_(["refund", "usage_refund"]), WalletTransaction.amount_coins), else_=0)), 0),
        ).where(WalletTransaction.type == "credit", WalletTransaction.created_at >= r.start_utc, WalletTransaction.created_at < r.end_utc)).one()
        addon_coins = self.db.scalar(select(func.coalesce(func.sum(UserAddon.price_paid_coins), 0)).where(UserAddon.created_at >= r.start_utc, UserAddon.created_at < r.end_utc)) or 0
        usage = self.db.execute(select(
            func.coalesce(func.sum(UsageCharge.charged_coins), 0),
            func.coalesce(func.sum(UsageCharge.refunded_coins), 0),
            func.coalesce(func.sum(UsageCharge.actual_cost_usd), 0),
            func.coalesce(func.sum(UsageCharge.actual_cost_usd * UsageCharge.exchange_rate_toman), 0),
            func.coalesce(func.sum(UsageCharge.charged_coins * UsageCharge.toman_per_coin), 0),
        ).where(UsageCharge.created_at >= r.start_utc, UsageCharge.created_at < r.end_utc)).one()
        wallet = self.db.execute(select(func.coalesce(func.sum(Wallet.balance_coins), 0), func.count(case((Wallet.balance_coins <= 0, 1))), func.count(case((and_(Wallet.balance_coins > 0, Wallet.balance_coins < self._setting_int("admin.metrics.low_balance_coins", 50)), 1))))).one()
        pending = self.db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status == "pending")) or 0
        provider_toman = int(usage[3] or 0); gross = int(usage[4] or 0)
        return {"approved_topup_amount_toman": int(pr[0] or 0), "topup_coins_credited": int(pr[1] or 0), "gift_promotional_coins_credited": int(tx[0] or 0), "welcome_coins_credited": int(tx[1] or 0), "refund_coins_credited": int(tx[2] or 0), "addon_purchase_coins": int(addon_coins or 0), "usage_coins_charged": int(usage[0] or 0), "usage_coins_refunded": int(usage[1] or 0), "net_usage_coins": int((usage[0] or 0) - (usage[1] or 0)), "provider_cost_usd": float(usage[2] or 0), "provider_cost_toman": provider_toman, "gross_billed_value": gross, "estimated_gross_margin": gross - provider_toman, "current_total_wallet_balance": int(wallet[0] or 0), "current_wallet_credit_value_toman": int(wallet[0] or 0) * 100, "zero_balance_users": int(wallet[1] or 0), "low_balance_users": int(wallet[2] or 0), "pending_payment_receipts": int(pending)}

    def usage_breakdown(self, r: MetricsRange, group_by: tuple[str, ...] = ("feature", "provider", "model", "status")) -> list[dict]:
        cols = [getattr(UsageCharge, g) for g in group_by]
        rows = self.db.execute(select(*cols, func.count(UsageCharge.id), func.sum(case((UsageCharge.status == "settled", 1), else_=0)), func.coalesce(func.sum(UsageCharge.charged_coins),0), func.coalesce(func.sum(UsageCharge.refunded_coins),0), func.coalesce(func.sum(UsageCharge.actual_cost_usd),0)).where(UsageCharge.created_at >= r.start_utc, UsageCharge.created_at < r.end_utc).group_by(*cols).limit(500)).all()
        out=[]
        for row in rows:
            dims = dict(zip(group_by, row[:len(group_by)])); req=int(row[-5] or 0); ok=int(row[-4] or 0); charged=int(row[-3] or 0)
            out.append({**dims, "requests": req, "success_rate": round(ok/req*100,2) if req else 0, "charged_coins": charged, "refunded_coins": int(row[-2] or 0), "provider_cost": float(row[-1] or 0), "average_latency": None, "average_coins_per_successful_request": round(charged/ok,2) if ok else 0})
        return out

    def operations_summary(self, r: MetricsRange) -> dict:
        now = datetime.utcnow(); reserved_age = self._setting_int("admin.metrics.reserved_charge_minutes", 30); pending_age = self._setting_int("admin.metrics.pending_charge_minutes", 60)
        return {"billing": {"stuck_reserved": self.db.scalar(select(func.count(UsageCharge.id)).where(UsageCharge.status=="reserved", UsageCharge.created_at < now-timedelta(minutes=reserved_age))) or 0, "old_pending_charges": self.db.scalar(select(func.count(UsageCharge.id)).where(UsageCharge.status.in_(["pending","reserved"]), UsageCharge.created_at < now-timedelta(minutes=pending_age))) or 0, "failed_settlements": self.db.scalar(select(func.count(UsageCharge.id)).where(UsageCharge.status=="failed")) or 0, "recent_refunds": self.db.scalar(select(func.count(UsageCharge.id)).where(UsageCharge.refunded_at >= r.start_utc, UsageCharge.refunded_at < r.end_utc)) or 0, "insufficient_balance_failures": self.db.scalar(select(func.count(UsageCharge.id)).where(UsageCharge.error.ilike("%insufficient%"))) or 0}, "image_jobs": self._image_jobs(now), "generated_voice": self._voice(), "proactive": {"enabled_users": 0, "overdue_scheduled_sends": self.db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.status.in_(["selected","pending"]), ProactiveMessage.created_at < now-timedelta(hours=1))) or 0, "recent_failures": self.db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.status=="failed", ProactiveMessage.created_at >= r.start_utc)) or 0, "unreachable_telegram_chats": self.db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.error.ilike("%chat%"))) or 0}, "payments": {"pending_receipts": self.db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status=="pending")) or 0, "oldest_pending_receipt": self.db.scalar(select(func.min(PaymentReceipt.created_at)).where(PaymentReceipt.status=="pending")), "recent_approval_count": self.db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status=="approved", PaymentReceipt.reviewed_at >= r.start_utc)) or 0, "recent_rejection_count": self.db.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.status=="rejected", PaymentReceipt.reviewed_at >= r.start_utc)) or 0}, "application": {"current_alembic_revision": "unknown", "expected_migration_head": "unknown", "worker_heartbeat_status": "unavailable", "webhook_freshness": "unavailable", "latest_provider_error_summaries": self.provider_errors(r)}}

    def _image_jobs(self, now):
        count = lambda cond: self.db.scalar(select(func.count(ImageGenerationJob.id)).where(cond)) or 0
        return {"queued": count(ImageGenerationJob.status=="queued"), "processing": count(ImageGenerationJob.status=="processing"), "sent": count(ImageGenerationJob.status=="sent"), "delivery_failed": count(ImageGenerationJob.status=="delivery_failed"), "generation_failed": count(ImageGenerationJob.status=="generation_failed"), "oldest_queued_job_age": self.db.scalar(select(func.min(ImageGenerationJob.created_at)).where(ImageGenerationJob.status=="queued")), "expired_locks": count(ImageGenerationJob.lock_expires_at < now), "failed_archive_deliveries": count(ImageGenerationJob.archive_status=="failed")}

    def _voice(self):
        count = lambda cond: self.db.scalar(select(func.count(GeneratedVoiceOutput.id)).where(cond)) or 0
        return {"pending": count(GeneratedVoiceOutput.status=="pending"), "failed": count(GeneratedVoiceOutput.status=="failed"), "archive_failed": count(GeneratedVoiceOutput.archive_status=="failed"), "missing_telegram_message_ids": count(GeneratedVoiceOutput.user_telegram_message_id.is_(None))}

    def provider_errors(self, r):
        rows = self.db.execute(select(AiUsageEvent.provider, AiUsageEvent.model, func.count(AiUsageEvent.id)).where(AiUsageEvent.status != "success", AiUsageEvent.created_at >= r.start_utc, AiUsageEvent.created_at < r.end_utc).group_by(AiUsageEvent.provider, AiUsageEvent.model).limit(20)).all()
        return [{"provider": a, "model": b, "count": int(c)} for a,b,c in rows]

    def alerts(self, ops: dict) -> list[dict]:
        alerts=[]
        if ops["billing"]["stuck_reserved"] >= self._setting_int("admin.alert.reserved_charge_count", 1): alerts.append({"severity":"critical", "title":"Old reserved charges", "count": ops["billing"]["stuck_reserved"]})
        if ops["image_jobs"]["failed_archive_deliveries"] >= self._setting_int("admin.alert.archive_failure_count", 1): alerts.append({"severity":"warning", "title":"Archive delivery failures", "count": ops["image_jobs"]["failed_archive_deliveries"]})
        if ops["payments"]["pending_receipts"] >= self._setting_int("admin.alert.pending_receipts_count", 5): alerts.append({"severity":"warning", "title":"Payment receipt backlog", "count": ops["payments"]["pending_receipts"]})
        if not alerts: alerts.append({"severity":"information", "title":"No configured operational alerts", "count": 0})
        return alerts

    def dashboard(self, r: MetricsRange, role: str = "viewer") -> dict:
        ops = self.operations_summary(r); can_finance = has_permission(role, "financial_metrics.read") or role in {"owner", "finance"}
        return {"range": r, "financial": self.financial_summary(r) if can_finance else None, "usage": self.usage_breakdown(r), "operations": ops, "alerts": self.alerts(ops), "can_view_financial": can_finance}
