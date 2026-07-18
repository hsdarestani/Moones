from dataclasses import dataclass
from datetime import datetime
import logging

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction

VALID_TRANSACTION_TYPES = {"credit", "debit", "adjustment", "refund"}
WELCOME_CREDIT_REASON = "signup_welcome_credit"
WELCOME_CREDIT_IDEMPOTENCY_PREFIX = "welcome:"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WelcomeCreditResult:
    status: str
    wallet: Wallet
    transaction: WalletTransaction | None = None
    amount: int | None = None



class WalletService:
    def get_or_create_wallet(self, db: Session, user: User) -> Wallet:
        wallet = user.wallet or db.scalar(select(Wallet).where(Wallet.user_id == user.id))
        if wallet:
            return wallet
        wallet = Wallet(user_id=user.id)
        wallet.user = user
        db.add(wallet)
        db.flush()
        return wallet

    def get_balance(self, db: Session, user: User) -> int:
        return self.get_or_create_wallet(db, user).balance_coins

    def can_afford(self, db: Session, user: User, amount_coins: int) -> bool:
        return self.get_balance(db, user) >= amount_coins

    def credit(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None, idempotency_key: str | None = None) -> Wallet:
        if amount_coins <= 0:
            raise ValueError("Credit amount must be positive")
        if idempotency_key and db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idempotency_key)):
            return self.get_or_create_wallet(db, user)
        wallet = self.get_or_create_wallet(db, user)
        wallet.balance_coins += amount_coins
        wallet.total_added_coins += amount_coins
        wallet.last_recharged_at = datetime.utcnow()
        user.low_balance_notified_level = None
        self._record(db, user, wallet, "credit", amount_coins, reason, metadata, idempotency_key=idempotency_key)
        return wallet

    def debit(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None, idempotency_key: str | None = None) -> Wallet:
        if amount_coins <= 0:
            raise ValueError("Debit amount must be positive")
        if idempotency_key and db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idempotency_key)):
            return self.get_or_create_wallet(db, user)
        wallet = self.get_or_create_wallet(db, user)
        if wallet.balance_coins < amount_coins:
            raise ValueError("Insufficient wallet balance")
        wallet.balance_coins -= amount_coins
        wallet.total_spent_coins += amount_coins
        self._record(db, user, wallet, "debit", amount_coins, reason, metadata, idempotency_key=idempotency_key)
        return wallet

    def adjust(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None, idempotency_key: str | None = None) -> Wallet:
        if idempotency_key and db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idempotency_key)):
            return self.get_or_create_wallet(db, user)
        wallet = self.get_or_create_wallet(db, user)
        wallet.balance_coins += amount_coins
        if amount_coins >= 0:
            wallet.total_added_coins += amount_coins
        else:
            wallet.total_spent_coins += abs(amount_coins)
        self._record(db, user, wallet, "adjustment", abs(amount_coins), reason, metadata, idempotency_key=idempotency_key)
        return wallet

    def latest_transactions(self, db: Session, user: User, limit: int = 10) -> list[WalletTransaction]:
        wallet = self.get_or_create_wallet(db, user)
        return list(db.scalars(select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id).order_by(WalletTransaction.created_at.desc()).limit(limit)).all())

    def _record(self, db: Session, user: User, wallet: Wallet, type_: str, amount_coins: int, reason: str, metadata: dict | None, idempotency_key: str | None = None) -> None:
        if type_ not in VALID_TRANSACTION_TYPES:
            raise ValueError("Invalid transaction type")
        db.flush()
        db.add(WalletTransaction(user_id=user.id, wallet_id=wallet.id, type=type_, amount_coins=amount_coins, balance_after=wallet.balance_coins, reason=reason, metadata_json=metadata, unit="coin", idempotency_key=idempotency_key))


def _welcome_idempotency_key(user: User) -> str:
    return f"{WELCOME_CREDIT_IDEMPOTENCY_PREFIX}{user.id}"


def _find_welcome_transaction(db: Session, user: User) -> WalletTransaction | None:
    return db.scalar(
        select(WalletTransaction)
        .where(
            WalletTransaction.user_id == user.id,
            or_(
                WalletTransaction.idempotency_key == _welcome_idempotency_key(user),
                WalletTransaction.reason == WELCOME_CREDIT_REASON,
            ),
        )
        .order_by(WalletTransaction.created_at.asc(), WalletTransaction.id.asc())
        .limit(1)
    )


def ensure_signup_welcome_credit(db: Session, *, user: User, source: str) -> WelcomeCreditResult:
    from app.services.settings_service import SettingsService

    wallet_service = WalletService()
    wallet = wallet_service.get_or_create_wallet(db, user)
    tx = _find_welcome_transaction(db, user)
    logger.info("WELCOME_CREDIT_CHECK user_id=%s source=%s", user.id, source)
    if tx is not None:
        repaired = False
        if user.welcome_coins_granted_at is None:
            user.welcome_coins_granted_at = tx.created_at or datetime.utcnow()
            repaired = True
        if user.welcome_coins_amount is None and tx.amount_coins is not None:
            user.welcome_coins_amount = tx.amount_coins
            repaired = True
        if repaired:
            db.flush()
            logger.info("WELCOME_CREDIT_MARKER_REPAIRED user_id=%s source=%s", user.id, source)
            return WelcomeCreditResult("marker_repaired", wallet, tx, tx.amount_coins)
        logger.info("WELCOME_CREDIT_ALREADY_GRANTED user_id=%s source=%s", user.id, source)
        return WelcomeCreditResult("already_granted", wallet, tx, tx.amount_coins)

    if user.welcome_coins_granted_at is not None:
        logger.warning("WELCOME_CREDIT_INCONSISTENT user_id=%s source=%s", user.id, source)
        return WelcomeCreditResult("inconsistent", wallet, None, user.welcome_coins_amount)

    amount = SettingsService().get_int(db, "billing.signup_bonus_coins", 200)
    idem = _welcome_idempotency_key(user)
    try:
        with db.begin_nested():
            wallet = wallet_service.credit(db, user, amount, WELCOME_CREDIT_REASON, {"source": source}, idempotency_key=idem)
            user.welcome_coins_granted_at = datetime.utcnow()
            user.welcome_coins_amount = amount
            db.flush()
    except IntegrityError:
        tx = _find_welcome_transaction(db, user)
        if tx is None:
            raise
        if user.welcome_coins_granted_at is None:
            user.welcome_coins_granted_at = tx.created_at or datetime.utcnow()
        if user.welcome_coins_amount is None:
            user.welcome_coins_amount = tx.amount_coins
        db.flush()
        logger.info("WELCOME_CREDIT_ALREADY_GRANTED user_id=%s source=%s", user.id, source)
        return WelcomeCreditResult("already_granted", wallet_service.get_or_create_wallet(db, user), tx, tx.amount_coins)

    tx = _find_welcome_transaction(db, user)
    logger.info("WELCOME_CREDIT_GRANTED user_id=%s source=%s", user.id, source)
    return WelcomeCreditResult("granted", wallet, tx, amount)


def grant_signup_welcome_credit(db: Session, user: User) -> Wallet:
    return ensure_signup_welcome_credit(db, user=user, source="legacy").wallet
