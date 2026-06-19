#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__test__ = False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Send one proactive test message to a specific user.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--force", action="store_true", help="Bypass opt-out/blocked safety checks for admin testing.")
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services.proactive_service import ProactiveService

    db = SessionLocal()
    try:
        user = db.get(User, args.user_id)
        if not user:
            print(f"user_id={args.user_id} found=False sent=False")
            return 1
        svc = ProactiveService()
        now = datetime.utcnow()
        enabled = svc.enabled(db)
        quiet_hours = svc.in_quiet_hours(db, now)
        active_plan_code = svc.subs.active_plan_code(db, user)
        skip_reason = None if args.force else svc.skip_reason(db, user, now)
        sent = False
        if args.force or skip_reason is None:
            sent = await svc.send_one(db, user, bypass_schedule=True, force=args.force)
            db.commit()
        else:
            db.rollback()
        print(f"enabled={enabled} quiet_hours={quiet_hours} active_plan_code={active_plan_code} skip_reason={skip_reason} sent={sent}")
        return 0 if sent else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
