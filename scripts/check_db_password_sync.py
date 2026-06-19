#!/usr/bin/env python3
"""Validate and optionally sync PostgreSQL password env values without leaking secrets."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


REDACTED = "<redacted>"


@dataclass(frozen=True)
class DatabaseUrlParts:
    username: str | None
    password: str | None
    hostname: str | None
    port: int | None
    database: str | None


def parse_database_url(database_url: str) -> DatabaseUrlParts:
    """Parse a SQLAlchemy DATABASE_URL and return credentials without logging them."""
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    parsed = urlsplit(database_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("DATABASE_URL must include a scheme and network location")
    database = parsed.path[1:] if parsed.path.startswith("/") else parsed.path or None
    return DatabaseUrlParts(
        username=unquote(parsed.username) if parsed.username is not None else None,
        password=unquote(parsed.password) if parsed.password is not None else None,
        hostname=parsed.hostname,
        port=parsed.port,
        database=database,
    )


def _canonical_env_password(env: dict[str, str]) -> tuple[str, str]:
    db_password = env.get("DB_PASSWORD") or ""
    postgres_password = env.get("POSTGRES_PASSWORD") or ""
    if db_password and postgres_password and db_password != postgres_password:
        raise ValueError("DB_PASSWORD and POSTGRES_PASSWORD are both set but do not match")
    if db_password:
        return "DB_PASSWORD", db_password
    if postgres_password:
        return "POSTGRES_PASSWORD", postgres_password
    raise ValueError("DB_PASSWORD or POSTGRES_PASSWORD is required")


def validate_password_sync(env: dict[str, str] | None = None) -> DatabaseUrlParts:
    env = dict(os.environ if env is None else env)
    database_url = env.get("DATABASE_URL") or ""
    parts = parse_database_url(database_url)
    if not parts.password:
        raise ValueError("DATABASE_URL must include a password")
    env_name, env_password = _canonical_env_password(env)
    if parts.password != env_password:
        raise ValueError(f"DATABASE_URL password does not match {env_name}")
    return parts


def sync_live_postgres_password(database_url: str, role: str) -> None:
    """Set the live PostgreSQL role password to the DATABASE_URL password."""
    parts = parse_database_url(database_url)
    if not parts.password:
        raise ValueError("DATABASE_URL must include a password before sync")
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:  # pragma: no cover - dependency is present in app env
        raise RuntimeError("psycopg is required for --sync-live") from exc

    with psycopg.connect(database_url) as conn:
        conn.execute(
            sql.SQL("ALTER USER {} WITH PASSWORD {}").format(
                sql.Identifier(role),
                sql.Literal(parts.password),
            )
        )
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check DB password env sync without printing secrets.")
    parser.add_argument("--sync-live", action="store_true", help="ALTER the live Postgres role password to the DATABASE_URL password")
    parser.add_argument("--role", default=os.environ.get("DB_USER", "postgres"), help="Postgres role to update with --sync-live")
    args = parser.parse_args(argv)

    try:
        parts = validate_password_sync()
        if args.sync_live:
            sync_live_postgres_password(os.environ["DATABASE_URL"], args.role)
            print(f"OK: environment passwords match and live Postgres role '{args.role}' was synced; password={REDACTED}")
        else:
            target = f"{parts.username or '<user>'}@{parts.hostname or '<host>'}/{parts.database or '<database>'}"
            print(f"OK: DB password environment values are synchronized for {target}; password={REDACTED}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}; password={REDACTED}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
