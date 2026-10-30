"""Read-only migration verification for an isolated staging/Postgres database."""
from sqlalchemy import create_engine, inspect, text
from app.core.config import get_settings


def main():
    url = get_settings().database_url
    if not url.startswith(("postgresql+", "postgresql:")):
        raise SystemExit("PostgreSQL URL required; aborting")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0043_memory_semantic_index":
                raise AssertionError(f"Unexpected Alembic head: {revision!r}")
            info = inspect(conn)
            for table in ("messages", "memory_events", "memory_facts", "memory_embeddings"):
                if not info.has_table(table):
                    raise AssertionError(f"Missing table: {table}")
            idx = {entry["name"] for entry in info.get_indexes("memory_embeddings")}
            if "ix_memory_embeddings_user_model" not in idx:
                raise AssertionError("Missing user-scoped semantic index")
            constraints = info.get_unique_constraints("memory_embeddings")
            if "uq_memory_embeddings_source_model" not in {c["name"] for c in constraints}:
                raise AssertionError("Missing per-user source/model uniqueness")
            fks = info.get_foreign_keys("memory_embeddings")
            if not any(
                fk["referred_table"] == "messages"
                and fk.get("options", {}).get("ondelete", "").upper() == "CASCADE"
                for fk in fks
            ):
                raise AssertionError("Missing source-message deletion cascade")
    finally:
        engine.dispose()
    print("PostgreSQL migration 0043 checks passed (read-only)")


if __name__ == "__main__":
    main()
