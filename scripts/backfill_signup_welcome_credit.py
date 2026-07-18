#!/usr/bin/env python
"""Safely backfill signup welcome coins for completed users who missed them.

This command is intentionally manual: deployments must not run it automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.wallet_service import grant_signup_welcome_credit


def parse_created_after(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def eligible_users(db, created_after: datetime | None, limit: int | None = None):
    existing_tx = select(WalletTransaction.user_id).where(
        WalletTransaction.reason == "signup_welcome_credit"
    )
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missed signup welcome coin grants.")
    parser.add_argument("--dry-run", action="store_true", help="Report eligible users without crediting wallets.")
    parser.add_argument("--created-after", help="Only include users created on/after this ISO datetime.")
    parser.add_argument("--limit", type=int, help="Maximum number of eligible users to process.")
    args = parser.parse_args()

    created_after = parse_created_after(args.created_after)
    with SessionLocal() as db:
        users = eligible_users(db, created_after, args.limit)
        if args.dry_run:
            print(f"dry_run=true eligible_users={len(users)} user_ids={[u.id for u in users]}")
            return 0
        processed = 0
        for user in users:
            grant_signup_welcome_credit(db, user)
            processed += 1
        db.commit()
        print(f"dry_run=false processed_users={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
