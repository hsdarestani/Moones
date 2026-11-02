"""Read-only dependency inventory before implementing irreversible account deletion.

Never deletes users or messages. Schema inspection exposes all foreign-key
references (including unexpected legacy tables), their ON DELETE rules and
nullability, so a real deletion workflow cannot silently miss dependencies.

Run only with a staging database:
  python -m scripts.audit_account_erasure
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect
from app.core.config import get_settings


def dependency_inventory(engine) -> dict:
    inspector = inspect(engine)
    results = []
    for table in sorted(inspector.get_table_names()):
        cols = {c["name"]: bool(c["nullable"]) for c in inspector.get_columns(table)}
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") not in ("users", "messages"):
                continue
            delete_rule = (fk.get("options") or {}).get("ondelete") or "NO ACTION"
            referenced = fk.get("referred_table")
            result = {
                "table": table,
                "references": referenced,
                "columns": list(fk["constrained_columns"]),
                "on_delete": delete_rule,
                "nullable": all(cols.get(c, False) for c in fk["constrained_columns"]),
            }
            result["safe_to_cascade_without_extra_steps"] = delete_rule.upper() in ("CASCADE", "SET NULL")
            results.append(result)
    blockers = [r for r in results if not r["safe_to_cascade_without_extra_steps"]]
    return {"dependencies": results, "requires_manual_erasure_plan": blockers,
            "ready_for_automatic_user_deletion": not blockers}


def main():
    url = get_settings().database_url
    if not url.startswith(("postgresql+", "postgresql:")):
        raise SystemExit("Only staging PostgreSQL is supported")
    engine = create_engine(url)
    try:
        print(json.dumps(dependency_inventory(engine), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
