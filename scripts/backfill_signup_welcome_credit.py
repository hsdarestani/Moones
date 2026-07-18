#!/usr/bin/env python
"""Safely audit and repair signup welcome coins.

This command is intentionally manual: deployments must not run it automatically.
It is dry-run by default; pass --apply to write marker repairs or grants.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select

from app.db.session import SessionLocal
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.wallet_service import WELCOME_CREDIT_REASON, ensure_signup_welcome_credit


def parse_created_after(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _welcome_tx_filter():
    return or_(
        WalletTransaction.reason == WELCOME_CREDIT_REASON,
        WalletTransaction.idempotency_key.like("welcome:%"),
    )


def eligible_users(db, created_after: datetime | None, limit: int | None = None):
    existing_tx = select(WalletTransaction.user_id).where(_welcome_tx_filter())
    stmt = select(User).where(
        User.onboarding_step == "complete",
        User.welcome_coins_granted_at.is_(None),
        User.id.not_in(existing_tx),
    ).order_by(User.id)
    if created_after is not None:
        stmt = stmt.where(User.created_at >= created_after)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def audit_users(db, created_after: datetime | None, limit: int | None = None):
    stmt = select(User).where(User.onboarding_step == "complete").order_by(User.id)
    if created_after is not None:
        stmt = stmt.where(User.created_at >= created_after)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and optionally repair signup welcome coin grants.")
    parser.add_argument("--apply", action="store_true", help="Apply safe repairs and missing grants. Default is dry-run only.")
    parser.add_argument("--dry-run", action="store_true", help="Accepted for compatibility; dry-run is already the default.")
    parser.add_argument("--created-after", help="Only include users created on/after this ISO datetime.")
    parser.add_argument("--limit", type=int, help="Maximum number of completed users to inspect.")
    args = parser.parse_args()

    created_after = parse_created_after(args.created_after)
    counts = {"eligible": 0, "granted": 0, "repaired": 0, "skipped": 0, "inconsistent": 0}
    dry_run = not args.apply
    with SessionLocal() as db:
        users = audit_users(db, created_after, args.limit)
        for user in users:
            tx = db.scalar(select(WalletTransaction).where(WalletTransaction.user_id == user.id, _welcome_tx_filter()).limit(1))
            if tx and user.welcome_coins_granted_at is None:
                counts["repaired"] += 1
                print(f"WELCOME_CREDIT_MARKER_REPAIRED user_id={user.id} source=repair_dry_run" if dry_run else f"WELCOME_CREDIT_MARKER_REPAIRED user_id={user.id} source=repair_apply")
                if not dry_run:
                    ensure_signup_welcome_credit(db, user=user, source="repair")
                continue
            if user.welcome_coins_granted_at is not None and not tx:
                counts["inconsistent"] += 1
                print(f"WELCOME_CREDIT_INCONSISTENT user_id={user.id} source=repair")
                continue
            if tx and user.welcome_coins_granted_at is not None:
                counts["skipped"] += 1
                print(f"WELCOME_CREDIT_ALREADY_GRANTED user_id={user.id} source=repair")
                continue
            counts["eligible"] += 1
            print(f"WELCOME_CREDIT_CHECK user_id={user.id} source=repair")
            if not dry_run:
                result = ensure_signup_welcome_credit(db, user=user, source="repair")
                if result.status == "granted":
                    counts["granted"] += 1
        if dry_run:
            db.rollback()
        else:
            db.commit()
    print("WELCOME_CREDIT_REPAIR_SUMMARY " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" dry_run={str(dry_run).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
