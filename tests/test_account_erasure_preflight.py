"""No network and no deletion: account-erasure preflight must fail closed."""
from __future__ import annotations

import json
from sqlalchemy import (
    Column, ForeignKey, Integer, MetaData, String, Table,
    create_engine, insert
)

from app.privacy.erasure_preflight import (
    categorize, inspect_erasure_dependencies, preview_user_scope,
)


def make_schema():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    m = MetaData()
    users = Table("users", m, Column("id", Integer, primary_key=True),
                  Column("display_name", String))
    messages = Table("messages", m, Column("id", Integer, primary_key=True),
                     Column("user_id", ForeignKey("users.id")),
                     Column("content", String))
    bills = Table("payment_receipts", m, Column("id", Integer, primary_key=True),
                  Column("user_id", ForeignKey("users.id", ondelete="CASCADE")),
                  Column("receipt_metadata", String))
    media = Table("media_messages", m, Column("id", Integer, primary_key=True),
                  Column("user_id", ForeignKey("users.id")),
                  Column("message_id", ForeignKey("messages.id")),
                  Column("transcript", String), Column("stored_path", String))
    artifacts = Table("voice_artifacts", m, Column("id", Integer, primary_key=True),
                      Column("media_id", ForeignKey("media_messages.id")),
                      Column("file_path", String))
    m.create_all(engine)
    with engine.begin() as cx:
        cx.execute(insert(users), [
            {"id": 1, "display_name": "_PRIVATE_USER_"},
            {"id": 2, "display_name": "UNRELATED_USER"},
        ])
        cx.execute(insert(messages), [
            {"id": 10, "user_id": 1, "content": "PRIVATE_CHAT_SECRET"},
            {"id": 20, "user_id": 2, "content": "OTHER_CHAT_SECRET"},
        ])
        cx.execute(insert(bills), [{"id": 30, "user_id": 1, "receipt_metadata": "PRIVATE_LEDGER"}])
        cx.execute(insert(media), [{
            "id": 40, "user_id": 1, "message_id": 10,
            "transcript": "PRIVATE_AUDIO", "stored_path": "/var/secret/private.wav",
        }])
        cx.execute(insert(artifacts), [{"id": 50, "media_id": 40, "file_path": "/private/file.mp3"}])
    return engine


def test_preflight_finds_indirect_foreign_keys_and_cascade_financial_risk():
    engine = make_schema()
    try:
        report = inspect_erasure_dependencies(engine)
        assert not report["automatic_erasure_permitted"]
        assert report["status"].startswith("PREVIEW_ONLY")
        assert "voice_artifacts" in report["reachable_tables"]
        assert "media_messages" in report["manual_fk_action_tables"]
        assert "payment_receipts" in report["financial_or_external_review_tables"]
        assert any(row["child_table"] == "payment_receipts" and row["on_delete"] == "CASCADE"
                   and row["risk"] == "financial_retention_review"
                   for row in report["foreign_key_edges"])
        assert any(row["table"] == "media_messages" and "transcript" in row["columns"]
                   for row in report["potential_personal_columns"])
    finally:
        engine.dispose()


def test_preflight_counts_only_requested_owner_without_leaking_content():
    engine = make_schema()
    try:
        with engine.connect() as conn:
            first = preview_user_scope(conn, 1)
            other = preview_user_scope(conn, 2)
            rows = {x["table"]: x["rows"] for x in first["direct_owned_row_counts"]}
            assert rows["users"] == 1
            assert rows["messages"] == 1
            assert rows["payment_receipts"] == 1
            assert rows["media_messages"] == 1
            assert "voice_artifacts" in first["indirect_rows_not_counted"]
            assert {x["table"]: x["rows"] for x in other["direct_owned_row_counts"]} == {
                "messages": 1, "users": 1
            }
            report_text = json.dumps({"first": first, "schema": inspect_erasure_dependencies(engine)})
            for marker in ("PRIVATE_CHAT_SECRET", "PRIVATE_LEDGER", "/var/secret/",
                           "OTHER_CHAT_SECRET", "PRIVATE_AUDIO", "_PRIVATE_USER_"):
                assert marker not in report_text
    finally:
        engine.dispose()


def test_preflight_never_deletes_any_rows_even_when_called_repeatedly():
    engine = make_schema()
    try:
        with engine.connect() as cx:
            one = preview_user_scope(cx, 1)
            two = preview_user_scope(cx, 1)
            assert one == two
            assert cx.scalar(__import__("sqlalchemy").text("select count(*) from messages")) == 2
            assert cx.scalar(__import__("sqlalchemy").text("select count(*) from payment_receipts")) == 1
    finally:
        engine.dispose()
