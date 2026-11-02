"""Read-only, fail-closed account erasure preflight.

Do NOT call this module "delete account". It checks FK dependencies,
financial retention, external media and denormalized PII before anyone
can design a destructive workflow. No user text, emails, Telegram IDs,
file names, or media paths appear in the report.

Never authorizes destructive writes.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

# Retention needs legal review even when ON DELETE CASCADE is configured.
BILLING_TERMS = (
    "wallet", "payment", "receipt", "invoice", "subscription",
    "usage", "charge", "transaction", "refund", "coin", "topup",
)
EXTERNAL_TERMS = (
    "media", "image", "voice", "audio", "photo", "video", "file",
    "support", "attachment", "generated",
)
PERSONAL_COLUMN_TERMS = (
    "prompt", "transcript", "content", "summary", "metadata", "file_id",
    "file_path", "stored_path", "telegram_", "message", "payload", "raw_",
    "correlation", "username", "first_name", "display_name", "text",
    "response", "last_", "description", "profile",
)
ROOTS = {"users", "messages"}
SCHEMA_ROUNDTRIP_TABLES = {"users", "messages", "media_messages"}


def categorize(name: str) -> str:
    val = name.lower()
    if any(term in val for term in BILLING_TERMS):
        return "financial_retention_review"
    if any(term in val for term in EXTERNAL_TERMS):
        return "external_media_or_support_review"
    return "conversation_or_account_review"


def inspect_erasure_dependencies(engine: Engine) -> dict[str, Any]:
    """Schema-only report; counts and content never inspected here."""
    inspector = inspect(engine)
    names = sorted(inspector.get_table_names())
    edges: list[dict] = []
    personal_columns: list[dict] = []
    owner_tables: set[str] = set()
    all_fks = []
    for table_name in names:
        cols = inspector.get_columns(table_name)
        colnames = {col["name"] for col in cols}
        nullable = {col["name"]: bool(col["nullable"]) for col in cols}
        if table_name != "users" and "user_id" in colnames:
            owner_tables.add(table_name)
        sensitive = sorted(
            col for col in colnames
            if any(token in col.lower() for token in PERSONAL_COLUMN_TERMS)
        )
        if sensitive:
            personal_columns.append({
                "table": table_name, "category": categorize(table_name),
                "columns": sensitive,
            })
        for fk in inspector.get_foreign_keys(table_name):
            parent = fk.get("referred_table") or ""
            child_cols = list(fk.get("constrained_columns") or [])
            if not parent or not child_cols:
                continue
            on_delete = ((fk.get("options") or {}).get("ondelete") or "NO ACTION").upper()
            all_fks.append((table_name, parent, child_cols, on_delete, nullable))
    # Follow *all* foreign-key chains from users/messages, including indirect
    # dependents (media -> usage; image jobs -> artifacts; etc.).
    reachable = set(ROOTS)
    changed = True
    while changed:
        changed = False
        for child, parent, _, _, _ in all_fks:
            if parent in reachable and child not in reachable:
                reachable.add(child)
                changed = True

    for child, parent, child_cols, rule, nullable in all_fks:
        if child not in reachable or parent not in reachable:
            continue
        requires_action = rule not in {"CASCADE", "SET NULL"}
        risk = categorize(child)
        edges.append({
            "child_table": child,
            "parent_table": parent,
            "fk_columns": child_cols,
            "on_delete": rule,
            "all_nullable": all(nullable.get(c, False) for c in child_cols),
            "explicit_relationship_action_required": requires_action,
            "risk": risk,
        })
    edges.sort(key=lambda item: (item["child_table"], item["parent_table"], item["fk_columns"]))
    risk_tables = sorted(t for t in reachable if categorize(t) != "conversation_or_account_review")
    blocking = sorted({x["child_table"] for x in edges if x["explicit_relationship_action_required"]})
    fingerprint_input = json.dumps({"tables": names, "edges": edges}, sort_keys=True, ensure_ascii=False)
    return {
        "status": "PREVIEW_ONLY_AUTOMATIC_DELETION_DISABLED",
        "schema_sha256": hashlib.sha256(fingerprint_input.encode()).hexdigest(),
        "reachable_tables": sorted(reachable & set(names)),
        "owner_column_tables": sorted(owner_tables),
        "foreign_key_edges": edges,
        "manual_fk_action_tables": blocking,
        "financial_or_external_review_tables": risk_tables,
        "potential_personal_columns": personal_columns,
        "external_requirements": [
            "Audit object storage and local media paths; database deletion cannot remove files.",
            "Check Telegram and support copies, Redis/cache, backups and legal retention.",
            "Check denormalized JSON/text that can contain user prompts or identifiers.",
            "Agree on accounting retention and pseudonymization before deleting any wallet, receipt, charge or usage ledger.",
            "Revoke tokens, prevent new async work, and verify idempotent account re-deletion in staging.",
        ],
        "automatic_erasure_permitted": False,
    }


def preview_user_scope(conn: Connection, user_id: int) -> dict[str, Any]:
    """Per-user ROW COUNTS only, never record text.

    Qualified identifier strings originate only from SQLAlchemy's inspector.
    A user_id match is authoritative only for an actual owner column. For
    indirect tables, report the dependency graph rather than guessing owner.
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
        raise ValueError("Expected a positive integer internal user ID")
    schema = inspect_erasure_dependencies(conn.engine)
    inspector = inspect(conn)
    preparer = conn.dialect.identifier_preparer
    results = []
    known = set(schema["owner_column_tables"])
    for table_name in schema["reachable_tables"]:
        if table_name not in known and table_name not in ROOTS:
            continue
        column = "id" if table_name == "users" else "user_id"
        colnames = {col["name"] for col in inspector.get_columns(table_name)}
        if column not in colnames:
            continue
        sql = text(
            f"SELECT count(*) FROM {preparer.quote(table_name)} "
            f"WHERE {preparer.quote(column)} = :owner_id"
        )
        count = int(conn.scalar(sql, {"owner_id": user_id}) or 0)
        if count:
            results.append({"table": table_name, "rows": count,
                            "risk": categorize(table_name)})
    return {
        "status": "COUNT_ONLY_PREVIEW_NO_MUTATIONS",
        "schema_sha256": schema["schema_sha256"],
        "internal_user_id": user_id,
        "direct_owned_row_counts": results,
        "indirect_rows_not_counted": sorted(set(schema["reachable_tables"]) - known - ROOTS),
        "automatic_erasure_permitted": False,
        "warning": "Counts do not include every indirect row, remote file, cache, archive or backup.",
    }
