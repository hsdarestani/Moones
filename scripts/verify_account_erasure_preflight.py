"""CI-only isolated PostgreSQL account-erasure preview, with two synthetic users.

Creates disposable fictional rows in CI PostgreSQL after a full migration.
Never deletes or reads live accounts and NEVER calls any provider.
"""
from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User
from app.models.message import Message
from app.privacy.erasure_preflight import inspect_erasure_dependencies, preview_user_scope


def main():
    engine = create_engine(get_settings().database_url)
    url = engine.url
    if (os.getenv("MONES_ERASURE_PREFLIGHT_CI") != "1"
            or url.get_backend_name() != "postgresql"
            or url.host not in {"localhost", "127.0.0.1"}
            or url.database != "mones_test"):
        engine.dispose()
        raise SystemExit("Refused: requires disposable localhost/mones_test and explicit CI opt-in")
    try:
        with Session(engine) as db:
            a = User(telegram_id=995900117, display_name="SYNTHETIC_ACCOUNT_A")
            b = User(telegram_id=995900118, display_name="SYNTHETIC_ACCOUNT_B")
            db.add_all([a, b])
            db.flush()
            id_a, id_b = a.id, b.id
            db.add_all([
                Message(user_id=id_a, role="user", input_type="text",
                        content="NEVER_LEAK_THIS_PRIVATE_ABC"),
                Message(user_id=id_b, role="user", input_type="text",
                        content="NEVER_LEAK_THIS_PRIVATE_DEF"),
            ])
            db.commit()
        with engine.connect() as conn:
            schema = inspect_erasure_dependencies(engine)
            a = preview_user_scope(conn, id_a)
            b = preview_user_scope(conn, id_b)
            assert not schema["automatic_erasure_permitted"]
            assert "messages" in schema["reachable_tables"]
            assert len(schema["manual_fk_action_tables"]) > 0
            assert "payment_receipts" in schema["financial_or_external_review_tables"]
            a_rows = {r["table"]: r["rows"] for r in a["direct_owned_row_counts"]}
            b_rows = {r["table"]: r["rows"] for r in b["direct_owned_row_counts"]}
            assert a_rows["users"] == b_rows["users"] == 1
            assert a_rows["messages"] == b_rows["messages"] == 1
            assert a["schema_sha256"] == b["schema_sha256"]
            output = json.dumps({"schema": schema, "a": a, "b": b}, ensure_ascii=False)
            assert "NEVER_LEAK_THIS_PRIVATE_" not in output
        with Session(engine) as db:
            assert db.scalar(select(Message).where(Message.user_id == id_a)).content == "NEVER_LEAK_THIS_PRIVATE_ABC"
            assert db.scalar(select(Message).where(Message.user_id == id_b)).content == "NEVER_LEAK_THIS_PRIVATE_DEF"
        print(json.dumps({
            "status": "SAFE_PREVIEW_ONLY",
            "schema_fingerprint": schema["schema_sha256"][:12],
            "fk_edges": len(schema["foreign_key_edges"]),
            "manual_fk_action_tables": len(schema["manual_fk_action_tables"]),
            "retention_review_tables": len(schema["financial_or_external_review_tables"]),
            "other_user_isolated": True,
            "all_user_data_intact": True,
        }))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
