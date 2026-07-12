#!/usr/bin/env python
from __future__ import annotations
import argparse, getpass, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.core.admin_security import hash_password, normalize_username
from app.db.session import SessionLocal
from app.models.admin_security import ADMIN_ROLES, AdminUser

def main():
    ap = argparse.ArgumentParser(description="Create a database-backed Moones admin user")
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=ADMIN_ROLES, default="owner")
    ap.add_argument("--display-name", default="")
    args = ap.parse_args()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    db = SessionLocal()
    try:
        username = normalize_username(args.username)
        if db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none():
            raise SystemExit("Admin username already exists")
        db.add(AdminUser(username=username, display_name=args.display_name or username, role=args.role, password_hash=hash_password(password), is_active=True))
        db.commit()
        print(f"Created admin user {username!r} with role {args.role!r}")
    finally:
        db.close()
if __name__ == "__main__": main()
