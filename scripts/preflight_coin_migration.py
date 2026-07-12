#!/usr/bin/env python
"""Read-only preflight for deploying Alembic 0030 coin migration.

Runs the dry-run report against DATABASE_URL and exits non-zero only on script or
connection errors. Production may still be at 0028; missing 0030 columns are OK.
"""
from scripts.dry_run_coin_currency_migration import main

if __name__ == "__main__":
    raise SystemExit(main())
