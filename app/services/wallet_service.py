from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction

VALID_TRANSACTION_TYPES = {"credit", "debit", "adjustment", "refund"}


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

    def credit(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None) -> Wallet:
        if amount_coins <= 0:
            raise ValueError("Credit amount must be positive")
        wallet = self.get_or_create_wallet(db, user)
        wallet.balance_coins += amount_coins
        wallet.total_added_coins += amount_coins
        self._record(db, user, wallet, "credit", amount_coins, reason, metadata)
        return wallet

    def debit(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None) -> Wallet:
        if amount_coins <= 0:
            raise ValueError("Debit amount must be positive")
        wallet = self.get_or_create_wallet(db, user)
        if wallet.balance_coins < amount_coins:
            raise ValueError("Insufficient wallet balance")
        wallet.balance_coins -= amount_coins
        wallet.total_spent_coins += amount_coins
        self._record(db, user, wallet, "debit", amount_coins, reason, metadata)
        return wallet

    def adjust(self, db: Session, user: User, amount_coins: int, reason: str, metadata: dict | None = None) -> Wallet:
        wallet = self.get_or_create_wallet(db, user)
        wallet.balance_coins += amount_coins
        if amount_coins >= 0:
            wallet.total_added_coins += amount_coins
        else:
            wallet.total_spent_coins += abs(amount_coins)
        self._record(db, user, wallet, "adjustment", abs(amount_coins), reason, metadata)
        return wallet

    def latest_transactions(self, db: Session, user: User, limit: int = 10) -> list[WalletTransaction]:
        wallet = self.get_or_create_wallet(db, user)
        return list(db.scalars(select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id).order_by(WalletTransaction.created_at.desc()).limit(limit)).all())

    def _record(self, db: Session, user: User, wallet: Wallet, type_: str, amount_coins: int, reason: str, metadata: dict | None) -> None:
        if type_ not in VALID_TRANSACTION_TYPES:
            raise ValueError("Invalid transaction type")
        db.flush()
        db.add(WalletTransaction(user_id=user.id, wallet_id=wallet.id, type=type_, amount_coins=amount_coins, balance_after=wallet.balance_coins, reason=reason, metadata_json=metadata))
