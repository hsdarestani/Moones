"""Standalone read-only erasure preview for a disposable staging PostgreSQL.

No --apply option exists. Never connects to production. Prints only counts,
table/column names, FK metadata and a schema hash; no chat content.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

from sqlalchemy import create_engine

from app.core.config import get_settings
from app.privacy.erasure_preflight import inspect_erasure_dependencies, preview_user_scope


def main():
    parser = argparse.ArgumentParser(description="Preview Moones account-erasure blockers (READ ONLY)")
    parser.add_argument("--user-id", type=int, help="Optional internal staging user ID; only returns counts")
    parser.add_argument("--ack-incomplete", action="store_true",
                        help="Acknowledge financial/media/backup review is still required")
    args = parser.parse_args()
    if not args.ack_incomplete:
        parser.error("--ack-incomplete required; this preview cannot guarantee erasure readiness")
    if args.user_id is not None and args.user_id <= 0:
        parser.error("--user-id must be a positive integer")

    cfg = get_settings()
    if cfg.environment.lower() in {"production", "prod"}:
        parser.error("Production access is disabled")
    engine = create_engine(cfg.database_url)
    try:
        url = engine.url
        if (url.get_backend_name() != "postgresql"
                or url.host not in {"localhost", "127.0.0.1"}
                or url.database != "mones_test"
                or os.getenv("MONES_ERASURE_PREFLIGHT_CI") != "1"):
            parser.error("Requires explicitly authorized disposable localhost/mones_test database")
        if args.user_id is None:
            report = inspect_erasure_dependencies(engine)
        else:
            with engine.connect() as conn:
                report = preview_user_scope(conn, args.user_id)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
