#!/usr/bin/env python
"""Read-only post-migration verification for legacy subscription preservations."""
from __future__ import annotations
import os
from sqlalchemy import create_engine, inspect, text


def main() -> int:
    url = os.environ.get("DATABASE_URL", "sqlite:///./app.db")
    safe = url.split("@")[-1] if "@" in url else url.split("://")[0] + "://…"
    engine = create_engine(url)
    with engine.connect() as c:
        names = set(inspect(c).get_table_names())
        print(f"database={safe}")
        if "legacy_subscription_preservations" not in names:
            print("preservation_row_count=0")
            print("error=legacy_subscription_preservations table missing; migration 0030 has not completed")
            return 1
        count = c.execute(text("SELECT COUNT(*) FROM legacy_subscription_preservations")).scalar() or 0
        print(f"preservation_row_count={count}")
        if "subscriptions" not in names:
            print("active_preserved_subscriptions=0")
            print("expired_preserved_subscriptions=0")
            print(f"preserved_missing_matching_subscription={count}")
            print("preserved_mismatched_user_ids=unknown_no_subscriptions_table")
            return 0
        active = c.execute(text("""
            SELECT COUNT(*) FROM legacy_subscription_preservations p
            JOIN subscriptions s ON s.id=p.subscription_id
            WHERE p.preservation_policy='preserve_until_expiry'
              AND s.user_id=p.user_id AND s.status IN ('active','trialing')
              AND COALESCE(s.plan,'free') <> 'free'
              AND (s.expires_at IS NULL OR s.expires_at > CURRENT_TIMESTAMP)
        """)).scalar() or 0
        expired = c.execute(text("""
            SELECT COUNT(*) FROM legacy_subscription_preservations p
            JOIN subscriptions s ON s.id=p.subscription_id
            WHERE s.expires_at IS NOT NULL AND s.expires_at <= CURRENT_TIMESTAMP
        """)).scalar() or 0
        missing = c.execute(text("""
            SELECT COUNT(*) FROM legacy_subscription_preservations p
            LEFT JOIN subscriptions s ON s.id=p.subscription_id
            WHERE s.id IS NULL
        """)).scalar() or 0
        mismatched = c.execute(text("""
            SELECT COUNT(*) FROM legacy_subscription_preservations p
            JOIN subscriptions s ON s.id=p.subscription_id
            WHERE s.user_id <> p.user_id
        """)).scalar() or 0
        print(f"active_preserved_subscriptions={active}")
        print(f"expired_preserved_subscriptions={expired}")
        print(f"preserved_missing_matching_subscription={missing}")
        print(f"preserved_mismatched_user_ids={mismatched}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
